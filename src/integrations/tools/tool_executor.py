"""Tool executor and registry for AI tools.

Enforces tool permissions, input validation, execution limits, and safe error handling.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.integrations.tools.task_tools import CreateTaskTool, UpdateTaskTool
from src.task.service import TaskServiceError

logger = get_logger(__name__)


class ToolExecutionStatus(str, Enum):
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    SUCCESS = "SUCCESS"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    ERROR = "ERROR"


class ToolResult:
    """Normalized tool outcome container."""

    def __init__(
        self,
        status: ToolExecutionStatus,
        *,
        tool_name: str,
        data: dict[str, Any] | None = None,
        error_message: str | None = None,
        status_code: int = 200,
    ) -> None:
        self.status = status
        self.tool_name = tool_name
        self.data = data or {}
        self.error_message = error_message
        self.status_code = status_code

    @property
    def is_success(self) -> bool:
        return self.status == ToolExecutionStatus.SUCCESS

    @property
    def requires_confirmation(self) -> bool:
        return self.status == ToolExecutionStatus.REQUIRES_CONFIRMATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "tool_name": self.tool_name,
            "data": self.data,
            "error_message": self.error_message,
            "status_code": self.status_code,
        }


class ToolExecutor:
    """Central registry and executor for AI assistant tools."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._tools: dict[str, Any] = {}
        # Register default permitted tools
        self.register(CreateTaskTool())
        self.register(UpdateTaskTool())

    def register(self, tool: Any) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool
        logger.info("Registered AI tool: %s", tool.name)

    def get_tool(self, name: str) -> Any | None:
        return self._tools.get(name)

    def get_registered_declarations(self) -> list[dict[str, Any]]:
        """Return function calling declarations for all registered tools."""
        return [tool.get_tool_declaration() for tool in self._tools.values()]

    async def execute_or_prepare(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        user_id: str,
        organization_id: str,
        role: str,
        db: AsyncSession,
        redis: Any = None,
        skip_confirmation: bool = False,
    ) -> ToolResult:
        """Validate, authorize, and either execute or prepare tool for confirmation."""
        tool = self.get_tool(tool_name)
        if tool is None:
            logger.warning("Attempted execution of unknown tool: %s", tool_name)
            return ToolResult(
                ToolExecutionStatus.UNKNOWN_TOOL,
                tool_name=tool_name,
                error_message=f"Tool '{tool_name}' is not recognized or permitted.",
                status_code=400,
            )

        # 1. Parse and validate arguments strictly
        try:
            parsed_args = tool.parse_arguments(arguments)
        except ValidationError as val_err:
            first_err = val_err.errors()[0]
            field = ".".join(str(loc) for loc in first_err.get("loc", []))
            msg = first_err.get("msg", "Validation error")
            err_msg = f"Invalid argument '{field}': {msg}"
            logger.info("Tool argument validation failure for %s: %s", tool_name, err_msg)
            return ToolResult(
                ToolExecutionStatus.VALIDATION_FAILURE,
                tool_name=tool_name,
                error_message=err_msg,
                status_code=400,
            )
        except Exception as exc:
            logger.info("Unexpected error parsing tool arguments: %s", exc)
            return ToolResult(
                ToolExecutionStatus.VALIDATION_FAILURE,
                tool_name=tool_name,
                error_message="Invalid tool arguments format.",
                status_code=400,
            )

        # 2. Check preconditions and authorization
        try:
            async with asyncio.timeout(self.timeout_seconds):
                proposed_data = await tool.validate_preconditions(
                    parsed_args,
                    user_id=user_id,
                    organization_id=organization_id,
                    role=role,
                    db=db,
                    redis=redis,
                )
        except TaskServiceError as tse:
            logger.warning("TaskService authorization/validation error in tool %s: %s", tool_name, tse.message)
            status = (
                ToolExecutionStatus.PERMISSION_DENIED
                if tse.status_code == 403
                else ToolExecutionStatus.VALIDATION_FAILURE
            )
            return ToolResult(
                status,
                tool_name=tool_name,
                error_message=tse.message,
                status_code=tse.status_code,
            )
        except TimeoutError:
            logger.error("Tool execution timed out during precondition check: %s", tool_name)
            return ToolResult(
                ToolExecutionStatus.ERROR,
                tool_name=tool_name,
                error_message="Tool validation timed out. Please try again.",
                status_code=504,
            )
        except Exception as exc:
            logger.error("Unexpected error in tool precondition validation: %s", exc, exc_info=True)
            return ToolResult(
                ToolExecutionStatus.ERROR,
                tool_name=tool_name,
                error_message="Internal error during tool validation.",
                status_code=500,
            )

        # 3. If tool is a write tool and skip_confirmation is False, return REQUIRES_CONFIRMATION
        if getattr(tool, "is_write", False) and not skip_confirmation:
            return ToolResult(
                ToolExecutionStatus.REQUIRES_CONFIRMATION,
                tool_name=tool_name,
                data=proposed_data,
                status_code=200,
            )

        # 4. Otherwise execute directly
        try:
            async with asyncio.timeout(self.timeout_seconds):
                result_data = await tool.execute(
                    parsed_args,
                    user_id=user_id,
                    organization_id=organization_id,
                    role=role,
                    db=db,
                    redis=redis,
                )
            return ToolResult(
                ToolExecutionStatus.SUCCESS,
                tool_name=tool_name,
                data=result_data,
                status_code=200,
            )
        except TaskServiceError as tse:
            status = (
                ToolExecutionStatus.PERMISSION_DENIED
                if tse.status_code == 403
                else ToolExecutionStatus.VALIDATION_FAILURE
            )
            return ToolResult(
                status,
                tool_name=tool_name,
                error_message=tse.message,
                status_code=tse.status_code,
            )
        except TimeoutError:
            return ToolResult(
                ToolExecutionStatus.ERROR,
                tool_name=tool_name,
                error_message="Tool execution timed out.",
                status_code=504,
            )
        except Exception as exc:
            logger.error("Unexpected error during tool execution: %s", exc, exc_info=True)
            return ToolResult(
                ToolExecutionStatus.ERROR,
                tool_name=tool_name,
                error_message="Internal server error during tool execution.",
                status_code=500,
            )
