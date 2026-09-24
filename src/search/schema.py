from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def omit_empty(value: Any) -> bool:
    return value is None or value == ""


class SearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SearchResult(SearchSchema):
    id: str
    type: str
    title: str
    key: str = Field(default="", exclude_if=omit_empty)
    description: str = Field(default="", exclude_if=omit_empty)
    status: str = Field(default="", exclude_if=omit_empty)
    priority: str = Field(default="", exclude_if=omit_empty)
    avatar_url: str = Field(default="", exclude_if=omit_empty)
    project_id: str | None = Field(default=None, exclude_if=omit_empty)
    project_name: str = Field(default="", exclude_if=omit_empty)
    project_slug: str = Field(default="", exclude_if=omit_empty)


class GlobalSearchResponse(SearchSchema):
    tasks: list[SearchResult] = Field(default_factory=list)
    user_stories: list[SearchResult] = Field(default_factory=list)
    projects: list[SearchResult] = Field(default_factory=list)
    members: list[SearchResult] = Field(default_factory=list)
    sprints: list[SearchResult] = Field(default_factory=list)


class SearchSuccessResponse(SearchSchema):
    success: bool
    status_code: int
    message: str
    data: GlobalSearchResponse


class ErrorDetail(SearchSchema):
    code: str
    status_code: int
    message: str


class ErrorResponse(SearchSchema):
    success: bool
    error: ErrorDetail
