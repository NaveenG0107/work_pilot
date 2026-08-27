from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaginationResponse(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class UserSummary(BaseModel):
    id: str
    full_name: str | None = Field(default=None, serialization_alias="name")
    email: str | None = None
    avatar_url: str | None = None
    color: str | None = None
    role: str | None = None

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

    project_id: str | None = None
    project_name: str | None = None

    organization_id: str | None = None

    user: UserSummary | None = None

    action: str
    resource_type: str
    resource_id: str | None = None

    details: str | None = None
    created_at: datetime

    task_key: str | None = None
    task_id: str | None = None

    user_story_id: str | None = None

    title: str | None = None

    task_name: str | None = None
    user_story_name: str | None = None
    sprint_name: str | None = None

    type: str | None = None


class AuditLogResponseWrapper(BaseModel):
    user: UserSummary | None = None
    activities: list[AuditLogResponse]


class AuditSuccessResponse(BaseModel):
    success: bool
    status_code: int
    message: str
    data: AuditLogResponseWrapper
    meta: PaginationResponse
