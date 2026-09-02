from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from src.task.schema import TaskResponse
from src.user_story.schema import UserStoryResponse


def omit_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def serialize_go_time(value: datetime | None) -> str | None:
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


class WorkItemResponse(BaseModel):
    work_item_type: str  # "task" or "user_story"
    id: str
    project_id: str
    serial_number: int
    formatted_serial_number: str = Field(default="", exclude_if=omit_empty)
    title: str
    description: str = Field(default="", exclude_if=omit_empty)
    priority: str
    status_id: str
    status: str
    status_color: str = Field(default="", exclude_if=omit_empty)
    is_favourite: bool = False
    story_points: int = 0
    sprint_id: Optional[str] = Field(default=None, exclude_if=omit_empty)
    sprint_name: str = Field(default="", exclude_if=omit_empty)
    assignee_id: Optional[str] = Field(default=None, exclude_if=omit_empty)
    assignee_name: str = Field(default="", exclude_if=omit_empty)
    reporter_id: Optional[str] = Field(default=None, exclude_if=omit_empty)
    reporter_name: str = Field(default="", exclude_if=omit_empty)
    created_at: datetime
    updated_at: datetime
    task_details: Optional[TaskResponse] = Field(default=None, exclude_if=omit_empty)
    user_story_details: Optional[UserStoryResponse] = Field(default=None, exclude_if=omit_empty)

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        populate_by_name=True,
    )

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return serialize_go_time(value)
