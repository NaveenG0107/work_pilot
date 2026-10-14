"""Task creation tool for the WorkPilot AI Assistant.

Integrates with the existing WorkPilot TaskService and validates permissions,
project scope, and entity associations before executing or proposing task creation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.task.schema import (
    TASK_PRIORITIES,
    TASK_TYPES,
    CreateTaskRequest,
    TaskPriority,
    TaskType,
)
from src.task.service import TaskService, TaskServiceError

logger = get_logger(__name__)


def _is_fibonacci(value: int) -> bool:
    if value < 0:
        return False
    if value in {0, 1}:
        return True
    left, right = 1, 1
    while right < value:
        left, right = right, left + right
    return right == value


class CreateTaskToolArgs(BaseModel):
    """Strict argument schema for the create_task tool.

    Rejects unknown fields and enforces WorkPilot business constraints.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: UUID = Field(
        ...,
        description="The UUID of the project where the task will be created.",
    )
    title: str = Field(
        ...,
        min_length=3,
        max_length=200,
        description="A concise title for the task (3 to 200 characters).",
    )
    description: str = Field(
        default="",
        description="Optional detailed description of the task.",
    )
    type: TaskType = Field(
        default="task",
        description=f"Type of the task. Allowed: {', '.join(TASK_TYPES)}.",
    )
    priority: TaskPriority = Field(
        default="medium",
        description=f"Priority level. Allowed: {', '.join(TASK_PRIORITIES)}.",
    )
    assignee_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the user assigned to this task.",
    )
    sprint_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the sprint this task belongs to.",
    )
    user_story_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the user story this task is linked to.",
    )
    story_points: int = Field(
        default=0,
        ge=0,
        description="Story points following the Fibonacci sequence (0, 1, 2, 3, 5, 8, ...).",
    )
    due_date: datetime | None = Field(
        default=None,
        description="Optional due date/time for the task.",
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        clean = v.strip()
        if not (3 <= len(clean) <= 200):
            raise ValueError("Task title must be between 3 and 200 characters.")
        return clean

    @field_validator("story_points")
    @classmethod
    def validate_story_points(cls, v: int) -> int:
        if not _is_fibonacci(v):
            raise ValueError("Story points must follow the Fibonacci scale (0, 1, 2, 3, 5, 8, 13...).")
        return v


class CreateTaskTool:
    """Encapsulates the 'create_task' AI tool."""

    name: str = "create_task"
    description: str = (
        "Create a new task in WorkPilot within a specific project. "
        "Requires project_id, title, type, and priority."
    )
    is_write: bool = True

    @classmethod
    def get_tool_declaration(cls) -> dict[str, Any]:
        """Return the tool schema formatted for LLM function calling."""
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "UUID of the project where the task will be created.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Task title (between 3 and 200 characters).",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional details or instructions for the task.",
                        },
                        "type": {
                            "type": "string",
                            "enum": TASK_TYPES,
                            "description": "Task type (bug, feature, task, chore, story). Default: task.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": TASK_PRIORITIES,
                            "description": "Priority level (low, medium, high, critical). Default: medium.",
                        },
                        "assignee_id": {
                            "type": "string",
                            "description": "Optional UUID of the assigned team member.",
                        },
                        "sprint_id": {
                            "type": "string",
                            "description": "Optional UUID of the sprint to attach this task to.",
                        },
                        "user_story_id": {
                            "type": "string",
                            "description": "Optional UUID of the parent user story.",
                        },
                        "story_points": {
                            "type": "integer",
                            "description": "Fibonacci story points (0, 1, 2, 3, 5, 8, ...). Default: 0.",
                        },
                    },
                    "required": ["project_id", "title"],
                },
            },
        }

    @staticmethod
    def parse_arguments(raw_args: dict[str, Any]) -> CreateTaskToolArgs:
        """Parse and validate raw tool arguments strictly."""
        return CreateTaskToolArgs.model_validate(raw_args)

    async def validate_preconditions(
        self,
        args: CreateTaskToolArgs,
        *,
        user_id: str,
        organization_id: str,
        role: str,
        db: AsyncSession,
        redis: Any = None,
    ) -> dict[str, Any]:
        """Validate user permissions, tenant isolation, and related entities.

        Does not create the task. Prepares the validated proposed payload.
        """
        task_service = TaskService(db, redis)
        proj_id_str = str(args.project_id)

        # 1. Check project and authorization
        project, actor = await task_service._check_authorization(
            proj_id_str,
            user_id,
            "You do not have permission to view tasks in this project",
        )

        # 2. Check task creation permission
        if not await task_service._has_permission(project, actor, "tasks", "add"):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "You do not have permission to create tasks in this project",
            )

        # 3. Check organization isolation
        if str(project.organization_id) != str(organization_id):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "Project belongs to a different organization",
            )

        # 4. Check assignee if provided
        assignee_name: str | None = None
        if args.assignee_id:
            assignee = await task_service._validate_assignee(
                project, str(args.assignee_id), organization_id
            )
            assignee_name = assignee.full_name or assignee.username

        # 5. Check sprint if provided
        sprint_name: str | None = None
        if args.sprint_id:
            sprint = await task_service._validate_sprint(str(args.sprint_id), proj_id_str)
            if sprint.status == "completed":
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    "Cannot assign a task to a completed sprint",
                )
            sprint_name = sprint.name

        # 6. Check user story if provided
        if args.user_story_id:
            await task_service._validate_story(str(args.user_story_id), proj_id_str)

        # Build clean proposed dictionary
        proposed = {
            "project_id": proj_id_str,
            "project_name": project.name,
            "title": args.title,
            "description": args.description,
            "type": args.type,
            "priority": args.priority,
            "story_points": args.story_points,
            "assignee_id": str(args.assignee_id) if args.assignee_id else None,
            "assignee_name": assignee_name,
            "sprint_id": str(args.sprint_id) if args.sprint_id else None,
            "sprint_name": sprint_name,
            "user_story_id": str(args.user_story_id) if args.user_story_id else None,
            "due_date": args.due_date.isoformat() if args.due_date else None,
        }
        return proposed

    async def execute(
        self,
        args: CreateTaskToolArgs,
        *,
        user_id: str,
        organization_id: str,
        role: str,
        db: AsyncSession,
        redis: Any = None,
    ) -> dict[str, Any]:
        """Execute task creation using the existing TaskService.create method."""
        task_service = TaskService(db, redis)
        proj_id_str = str(args.project_id)

        # Construct existing CreateTaskRequest
        req = CreateTaskRequest(
            title=args.title,
            description=args.description,
            type=args.type,
            priority=args.priority,
            assignee_id=args.assignee_id,
            sprint_id=args.sprint_id,
            user_story_id=args.user_story_id,
            story_points=args.story_points,
            due_date=args.due_date,
        )

        task_id = await task_service.create(
            proj_id_str,
            req,
            user_id,
            organization_id,
            role,
        )

        # Retrieve created task to return complete normalized result
        task = await task_service._task(task_id, proj_id_str)
        return {
            "task_id": str(task.id),
            "key": task.key,
            "title": task.title,
            "status": task.status,
            "project_id": str(task.project_id),
            "type": task.type,
            "priority": task.priority,
        }
