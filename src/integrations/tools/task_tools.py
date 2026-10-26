"""Task creation tool for the WorkPilot AI Assistant.

Integrates with the existing WorkPilot TaskService and validates permissions,
project scope, and entity associations before executing or proposing task creation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any, Literal, Self
from uuid import UUID

from dateutil import parser as date_parser
from dateutil.parser import ParserError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.task.models import DEFAULT_STATUS_COLORS, Task, normalize_task_status
from src.task.schema import (
    TASK_PRIORITIES,
    TASK_TYPES,
    CreateTaskRequest,
    UpdateTaskRequest,
    TaskPriority,
    TaskType,
)
from src.task.service import TaskService, TaskServiceError, _is_backdated

logger = get_logger(__name__)


def parse_due_date(val: Any) -> tuple[datetime | None, bool]:
    """Parse and validate a due date value.

    Returns:
        (datetime_obj, True) if valid datetime
        (None, True) if explicitly omitted/null
        (None, False) if provided but parsing failed
    """
    if val is None or val == "":
        return None, True
    if isinstance(val, str):
        clean = val.strip().lower()
        if clean in ("none", "null", "undefined"):
            return None, True
        now = datetime.now(timezone.utc)
        if clean == "today":
            return now, True
        if clean == "tomorrow":
            return now + timedelta(days=1), True
        if clean == "yesterday":
            return now - timedelta(days=1), True
        try:
            dt = date_parser.parse(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt, True
        except (ParserError, ValueError, OverflowError, TypeError):
            return None, False
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc), True
        return val, True
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time(), tzinfo=timezone.utc), True
    return None, False


def is_past_due_date(dt: datetime) -> bool:
    """Check if the due date is in the past.

    A due date is considered in the past if it is earlier than today's date.
    """
    now_utc = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt_utc = dt.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt.astimezone(timezone.utc)

    if dt_utc.date() < now_utc.date():
        return True

    local_now = datetime.now().astimezone()
    if dt.tzinfo is None:
        dt_local = dt.replace(tzinfo=local_now.tzinfo)
    else:
        dt_local = dt.astimezone(local_now.tzinfo)
    if dt_local.date() < local_now.date():
        return True

    return False


def _is_fibonacci(value: int) -> bool:
    if value < 0:
        return False
    if value in {0, 1}:
        return True
    left, right = 1, 1
    while right < value:
        left, right = right, left + right
    return right == value


GENERIC_TITLES = {
    "task",
    "a task",
    "the task",
    "new task",
    "create task",
    "create a task",
    "chore",
    "a chore",
    "the chore",
    "new chore",
    "create chore",
    "create a chore",
    "bug",
    "a bug",
    "the bug",
    "new bug",
    "create bug",
    "create a bug",
    "bug fix",
    "feature",
    "a feature",
    "the feature",
    "new feature",
    "create feature",
    "story",
    "a story",
    "the story",
    "new story",
    "user story",
    "untitled",
    "untitled task",
    "untitled chore",
    "untitled bug",
    "none",
    "no title",
    "null",
    "undefined",
    "n/a",
    "na",
    "test",
    "test task",
    "sample",
    "sample task",
    "demo",
    "demo task",
    "todo",
    "to do",
    "[title]",
    "<title>",
    "{title}",
    "placeholder",
    "dummy",
}


def is_generic_or_empty_title(title: str | None) -> bool:
    if not title:
        return True
    clean = str(title).strip().lower()
    if clean in GENERIC_TITLES:
        return True
    clean_no_punct = "".join(c for c in clean if c.isalnum() or c.isspace()).strip()
    if clean_no_punct in GENERIC_TITLES:
        return True
    return False


class CreateTaskToolArgs(BaseModel):
    """Strict argument schema for the create_task tool.

    Rejects unknown fields and enforces WorkPilot business constraints.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: UUID = Field(
        ...,
        description="The UUID of the project where the task will be created. Mandatory.",
    )
    project_name: str | None = Field(
        default=None,
        description="Optional name of the project where the task will be created.",
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
        description="Optional username, full name, or email of the assignee (e.g. 'alex').",
    )
    reporter_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the reporter for this task.",
    )
    reporter: str | None = Field(
        default=None,
        description="Optional username, full name, or email of the reporter (e.g. 'alex').",
    )
    sprint_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the sprint this task belongs to.",
    )
    sprint_name: str | None = Field(
        default=None,
        description="Optional name of the sprint this task belongs to (e.g. 'TestModule').",
    )
    user_story_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the user story this task is linked to.",
    )
    user_story_name: str | None = Field(
        default=None,
        description="Optional name or title of the user story this task is linked to (e.g. 'Mithun').",
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
    def normalize_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # Handle non-UUID project_id string (e.g. LLM passes "demo" in project_id)
        raw_pid = data.get("project_id")
        if raw_pid is not None:
            pid_str = str(raw_pid).strip()
            if pid_str:
                try:
                    UUID(pid_str)
                except ValueError:
                    if not data.get("project_name"):
                        data["project_name"] = pid_str
                    data["project_id"] = None

        if "project_id" in data and (data["project_id"] is None or data["project_id"] == "" or str(data["project_id"]).strip().lower() in ("none", "null", "undefined")):
            data.pop("project_id", None)

        # If assignee_id is not a valid UUID string (e.g. "demi"), treat as assignee name
        if data.get("assignee_id") is not None:
            val = str(data["assignee_id"]).strip()
            try:
                UUID(val)
            except ValueError:
                if not data.get("assignee"):
                    data["assignee"] = val
                data["assignee_id"] = None

        # Clean empty/none strings for assignee
        if data.get("assignee") is not None:
            a_val = str(data["assignee"]).strip()
            if a_val.lower() in ("", "none", "null", "unassigned", "undefined"):
                data["assignee"] = None

        # If assignee matches project_name, clear assignee so omitted assignee stays null
        p_name = data.get("project_name")
        if p_name and isinstance(p_name, str):
            p_clean = p_name.strip().lower()
            if data.get("assignee") and str(data["assignee"]).strip().lower() == p_clean:
                data["assignee"] = None
            if data.get("assignee_id") and str(data["assignee_id"]).strip().lower() == p_clean:
                data["assignee_id"] = None

        # If reporter_id is not a valid UUID string (e.g. "demi"), treat as reporter name
        if data.get("reporter_id") is not None:
            val = str(data["reporter_id"]).strip()
            try:
                UUID(val)
            except ValueError:
                if not data.get("reporter"):
                    data["reporter"] = val
                data["reporter_id"] = None

        # Clean empty/none strings for reporter
        if data.get("reporter") is not None:
            r_val = str(data["reporter"]).strip()
            if r_val.lower() in ("", "none", "null", "unassigned", "undefined"):
                data["reporter"] = None

        if p_name and isinstance(p_name, str):
            p_clean = p_name.strip().lower()
            if data.get("reporter") and str(data["reporter"]).strip().lower() == p_clean:
                data["reporter"] = None
            if data.get("reporter_id") and str(data["reporter_id"]).strip().lower() == p_clean:
                data["reporter_id"] = None

        # Normalize sprint arguments (sprint / sprint_name / sprint_id)
        if "sprint" in data and not data.get("sprint_name"):
            data["sprint_name"] = data["sprint"]
        if data.get("sprint_id") is not None:
            val = str(data["sprint_id"]).strip()
            try:
                UUID(val)
            except ValueError:
                if not data.get("sprint_name"):
                    data["sprint_name"] = val
                data["sprint_id"] = None

        # Normalize user story arguments (user_story / userstory / user_story_name / user_story_id)
        if "user_story" in data and not data.get("user_story_name"):
            data["user_story_name"] = data["user_story"]
        if "userstory" in data and not data.get("user_story_name"):
            data["user_story_name"] = data["userstory"]
        if "story" in data and not data.get("user_story_name") and data.get("type") != "story":
            data["user_story_name"] = data["story"]
        if data.get("user_story_id") is not None:
            val = str(data["user_story_id"]).strip()
            try:
                UUID(val)
            except ValueError:
                if not data.get("user_story_name"):
                    data["user_story_name"] = val
                data["user_story_id"] = None

        # Normalize story_points
        sp = data.get("story_points")
        if sp is None or sp == "":
            data["story_points"] = 0
        elif isinstance(sp, str):
            digits = "".join(c for c in sp if c.isdigit())
            data["story_points"] = int(digits) if digits else 0

        # Normalize natural-language priority phrases such as 'medium-priority' to 'medium'
        raw_type = data.get("type")
        raw_priority = data.get("priority")
        type_str = str(raw_type).strip().lower() if raw_type is not None else ""
        priority_str = str(raw_priority).strip().lower() if raw_priority is not None else ""

        norm_priority_str = re.sub(r"[-\s]+(?:priority|level)$", "", priority_str).strip()
        norm_type_str = re.sub(r"[-\s]+(?:priority|level)$", "", type_str).strip()

        if norm_priority_str in TASK_PRIORITIES:
            priority_str = norm_priority_str
            data["priority"] = norm_priority_str

        if norm_type_str in TASK_PRIORITIES and type_str not in TASK_TYPES:
            type_str = norm_type_str

        if priority_str in TASK_TYPES and type_str in TASK_PRIORITIES:
            # Swapped type and priority
            data["type"] = priority_str
            data["priority"] = type_str
        elif priority_str in TASK_TYPES and (not type_str or type_str == "task"):
            # Priority was populated with a task type
            data["type"] = priority_str
            data["priority"] = "low"
        elif type_str in TASK_PRIORITIES and (not priority_str or priority_str not in TASK_PRIORITIES):
            # Type was populated with a priority
            data["priority"] = type_str
            data["type"] = "task"
        elif type_str in TASK_PRIORITIES:
            # Type was populated with a priority, but priority was already set
            data["type"] = "task"

        # If no type is specified, use the documented default 'task'
        if not data.get("type") or str(data.get("type")).strip().lower() in ("", "none", "null", "undefined"):
            data["type"] = "task"

        # Clean empty/none strings for due_date
        if "due_date" in data:
            raw_due = data.get("due_date")
            if raw_due is None or (isinstance(raw_due, str) and raw_due.strip().lower() in ("", "none", "null", "undefined")):
                data["due_date"] = None

        return data

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or is_generic_or_empty_title(clean):
            raise ValueError("Task title is required. Please provide a title or meaningful name before creation.")
        if not (3 <= len(clean) <= 200):
            raise ValueError("Task title must be between 3 and 200 characters.")
        return clean

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, v: Any) -> str:
        if v is None or v == "" or str(v).strip().lower() in ("none", "null", "undefined"):
            return "task"
        clean = str(v).strip().lower()
        if clean not in TASK_TYPES:
            raise ValueError(
                f"Task type '{v}' is not supported. Allowed types are: {', '.join(TASK_TYPES)}."
            )
        return clean

    @field_validator("priority", mode="before")
    @classmethod
    def validate_priority(cls, v: Any) -> str:
        if v is None or v == "" or str(v).strip().lower() in ("none", "null", "undefined"):
            return "low"
        clean = str(v).strip().lower()
        clean = re.sub(r"[-\s]+(?:priority|level)$", "", clean).strip()
        if clean not in TASK_PRIORITIES:
            raise ValueError(
                f"Priority '{v}' is not supported. Allowed priorities are: {', '.join(TASK_PRIORITIES)}."
            )
        return clean

    @field_validator("story_points")
    @classmethod
    def validate_story_points(cls, v: int) -> int:
        if not _is_fibonacci(v):
            raise ValueError("Story points must follow the Fibonacci scale (0, 1, 2, 3, 5, 8, 13...).")
        return v

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_due_date(cls, v: Any) -> datetime | None:
        if v is None or v == "":
            return None
        dt, ok = parse_due_date(v)
        if not ok:
            raise ValueError(f"Invalid due date '{v}'. Please provide a valid date (e.g. YYYY-MM-DD).")
        if dt is not None and is_past_due_date(dt):
            raise ValueError(f"The specified due date '{v}' is in the past. Past due dates are not permitted.")
        return dt


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
                            "description": "Mandatory UUID of the project where the task will be created.",
                        },
                        "project_name": {
                            "type": "string",
                            "description": "Optional name of the project (e.g. 'demo').",
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
                            "description": "Task work-item type (bug, feature, task, chore, story). Default: task. NEVER pass priority levels (e.g. high, low) here.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": TASK_PRIORITIES,
                            "description": "Priority level (low, medium, high, critical). Default: low. NEVER pass work-item types (e.g. bug, feature) here.",
                        },
                        "assignee": {
                            "type": "string",
                            "description": "Optional name, username, or email of the team member to assign the task to (e.g. 'alex'). NEVER pass project names (e.g. 'demo') here.",
                        },
                        "assignee_id": {
                            "type": "string",
                            "description": "Optional UUID or username of the assigned team member.",
                        },
                        "reporter": {
                            "type": "string",
                            "description": "Optional name, username, or email of the reporter for this task (e.g. 'alex'). NEVER pass project names (e.g. 'demo') here.",
                        },
                        "reporter_id": {
                            "type": "string",
                            "description": "Optional UUID or username of the reporter.",
                        },
                        "sprint_id": {
                            "type": "string",
                            "description": "Optional UUID of the sprint to attach this task to.",
                        },
                        "sprint_name": {
                            "type": "string",
                            "description": "Optional name of the sprint to attach this task to (e.g. 'TestModule' or 'Sprint 1').",
                        },
                        "user_story_id": {
                            "type": "string",
                            "description": "Optional UUID of the parent user story.",
                        },
                        "user_story_name": {
                            "type": "string",
                            "description": "Optional name or title of the parent user story (e.g. 'Mithun' or 'Auth Flow').",
                        },
                        "story_points": {
                            "type": "integer",
                            "description": "Fibonacci story points (0, 1, 2, 3, 5, 8, ...). Default: 0.",
                        },
                        "due_date": {
                            "type": "string",
                            "description": "Optional due date/time for the task in ISO 8601 format (e.g. 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SSZ'). Leave null or omit if not provided by the user.",
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
        current_user_id: str | None = None,
    ) -> Any:
        """Resolve a user by UUID, username, full name, or email within organization and project."""
        from src.auth.models import User
        from sqlalchemy import select, or_, func
        from sqlalchemy.orm import joinedload
        import uuid

        clean = str(identifier).strip()
        task_service = TaskService(db)

        # 0. Handle 'me', 'myself', 'self', '@me' referencing the current user
        if clean.lower() in ("me", "myself", "self", "@me") and current_user_id:
            user = (
                await db.execute(
                    select(User)
                    .where(
                        User.id == str(current_user_id),
                        User.organization_id == str(organization_id),
                        User.deleted_at.is_(None),
                    )
                    .options(joinedload(User.role))
                )
            ).scalar_one_or_none()
            if not user:
                raise TaskServiceError(404, "NOT_FOUND", "Current user not found in this organization")
            if not user.is_active:
                raise TaskServiceError(400, "VALIDATION_ERROR", f"{field_name.capitalize()} user is not active")
            if not await task_service._is_project_member(project, user):
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    f"User '{user.full_name or user.username}' must be a member of project '{project.name}'",
                )
            return user

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

    async def _resolve_sprint(
        self,
        identifier: str,
        project_id: str,
        db: AsyncSession,
    ) -> Any:
        """Resolve a sprint by UUID or name within the project."""
        from src.sprint.models import Sprint
        from sqlalchemy import select, func
        import uuid

        clean = str(identifier).strip()
        # 1. Try UUID
        try:
            target_uuid = str(uuid.UUID(clean))
            stmt = select(Sprint).where(
                Sprint.id == target_uuid,
                Sprint.project_id == project_id,
                Sprint.deleted_at.is_(None),
            )
            sprint = (await db.execute(stmt)).scalar_one_or_none()
            if sprint:
                return sprint
        except ValueError:
            pass

        # 2. Try exact case-insensitive match by name
        stmt = select(Sprint).where(
            Sprint.project_id == project_id,
            Sprint.deleted_at.is_(None),
            func.lower(Sprint.name) == clean.lower(),
        )
        sprints = (await db.execute(stmt)).scalars().all()
        if len(sprints) == 1:
            return sprints[0]
        if len(sprints) > 1:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"Multiple sprints match '{clean}'. Please provide a specific sprint ID.",
            )

        # 3. Try substring/ilike match by name
        stmt_like = select(Sprint).where(
            Sprint.project_id == project_id,
            Sprint.deleted_at.is_(None),
            Sprint.name.ilike(f"%{clean}%"),
        )
        sprints_like = (await db.execute(stmt_like)).scalars().all()
        if len(sprints_like) == 1:
            return sprints_like[0]
        if len(sprints_like) > 1:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"Multiple sprints match '{clean}'. Please provide a specific sprint ID.",
            )

        raise TaskServiceError(
            404,
            "NOT_FOUND",
            f"Sprint '{clean}' not found in this project",
        )

    async def _resolve_user_story(
        self,
        identifier: str,
        project_id: str,
        db: AsyncSession,
    ) -> Any:
        """Resolve a user story by UUID, key, or title within the project."""
        from src.user_story.models import UserStory
        from sqlalchemy import select, func, or_
        import uuid

        clean = str(identifier).strip()
        # 1. Try UUID
        try:
            target_uuid = str(uuid.UUID(clean))
            stmt = select(UserStory).where(
                UserStory.id == target_uuid,
                UserStory.project_id == project_id,
                UserStory.deleted_at.is_(None),
            )
            story = (await db.execute(stmt)).scalar_one_or_none()
            if story:
                return story
        except ValueError:
            pass

        # 2. Try exact case-insensitive match by key or title
        stmt = select(UserStory).where(
            UserStory.project_id == project_id,
            UserStory.deleted_at.is_(None),
            or_(
                func.lower(UserStory.key) == clean.lower(),
                func.lower(UserStory.title) == clean.lower(),
            ),
        )
        stories = (await db.execute(stmt)).scalars().all()
        if len(stories) == 1:
            return stories[0]
        if len(stories) > 1:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"Multiple user stories match '{clean}'. Please provide a specific user story ID or key.",
            )

        # 3. Try substring match by title
        stmt_like = select(UserStory).where(
            UserStory.project_id == project_id,
            UserStory.deleted_at.is_(None),
            UserStory.title.ilike(f"%{clean}%"),
        )
        stories_like = (await db.execute(stmt_like)).scalars().all()
        if len(stories_like) == 1:
            return stories_like[0]
        if len(stories_like) > 1:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"Multiple user stories match '{clean}'. Please provide a specific user story ID or key.",
            )

        raise TaskServiceError(
            404,
            "NOT_FOUND",
            f"User story '{clean}' not found in this project",
        )

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

        # 0. Validate title is not generic or empty
        if is_generic_or_empty_title(args.title):
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                "Task title is required. Please provide a title or meaningful name before creation.",
            )

        # 0b. Validate priority strictly against allowed enum
        if args.priority and args.priority.lower() not in TASK_PRIORITIES:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"Priority '{args.priority}' is not supported. Allowed priorities are: {', '.join(TASK_PRIORITIES)}.",
            )

        # 0c. Validate due_date is not in the past
        if args.due_date is not None and is_past_due_date(args.due_date):
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"The specified due date '{args.due_date.strftime('%Y-%m-%d')}' is in the past. Past due dates are not permitted.",
            )

        # 1. Project is mandatory
        if not args.project_id:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                "project_id is mandatory for the create_task tool",
            )
        proj_id_str = str(args.project_id)

        # Check project and authorization
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
        if assignee_target and project:
            target_clean = assignee_target.strip().lower()
            proj_name = (project.name or "").strip().lower()
            proj_key = (getattr(project, "key", None) or "").strip().lower()
            proj_slug = (getattr(project, "slug", None) or "").strip().lower()
            if target_clean in (proj_name, proj_key, proj_slug) or target_clean == "demo":
                logger.warning(
                    "Assignee argument %r matches project '%s' (key=%r). Clearing assignee to keep it null.",
                    assignee_target, project.name, getattr(project, "key", None),
                )
                assignee_target = None
                args.assignee = None
                args.assignee_id = None

        if assignee_target:
            assignee_user = await self._resolve_user(
                assignee_target, project, organization_id, db, field_name="assignee", current_user_id=user_id
            )
            args.assignee_id = UUID(str(assignee_user.id))
            assignee_name = assignee_user.full_name or assignee_user.username

        # 4b. Check and resolve reporter if provided by ID or name
        reporter_name: str | None = None
        reporter_target = args.reporter or (str(args.reporter_id) if args.reporter_id else None)
        if reporter_target and project:
            target_clean = reporter_target.strip().lower()
            proj_name = (project.name or "").strip().lower()
            proj_key = (getattr(project, "key", None) or "").strip().lower()
            proj_slug = (getattr(project, "slug", None) or "").strip().lower()
            if target_clean in (proj_name, proj_key, proj_slug) or target_clean == "demo":
                logger.warning(
                    "Reporter argument %r matches project '%s' (key=%r). Clearing reporter.",
                    reporter_target, project.name, getattr(project, "key", None),
                )
                reporter_target = None
                args.reporter = None
                args.reporter_id = None

        if reporter_target:
            reporter_user = await self._resolve_user(
                reporter_target, project, organization_id, db, field_name="reporter", current_user_id=user_id
            )
            args.reporter_id = UUID(str(reporter_user.id))
            reporter_name = reporter_user.full_name or reporter_user.username

        # 5. Check sprint if provided by ID or name
        sprint_name: str | None = None
        sprint_target = args.sprint_name or (str(args.sprint_id) if args.sprint_id else None)
        if sprint_target:
            sprint = await self._resolve_sprint(sprint_target, proj_id_str, db)
            if sprint.status == "completed":
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    "Cannot assign a task to a completed sprint",
                )
            args.sprint_id = UUID(str(sprint.id))
            sprint_name = sprint.name

        # 6. Check user story if provided by ID or name
        user_story_name: str | None = None
        story_target = args.user_story_name or (str(args.user_story_id) if args.user_story_id else None)
        if story_target:
            user_story = await self._resolve_user_story(story_target, proj_id_str, db)
            args.user_story_id = UUID(str(user_story.id))
            user_story_name = user_story.title

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
            "user_story_name": user_story_name,
            "due_date": args.due_date.isoformat() if args.due_date else None,
        }
        logger.info(
            "Validated tool arguments for task preparation: "
            "project_id=%s, title=%r, type=%r, priority=%r, story_points=%s, "
            "assignee_id=%s, reporter_id=%s, sprint_id=%s, user_story_id=%s, due_date=%s",
            proj_id_str,
            args.title,
            args.type,
            args.priority,
            args.story_points,
            args.assignee_id,
            args.reporter_id,
            args.sprint_id,
            args.user_story_id,
            args.due_date,
        )
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

        logger.info(
            "Calling task service create with validated tool arguments: "
            "project_id=%s, title=%r, description=%r, type=%r, priority=%r, "
            "story_points=%s, assignee_id=%s, reporter_id=%s, sprint_id=%s, "
            "user_story_id=%s, due_date=%s",
            proj_id_str,
            req.title,
            req.description,
            req.type,
            req.priority,
            req.story_points,
            req.assignee_id,
            req.reporter_id,
            req.sprint_id,
            req.user_story_id,
            req.due_date,
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


class UpdateTaskToolArgs(BaseModel):
    """Strictly typed and normalized arguments for the update_task tool."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    task_id: UUID | None = None
    task_key: str | None = None
    project_id: UUID | None = None
    project_name: str | None = None

    title: str | None = None
    description: str | None = None
    type: TaskType | None = None
    priority: TaskPriority | None = None
    status: str | None = None
    status_id: UUID | None = None

    assignee: str | None = None
    assignee_id: UUID | None = None
    clear_assignee: bool = False

    reporter: str | None = None
    reporter_id: UUID | None = None

    sprint: str | None = None
    sprint_name: str | None = None
    sprint_id: UUID | None = None
    clear_sprint: bool = False

    user_story: str | None = None
    user_story_name: str | None = None
    user_story_id: UUID | None = None
    clear_user_story: bool = False

    story_points: int | None = None
    due_date: datetime | None = None
    clear_due_date: bool = False

    estimated_hours: float | None = None
    actual_hours: float | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)

        # Aliases for task identification
        if "key" in data and not data.get("task_key"):
            data["task_key"] = data["key"]
        if "id" in data and not data.get("task_id"):
            data["task_id"] = data["id"]

        # Aliases for sprint
        for alias in ("sprint", "sprint_name"):
            if alias in data and data[alias] is not None:
                val = str(data[alias]).strip().lower()
                if val in ("none", "clear", "remove", "null", "unassign"):
                    data["clear_sprint"] = True
                    data["sprint_name"] = None
                    data["sprint_id"] = None
                elif not data.get("sprint_name") and not data.get("sprint_id"):
                    try:
                        data["sprint_id"] = UUID(str(data[alias]))
                    except (ValueError, TypeError):
                        data["sprint_name"] = str(data[alias])
                break

        # Aliases for user story
        for alias in ("story", "user_story", "story_name", "user_story_name"):
            if alias in data and data[alias] is not None:
                val = str(data[alias]).strip().lower()
                if val in ("none", "clear", "remove", "null", "unassign"):
                    data["clear_user_story"] = True
                    data["user_story_name"] = None
                    data["user_story_id"] = None
                elif not data.get("user_story_name") and not data.get("user_story_id"):
                    try:
                        data["user_story_id"] = UUID(str(data[alias]))
                    except (ValueError, TypeError):
                        data["user_story_name"] = str(data[alias])
                break

        # Aliases for assignee
        if "assignee" in data and data["assignee"] is not None:
            val = str(data["assignee"]).strip().lower()
            if val in ("none", "clear", "unassign", "unassigned", "null"):
                data["clear_assignee"] = True
                data["assignee"] = None
                data["assignee_id"] = None

        # Aliases for due date
        if "due_date" in data and data["due_date"] is not None:
            val = str(data["due_date"]).strip().lower()
            if val in ("none", "clear", "remove", "null"):
                data["clear_due_date"] = True
                data["due_date"] = None

        return data

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_due_date(cls, v: Any) -> datetime | None:
        if v is None or v == "":
            return None
        dt, ok = parse_due_date(v)
        if not ok:
            raise ValueError(f"Invalid due date '{v}'. Please provide a valid date (e.g. YYYY-MM-DD).")
        return dt


class UpdateTaskTool:
    """Encapsulates the 'update_task' AI tool."""

    name: str = "update_task"
    description: str = (
        "Update an existing task in WorkPilot. "
        "Requires task_key (e.g. 'DEM-12') or task_id to identify the task, "
        "and any fields to update (title, description, type, priority, status, assignee, sprint_name, user_story_name, story_points, due_date)."
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
                        "task_key": {
                            "type": "string",
                            "description": "The key of the task to update (e.g. 'DEM-12' or 'WP-5'). Highly recommended if known.",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "The UUID of the task to update (if task_key is not known).",
                        },
                        "project_id": {
                            "type": "string",
                            "description": "Optional UUID of the project for verification.",
                        },
                        "project_name": {
                            "type": "string",
                            "description": "Optional name of the project.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Updated task title (between 3 and 200 characters).",
                        },
                        "description": {
                            "type": "string",
                            "description": "Updated details or instructions for the task.",
                        },
                        "type": {
                            "type": "string",
                            "enum": TASK_TYPES,
                            "description": "Updated task work-item type (bug, feature, task, chore, story).",
                        },
                        "priority": {
                            "type": "string",
                            "enum": TASK_PRIORITIES,
                            "description": "Updated priority level (low, medium, high, critical).",
                        },
                        "status": {
                            "type": "string",
                            "description": "Updated workflow status name (e.g. 'in_progress', 'todo', 'testing', 'completed', 'blocked').",
                        },
                        "status_id": {
                            "type": "string",
                            "description": "Optional UUID of the target status.",
                        },
                        "assignee": {
                            "type": "string",
                            "description": "Team member to assign the task to (e.g. 'alex', 'me'). Pass 'none' or 'unassigned' to unassign.",
                        },
                        "assignee_id": {
                            "type": "string",
                            "description": "Optional UUID of the assigned team member.",
                        },
                        "clear_assignee": {
                            "type": "boolean",
                            "description": "Set to true to unassign the task.",
                        },
                        "reporter": {
                            "type": "string",
                            "description": "Optional name or email of the reporter.",
                        },
                        "reporter_id": {
                            "type": "string",
                            "description": "Optional UUID of the reporter.",
                        },
                        "sprint_name": {
                            "type": "string",
                            "description": "Name of the sprint to move this task to. Pass 'none' or 'clear' to remove from sprint.",
                        },
                        "sprint_id": {
                            "type": "string",
                            "description": "Optional UUID of the sprint.",
                        },
                        "clear_sprint": {
                            "type": "boolean",
                            "description": "Set to true to remove the task from its current sprint.",
                        },
                        "user_story_name": {
                            "type": "string",
                            "description": "Name or title of the parent user story. Pass 'none' or 'clear' to remove.",
                        },
                        "user_story_id": {
                            "type": "string",
                            "description": "Optional UUID of the parent user story.",
                        },
                        "clear_user_story": {
                            "type": "boolean",
                            "description": "Set to true to detach the task from its user story.",
                        },
                        "story_points": {
                            "type": "integer",
                            "description": "Updated Fibonacci story points (0, 1, 2, 3, 5, 8, ...).",
                        },
                        "due_date": {
                            "type": "string",
                            "description": "Updated due date/time in ISO 8601 format (e.g. 'YYYY-MM-DD'). Pass 'none' or 'clear' to remove due date.",
                        },
                        "clear_due_date": {
                            "type": "boolean",
                            "description": "Set to true to clear the task's due date.",
                        },
                        "estimated_hours": {
                            "type": "number",
                            "description": "Estimated hours for the task.",
                        },
                        "actual_hours": {
                            "type": "number",
                            "description": "Actual hours spent on the task.",
                        },
                    },
                },
            },
        }

    @staticmethod
    def parse_arguments(raw_args: dict[str, Any]) -> UpdateTaskToolArgs:
        """Parse and validate raw tool arguments strictly."""
        return UpdateTaskToolArgs.model_validate(raw_args)

    async def _find_task(
        self,
        args: UpdateTaskToolArgs,
        db: AsyncSession,
    ) -> Task:
        """Find the task by task_id or task_key."""
        from sqlalchemy import func, select
        from sqlalchemy.orm import joinedload

        if not args.task_id and not args.task_key:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                "Must provide task_key (e.g. 'DEM-12') or task_id to identify the task to update.",
            )

        stmt = (
            select(Task)
            .options(
                joinedload(Task.project),
                joinedload(Task.assignee),
                joinedload(Task.reporter),
                joinedload(Task.sprint),
                joinedload(Task.user_story),
            )
            .where(Task.deleted_at.is_(None))
        )
        if args.task_id:
            stmt = stmt.where(Task.id == str(args.task_id))
        elif args.task_key:
            clean_key = args.task_key.strip()
            stmt = stmt.where(func.lower(Task.key) == clean_key.lower())

        task = (await db.execute(stmt)).scalar_one_or_none()
        if not task:
            ident = str(args.task_id) if args.task_id else args.task_key
            raise TaskServiceError(404, "NOT_FOUND", f"Task '{ident}' not found.")
        return task

    async def validate_preconditions(
        self,
        args: UpdateTaskToolArgs,
        *,
        user_id: str,
        organization_id: str,
        role: str,
        db: AsyncSession,
        redis: Any = None,
    ) -> dict[str, Any]:
        """Validate user permissions, tenant isolation, and related entities for updating."""
        task_service = TaskService(db, redis)

        # 1. Locate task
        task = await self._find_task(args, db)
        proj_id_str = str(task.project_id)
        project = task.project

        # 2. Check organization isolation
        if str(project.organization_id) != str(organization_id):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "Task belongs to a project in a different organization",
            )

        # 3. Check authorization & permissions
        project_model, actor = await task_service._check_authorization(
            proj_id_str,
            user_id,
            "You do not have permission to view tasks in this project",
        )
        if not await task_service._has_permission(project_model, actor, "tasks", "modify"):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "You do not have permission to update tasks in this project",
            )
        is_pm_or_admin = await task_service._has_permission(project_model, actor, "projects", "modify")

        # 4. Validate fields
        if args.title is not None and not (3 <= len(args.title) <= 200):
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                "Task title must be between 3 and 200 characters",
            )

        if args.priority is not None and args.priority.lower() not in TASK_PRIORITIES:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                f"Priority '{args.priority}' is not supported. Allowed priorities are: {', '.join(TASK_PRIORITIES)}.",
            )

        if args.story_points is not None:
            if args.story_points < 0 or not _is_fibonacci(args.story_points):
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    "Story points must follow the Fibonacci scale",
                )

        if args.due_date is not None and _is_backdated(args.due_date) and not is_pm_or_admin:
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                "Due date cannot be backdated unless set by a PM or Admin",
            )

        # Assignee resolution
        helper = CreateTaskTool()
        resolved_assignee_id: str | None = None
        assignee_name: str | None = None
        if args.clear_assignee:
            resolved_assignee_id = None
            assignee_name = None
        elif args.assignee is not None or args.assignee_id is not None:
            target = args.assignee or str(args.assignee_id)
            user = await helper._resolve_user(
                target, project_model, organization_id, db, field_name="assignee", current_user_id=user_id
            )
            resolved_assignee_id = str(user.id)
            assignee_name = user.full_name or user.username
        else:
            resolved_assignee_id = str(task.assignee_id) if task.assignee_id else None
            assignee_name = (task.assignee.full_name or task.assignee.username) if task.assignee else None

        # Reporter resolution
        resolved_reporter_id: str | None = None
        reporter_name: str | None = None
        if args.reporter is not None or args.reporter_id is not None:
            target = args.reporter or str(args.reporter_id)
            user = await helper._resolve_user(
                target, project_model, organization_id, db, field_name="reporter", current_user_id=user_id
            )
            resolved_reporter_id = str(user.id)
            reporter_name = user.full_name or user.username
        else:
            resolved_reporter_id = str(task.reporter_id) if task.reporter_id else None
            reporter_name = (task.reporter.full_name or task.reporter.username) if getattr(task, "reporter", None) else None

        # Sprint resolution
        resolved_sprint_id: str | None = None
        sprint_name: str | None = None
        if args.clear_sprint:
            resolved_sprint_id = None
            sprint_name = None
        elif args.sprint_name is not None or args.sprint_id is not None or args.sprint is not None:
            target = args.sprint_name or (str(args.sprint_id) if args.sprint_id else args.sprint)
            sprint = await helper._resolve_sprint(target, proj_id_str, db)
            if sprint.status == "completed":
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    "Cannot assign a task to a completed sprint",
                )
            resolved_sprint_id = str(sprint.id)
            sprint_name = sprint.name
        else:
            resolved_sprint_id = str(task.sprint_id) if task.sprint_id else None
            sprint_name = task.sprint.name if task.sprint else None

        # User story resolution
        resolved_user_story_id: str | None = None
        user_story_name: str | None = None
        if args.clear_user_story:
            resolved_user_story_id = None
            user_story_name = None
        elif args.user_story_name is not None or args.user_story_id is not None or args.user_story is not None:
            target = args.user_story_name or (str(args.user_story_id) if args.user_story_id else args.user_story)
            story = await helper._resolve_user_story(target, proj_id_str, db)
            resolved_user_story_id = str(story.id)
            user_story_name = story.title
        else:
            resolved_user_story_id = str(task.user_story_id) if task.user_story_id else None
            user_story_name = task.user_story.title if task.user_story else None

        # Status resolution
        resolved_status_name = task.status
        if args.status is not None or args.status_id is not None:
            statuses = await task_service._statuses(proj_id_str)
            new_status_id, new_status_name = await task_service._resolve_status(
                proj_id_str, args.status_id, args.status, statuses
            )
            if new_status_id != str(task.status_id) and not is_pm_or_admin:
                old_status = normalize_task_status(task.status)
                new_status = normalize_task_status(new_status_name)
                transition_map = {
                    "todo": {"in_progress", "blocked"},
                    "in_progress": {"in_review", "blocked"},
                    "in_review": {"testing", "blocked"},
                    "testing": {"completed", "blocked"},
                    "completed": {"blocked"},
                    "blocked": {"in_progress", "todo"},
                }
                both_default = old_status in DEFAULT_STATUS_COLORS and new_status in DEFAULT_STATUS_COLORS
                if both_default and new_status not in transition_map.get(old_status, set()):
                    raise TaskServiceError(
                        400,
                        "INVALID_STATUS_TRANSITION",
                        f"Invalid status transition from {old_status} to {new_status} for developers",
                    )
            resolved_status_name = new_status_name

        # Due date resolution
        if args.clear_due_date:
            due_date_iso = None
        elif args.due_date is not None:
            due_date_iso = args.due_date.isoformat()
        else:
            due_date_iso = task.due_date.isoformat() if task.due_date else None

        proposed = {
            "task_id": str(task.id),
            "key": task.key,
            "project_id": proj_id_str,
            "project_name": project.name,
            "title": args.title if args.title is not None else task.title,
            "description": args.description if args.description is not None else (task.description or ""),
            "type": args.type if args.type is not None else task.type,
            "priority": args.priority if args.priority is not None else task.priority,
            "status": resolved_status_name,
            "story_points": args.story_points if args.story_points is not None else task.story_points,
            "assignee_id": resolved_assignee_id,
            "assignee_name": assignee_name,
            "reporter_id": resolved_reporter_id,
            "reporter_name": reporter_name,
            "sprint_id": resolved_sprint_id,
            "sprint_name": sprint_name,
            "user_story_id": resolved_user_story_id,
            "user_story_name": user_story_name,
            "due_date": due_date_iso,
            "estimated_hours": float(args.estimated_hours) if args.estimated_hours is not None else (float(task.estimated_hours) if task.estimated_hours is not None else None),
            "actual_hours": float(args.actual_hours) if args.actual_hours is not None else (float(task.actual_hours) if task.actual_hours is not None else None),
        }
        return proposed

    async def execute(
        self,
        args: UpdateTaskToolArgs,
        *,
        user_id: str,
        organization_id: str,
        role: str,
        db: AsyncSession,
        redis: Any = None,
    ) -> dict[str, Any]:
        """Execute task update using TaskService.update method."""
        task_service = TaskService(db, redis)
        task = await self._find_task(args, db)
        proj_id_str = str(task.project_id)
        project = task.project
        helper = CreateTaskTool()

        update_kwargs: dict[str, Any] = {}
        if args.title is not None:
            update_kwargs["title"] = args.title
        if args.description is not None:
            update_kwargs["description"] = args.description
        if args.type is not None:
            update_kwargs["type"] = args.type
        if args.priority is not None:
            update_kwargs["priority"] = args.priority
        if args.status is not None:
            update_kwargs["status"] = args.status
        if args.status_id is not None:
            update_kwargs["status_id"] = args.status_id

        if args.clear_assignee:
            update_kwargs["assignee_id"] = None
        elif args.assignee_id is not None:
            update_kwargs["assignee_id"] = args.assignee_id
        elif args.assignee is not None:
            user = await helper._resolve_user(
                args.assignee, project, organization_id, db, field_name="assignee", current_user_id=user_id
            )
            update_kwargs["assignee_id"] = UUID(str(user.id))

        if args.reporter_id is not None:
            update_kwargs["reporter_id"] = args.reporter_id
        elif args.reporter is not None:
            user = await helper._resolve_user(
                args.reporter, project, organization_id, db, field_name="reporter", current_user_id=user_id
            )
            update_kwargs["reporter_id"] = UUID(str(user.id))

        if args.clear_sprint:
            update_kwargs["sprint_id"] = None
        elif args.sprint_id is not None:
            update_kwargs["sprint_id"] = args.sprint_id
        elif args.sprint_name is not None or args.sprint is not None:
            target_s = args.sprint_name or args.sprint
            sprint = await helper._resolve_sprint(target_s, proj_id_str, db)
            update_kwargs["sprint_id"] = UUID(str(sprint.id))

        if args.clear_user_story:
            update_kwargs["user_story_id"] = None
        elif args.user_story_id is not None:
            update_kwargs["user_story_id"] = args.user_story_id
        elif args.user_story_name is not None or args.user_story is not None:
            target_st = args.user_story_name or args.user_story
            story = await helper._resolve_user_story(target_st, proj_id_str, db)
            update_kwargs["user_story_id"] = UUID(str(story.id))

        if args.story_points is not None:
            update_kwargs["story_points"] = args.story_points

        if args.clear_due_date:
            update_kwargs["due_date"] = None
        elif args.due_date is not None:
            update_kwargs["due_date"] = args.due_date

        if args.estimated_hours is not None:
            update_kwargs["estimated_hours"] = args.estimated_hours
        if args.actual_hours is not None:
            update_kwargs["actual_hours"] = args.actual_hours

        req = UpdateTaskRequest(**update_kwargs)

        await task_service.update(
            proj_id_str,
            str(task.id),
            req,
            user_id,
            organization_id,
            role,
        )

        updated_task = await task_service._task(str(task.id), proj_id_str)
        return {
            "task_id": str(updated_task.id),
            "key": updated_task.key,
            "title": updated_task.title,
            "status": updated_task.status,
            "project_id": str(updated_task.project_id),
            "type": updated_task.type,
            "priority": updated_task.priority,
            "assignee_id": str(updated_task.assignee_id) if updated_task.assignee_id else None,
            "reporter_id": str(updated_task.reporter_id) if updated_task.reporter_id else None,
        }
