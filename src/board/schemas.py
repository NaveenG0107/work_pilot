from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

from src.task.schema import TaskResponse, UserSummary


class BoardMode(str, Enum):
    STORIES = "stories"
    STORY = "story"
    STORY_STATUS_TASKS = "story_status_tasks"
    STORYLESS_TASKS = "storyless_tasks"


class BoardPagination(BaseModel):
    page: int
    page_size: int
    total: int
    has_next: bool


class BoardStatusGroup(BaseModel):
    status_id: str
    status_name: str
    color: str = ""
    display_order: int = 0
    task_count: int = 0
    tasks: list[TaskResponse] = Field(default_factory=list)
    meta: BoardPagination


class BoardStorySummary(BaseModel):
    id: str
    project_id: str
    title: str
    total_tasks: int = 0
    completed_tasks: int = 0
    progress: float = 0.0
    description: Optional[str] = None
    assignee: Optional[UserSummary] = None
    reporter: Optional[UserSummary] = None
    due_date: Optional[datetime] = None
    key: Optional[str] = None
    serial_number: Optional[int] = None
    priority: str = ""
    is_favourite: bool = False
    status_id: Optional[str] = None
    status: Optional[str] = None
    status_color: str = ""
    story_points: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    statuses: list[BoardStatusGroup] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class BoardStoryDetail(BaseModel):
    id: str
    project_id: str
    title: str
    key: Optional[str] = None
    serial_number: Optional[int] = None
    description: Optional[str] = None
    priority: str = ""
    is_favourite: bool = False
    story_points: int = 0
    total_tasks: int = 0
    completed_tasks: int = 0
    progress: float = 0.0
    assignee: Optional[UserSummary] = None
    reporter: Optional[UserSummary] = None
    due_date: Optional[datetime] = None
    status_id: Optional[str] = None
    status: Optional[str] = None
    status_color: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True)


class BoardStatusInfo(BaseModel):
    id: str
    name: str


class BoardStoryDetailData(BoardStoryDetail):
    statuses: list[BoardStatusGroup] = Field(default_factory=list)
    

class BoardResponse(BaseModel):
    success: bool = True
    status_code: int
    message: str = ""
    data: Any
    meta: Optional[dict[str, Any]] = None
