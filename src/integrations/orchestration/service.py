"""AI Chatbot orchestration service for WorkPilot.

Coordinates LLM provider, tool execution, server-side confirmation workflow,
and user permissions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import UUID, uuid4
import re

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import AI_ENABLED, get_logger
from src.integrations.orchestration.prompts import AI_ASSISTANT_SYSTEM_PROMPT
from src.integrations.orchestration.provider import LLMProvider, get_llm_provider
from src.integrations.orchestration.schemas import (
    ChatRequest,
    ChatResponseData,
    CreatedTaskResult,
    PendingAction,
    ProposedTask,
)
from src.integrations.tools.task_tools import (
    is_generic_or_empty_title,
    is_past_due_date,
    parse_due_date,
)
from src.integrations.tools.tool_executor import (
    ToolExecutionStatus,
    ToolExecutor,
    ToolResult,
)
from src.task.schema import TASK_PRIORITIES, TASK_TYPES
from src.task.service import TaskServiceError

logger = get_logger(__name__)

# Fallback in-memory store for pending actions if Redis is not configured or offline
_MEMORY_ACTION_STORE: dict[str, dict[str, Any]] = {}
_MEMORY_STORE_LOCK = asyncio.Lock()
CONFIRMATION_TTL_SECONDS = 900  # 15 minutes
DEFAULT_CLAIM_TTL_SECONDS = 30  # 30 seconds claim execution lock

FOLLOW_UP_TITLE_PROMPT = (
    "What would you like the title of the task to be? "
    "Please provide a title or meaningful name before creation."
)

METADATA_COMMAND_WORDS = {
    # Actions & auxiliary verbs
    "create", "creating", "make", "making", "add", "adding", "new", "put", "generate",
    "build", "open", "file", "insert", "please", "can", "you", "i", "want", "need",
    "would", "like", "to", "do", "help",
    # Work items
    "task", "tasks", "chore", "chores", "bug", "bugs", "feature", "features",
    "story", "stories", "user_story", "userstory", "issue", "issues", "item", "items",
    "work", "workitem", "ticket", "tickets", "workpilot",
    # Context / Prepositions
    "in", "for", "the", "a", "an", "on", "at", "of", "and", "or", "with", "from",
    "project", "projects", "current", "this", "my", "our", "workspace", "org",
    "organization", "board", "backlog", "sprint", "active",
    # Fields & values
    "set", "type", "priority", "status", "low", "medium", "high", "critical",
    "assign", "assignee", "assigned", "reporter", "reported", "me", "myself", "self",
    "story_points", "points", "storypoints", "fibonacci", "point", "pts", "sp",
    "due", "date", "duedate", "deadline", "time", "estimate", "estimated", "hours",
    # Omission words
    "empty", "none", "null", "leave", "keep", "without", "no", "blank",
    "description", "details", "titled", "named", "called", "title", "name",
}


def _extract_content_tokens(text: str) -> list[str]:
    tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    return [t for t in tokens if t not in METADATA_COMMAND_WORDS and len(t) > 1]


def is_user_title_omitted_or_invented(user_message: str, candidate_title: str) -> bool:
    """Detect if the user omitted the title from their request or if candidate_title was invented by the LLM."""
    if not candidate_title or is_generic_or_empty_title(candidate_title):
        return True

    candidate_clean = candidate_title.strip()
    title_tokens = _extract_content_tokens(candidate_clean)
    if not title_tokens:
        # Candidate title only contains metadata words (e.g. "Chore in project")
        return True

    return False


CONNECTING_WORDS = {
    "and", "or", "but", "nor", "so", "yet", "with", "without", "has", "have", "had",
    "set", "give", "giving", "given", "the", "a", "an", "this", "that", "these", "those",
    "its", "their", "our", "your", "my", "his", "her", "no", "not", "at", "in", "on",
    "by", "for", "from", "to", "as", "of", "also", "then", "plus", "is", "are", "was",
    "were", "be", "been", "being", "add", "make", "create", "update", "assign", "new",
    "some", "any", "all", "each", "every", "both", "either", "neither", "into", "onto",
    "upon", "about", "after", "before", "between", "under", "over", "through", "during",
    "level", "order",
}


def extract_all_user_priorities(user_message: str) -> list[str]:
    """Extract all priorities explicitly specified by the user in natural language.

    Returns a list of distinct normalized priority strings in the order they appear.
    """
    unquoted = re.sub(r"['\"`“”‘’][^'\"`“”‘’]+['\"`“”‘’]", " ", user_message).lower()
    found: list[str] = []

    ALL_PRIORITY_CANDIDATES = {
        "critical", "high", "medium", "low",
        "urgent", "super_urgent", "blocker", "trivial", "minor", "major", "normal", "highest", "lowest", "immediate",
    }

    def _add(val: str):
        norm = re.sub(r"[-\s]+(?:priority|level)$", "", val.strip().lower()).strip()
        if norm and norm not in CONNECTING_WORDS and norm in ALL_PRIORITY_CANDIDATES and norm not in found:
            found.append(norm)

    # 1. Compound expressions: "priority low and high", "priority: low or high", "priority low, high"
    m_compound = re.search(
        r"\bpriority(?:\s+level)?\s*(?::|=|\bis\b|\bto\b|\bas\b|\bof\b)?\s*([a-zA-Z0-9_-]+)\s*(?:\band\b|\bor\b|,|\/)\s*([a-zA-Z0-9_-]+)\b",
        unquoted,
    )
    if m_compound:
        _add(m_compound.group(1))
        _add(m_compound.group(2))

    # 2. Check all "set (the) priority to <val>"
    for m in re.finditer(r"\bset\s+(?:the\s+)?priority\s+(?:to\s+|as\s+)?([a-zA-Z0-9_-]+)\b", unquoted):
        _add(m.group(1))

    # 3. Check all "<val>-priority" or "<val> priority"
    for m in re.finditer(r"\b([a-zA-Z0-9_-]+)[-\s]+priority\b", unquoted):
        _add(m.group(1))

    # 4. Check all "priority: <val>", "priority is <val>", "priority <val>"
    for m in re.finditer(
        r"\bpriority(?:\s+level)?\s*(?::|=|\bis\b|\bto\b|\bas\b|\bof\b)?(?:\s+(?:is|set|to|marked|as)\b)*\s*([a-zA-Z0-9_-]+)\b",
        unquoted,
    ):
        _add(m.group(1))

    # 5. Check direct priority keyword mentions in priority context
    if "priority" in unquoted or "level" in unquoted:
        for p in ("critical", "high", "medium", "low", "urgent", "super_urgent", "blocker", "trivial", "minor", "major", "normal", "highest", "lowest", "immediate"):
            if re.search(rf"\b{re.escape(p)}\b", unquoted):
                _add(p)
    else:
        for p in ("critical", "high", "medium", "low", "urgent", "super_urgent", "blocker", "trivial", "minor", "major", "normal", "highest", "lowest", "immediate"):
            if re.search(rf"\b{re.escape(p)}[-\s]+(?:priority|level|task|bug|ticket|chore|story|item|issue)\b", unquoted) or re.search(rf"\b(?:is|marked|flagged|as)\s+{re.escape(p)}\b", unquoted):
                _add(p)

    return found


def extract_user_priority(user_message: str) -> str | None:
    """Extract priority explicitly specified by the user in natural language, if any."""
    priorities = extract_all_user_priorities(user_message)
    return priorities[0] if priorities else None


COMMON_UNSUPPORTED_TASK_TYPES = {
    "epic",
    "subtask",
    "sub-task",
    "defect",
    "spike",
    "improvement",
    "enhancement",
    "incident",
    "initiative",
    "milestone",
    "feature_request",
    "change_request",
}


def extract_all_user_task_types(user_message: str) -> list[str]:
    """Extract all task types explicitly specified by the user in natural language."""
    unquoted = re.sub(r"['\"`“”‘’][^'\"`“”‘’]+['\"`“”‘’]", " ", user_message).lower()
    unquoted_masked = re.sub(r"\bstory\s+(?:points?|pts?|sp)\b", " ", unquoted)
    found: list[str] = []

    ALL_TASK_TYPE_CANDIDATES = set(TASK_TYPES) | COMMON_UNSUPPORTED_TASK_TYPES

    def _is_priority_phrase(s: str) -> bool:
        norm = re.sub(r"[-\s]+(?:priority|level)$", "", s.strip()).strip()
        return norm in TASK_PRIORITIES or "priority" in s.lower() or "level" in s.lower()

    def _add(val: str):
        norm = val.strip().lower()
        if norm and norm not in CONNECTING_WORDS and not _is_priority_phrase(norm) and norm in ALL_TASK_TYPE_CANDIDATES and norm not in found:
            found.append(norm)

    # 1. Compound: "type bug and chore", "type: feature or bug"
    m_compound = re.search(
        r"\btype\s*(?::|=|\bis\b|\bto\b|\bas\b|\bof\b)?\s*([a-zA-Z0-9_-]+)\s*(?:\band\b|\bor\b|,|\/)\s*([a-zA-Z0-9_-]+)\b",
        unquoted_masked,
    )
    if m_compound:
        _add(m_compound.group(1))
        _add(m_compound.group(2))

    # 2. "set (the) type to <val>"
    for m in re.finditer(r"\bset\s+(?:the\s+)?type\s+to\s+([a-zA-Z0-9_-]+)\b", unquoted_masked):
        _add(m.group(1))

    # 3. "type: <val>", "type is <val>", "type = <val>", "type as <val>", "type to <val>", "type of <val>"
    for m in re.finditer(r"\btype\s*(?::|=|\bis\b|\bto\b|\bas\b|\bof\b)\s*([a-zA-Z0-9_-]+)\b", unquoted_masked):
        _add(m.group(1))

    # 4. "<val> type" or "<val>-type" (e.g. "chore type", "epic type")
    for m in re.finditer(r"\b([a-zA-Z0-9_-]+)[-\s]+type\b", unquoted_masked):
        val = m.group(1).strip()
        if val != "task":
            _add(val)

    # 5. Check for known valid task types mentioned directly in the message
    for t in ("bug", "feature", "chore", "story"):
        if re.search(rf"\b{t}\b", unquoted_masked):
            _add(t)

    # 6. Check for common unsupported work item types mentioned directly
    for ut in sorted(COMMON_UNSUPPORTED_TASK_TYPES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(ut)}\b", unquoted_masked):
            _add(ut)

    # Filter out generic 'task' if more specific types were found (e.g. 'bug task' -> 'bug')
    specific_types = [t for t in found if t != "task"]
    return specific_types if specific_types else found


def extract_user_task_type(user_message: str) -> str | None:
    """Extract task type explicitly specified by the user in natural language, if any."""
    types = extract_all_user_task_types(user_message)
    return types[0] if types else None


def extract_user_due_date(user_message: str) -> str | None:
    """Extract due date explicitly specified by the user in natural language, if any."""
    unquoted = re.sub(r"['\"`“”‘’][^'\"`“”‘’]+['\"`“”‘’]", " ", user_message).lower()

    # 1. 'due date: <val>', 'due date is <val>', 'due date = <val>', 'due date <val>'
    m1 = re.search(r"\bdue\s+date\s*(?::|=|\bis\b|\bto\b|\bas\b)?\s*([a-zA-Z0-9_\-\.\/]+(?:\s+[a-zA-Z0-9_\-\.\/]+)?)\b", unquoted)
    if m1:
        val = m1.group(1).strip()
        if val not in {"is", "the", "a", "an", "for", "in", "of", "to"}:
            return val

    # 2. 'deadline: <val>', 'deadline is <val>', 'deadline <val>'
    m2 = re.search(r"\bdeadline\s*(?::|=|\bis\b|\bto\b|\bas\b)?\s*([a-zA-Z0-9_\-\.\/]+(?:\s+[a-zA-Z0-9_\-\.\/]+)?)\b", unquoted)
    if m2:
        val = m2.group(1).strip()
        if val not in {"is", "the", "a", "an", "for", "in", "of", "to"}:
            return val

    # 3. 'due on <val>', 'due by <val>', 'due before <val>'
    m3 = re.search(r"\bdue\s+(?:on|by|before)\s+([a-zA-Z0-9_\-\.\/]+(?:\s+[a-zA-Z0-9_\-\.\/]+)?)\b", unquoted)
    if m3:
        val = m3.group(1).strip()
        if val not in {"the", "a", "an"}:
            return val

    # 4. 'due: <val>'
    m4 = re.search(r"\bdue\s*:\s*([a-zA-Z0-9_\-\.\/]+(?:\s+[a-zA-Z0-9_\-\.\/]+)?)\b", unquoted)
    if m4:
        return m4.group(1).strip()

    # 5. 'due <val>' (avoid prepositions)
    m5 = re.search(r"\bdue\s+([a-zA-Z0-9_\-\.\/]+)\b", unquoted)
    if m5:
        val = m5.group(1).strip()
        if val not in {"to", "in", "at", "for", "the", "a", "an", "date", "on", "by", "before"}:
            return val

    return None


LUA_CLAIM_ACTION = """
local key = KEYS[1]
local raw = redis.call('GET', key)
if not raw then
    return cjson.encode({result = 'not_found'})
