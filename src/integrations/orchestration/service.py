"""AI Chatbot orchestration service for WorkPilot.

Coordinates LLM provider, tool execution, server-side confirmation workflow,
and user permissions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

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
from src.integrations.tools.tool_executor import (
    ToolExecutionStatus,
    ToolExecutor,
    ToolResult,
)
from src.task.service import TaskServiceError

logger = get_logger(__name__)

# Fallback in-memory store for pending actions if Redis is not configured or offline
_MEMORY_ACTION_STORE: dict[str, dict[str, Any]] = {}
_MEMORY_STORE_LOCK = asyncio.Lock()
CONFIRMATION_TTL_SECONDS = 900  # 15 minutes
DEFAULT_CLAIM_TTL_SECONDS = 30  # 30 seconds claim execution lock

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
        if context_proj_id:
            messages.append({
                "role": "system",
                "content": f"Active context: The user is currently in project_id='{context_proj_id}'. "
                           f"Use this project_id if the user asks to create a task without specifying another project."
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

            # If tool is create_task, inject context project_id if missing
            if tool_name == "create_task":
                if context_proj_id and not args.get("project_id"):
                    args["project_id"] = context_proj_id

                if not args.get("project_id"):
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message="Please specify the project where you would like to create this task.",
                        action_state="NONE",
                    )

                # Ensure user provides a meaningful name/title before task creation
                raw_title = args.get("title")
                title = str(raw_title).strip() if raw_title is not None else ""
                generic_titles = {
                    "task",
                    "a task",
                    "the task",
                    "new task",
                    "create task",
                    "create a task",
                    "untitled",
                    "untitled task",
                }
                if raw_title is None or title == "" or title.lower() in generic_titles:
                    return ChatResponseData(
                        conversation_id=conversation_id,
                        message="Please provide a meaningful name for the task before creation.",
                        action_state="NONE",
                    )

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
                    f"I've prepared a {p_task.priority}-priority {p_task.type} task "
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
