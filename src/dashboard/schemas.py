from datetime import date
from typing import List, Optional, Union
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


class SprintBurndownPoint(BaseModel):
    day: int = Field(..., description="Day number starting from 1")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    ideal_hours: float = Field(..., description="Ideal remaining hours")
    actual_hours: float = Field(..., description="Actual remaining hours")


class SprintBurndownData(BaseModel):
    sprint_id: UUID = Field(..., description="Sprint UUID")
    sprint_name: str = Field(..., description="Sprint name")
    data: List[SprintBurndownPoint] = Field(default_factory=list, description="Daily burndown points")


class DashboardSprintBurndownResponse(BaseModel):
    sprint_burndown: Union[SprintBurndownData, List[SprintBurndownData], None] = Field(
        ..., description="Sprint burndown data object or list of active sprints"
    )


class WeeklyProgress(BaseModel):
    day: str = Field(..., description="Day abbreviation (e.g. Mon, Tue)")
    planned: int = Field(default=0, description="Number of planned tasks")
    completed: int = Field(default=0, description="Number of completed tasks")


class TeamWorkload(BaseModel):
    user_id: UUID = Field(..., description="User UUID")
    user_name: str = Field(..., description="Username")
    full_name: str = Field(default="", description="User full name")
    avatar_url: str = Field(default="", description="User avatar URL")
    color: str = Field(default="", description="User profile hex color")
    task_count: int = Field(default=0, description="Total assigned tasks count")
    points: float = Field(default=0.0, description="Total story points assigned")


class DashboardResponse(BaseModel):
    overview: DashboardOverview = Field(..., description="Task overview counts")
    task_status: dict = Field(..., description="Task status counts by custom status")
    sprint_burndown: Union[SprintBurndownData, List[SprintBurndownData], None] = Field(
        default=None, description="Sprint burndown data"
    )
    team_workload: List[TeamWorkload] = Field(default_factory=list, description="Team workload data")


