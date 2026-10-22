from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def omit_empty(value: Any) -> bool:
    return value is None or value == ""


class SearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SearchResult(SearchSchema):
    id: str
    type: str = Field(default="", exclude_if=omit_empty)
    title: str = Field(default="", exclude_if=omit_empty)
    key: str = Field(default="", exclude_if=omit_empty)
    description: str = Field(default="", exclude_if=omit_empty)
    status: str = Field(default="", exclude_if=omit_empty)
    priority: str = Field(default="", exclude_if=omit_empty)
    avatar_url: str = Field(default="", exclude_if=omit_empty)
    project_id: str | None = Field(default=None, exclude_if=omit_empty)
    project_name: str = Field(default="", exclude_if=omit_empty)
    project_slug: str = Field(default="", exclude_if=omit_empty)
    name: str = Field(default="", exclude_if=omit_empty)
    user_name: str = Field(default="", exclude_if=omit_empty)
    email: str = Field(default="", exclude_if=omit_empty)
    role: str = Field(default="", exclude_if=omit_empty)
    color: str = Field(default="", exclude_if=omit_empty)


class MemberSearchResult(SearchSchema):
    id: str
    name: str = ""
    user_name: str = ""
    email: str = ""
    role: str = ""
    avatar_url: str = Field(default="", exclude_if=omit_empty)
    color: str = ""


class SearchMeta(SearchSchema):
    page: int = 1
    page_size: int = 10
    has_next: bool = False
    total: int = 0
    total_tasks: int = 0
    total_projects: int = 0
    total_members: int = 0
    total_sprints: int = 0
    total_user_stories: int = 0


SearchPaginationMeta = SearchMeta


class GlobalSearchData(SearchSchema):
    tasks: list[SearchResult] = Field(default_factory=list)
    user_stories: list[SearchResult] = Field(default_factory=list)
    projects: list[SearchResult] = Field(default_factory=list)
    members: list[MemberSearchResult | SearchResult] = Field(default_factory=list)
    sprints: list[SearchResult] = Field(default_factory=list)


GlobalSearchResponse = GlobalSearchData


class GlobalSearchResult(SearchSchema):
    data: GlobalSearchData = Field(default_factory=GlobalSearchData)
    meta: SearchMeta = Field(default_factory=SearchMeta)


class SearchSuccessResponse(SearchSchema):
    success: bool
    status_code: int
    message: str
    data: GlobalSearchData
    meta: SearchMeta = Field(default_factory=SearchMeta)


class ErrorDetail(SearchSchema):
    code: str
    status_code: int
    message: str


class ErrorResponse(SearchSchema):
    success: bool
    error: ErrorDetail
