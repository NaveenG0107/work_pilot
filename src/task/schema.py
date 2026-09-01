from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)


TASK_STATUSES = [
    "todo",
    "in_progress",
    "in_review",
    "testing",
    "completed",
    "blocked",
]
TASK_TYPES = ["bug", "feature", "task", "chore", "story"]
TASK_PRIORITIES = ["low", "medium", "high", "critical"]

TaskType = Literal["bug", "feature", "task", "chore", "story"]
TaskPriority = Literal["low", "medium", "high", "critical"]


def omit_empty(value: Any) -> bool:
    """Match Go's ``omitempty`` for the Task response DTOs."""

    return value is None or value == "" or value == []


def serialize_go_time(value: datetime | None) -> str | None:
    """Serialize datetimes like Go's RFC3339 JSON encoder."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    if value.microsecond:
        base = value.strftime("%Y-%m-%dT%H:%M:%S")
        fraction = f"{value.microsecond:06d}".rstrip("0")
        return f"{base}.{fraction}Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class TaskSchema(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ---------------------------------------------------------------------------
# Go: internal/handlers/dto/request/task.go
# ---------------------------------------------------------------------------


class CreateTaskRequest(TaskSchema):
    title: str = Field(min_length=3, max_length=255)
    description: str = ""
    type: TaskType
    priority: TaskPriority
    status_id: UUID | None = None
    status: str = ""
    assignee_id: UUID | None = None
    reporter_id: UUID | None = None
    sprint_id: UUID | None = None
    user_story_id: UUID | None = None
    story_points: int = Field(default=0, ge=0)
    due_date: datetime | None = None
    estimated_hours: float | None = Field(default=None, ge=0)
    actual_hours: float | None = Field(default=None, ge=0)
    label_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_story_id_alias(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("user_story_id") is None and data.get("story_id") is not None:
            # The Go decoder silently ignores an invalid legacy alias. A valid
            # alias is copied into the canonical request field.
            try:
                data["user_story_id"] = UUID(str(data["story_id"]))
            except (ValueError, TypeError, AttributeError):
                pass
        return data


class UpdateTaskRequest(TaskSchema):
    title: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = None
    type: TaskType | None = None
    priority: TaskPriority | None = None
    status_id: UUID | None = None
    status: str | None = None
    blocked_reason: str | None = None
    assignee_id: UUID | None = None
    reporter_id: UUID | None = None
    sprint_id: UUID | None = None
    user_story_id: UUID | None = None
    story_points: int | None = Field(default=None, ge=0)
    due_date: datetime | None = None
    estimated_hours: float | None = Field(default=None, ge=0)
    actual_hours: float | None = Field(default=None, ge=0)
    label_ids: list[UUID] | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_story_id_alias(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "story_id" not in data:
            return data
        if data["story_id"] is None:
            data["user_story_id"] = None
        elif data.get("user_story_id") is None:
            try:
                data["user_story_id"] = UUID(str(data["story_id"]))
            except (ValueError, TypeError, AttributeError):
                pass
        return data

    def explicitly_set(self, field: str) -> bool:
        return field in self.model_fields_set


class CloneTaskRequest(TaskSchema):
    keep_assignee: bool = False


class BulkUpdateTaskItem(TaskSchema):
    task_id: UUID
    status_id: UUID | None = None
    status: str | None = None
    blocked_reason: str | None = None
    sprint_id: UUID | None = None
    assignee_id: UUID | None = None


class BulkUpdateTasksRequest(TaskSchema):
    tasks: list[BulkUpdateTaskItem] = Field(min_length=1)


class BulkDeleteTasksRequest(TaskSchema):
    task_ids: list[UUID] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Go: internal/handlers/dto/response
# ---------------------------------------------------------------------------


class UserSummary(TaskSchema):
    id: str
    full_name: str = Field(default="", serialization_alias="name")
    email: str = ""
    avatar_url: str | None = None
    color: str = ""
    role: str | None = Field(default=None, exclude_if=omit_empty)

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        populate_by_name=True,
    )


class LabelResponse(TaskSchema):
    id: str
    name: str
    color: str


class PaginationResponse(TaskSchema):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class TaskResponse(TaskSchema):
    # Field order intentionally mirrors the Go struct because JSON preserves
    # struct/model field order in both implementations.
    id: str
    project_id: str
    project_name: str = Field(default="", exclude_if=omit_empty)
    sprint_id: str | None = None
    sprint_name: str = ""
    user_story_id: str | None = Field(default=None, exclude_if=omit_empty)
    user_story_title: str = Field(default="", exclude_if=omit_empty)
    key: str
    serial_number: int
    formatted_serial_number: str = Field(default="", exclude_if=omit_empty)
    title: str
    description: str = Field(default="", exclude_if=omit_empty)
    type: str
    priority: str
    status_id: str
    status: str
    status_color: str
    is_final: bool
    is_favourite: bool
    assignee_id: str | None = Field(default=None, exclude_if=omit_empty)
    reporter_id: str | None = Field(default=None, exclude_if=omit_empty)
    reporter_name: str = Field(default="", exclude_if=omit_empty)
    assignee_name: str = Field(default="", exclude_if=omit_empty)
    story_points: int
    due_date: datetime | None = Field(default=None, exclude_if=omit_empty)
    estimated_hours: float | None = Field(default=None, exclude_if=omit_empty)
    actual_hours: float | None = Field(default=None, exclude_if=omit_empty)
    blocked_reason: str = Field(default="", exclude_if=omit_empty)
    created_at: datetime
    updated_at: datetime
    labels: list[LabelResponse] = Field(default_factory=list, exclude_if=omit_empty)
    reporter: UserSummary | None = Field(default=None, exclude_if=omit_empty)
    assignee: UserSummary | None = Field(default=None, exclude_if=omit_empty)

    @field_serializer("due_date", "created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return serialize_go_time(value)


class BulkUpdateTasksResponse(TaskSchema):
    updated_count: int
    failed_task_ids: list[str]
    failure_reasons: dict[str, str]


class BulkDeleteTasksResponse(TaskSchema):
    deleted_count: int
    deleted_task_ids: list[str]
    failed_task_ids: list[str]
    failure_reasons: dict[str, str]


class AttachmentResponse(TaskSchema):
    id: str
    task_id: str
    original_filename: str
    mime_type: str
    file_size: int
    url: str = Field(default="", exclude_if=omit_empty)
    uploaded_by: str
    uploaded_at: datetime

    @field_serializer("uploaded_at")
    def serialize_uploaded_at(self, value: datetime) -> str:
        return serialize_go_time(value) or ""


class FavoriteResponse(TaskSchema):
    id: str
    user_id: str
    item_type: str
    user_story_id: str | None = Field(default=None, exclude_if=omit_empty)
    task_id: str | None = Field(default=None, exclude_if=omit_empty)
    project_id: str | None = Field(default=None, exclude_if=omit_empty)
    project_name: str = Field(default="", exclude_if=omit_empty)
    user_story_name: str = Field(default="", exclude_if=omit_empty)
    user_story_title: str = Field(default="", exclude_if=omit_empty)
    task_name: str = Field(default="", exclude_if=omit_empty)
    task_title: str = Field(default="", exclude_if=omit_empty)
    user_story: Any | None = Field(default=None, exclude_if=omit_empty)
    task: TaskResponse | None = Field(default=None, exclude_if=omit_empty)
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return serialize_go_time(value) or ""


class RemoveFavoriteResponse(TaskSchema):
    id: str


class SuccessResponse(TaskSchema):
    success: bool
    status_code: int
    message: str
    data: Any | None = Field(default=None, exclude_if=lambda value: value is None)
    meta: PaginationResponse | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class ErrorDetail(TaskSchema):
    code: str
    status_code: int
    message: str


class ErrorResponse(TaskSchema):
    success: bool = False
    error: ErrorDetail


class UserTaskInsightsResponse(TaskSchema):
    completed: int = 0
    completion_percentage: float = 0.0
    in_progress: int = 0
    total_assigned: int = 0
