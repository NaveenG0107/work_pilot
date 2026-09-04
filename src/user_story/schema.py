from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from src.user_story.models import format_serial_number


def validate_uuid(value: str | None) -> str | None:
    import uuid

    if value is None:
        return None
    try:
        cleaned = str(value).strip().strip('"').strip("'")
        return str(uuid.UUID(cleaned))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid request payload.") from exc


def omit_empty(value) -> bool:
    return value is None or value == "" or value == []


PRIORITIES = ["low", "medium", "high", "critical"]

ALLOWED_STATUS_FILTERS = [
    "todo",
    "in_progress",
    "in_review",
    "testing",
    "completed",
    "blocked",
]

ALLOWED_SORT_BY = [
    "title",
    "created_at",
    "updated_at",
    "priority",
    "status",
    "serial_number",
]

ALLOWED_SORT_ORDER = ["ASC", "DESC"]


class CreateUserStoryRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    priority: str = Field(..., json_schema_extra={"enum": PRIORITIES})
    status_id: Optional[str] = None
    status: Optional[str] = None
    story_points: int = Field(default=0, ge=0)
    assignee_id: Optional[str] = None
    sprint_id: Optional[str] = None

    @field_validator("status_id", "assignee_id", "sprint_id")
    @classmethod
    def validate_optional_ids(cls, value: str | None) -> str | None:
        return validate_uuid(value)


class UpdateUserStoryRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = None
    priority: Optional[str] = Field(
        default=None, json_schema_extra={"enum": PRIORITIES}
    )
    status_id: Optional[str] = None
    status: Optional[str] = None
    story_points: Optional[int] = Field(default=None, ge=0)
    is_closed: Optional[bool] = None
    assignee_id: Optional[str] = None
    sprint_id: Optional[str] = None

    @field_validator("status_id", "assignee_id", "sprint_id")
    @classmethod
    def validate_optional_ids(cls, value: str | None) -> str | None:
        return validate_uuid(value)


class UpdateUserStoryStatusAssignmentRequest(BaseModel):
    status_id: str

    @field_validator("status_id")
    @classmethod
    def validate_status_id(cls, value: str) -> str:
        return validate_uuid(value)  # type: ignore[return-value]


class ReorderUserStoriesRequest(BaseModel):
    story_ids: list[str] = Field(..., min_length=1)

    @field_validator("story_ids")
    @classmethod
    def validate_story_ids(cls, values: list[str]) -> list[str]:
        return [validate_uuid(v) for v in values]  # type: ignore[misc]


