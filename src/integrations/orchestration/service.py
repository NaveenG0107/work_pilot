"""AI Chatbot orchestration service for WorkPilot.

Coordinates LLM provider, tool execution, server-side confirmation workflow,
and user permissions.
"""

from __future__ import annotations

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
CONFIRMATION_TTL_SECONDS = 900  # 15 minutes


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
        _MEMORY_ACTION_STORE[action_id] = data

    async def get_action(self, action_id: str) -> dict[str, Any] | None:
        if self.redis is not None:
            try:
                raw = await self.redis.get(f"ai_action:{action_id}")
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("Failed to retrieve action from Redis: %s", exc)

        action = _MEMORY_ACTION_STORE.get(action_id)
        if action:
            # Check expiration
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
        action = await self.action_store.get_action(action_id)
        if not action:
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Confirmation action not found or expired.")

        # Re-verify tenant and user authorization
        if action["user_id"] != user_id or action["organization_id"] != organization_id:
            raise TaskServiceError(403, "FORBIDDEN", "You are not authorized to confirm this action.")

        # Check action status and enforce idempotency
        current_status = action.get("status")
        if current_status == "completed":
            raise TaskServiceError(409, "CONFLICT", "This action has already been executed.")
        elif current_status == "cancelled":
            raise TaskServiceError(409, "CONFLICT", "This action has been cancelled.")
        elif current_status != "pending":
            raise TaskServiceError(400, "BAD_REQUEST", f"Cannot execute action in status '{current_status}'.")

        # Check expiration
        expires_at = datetime.fromisoformat(action["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            action["status"] = "expired"
            await self.action_store.update_action(action_id, action)
            raise TaskServiceError(410, "GONE", "This confirmation request has expired.")

        # Atomic status transition to prevent concurrent double-execution
        action["status"] = "executing"
        await self.action_store.update_action(action_id, action)

        tool_name = action["action_type"]
        arguments = action["arguments"]

        try:
            # Execute tool directly
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
            # Revert status on failure so it doesn't stay stuck in executing
            action["status"] = "pending"
            await self.action_store.update_action(action_id, action)
            raise

        if not tool_result.is_success:
            action["status"] = "pending"
            await self.action_store.update_action(action_id, action)
            raise TaskServiceError(
                tool_result.status_code,
                "EXECUTION_FAILED",
                tool_result.error_message or "Failed to execute action.",
            )

        # Mark completed and record output
        action["status"] = "completed"
        action["result"] = tool_result.data
        await self.action_store.update_action(action_id, action)

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
        action = await self.action_store.get_action(action_id)
        if not action:
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Confirmation action not found or expired.")

        if action["user_id"] != user_id or action["organization_id"] != organization_id:
            raise TaskServiceError(403, "FORBIDDEN", "You are not authorized to cancel this action.")

        if action.get("status") == "completed":
            raise TaskServiceError(409, "CONFLICT", "Cannot cancel an action that has already been executed.")

        action["status"] = "cancelled"
        await self.action_store.update_action(action_id, action)
        return {
            "action_id": action_id,
            "status": "cancelled",
            "message": "Action cancelled successfully.",
        }
