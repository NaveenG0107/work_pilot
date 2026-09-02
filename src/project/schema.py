from datetime import date, datetime, time, timezone
import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

omit_empty = lambda value: value is None or value == "" or value == []

PROJECT_STATUSES = [
    "planning", "active", "on_hold", "completed", "cancelled", "archived"
]

ResponseData = TypeVar("ResponseData")


def validate_uuid(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid request payload.") from exc


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    description: str = ""


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=150)
    slug: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = None
    status: str | None = Field(
        default=None,
        json_schema_extra={"enum": PROJECT_STATUSES},
    )


class ProjectMemberRequest(BaseModel):
    user_id: str
    role_id: str | None = None
    project_role: str | None = None

    @field_validator("user_id", "role_id")
    @classmethod
    def validate_ids(cls, value: str | None) -> str | None:
        return validate_uuid(value)


class CreateProjectMemberRequest(BaseModel):
    project_id: str
    members: list[ProjectMemberRequest] = Field(min_length=1)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return validate_uuid(value)  # type: ignore[return-value]


class UpdateProjectMemberRequest(BaseModel):
    role_id: str | None = None
    project_role: str | None = None

    @field_validator("role_id")
    @classmethod
    def validate_role_id(cls, value: str | None) -> str | None:
        return validate_uuid(value)


class PaginationResponse(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class SuccessResponse(BaseModel, Generic[ResponseData]):
    success: bool
    status_code: int
    message: str
    data: ResponseData


class SuccessWithoutDataResponse(BaseModel):
    success: bool
    status_code: int
    message: str


class PaginatedSuccessResponse(SuccessResponse[ResponseData], Generic[ResponseData]):
    meta: PaginationResponse


class ProjectIDResponse(BaseModel):
    project_id: str


class LegacyProjectIDResponse(BaseModel):
    ProjectID: str


class ErrorDetail(BaseModel):
    code: str
    status_code: int
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail


class SprintResponse(BaseModel):
    id: str
    name: str
    goal: str | None = Field(default=None, exclude_if=omit_empty)
    status: str = ""
    start_date: datetime | None = None
    end_date: datetime | None = None
    tasks: list[Any] = Field(default_factory=list, exclude_if=omit_empty)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def date_as_go_time(cls, value: date | datetime | None) -> datetime | None:
        if isinstance(value, datetime) or value is None:
            return value
        return datetime.combine(value, time.min, tzinfo=timezone.utc)

    @field_validator("status", mode="before")
    @classmethod
    def nullable_status_as_go_string(cls, value: str | None) -> str:
        return value or ""


class ProjectSummary(BaseModel):
    id: str
    organization_id: str
    organization_name: str | None = Field(default=None, exclude_if=omit_empty)
    name: str
    key: str | None = Field(default=None, exclude_if=omit_empty)
    project_key: str | None = Field(default=None, exclude_if=omit_empty)
    description: str | None = Field(default=None, exclude_if=omit_empty)
    status: str
    created_by: str
    created_at: datetime
    sprint_count: int = 0
    total_tasks: int = 0
    total_members: int = 0
    slug: str | None = Field(default=None, exclude_if=omit_empty)
    sprints: list[SprintResponse] | None = Field(default=None, exclude_if=omit_empty)

    model_config = ConfigDict(from_attributes=True)


class ProjectListResponse(BaseModel):
    key: str | None = Field(default=None, exclude_if=omit_empty)
    organization_name: str | None = Field(default=None, exclude_if=omit_empty)
    project_id: str
    project_key: str | None = Field(default=None, exclude_if=omit_empty)
    project_name: str
    project_slug: str | None = Field(default=None, exclude_if=omit_empty)
    role: str | None = Field(default=None, exclude_if=omit_empty)
    status: str


class ProjectMemberResponse(BaseModel):
    user_id: str
    username: str
    full_name: str
    role: str
    avatar_url: str | None
    color: str = Field(default="", exclude_if=omit_empty)
    organization_name: str | None = Field(default=None, exclude_if=omit_empty)
    project_key: str | None = Field(default=None, exclude_if=omit_empty)


class ProjectMetrics(BaseModel):
    total_tasks: int = 0
    completed_tasks: int = 0
    pending_tasks: int = 0
    overdue_tasks: int = 0
    completed_tasks_percentage: int = 0
    total_sprints: int = 0
    active_sprints: int = 0
    completed_sprints: int = 0
    total_members: int = 0


class ProjectDetail(BaseModel):
    id: str
    organization_id: str
    organization_name: str | None = Field(default=None, exclude_if=omit_empty)
    name: str
    key: str | None = Field(default=None, exclude_if=omit_empty)
    project_key: str | None = Field(default=None, exclude_if=omit_empty)
    description: str | None = Field(default=None, exclude_if=omit_empty)
    status: str
    created_by: str
    creator: str
    created_at: datetime
    members: list[ProjectMemberResponse] = Field(default_factory=list)
    sprints: list[SprintResponse] = Field(default_factory=list)
    active_sprint: SprintResponse | None = Field(default=None, exclude_if=omit_empty)
    metrics: ProjectMetrics
    slug: str | None = Field(default=None, exclude_if=omit_empty)


class UserProject(BaseModel):
    project_id: str
    role: str
    project_name: str
    status: str
    organization_name: str | None = Field(default=None, exclude_if=omit_empty)
    project_key: str | None = Field(default=None, exclude_if=omit_empty)
    key: str | None = Field(default=None, exclude_if=omit_empty)
    project_slug: str | None = Field(default=None, exclude_if=omit_empty)


class UserProjectsResponse(BaseModel):
    user_id: str
    user_name: str
    full_name: str
    email: str
    avatar_url: str | None
    color: str
    role: str | None = Field(default=None, exclude_if=omit_empty)
    project: list[UserProject]


class UserProjectRoleResponse(BaseModel):
    project_id: str
    project_name: str
    project_key: str | None = Field(default=None, exclude_if=omit_empty)
    role_id: str
    role: str


class ProjectActivityUser(BaseModel):
    id: str
    name: str
    email: str
    avatar_url: str | None
    color: str
    role: str | None = Field(default=None, exclude_if=omit_empty)


class ProjectActivityResponse(BaseModel):
    id: str
    project_id: str | None = Field(default=None, exclude_if=omit_empty)
    project_name: str | None = Field(default=None, exclude_if=omit_empty)
    organization_id: str | None = Field(default=None, exclude_if=omit_empty)
    user: ProjectActivityUser | None = Field(default=None, exclude_if=omit_empty)
    action: str
    resource_type: str
    resource_id: str | None = Field(default=None, exclude_if=omit_empty)
    details: str | None = Field(default=None, exclude_if=omit_empty)
    timestamp: str
    task_key: str | None = Field(default=None, exclude_if=omit_empty)
    title: str | None = Field(default=None, exclude_if=omit_empty)
    task_name: str | None = Field(default=None, exclude_if=omit_empty)
    user_story_name: str | None = Field(default=None, exclude_if=omit_empty)
    sprint_name: str | None = Field(default=None, exclude_if=omit_empty)
