from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def omit_empty(value) -> bool:
    return value is None or value == "" or value == []


class PaginationResponse(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class UserSummary(BaseModel):
    id: str
    full_name: str = Field(default="", serialization_alias="name")
    email: str = ""
    avatar_url: str | None = Field(default=None, exclude_if=omit_empty)
    color: str | None = Field(default=None, exclude_if=omit_empty)
    role: str | None = Field(default=None, exclude_if=omit_empty)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AuditFilter(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1)

    user_id: str | None = None
    organization_id: str | None = None

    resource_type: str | None = None
    resource_id: str | None = None

    task_id: str | None = None
    user_story_id: str | None = None
    project_id: str | None = None

    type: str | None = None


class AuditLogResponse(BaseModel):
    id: str

    project_id: str | None = Field(default=None, exclude_if=omit_empty)
    project_name: str | None = Field(default=None, exclude_if=omit_empty)

    organization_id: str | None = Field(default=None, exclude_if=omit_empty)

    user: UserSummary | None = Field(default=None, exclude_if=omit_empty)

    action: str
    resource_type: str
    resource_id: str | None = Field(default=None, exclude_if=omit_empty)

    details: str | None = Field(default=None, exclude_if=omit_empty)
    created_at: datetime

    task_key: str | None = Field(default=None, exclude_if=omit_empty)
    task_id: str | None = Field(default=None, exclude_if=omit_empty)

    user_story_id: str | None = Field(default=None, exclude_if=omit_empty)

    title: str | None = Field(default=None, exclude_if=omit_empty)

    task_name: str | None = Field(default=None, exclude_if=omit_empty)
    user_story_name: str | None = Field(default=None, exclude_if=omit_empty)
    sprint_name: str | None = Field(default=None, exclude_if=omit_empty)

    type: str | None = Field(default=None, exclude_if=omit_empty)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        # Go emits RFC3339 with the UTC "Z" suffix (timestamptz).
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        return value.isoformat().replace("+00:00", "Z")


class AuditLogResponseWrapper(BaseModel):
    user: UserSummary | None = Field(default=None, exclude_if=omit_empty)
    activities: list[AuditLogResponse]


class AuditSuccessResponse(BaseModel):
    success: bool
    status_code: int
    message: str
    data: AuditLogResponseWrapper
    meta: PaginationResponse


class ErrorDetail(BaseModel):
    code: str
    status_code: int
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
