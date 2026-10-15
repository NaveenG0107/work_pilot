"""Task creation tool for the WorkPilot AI Assistant.

Integrates with the existing WorkPilot TaskService and validates permissions,
project scope, and entity associations before executing or proposing task creation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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
        default="low",
        description=f"Priority level. Allowed: {', '.join(TASK_PRIORITIES)}. Default is low.",
    )
    assignee_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the user assigned to this task.",
    )
    assignee: str | None = Field(
        default=None,
        description="Optional username, full name, or email of the assignee (e.g. 'demi').",
    )
    reporter_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the reporter for this task.",
    )
    reporter: str | None = Field(
        default=None,
        description="Optional username, full name, or email of the reporter (e.g. 'demi').",
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

    @model_validator(mode="before")
    @classmethod
    def normalize_user_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        # If assignee_id is not a valid UUID string (e.g. "demi"), treat as assignee name
        if data.get("assignee_id"):
            val = str(data["assignee_id"]).strip()
            try:
                UUID(val)
            except ValueError:
                if not data.get("assignee"):
                    data["assignee"] = val
                data["assignee_id"] = None

        # If reporter_id is not a valid UUID string (e.g. "demi"), treat as reporter name
        if data.get("reporter_id"):
            val = str(data["reporter_id"]).strip()
            try:
                UUID(val)
            except ValueError:
                if not data.get("reporter"):
                    data["reporter"] = val
                data["reporter_id"] = None
        return data

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        clean = v.strip()
        generic_titles = {
            "task",
            "a task",
            "the task",
            "new task",
            "create task",
            "create a task",
            "untitled",
            "untitled task",
            "none",
            "no title",
            "test",
        }
        if clean.lower() in generic_titles:
            raise ValueError("Please provide a meaningful name for the task before creation.")
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
                            "description": "Priority level (low, medium, high, critical). Default: low.",
                        },
                        "assignee": {
                            "type": "string",
                            "description": "Optional name, username, or email of the team member to assign the task to (e.g. 'demi').",
                        },
                        "assignee_id": {
                            "type": "string",
                            "description": "Optional UUID or username of the assigned team member.",
                        },
                        "reporter": {
                            "type": "string",
                            "description": "Optional name, username, or email of the reporter for this task (e.g. 'demi').",
                        },
                        "reporter_id": {
                            "type": "string",
                            "description": "Optional UUID or username of the reporter.",
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

    async def _resolve_user(
        self,
        identifier: str,
        project: Any,
        organization_id: str,
        db: AsyncSession,
        field_name: str = "assignee",
    ) -> Any:
        """Resolve a user by UUID, username, full name, or email within organization and project."""
        from src.auth.models import User
        from sqlalchemy import select, or_, func
        from sqlalchemy.orm import joinedload
        import uuid

        clean = str(identifier).strip()
        task_service = TaskService(db)

        # 1. Try UUID lookup
        try:
            target_uuid = str(uuid.UUID(clean))
            user = (
                await db.execute(
                    select(User)
                    .where(
                        User.id == target_uuid,
                        User.organization_id == str(organization_id),
                        User.deleted_at.is_(None),
                    )
                    .options(joinedload(User.role))
                )
            ).scalar_one_or_none()
            if user:
                if not user.is_active:
                    raise TaskServiceError(400, "VALIDATION_ERROR", f"{field_name.capitalize()} user is not active")
                if not await task_service._is_project_member(project, user):
                    raise TaskServiceError(400, "VALIDATION_ERROR", f"{field_name.capitalize()} must be a member of the project")
                return user
        except ValueError:
            pass

        # 2. Try exact match by username, full_name, or email
        stmt = (
            select(User)
            .where(
                User.organization_id == str(organization_id),
                User.deleted_at.is_(None),
                or_(
                    func.lower(User.username) == clean.lower(),
                    func.lower(User.full_name) == clean.lower(),
                    func.lower(User.email) == clean.lower(),
                ),
            )
            .options(joinedload(User.role))
        )
        users = (await db.execute(stmt)).scalars().all()
        if len(users) > 1:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"Multiple users match '{clean}' for {field_name}. Please provide a specific email or UUID.",
            )
        user = users[0] if users else None

        # 3. If no exact match, try prefix/contains match on username or full_name
        if not user:
            stmt_like = (
                select(User)
                .where(
                    User.organization_id == str(organization_id),
                    User.deleted_at.is_(None),
                    or_(
                        User.username.ilike(f"%{clean}%"),
                        User.full_name.ilike(f"%{clean}%"),
                    ),
                )
                .options(joinedload(User.role))
            )
            users_like = (await db.execute(stmt_like)).scalars().all()
            if len(users_like) > 1:
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    f"Multiple users match '{clean}' for {field_name}. Please provide a specific email or UUID.",
                )
            user = users_like[0] if users_like else None

        if not user:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"User '{clean}' for {field_name} not found in this organization",
            )

        if not user.is_active:
            raise TaskServiceError(400, "VALIDATION_ERROR", f"{field_name.capitalize()} user '{clean}' is not active")

        if not await task_service._is_project_member(project, user):
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"User '{user.full_name or user.username}' must be a member of project '{project.name}'",
            )

        return user

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

        # 4. Check and resolve assignee if provided by ID or name
        assignee_name: str | None = None
        assignee_target = args.assignee or (str(args.assignee_id) if args.assignee_id else None)
        if assignee_target:
            assignee_user = await self._resolve_user(
                assignee_target, project, organization_id, db, field_name="assignee"
            )
            args.assignee_id = UUID(str(assignee_user.id))
            assignee_name = assignee_user.full_name or assignee_user.username

        # 4b. Check and resolve reporter if provided by ID or name
        reporter_name: str | None = None
        reporter_target = args.reporter or (str(args.reporter_id) if args.reporter_id else None)
        if reporter_target:
            reporter_user = await self._resolve_user(
                reporter_target, project, organization_id, db, field_name="reporter"
            )
            args.reporter_id = UUID(str(reporter_user.id))
            reporter_name = reporter_user.full_name or reporter_user.username

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
            "reporter_id": str(args.reporter_id) if args.reporter_id else None,
            "reporter_name": reporter_name,
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
            reporter_id=args.reporter_id,
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
            "assignee_id": str(task.assignee_id) if task.assignee_id else None,
            "reporter_id": str(task.reporter_id) if task.reporter_id else None,
        }