class UserStoryFilter(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1)
    sort_by: str = Field(default="created_at")
    sort_order: str = Field(default="DESC")
    status: str = ""
    assignee_id: Optional[str] = None
    reporter_id: Optional[str] = None
    sprint_id: Optional[str] = None
    priority: str = ""
    search: str = ""
    fields: str = ""
    serial_number: Optional[int] = None
    sequence_number: Optional[int] = None
    is_unassigned_story: bool = False
    is_closed: Optional[bool] = None

    @field_validator("assignee_id", "reporter_id", "sprint_id")
    @classmethod
    def validate_optional_ids(cls, value: str | None) -> str | None:
        return validate_uuid(value)

    @field_validator("status", "priority", "sort_by", "sort_order")
    @classmethod
    def trim_strings(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("sort_order")
    @classmethod
    def normalize_sort_order(cls, value: str) -> str:
        return (value or "DESC").upper()


class UserSummary(BaseModel):
    id: str
    full_name: str = Field(default="", serialization_alias="name")
    email: str = ""
    avatar_url: str | None = Field(default=None, exclude_if=omit_empty)
    color: str | None = Field(default=None, exclude_if=omit_empty)
    role: str | None = Field(default=None, exclude_if=omit_empty)


class TaskSummary(BaseModel):
    id: str
    title: str
    key: str = ""
    type: str = ""
    status: str = ""
    status_color: str = ""
    status_is_final: bool = False
    priority: str = ""
    is_favourite: bool = False
    assignee_id: str | None = Field(default=None, exclude_if=omit_empty)
    assignee_name: str | None = Field(default=None, exclude_if=omit_empty)
    assignee: UserSummary | None = Field(default=None, exclude_if=omit_empty)


class UserStoryResponse(BaseModel):
    id: str
    project_id: str
    project_name: str | None = Field(default=None, exclude_if=omit_empty)
    sprint_id: str | None = Field(default=None, exclude_if=omit_empty)
    sprint_name: str | None = Field(default=None, exclude_if=omit_empty)
    serial_number: int
    formatted_serial_number: str = Field(default="", exclude_if=omit_empty)
    title: str
    description: str | None = Field(default=None, exclude_if=omit_empty)
    priority: str
    status_id: str | None = Field(default=None, exclude_if=omit_empty)
    status: str = ""
    status_color: str = ""
    is_closed: bool
    is_favourite: bool = False
    story_points: int
    assignee_id: str | None = Field(default=None, exclude_if=omit_empty)
    assignee_name: str | None = Field(default=None, exclude_if=omit_empty)
    reporter_id: str
    reporter_name: str = ""
    reporter: UserSummary | None = Field(default=None, exclude_if=omit_empty)
    assignee: UserSummary | None = Field(default=None, exclude_if=omit_empty)
    backlog_order: int
    total_tasks: int = 0
    completed_tasks: int = 0
    progress: float = 0.0
    created_at: datetime
    updated_at: datetime
    tasks: list[TaskSummary] = Field(default_factory=list, exclude_if=omit_empty)


class PaginationResponse(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


def user_summary_from_user(user) -> UserSummary | None:
    if user is None:
        return None
    role_name = None
    if "role" in user.__dict__ and user.role:
        role_name = getattr(user.role, "name", None)
    return UserSummary(
        id=str(user.id),
        full_name=getattr(user, "full_name", None) or "",
        email=getattr(user, "email", None) or "",
        avatar_url=getattr(user, "avatar_url", None) or None,
        color=getattr(user, "color", None) or None,
        role=role_name,
    )


def formatted_serial_number(seq: int) -> str:
    return format_serial_number(seq)


class FavoriteResponse(BaseModel):
    id: str
    user_id: str
    item_type: str = "user_story"
    user_story_id: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    user_story_name: Optional[str] = None
    user_story_title: Optional[str] = None
    created_at: datetime


class RemoveFavoriteResponse(BaseModel):
    id: str


class UserStoryAttachmentResponse(BaseModel):
    id: str
    user_story_id: str
    original_filename: str
    stored_filename: str
    mime_type: str
    file_size: int
    url: str
    uploaded_by: str
    uploaded_at: datetime


class CreateCommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    parent_comment_id: Optional[str] = None
    attachment_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_attachment_collection(cls, value):
        if not isinstance(value, dict) or value.get("attachment_ids"):
            return value
        attachments = value.get("attachments")
        if not isinstance(attachments, list):
            return value
        data = dict(value)
        data["attachment_ids"] = [
            item.get("id") if isinstance(item, dict) else item
            for item in attachments
            if (item.get("id") if isinstance(item, dict) else item)
        ]
        return data

    @field_validator("parent_comment_id")
    @classmethod
    def validate_parent_id(cls, value: str | None) -> str | None:
        return validate_uuid(value)

    @field_validator("attachment_ids")
    @classmethod
    def validate_attachment_ids(cls, values: List[str]) -> List[str]:
        return [validate_uuid(value) for value in values]


class UpdateCommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class CommentResponse(BaseModel):
    id: str
    task_id: Optional[str] = None
    user_story_id: Optional[str] = None
    user_id: str
    user_name: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    color: Optional[str] = None
    content: str
    parent_comment_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False
    attachments: list[dict] = Field(default_factory=list)
    replies_count: int = 0

