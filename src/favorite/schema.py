from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_serializer

from src.task.schema import (
    TaskResponse,
    TaskSchema,
    UserSummary,
    omit_empty,
    serialize_go_time,
)

FavoriteType = Literal["user_story", "task"]

FAVORITE_TYPES = ["user_story", "task"]

FAVORITE_SORT_FIELDS = ("created_at", "title", "name")


class AddFavoriteRequest(TaskSchema):
    item_id: str
    item_type: FavoriteType


class UserStoryResponse(TaskSchema):
    id: str
    project_id: str
    project_name: str = ""
    sprint_id: str | None = Field(default=None, exclude_if=omit_empty)
    sprint_name: str = ""
    sequence_number: int = 0
    serial_number: int = 0
    formatted_serial_number: str = ""
    title: str
    description: str = ""
    priority: str
    status_id: str
    status: str = ""
    status_color: str = ""
    is_closed: bool = False
    is_favourite: bool = True
    assignee_id: str | None = Field(default=None, exclude_if=omit_empty)
    reporter_id: str | None = Field(default=None, exclude_if=omit_empty)
    reporter_name: str = ""
    assignee_name: str = ""
    story_points: int = 0
    backlog_order: int = 0
    created_at: datetime
    updated_at: datetime
    total_tasks: int = 0
    completed_tasks: int = 0
    progress: float = 0.0
    tasks: list[TaskResponse] = Field(default_factory=list)
    reporter: UserSummary | None = Field(default=None, exclude_if=omit_empty)
    assignee: UserSummary | None = Field(default=None, exclude_if=omit_empty)

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
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
    user_story: UserStoryResponse | None = Field(default=None, exclude_if=omit_empty)
    task: TaskResponse | None = Field(default=None, exclude_if=omit_empty)
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return serialize_go_time(value) or ""


class FavoriteListResponse(TaskSchema):
    favorites: list[FavoriteResponse]
    total: int
    total_tasks: int
    total_user_stories: int


class RemoveFavoriteResponse(TaskSchema):
    id: str


class SuccessResponse(TaskSchema):
    success: bool
    status_code: int
    message: str
    data: Any | None = Field(default=None, exclude_if=lambda value: value is None)
    meta: Any | None = Field(default=None, exclude_if=lambda value: value is None)


class ErrorDetail(TaskSchema):
    code: str
    status_code: int
    message: str


class ErrorResponse(TaskSchema):
    success: bool
    error: ErrorDetail


class FavoriteListSuccessResponse(SuccessResponse):
    data: FavoriteListResponse


class FavoriteSuccessResponse(SuccessResponse):
    data: FavoriteResponse


class RemoveFavoriteSuccessResponse(SuccessResponse):
    data: RemoveFavoriteResponse