end

local data = cjson.decode(raw)
if data.status == 'completed' then
    return cjson.encode({result = 'already_completed'})
end
if data.status == 'cancelled' then
    return cjson.encode({result = 'already_cancelled'})
end

local now = tonumber(ARGV[1])
local claim_ttl = tonumber(ARGV[2])
local token = ARGV[3]

if data.expires_at_ts and now > tonumber(data.expires_at_ts) then
    return cjson.encode({result = 'expired'})
end

if data.status == 'executing' then
    local claim_expires = tonumber(data.claim_expires or 0)
    if now < claim_expires then
        return cjson.encode({result = 'already_executing'})
    end
end

if data.status ~= 'pending' and data.status ~= 'executing' then
    return cjson.encode({result = 'invalid_status', current_status = data.status})
end

data.status = 'executing'
data.claim_token = token
data.claim_expires = now + claim_ttl

local ttl = redis.call('TTL', key)
if ttl <= 0 then
    ttl = 900
end
redis.call('SET', key, cjson.encode(data), 'EX', ttl)

return cjson.encode({result = 'claimed', data = data})
"""

LUA_RELEASE_CLAIM = """
local key = KEYS[1]
local token = ARGV[1]
local raw = redis.call('GET', key)
if not raw then return 0 end
local data = cjson.decode(raw)
if data.status == 'executing' then
    if token == '' or data.claim_token == token then
        data.status = 'pending'
        data.claim_token = nil
        data.claim_expires = nil
        local ttl = redis.call('TTL', key)
        if ttl <= 0 then ttl = 900 end
        redis.call('SET', key, cjson.encode(data), 'EX', ttl)
        return 1
    end
