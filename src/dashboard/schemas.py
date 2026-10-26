from datetime import date, datetime
from typing import Any, List, Optional, Union
from uuid import UUID
from pydantic import BaseModel, Field


class DashboardOverview(BaseModel):
    total_tasks: int = Field(default=0, description="Total number of tasks")
    completed: int = Field(default=0, description="Number of completed tasks")
    pending: int = Field(default=0, description="Number of pending tasks")
    overdue: int = Field(default=0, description="Number of overdue tasks")
    due_soon: int = Field(default=0, description="Number of tasks due in the next 48 hours")


class TaskStatusItem(BaseModel):
    count: int = Field(default=0, description="Task count")
    color: str = Field(default="", description="Status color in hex")
    percentage: float = Field(default=0.0, description="Percentage of tasks with this status")
    # total: int = Field(default=0, description="Total number of tasks")


class SprintBurndownPoint(BaseModel):
    day: int = Field(..., description="Day number starting from 1")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    ideal_hours: float = Field(..., description="Ideal remaining hours")
    actual_hours: Optional[float] = Field(default=None, description="Actual remaining hours")


class SprintBurndownData(BaseModel):
    sprint_id: UUID = Field(..., description="Sprint UUID")
    sprint_name: str = Field(..., description="Sprint name")
    data: List[SprintBurndownPoint] = Field(default_factory=list, description="Daily burndown points")


class DashboardSprintBurndownResponse(BaseModel):
    sprint_burndown: Union[SprintBurndownData, List[SprintBurndownData], None] = Field(
        ..., description="Sprint burndown data object or list of active sprints"
    )


class RollingProgressPoint(BaseModel):
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    day: str = Field(..., description="Day abbreviation (e.g. Sat, Sun, Mon)")
    planned: int = Field(default=0, description="Number of planned tasks")
    completed: int = Field(default=0, description="Number of completed tasks")


WeeklyProgress = RollingProgressPoint


class RollingProgressSummary(BaseModel):
    total_planned: int = Field(default=0, description="Total planned tasks")
    total_completed: int = Field(default=0, description="Total completed tasks")
    remaining: int = Field(default=0, description="Remaining tasks (planned - completed)")
    completion_rate: float = Field(default=0.0, description="Percentage of planned tasks completed")


class RollingProgressData(BaseModel):
    start_date: str = Field(..., description="Start date in YYYY-MM-DD format")
    end_date: str = Field(..., description="End date in YYYY-MM-DD format")
    timezone: str = Field(default="UTC", description="Timezone used for reporting")
    series: List[RollingProgressPoint] = Field(default_factory=list, description="Seven daily data points")
    summary: RollingProgressSummary = Field(..., description="Summary metrics")


class TeamWorkload(BaseModel):
    user_id: UUID = Field(..., description="User UUID")
    user_name: str = Field(..., description="Username")
    full_name: str = Field(default="", description="User full name")
    avatar_url: str = Field(default="", description="User avatar URL")
    color: str = Field(default="", description="User profile hex color")
    task_count: int = Field(default=0, description="Total assigned tasks count")
    points: float = Field(default=0.0, description="Total story points assigned")


class UpcomingDeadline(BaseModel):
    """An open project task due within the dashboard's next-48-hours window."""

    id: UUID
    task_id: UUID | None = None
    project_id: UUID
    project_name: str
    sprint_id: UUID | None = None
    sprint_name: str | None = None
    key: str
    task_key: str | None = None
    title: str
    priority: str
    due_date: datetime
    deadline_status: str = Field(
        description="One of due_today or upcoming"
    )


class DashboardResponse(BaseModel):
    overview: DashboardOverview = Field(..., description="Task overview counts")
    task_status: dict = Field(..., description="Task status counts by custom status")


class PaginationResponse(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class SuccessResponse(BaseModel):
    success: bool
    status_code: int
    message: str
    data: Any | None = Field(default=None, exclude_if=lambda value: value is None)
    meta: Any | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )



