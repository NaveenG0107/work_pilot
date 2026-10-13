from typing import Optional, Literal

from pydantic import BaseModel, Field


class CreateSprint(BaseModel):
    name: str = Field(..., min_length=2, max_length=1000)
    goal: str = ""


class CreateSprintRequest(BaseModel):
    sprints: list[CreateSprint] = Field(..., min_length=1)


class StartSprintRequest(BaseModel):
    start_date: str = Field(
        ...,
        description="Sprint start UTC datetime in format YYYY-MM-DDTHH:MM:SSZ",
        examples=["YYYY-MM-DDTHH:MM:SSZ"],
    )
    end_date: str = Field(
        ...,
        description="Sprint end UTC datetime in format YYYY-MM-DDTHH:MM:SSZ",
        examples=["YYYY-MM-DDTHH:MM:SSZ"],
    )


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
    end_date: str | None = None
    status: Literal[
        "planned",
        "active",
        "on_hold",
        "completed",
        "cancelled",
        "archived",
    ] | None = None
