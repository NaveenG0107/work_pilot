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


class CategoryPaginationMeta(SearchSchema):
    page: int
    page_size: int
    has_next: bool
    total: int


class CategoryPaginationMap(SearchSchema):
    tasks: CategoryPaginationMeta | None = Field(default=None, exclude_if=omit_empty)
    user_stories: CategoryPaginationMeta | None = Field(default=None, exclude_if=omit_empty)
    projects: CategoryPaginationMeta | None = Field(default=None, exclude_if=omit_empty)
    members: CategoryPaginationMeta | None = Field(default=None, exclude_if=omit_empty)
    sprints: CategoryPaginationMeta | None = Field(default=None, exclude_if=omit_empty)


class SearchMeta(SearchSchema):
    total: int = 0
    page: int = 1
    page_size: int = 10
    has_next: bool = False
    total_tasks: int | None = Field(default=None, exclude_if=omit_empty)
    total_user_stories: int | None = Field(default=None, exclude_if=omit_empty)
    total_projects: int | None = Field(default=None, exclude_if=omit_empty)
    total_members: int | None = Field(default=None, exclude_if=omit_empty)
    total_sprints: int | None = Field(default=None, exclude_if=omit_empty)
    pagination: CategoryPaginationMap = Field(default_factory=CategoryPaginationMap)


SearchPaginationMeta = SearchMeta


class GlobalSearchData(SearchSchema):
    tasks: list[SearchResult] | None = Field(default=None, exclude_if=omit_empty)
    user_stories: list[SearchResult] | None = Field(default=None, exclude_if=omit_empty)
    projects: list[SearchResult] | None = Field(default=None, exclude_if=omit_empty)
    members: list[MemberSearchResult] | None = Field(default=None, exclude_if=omit_empty)
    sprints: list[SearchResult] | None = Field(default=None, exclude_if=omit_empty)


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