end
return 0
"""

LUA_COMPLETE_ACTION = """
local key = KEYS[1]
local token = ARGV[1]
local result_json = ARGV[2]
local raw = redis.call('GET', key)
if not raw then return 0 end
local data = cjson.decode(raw)
if data.status == 'executing' or data.status == 'pending' then
    if token == '' or not data.claim_token or data.claim_token == token then
        data.status = 'completed'
        data.result = cjson.decode(result_json)
        data.claim_token = nil
        data.claim_expires = nil
        local ttl = redis.call('TTL', key)
        if ttl <= 0 then ttl = 900 end
        redis.call('SET', key, cjson.encode(data), 'EX', ttl)
        return 1
    end
end
return 0
"""

LUA_CANCEL_ACTION = """
local key = KEYS[1]
local user_id = ARGV[1]
local organization_id = ARGV[2]
local now = tonumber(ARGV[3])

local raw = redis.call('GET', key)
if not raw then
    return cjson.encode({result = 'not_found'})
end
local data = cjson.decode(raw)

if tostring(data.user_id) ~= tostring(user_id) or tostring(data.organization_id) ~= tostring(organization_id) then
    return cjson.encode({result = 'forbidden'})
end

if data.status == 'completed' then
    return cjson.encode({result = 'already_completed'})
