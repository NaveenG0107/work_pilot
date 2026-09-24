from typing import Optional, Literal

from pydantic import BaseModel, Field


class CreateSprint(BaseModel):
    name: str = Field(..., min_length=2, max_length=1000)
    goal: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class CreateSprintRequest(BaseModel):
    sprints: list[CreateSprint] = Field(..., min_length=1)


class StartSprintRequest(BaseModel):
    project_id: Optional[str] = None

    start_date: str
    end_date: str


class UpdateSprintRequest(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )
    goal: str | None = Field(
        default=None,
        max_length=500,
    )
    start_date: str | None = None
    end_date: str | None = None
    status: Literal[
        "planned",
        "active",
        "on_hold",
        "completed",
        "cancelled",
        "archived",
    ] | None = None