end
if data.status == 'executing' then
    local claim_expires = tonumber(data.claim_expires or 0)
    if now < claim_expires then
        return cjson.encode({result = 'already_executing'})
    end
end

data.status = 'cancelled'
local ttl = redis.call('TTL', key)
if ttl <= 0 then ttl = 900 end
redis.call('SET', key, cjson.encode(data), 'EX', ttl)
return cjson.encode({result = 'cancelled'})
"""


class ActionStore:
    """Manages server-side storage and lifecycle of pending confirmation actions."""

    def __init__(self, redis: Any = None) -> None:
        self.redis = redis

    async def save_pending_action(
        self,
        action_id: str,
        data: dict[str, Any],
        ttl_seconds: int = CONFIRMATION_TTL_SECONDS,
    ) -> None:
        serialized = json.dumps(data)
        if self.redis is not None:
            try:
                await self.redis.set(f"ai_action:{action_id}", serialized, ex=ttl_seconds)
                return
            except Exception as exc:
                logger.warning("Failed to store action in Redis, falling back to memory: %s", exc)

        # In-memory store fallback
        async with _MEMORY_STORE_LOCK:
            _MEMORY_ACTION_STORE[action_id] = data

    async def get_action(self, action_id: str) -> dict[str, Any] | None:
        if self.redis is not None:
            try:
                raw = await self.redis.get(f"ai_action:{action_id}")
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("Failed to retrieve action from Redis: %s", exc)

        async with _MEMORY_STORE_LOCK:
            action = _MEMORY_ACTION_STORE.get(action_id)
            if action:
                if action.get("expires_at"):
                    expires_at = datetime.fromisoformat(action["expires_at"])
                    if datetime.now(timezone.utc) > expires_at:
                        _MEMORY_ACTION_STORE.pop(action_id, None)
                        return None
                return action
            return None

    async def update_action(
        self,
        action_id: str,
        data: dict[str, Any],
        ttl_seconds: int = CONFIRMATION_TTL_SECONDS,
    ) -> None:
        await self.save_pending_action(action_id, data, ttl_seconds=ttl_seconds)

    async def claim_action(
        self,
        action_id: str,
        token: str,
        claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    ) -> dict[str, Any]:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if self.redis is not None:
            try:
                res = await self.redis.eval(
                    LUA_CLAIM_ACTION,
                    1,
                    f"ai_action:{action_id}",
                    now_ts,
                    claim_ttl_seconds,
                    token,
                )
                if res:
                    return json.loads(res)
            except Exception as exc:
                logger.warning("Redis Lua claim failed, using in-memory store: %s", exc)

        async with _MEMORY_STORE_LOCK:
            data = _MEMORY_ACTION_STORE.get(action_id)
            if not data:
                return {"result": "not_found"}

            if data.get("expires_at"):
                expires_at = datetime.fromisoformat(data["expires_at"])
                if datetime.now(timezone.utc) > expires_at:
                    data["status"] = "expired"
                    return {"result": "expired"}

            status = data.get("status")
            if status == "completed":
                return {"result": "already_completed"}
            if status == "cancelled":
                return {"result": "already_cancelled"}
            if status == "executing":
                claim_expires = data.get("claim_expires", 0)
                if now_ts < claim_expires:
                    return {"result": "already_executing"}

            if status not in ("pending", "executing"):
                return {"result": "invalid_status", "current_status": status}

            data["status"] = "executing"
            data["claim_token"] = token
            data["claim_expires"] = now_ts + claim_ttl_seconds
            return {"result": "claimed", "data": dict(data)}

    async def release_claim(self, action_id: str, token: str) -> None:
        if self.redis is not None:
            try:
                await self.redis.eval(
                    LUA_RELEASE_CLAIM,
                    1,
                    f"ai_action:{action_id}",
                    token,
                )
                return
            except Exception as exc:
                logger.warning("Redis release_claim failed, using in-memory: %s", exc)

        async with _MEMORY_STORE_LOCK:
            data = _MEMORY_ACTION_STORE.get(action_id)
            if data and data.get("status") == "executing":
                if not token or data.get("claim_token") == token:
                    data["status"] = "pending"
                    data.pop("claim_token", None)
                    data.pop("claim_expires", None)

    async def complete_action(self, action_id: str, token: str, result_data: dict[str, Any]) -> None:
        serialized = json.dumps(result_data)
        if self.redis is not None:
            try:
                await self.redis.eval(
                    LUA_COMPLETE_ACTION,
                    1,
                    f"ai_action:{action_id}",
                    token,
                    serialized,
                )
                return
            except Exception as exc:
                logger.warning("Redis complete_action failed, using in-memory: %s", exc)

        async with _MEMORY_STORE_LOCK:
            data = _MEMORY_ACTION_STORE.get(action_id)
            if data:
                data["status"] = "completed"
                data["result"] = result_data
                data.pop("claim_token", None)
                data.pop("claim_expires", None)

    async def cancel_action_atomic(
        self,
        action_id: str,
        *,
        user_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if self.redis is not None:
            try:
                res = await self.redis.eval(
                    LUA_CANCEL_ACTION,
                    1,
                    f"ai_action:{action_id}",
                    str(user_id),
                    str(organization_id),
                    now_ts,
                )
                if res:
                    return json.loads(res)
            except Exception as exc:
                logger.warning("Redis Lua cancel failed, using in-memory store: %s", exc)

        async with _MEMORY_STORE_LOCK:
            data = _MEMORY_ACTION_STORE.get(action_id)
            if not data:
                return {"result": "not_found"}

            if str(data.get("user_id")) != str(user_id) or str(data.get("organization_id")) != str(organization_id):
                return {"result": "forbidden"}

            status = data.get("status")
            if status == "completed":
                return {"result": "already_completed"}
            if status == "executing":
                claim_expires = data.get("claim_expires", 0)
                if now_ts < claim_expires:
                    return {"result": "already_executing"}

            data["status"] = "cancelled"
            return {"result": "cancelled"}

    async def mark_expired(self, action_id: str) -> None:
        action = await self.get_action(action_id)
        if action:
            action["status"] = "expired"
            await self.update_action(action_id, action)


class AIOrchestrationService:
    """Coordinates chat, LLM responses, tool calling, and action confirmation."""

    def __init__(
        self,
        db: AsyncSession,
        redis: Any = None,
        provider: LLMProvider | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        self.db = db
        self.redis = redis
        self.provider = provider or get_llm_provider()
        self.executor = executor or ToolExecutor()
        self.action_store = ActionStore(redis)

    async def process_chat(
        self,
        request: ChatRequest,
        *,
        user_id: str,
        organization_id: str,
        role: str,
    ) -> ChatResponseData:
        """Handle incoming natural-language chat request."""
        if not AI_ENABLED:
            raise TaskServiceError(503, "SERVICE_UNAVAILABLE", "AI Assistant is currently disabled.")

        conversation_id = request.conversation_id or str(uuid4())
        context_proj_id = str(request.context.project_id) if request.context and request.context.project_id else None

        # Build message history
        messages: list[dict[str, str]] = [
            {"role": "system", "content": AI_ASSISTANT_SYSTEM_PROMPT}
        ]
        context_project = None
        if context_proj_id:
            from src.project.models import Project
            try:
                context_project = await self.db.get(Project, context_proj_id)
            except Exception:
                context_project = None
            proj_name_str = f"'{context_project.name}'" if context_project and context_project.name else "the current project"
            messages.append({
                "role": "system",
                "content": f"Active context: The user is currently in project {proj_name_str} (project_id='{context_proj_id}'). "
                           f"Use project_id='{context_proj_id}' if the user asks to create a task in the current project without specifying another project. "
                           f"CRITICAL: Never put the project name in the assignee field. If the user does not specify an assignee, leave assignee null or omitted."
            })

        messages.append({"role": "user", "content": request.message})

        tools = self.executor.get_registered_declarations()

        # Generate LLM completion with tools
        llm_response = await self.provider.generate_completion(
            messages=messages,
            tools=tools,
        )

        # If model requested tool calls
        if llm_response.tool_calls:
            first_call = llm_response.tool_calls[0]
            tool_name = first_call.name
            args = dict(first_call.arguments)

            tool = self.executor.get_tool(tool_name)
            if tool is None:
                raise TaskServiceError(
                    400,
                    "UNKNOWN_TOOL",
                    f"Tool '{tool_name}' is not recognized or permitted.",
                )

            # If tool is create_task, inspect arguments and resolve project and fields
            if tool_name == "create_task":
                from src.project.models import Project
                from sqlalchemy import select, func

                raw_pid = args.get("project_id")
                raw_pname = args.get("project_name")
                raw_assignee = args.get("assignee")
                raw_type = args.get("type")
                raw_sp = args.get("story_points")

                logger.info(
                    "Inspecting create_task tool arguments: project_name=%r, project_id=%r, assignee=%r, type=%r, story_points=%r",
                    raw_pname, raw_pid, raw_assignee, raw_type, raw_sp,
                )

                # Check if project_id was passed as a non-UUID name string (e.g. "demo")
                if raw_pid:
                    try:
                        UUID(str(raw_pid).strip())
                    except ValueError:
                        if not raw_pname:
                            raw_pname = str(raw_pid).strip()
                            args["project_name"] = raw_pname
                        args["project_id"] = None
                        raw_pid = None

                # If project_id is missing and project_name is provided, resolve project_id independently
                if not args.get("project_id") and args.get("project_name"):
                    stmt = select(Project).where(
                        Project.organization_id == str(organization_id),
                        func.lower(Project.name) == str(args["project_name"]).strip().lower(),
                        Project.deleted_at.is_(None),
                    )
                    found_p = (await self.db.execute(stmt)).scalars().first()
                    if found_p:
                        args["project_id"] = str(found_p.id)
                        args["project_name"] = found_p.name
                    elif context_project and context_project.name.lower() == str(args["project_name"]).strip().lower():
                        args["project_id"] = str(context_project.id)
                        args["project_name"] = context_project.name

                # If project_id is still missing and context_proj_id exists, use context_proj_id
                if not args.get("project_id") and context_proj_id:
                    args["project_id"] = context_proj_id
                    if context_project and not args.get("project_name"):
                        args["project_name"] = context_project.name

                if not args.get("project_id") or str(args.get("project_id")).strip().lower() in ("", "none", "null", "undefined"):
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message="Please specify the project where you would like to create this task.",
                        action_state="NONE",
                    )

                # Check if assignee was erroneously populated with the project name or "demo"
                target_assignee = args.get("assignee") or args.get("assignee_id")
                if target_assignee:
                    a_clean = str(target_assignee).strip().lower()
                    resolved_pname = (args.get("project_name") or (context_project.name if context_project else "")).strip().lower()
                    if a_clean == "demo" or (resolved_pname and a_clean == resolved_pname):
                        logger.warning(
                            "Clearing misrouted assignee %r which matches project name %r; user did not specify this as an assignee.",
                            target_assignee, resolved_pname or "demo",
                        )
                        args["assignee"] = None
                        args["assignee_id"] = None

                # Normalize story_points
                if "story_points" in args and args["story_points"] is not None:
                    if isinstance(args["story_points"], str):
                        digits = "".join(c for c in args["story_points"] if c.isdigit())
                        args["story_points"] = int(digits) if digits else 0

                # Normalize swapped or mispopulated type and priority fields in LLM tool arguments
                raw_type = args.get("type")
                raw_priority = args.get("priority")
                type_str = str(raw_type).strip().lower() if raw_type is not None else ""
                priority_str = str(raw_priority).strip().lower() if raw_priority is not None else ""

                norm_priority_str = re.sub(r"[-\s]+(?:priority|level)$", "", priority_str).strip()
                norm_type_str = re.sub(r"[-\s]+(?:priority|level)$", "", type_str).strip()

                if norm_priority_str in TASK_PRIORITIES:
                    priority_str = norm_priority_str
                    args["priority"] = norm_priority_str

                if norm_type_str in TASK_PRIORITIES and type_str not in TASK_TYPES:
                    type_str = norm_type_str

                if priority_str in TASK_TYPES and type_str in TASK_PRIORITIES:
                    # Swapped arguments: type="high", priority="bug"
                    logger.warning(
                        "LLM generated swapped arguments: type=%r, priority=%r. Swapping to type=%r, priority=%r.",
                        raw_type, raw_priority, priority_str, type_str,
                    )
                    args["type"] = priority_str
                    args["priority"] = type_str
                    type_str, priority_str = priority_str, type_str
                elif priority_str in TASK_TYPES and (not type_str or type_str == "task"):
                    # Priority contains a task type (e.g. priority="bug")
                    logger.warning(
                        "LLM generated task type in priority field: priority=%r. Moving to type.",
                        raw_priority,
                    )
                    args["type"] = priority_str
                    user_prio = extract_user_priority(request.message)
                    if user_prio:
                        norm_up = re.sub(r"[-\s]+(?:priority|level)$", "", user_prio.lower()).strip()
                        if norm_up in TASK_PRIORITIES:
                            args["priority"] = norm_up
                        else:
                            args["priority"] = "low"
                    else:
                        args["priority"] = "low"
                    type_str = args["type"]
                    priority_str = args["priority"]
                elif type_str in TASK_PRIORITIES and (not priority_str or priority_str not in TASK_PRIORITIES):
                    # Type contains a priority (e.g. type="high" or type="medium-priority")
                    logger.warning(
                        "LLM generated priority in type field: type=%r. Moving to priority.",
                        raw_type,
                    )
                    args["priority"] = type_str
                    user_type = extract_user_task_type(request.message)
                    args["type"] = user_type if user_type else "task"
                    type_str = args["type"]
                    priority_str = args["priority"]
                elif type_str in TASK_PRIORITIES:
                    # Type contains a priority, but priority was already populated
                    user_type = extract_user_task_type(request.message)
                    args["type"] = user_type if user_type else "task"
                    type_str = args["type"]

                # Ensure user provides a meaningful name/title before task creation
                raw_title = args.get("title")
                title = str(raw_title).strip() if raw_title is not None else ""
                if is_user_title_omitted_or_invented(request.message, title):
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message=FOLLOW_UP_TITLE_PROMPT,
                        action_state="NONE",
                    )

                # Conflict detection for repeated fields (priority, type) before generating task proposal
                user_priorities = extract_all_user_priorities(request.message)
                if len(user_priorities) > 1:
                    p1, p2 = user_priorities[0], user_priorities[1]
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message=f"I noticed a conflict between the priorities '{p1}' and '{p2}'. Which priority would you like to set for this task?",
                        action_state="NONE",
                    )

                user_types_all = extract_all_user_task_types(request.message)
                if len(user_types_all) > 1:
                    t1, t2 = user_types_all[0], user_types_all[1]
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message=f"I noticed a conflict between the task types '{t1}' and '{t2}'. Which task type would you like to use for this task?",
                        action_state="NONE",
                    )

                # Validate priority against allowed enum before generating pending action
                user_priority = extract_user_priority(request.message)
                if user_priority:
                    norm_user_prio = re.sub(r"[-\s]+(?:priority|level)$", "", user_priority.lower()).strip()
                    if norm_user_prio not in TASK_PRIORITIES and norm_user_prio not in TASK_TYPES:
                        return ChatResponseData(
                            conversation_id=conversation_id,
                            message=f"Priority '{user_priority}' is not supported. Allowed priorities are: {', '.join(TASK_PRIORITIES)}. Please specify an allowed priority.",
                            action_state="NONE",
                        )
                else:
                    norm_user_prio = None

                curr_priority = args.get("priority")
                if curr_priority:
                    norm_curr_prio = re.sub(r"[-\s]+(?:priority|level)$", "", str(curr_priority).strip().lower()).strip()
                    if norm_curr_prio in TASK_PRIORITIES:
                        args["priority"] = norm_curr_prio
                    else:
                        return ChatResponseData(
                            conversation_id=conversation_id,
                            message=f"Priority '{curr_priority}' is not supported. Allowed priorities are: {', '.join(TASK_PRIORITIES)}. Please specify an allowed priority.",
                            action_state="NONE",
                        )

                # If priority is omitted from arguments, apply default 'low' (or user_priority if valid)
                if not args.get("priority"):
                    args["priority"] = norm_user_prio if (norm_user_prio and norm_user_prio in TASK_PRIORITIES) else "low"

                # Validate type against allowed enum before generating pending action
                user_type = extract_user_task_type(request.message)
                if user_type:
                    norm_ut = re.sub(r"[-\s]+(?:priority|level)$", "", user_type.lower()).strip()
                    if norm_ut in TASK_PRIORITIES or "priority" in user_type.lower():
                        user_type = None

                if user_type and user_type.lower() not in TASK_TYPES and user_type.lower() not in TASK_PRIORITIES:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message=f"Task type '{user_type}' is not supported. Allowed types are: {', '.join(TASK_TYPES)}. Please choose a valid type.",
                        action_state="NONE",
                    )

                curr_type = args.get("type")
                if curr_type:
                    norm_ct = re.sub(r"[-\s]+(?:priority|level)$", "", str(curr_type).strip().lower()).strip()
                    if norm_ct in TASK_PRIORITIES:
                        curr_type = None
                        args["type"] = None

                if curr_type and str(curr_type).strip().lower() not in TASK_TYPES:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message=f"Task type '{curr_type}' is not supported. Allowed types are: {', '.join(TASK_TYPES)}. Please choose a valid type.",
                        action_state="NONE",
                    )
                if not curr_type or str(curr_type).strip().lower() in ("", "none", "null", "undefined"):
                    args["type"] = user_type.lower() if (user_type and user_type.lower() in TASK_TYPES) else "task"

                # Validate due_date before generating pending action
                user_due_date_str = extract_user_due_date(request.message)
                raw_due = args.get("due_date")

                target_due_str = None
                if raw_due is not None and str(raw_due).strip().lower() not in ("", "none", "null", "undefined"):
                    target_due_str = str(raw_due).strip()
                elif user_due_date_str is not None:
                    target_due_str = user_due_date_str

                if target_due_str is not None:
                    parsed_dt, is_valid = parse_due_date(target_due_str)
                    if not is_valid:
                        return ChatResponseData(
                            conversation_id=conversation_id,
                            message=f"The specified due date '{target_due_str}' is invalid. Please provide a valid date (e.g. YYYY-MM-DD).",
                            action_state="NONE",
                        )
                    if parsed_dt is not None and is_past_due_date(parsed_dt):
                        return ChatResponseData(
                            conversation_id=conversation_id,
                            message=f"The specified due date '{target_due_str}' is in the past. Past due dates are not permitted. Please provide a valid date in the future.",
                            action_state="NONE",
                        )
                    args["due_date"] = parsed_dt.isoformat()
                else:
                    args["due_date"] = None

            logger.info("Calling %s with validated tool arguments: %s", tool_name, args)
            tool_result = await self.executor.execute_or_prepare(
                tool_name,
                args,
                user_id=user_id,
                organization_id=organization_id,
                role=role,
                db=self.db,
                redis=self.redis,
                skip_confirmation=False,
            )

            if tool_result.status == ToolExecutionStatus.REQUIRES_CONFIRMATION:
                action_id = str(uuid4())
                now = datetime.now(timezone.utc)
                expires_at = now + timedelta(seconds=CONFIRMATION_TTL_SECONDS)

                action_record = {
                    "action_id": action_id,
                    "action_type": first_call.name,
                    "user_id": user_id,
                    "organization_id": organization_id,
                    "role": role,
                    "project_id": args.get("project_id"),
                    "arguments": args,
                    "proposed_data": tool_result.data,
                    "status": "pending",
                    "created_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "expires_at_ts": int(expires_at.timestamp()),
                }
                await self.action_store.save_pending_action(action_id, action_record)

                proposed = tool_result.data
                p_task = ProposedTask(**proposed)
                pending_action = PendingAction(
                    action_id=action_id,
                    action_type="create_task",
                    project_id=str(args.get("project_id")),
                    proposed_task=p_task,
                    expires_at=expires_at.isoformat(),
                )

                confirmation_msg = (
                    f"I've prepared the {p_task.priority}-priority {p_task.type} "
                    f"'{p_task.title}' for project '{p_task.project_name or p_task.project_id}'. "
                    f"Please confirm to create it."
                )

                return ChatResponseData(
                    conversation_id=conversation_id,
                    message=confirmation_msg,
                    action_state="REQUIRES_CONFIRMATION",
                    pending_action=pending_action,
                )

            elif tool_result.status == ToolExecutionStatus.PERMISSION_DENIED:
                raise TaskServiceError(
                    403,
                    "FORBIDDEN",
                    tool_result.error_message or "You do not have permission to perform this action.",
                )

            elif tool_result.status == ToolExecutionStatus.VALIDATION_FAILURE:
                err_msg = (tool_result.error_message or "").lower()
                if "invalid argument 'project_id'" in err_msg or "project_id" in err_msg:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message="Please specify the project where you would like to create this task.",
                        action_state="NONE",
                    )
                if "past" in err_msg:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message="The specified due date is in the past. Past due dates are not permitted. Please provide a valid date in the future.",
                        action_state="NONE",
                    )
                if "due_date" in err_msg or "due date" in err_msg:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message="The specified due date is invalid. Please provide a valid date (e.g. YYYY-MM-DD).",
                        action_state="NONE",
                    )
                if "invalid argument 'priority'" in err_msg or "priority '" in err_msg:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message=f"The specified priority is not supported. Allowed priorities are: {', '.join(TASK_PRIORITIES)}. Please specify an allowed priority.",
                        action_state="NONE",
                    )
                if "invalid argument 'type'" in err_msg or "task type '" in err_msg:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message=f"The specified task type is not supported. Allowed types are: {', '.join(TASK_TYPES)}. Please choose a valid type.",
                        action_state="NONE",
                    )
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    tool_result.error_message or "Invalid task details provided.",
                )

            elif tool_result.status == ToolExecutionStatus.UNKNOWN_TOOL:
                raise TaskServiceError(
                    400,
                    "UNKNOWN_TOOL",
                    tool_result.error_message or "Unknown tool requested.",
                )

            else:
                raise TaskServiceError(
                    tool_result.status_code,
                    "TOOL_ERROR",
                    tool_result.error_message or "Failed to process tool request.",
                )

        # Default conversational response
        return ChatResponseData(
            conversation_id=conversation_id,
            message=llm_response.content or "How can I assist you with WorkPilot today?",
            action_state="NONE",
        )

    async def confirm_action(
        self,
        action_id: str,
        *,
        user_id: str,
        organization_id: str,
        role: str,
    ) -> dict[str, Any]:
        """Execute a previously prepared action upon explicit user confirmation."""
        # 1. Atomically claim the action using Redis Lua or in-memory lock
        claim_token = str(uuid4())
        claim_res = await self.action_store.claim_action(action_id, claim_token)

        res_type = claim_res.get("result")
        if res_type == "not_found":
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Confirmation action not found or expired.")
        elif res_type == "already_completed":
            raise TaskServiceError(409, "CONFLICT", "This action has already been executed.")
        elif res_type == "already_cancelled":
            raise TaskServiceError(409, "CONFLICT", "This action has been cancelled.")
        elif res_type == "already_executing":
            raise TaskServiceError(409, "CONFLICT", "This action is currently being executed by another confirmation request.")
        elif res_type == "expired":
            await self.action_store.mark_expired(action_id)
            raise TaskServiceError(410, "GONE", "This confirmation request has expired.")
        elif res_type != "claimed":
            raise TaskServiceError(400, "BAD_REQUEST", f"Cannot execute action in status '{res_type}'.")

        action = claim_res["data"]

        # 2. Re-verify tenant and user authorization
        if action["user_id"] != user_id or action["organization_id"] != organization_id:
            await self.action_store.release_claim(action_id, claim_token)
            raise TaskServiceError(403, "FORBIDDEN", "You are not authorized to confirm this action.")

        # 3. Check expiration timestamp if present
        if action.get("expires_at"):
            expires_at = datetime.fromisoformat(action["expires_at"])
            if datetime.now(timezone.utc) > expires_at:
                await self.action_store.mark_expired(action_id)
                raise TaskServiceError(410, "GONE", "This confirmation request has expired.")

        tool_name = action["action_type"]
        arguments = action["arguments"]

        try:
            # 4. Execute tool directly
            tool_result = await self.executor.execute_or_prepare(
                tool_name,
                arguments,
                user_id=user_id,
                organization_id=organization_id,
                role=role,
                db=self.db,
                redis=self.redis,
                skip_confirmation=True,
            )
        except Exception:
            # Rollback database transaction and safely release claim on failure
            await self.db.rollback()
            await self.action_store.release_claim(action_id, claim_token)
            raise

        if not tool_result.is_success:
            await self.db.rollback()
            await self.action_store.release_claim(action_id, claim_token)
            raise TaskServiceError(
                tool_result.status_code,
                "EXECUTION_FAILED",
                tool_result.error_message or "Failed to execute action.",
            )

        # 5. Mark completed and durably record output
        await self.action_store.complete_action(action_id, claim_token, tool_result.data)

        task_res = CreatedTaskResult(**tool_result.data)
        return {
            "action_id": action_id,
            "status": "completed",
            "message": f"Task '{task_res.key}' created successfully.",
            "task": task_res.model_dump(),
        }

    async def cancel_action(
        self,
        action_id: str,
        *,
        user_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        """Cancel a pending action."""
        cancel_res = await self.action_store.cancel_action_atomic(
            action_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        res_type = cancel_res.get("result")
        if res_type == "not_found":
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Confirmation action not found or expired.")
        elif res_type == "forbidden":
            raise TaskServiceError(403, "FORBIDDEN", "You are not authorized to cancel this action.")
        elif res_type == "already_completed":
            raise TaskServiceError(409, "CONFLICT", "Cannot cancel an action that has already been executed.")
        elif res_type == "already_executing":
            raise TaskServiceError(409, "CONFLICT", "Cannot cancel an action that is currently being executed.")
        elif res_type != "cancelled":
            raise TaskServiceError(400, "BAD_REQUEST", f"Cannot cancel action in status '{res_type}'.")

        return {
            "action_id": action_id,
            "status": "cancelled",
            "message": "Action cancelled successfully.",
        }
