from __future__ import annotations

import re

from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.models import User
from src.label.models import Label as _Label  # noqa: F401
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.search.schema import GlobalSearchResponse, SearchResult
from src.sprint.models import Sprint
from src.task.models import Task
from src.user_story.models import UserStory
from src.user_story_status.models import (
    UserStoryStatus as _UserStoryStatus,  # noqa: F401
)


class SearchServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class SearchService:
    _PROJECT_VIEW_ROLES = {
        "org_admin",
        "project_manager",
        "developer",
        "member",
        "user",
        "qa",
        "tester",
        "stakeholder",
    }

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _search_predicate(query: str, *columns):
        raw_match = or_(*(column.ilike(f"%{query}%") for column in columns))
        words = [
            word
            for word in (re.sub(r"[^a-zA-Z0-9]+", "", item) for item in query.split())
            if word
        ]
        if not words:
            return raw_match
        word_match = and_(
            *(or_(*(column.ilike(f"%{word}%") for column in columns)) for word in words)
        )
        return or_(raw_match, word_match)

    @classmethod
    def _role_can_view_projects(cls, role: Role | None) -> bool:
        if role is None:
            return False
        if any(
            permission.resource == "projects" and permission.action == "view"
            for permission in role.permissions
        ):
            return True
        return (role.name or "").strip().lower() in cls._PROJECT_VIEW_ROLES

    async def _accessible_project_ids(
        self,
        user_id: str,
        organization_id: str,
    ) -> set[str]:
        user = (
            await self.db.execute(
                select(User)
                .where(
                    User.id == user_id,
                    User.organization_id == organization_id,
                    User.deleted_at.is_(None),
                )
                .options(selectinload(User.role).selectinload(Role.permissions))
            )
        ).scalar_one_or_none()
        if user is None:
            raise SearchServiceError(401, "UNAUTHORIZED", "User not found")

        project_ids = set(
            (
                await self.db.execute(
                    select(Project.id).where(
                        Project.organization_id == organization_id,
                        Project.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        if (user.role.name if user.role else "").strip().lower() == "org_admin":
            return project_ids

        memberships = list(
            (
                await self.db.execute(
                    select(ProjectMember)
                    .where(
                        ProjectMember.user_id == user_id,
                        ProjectMember.project_id.in_(project_ids),
                        ProjectMember.deleted_at.is_(None),
                    )
                    .options(
                        selectinload(ProjectMember.role).selectinload(Role.permissions)
                    )
                )
            ).scalars()
        )
        return {
            str(member.project_id)
            for member in memberships
            if self._role_can_view_projects(member.role)
        }

    async def global_search(
        self,
        user_id: str,
        organization_id: str,
        query: str,
    ) -> GlobalSearchResponse:
        query = query.strip()
        if not query:
            return GlobalSearchResponse()

        try:
            project_ids = await self._accessible_project_ids(
                user_id, organization_id
            )
            tasks = await self._search_tasks(project_ids, query)
            stories = await self._search_user_stories(project_ids, query)
            projects = await self._search_projects(project_ids, query)
            members = await self._search_members(organization_id, query)
            sprints = await self._search_sprints(project_ids, query)
        except SearchServiceError:
            raise
        except SQLAlchemyError as exc:
            raise SearchServiceError(
                500,
                "INTERNAL_SERVER_ERROR",
                "Failed to execute search queries",
            ) from exc

        return GlobalSearchResponse(
            tasks=tasks,
            user_stories=stories,
            projects=projects,
            members=members,
            sprints=sprints,
        )

    async def _search_tasks(
        self, project_ids: set[str], query: str
    ) -> list[SearchResult]:
        if not project_ids:
            return []
        predicate = or_(
            self._search_predicate(query, Task.title, Task.description, Task.key),
            cast(Task.serial_number, String) == query,
        )
        tasks = list(
            (
                await self.db.execute(
                    select(Task)
                    .where(
                        Task.project_id.in_(project_ids),
                        Task.deleted_at.is_(None),
                        predicate,
                    )
                    .options(selectinload(Task.project))
                    .limit(20)
                )
            ).scalars()
        )
        return [
            SearchResult(
                id=str(task.id),
                type="task",
                title=task.title,
                key=task.key or "",
                description=task.description or "",
                status=task.status or "",
                priority=task.priority or "",
                project_id=str(task.project_id),
                project_name=task.project.name if task.project else "",
                project_slug=task.project.slug if task.project else "",
            )
            for task in tasks
        ]

    async def _search_user_stories(
        self, project_ids: set[str], query: str
    ) -> list[SearchResult]:
        if not project_ids:
            return []
        predicate = or_(
            self._search_predicate(query, UserStory.title, UserStory.description),
            cast(UserStory.serial_number, String) == query,
        )
        stories = list(
            (
                await self.db.execute(
                    select(UserStory)
                    .where(
                        UserStory.project_id.in_(project_ids),
                        UserStory.deleted_at.is_(None),
                        predicate,
                    )
                    .options(
                        selectinload(UserStory.project),
                        selectinload(UserStory.status),
                    )
                    .limit(20)
                )
            ).scalars()
        )
        return [
            SearchResult(
                id=str(story.id),
                type="user_story",
                title=story.title,
                key=getattr(story, "key", "") or story.formatted_serial_number,
                description=story.description or "",
                status=story.status.name if story.status else "",
                priority=story.priority or "",
                project_id=str(story.project_id),
                project_name=story.project.name if story.project else "",
                project_slug=story.project.slug if story.project else "",
            )
            for story in stories
        ]

    async def _search_projects(
        self, project_ids: set[str], query: str
    ) -> list[SearchResult]:
        if not project_ids:
            return []
        projects = list(
            (
                await self.db.execute(
                    select(Project)
                    .where(
                        Project.id.in_(project_ids),
                        Project.deleted_at.is_(None),
                        self._search_predicate(
                            query, Project.name, Project.description, Project.slug
                        ),
                    )
                    .limit(20)
                )
            ).scalars()
        )
        return [
            SearchResult(
                id=str(project.id),
                type="project",
                title=project.name,
                key=project.slug or "",
                description=project.description or "",
                status=project.status or "",
                project_id=str(project.id),
                project_name=project.name,
                project_slug=project.slug or "",
            )
            for project in projects
        ]

    async def _search_members(
        self, organization_id: str, query: str
    ) -> list[SearchResult]:
        users = list(
            (
                await self.db.execute(
                    select(User)
                    .where(
                        User.organization_id == organization_id,
                        User.deleted_at.is_(None),
                        self._search_predicate(
                            query, User.full_name, User.username, User.email
                        ),
                    )
                    .limit(20)
                )
            ).scalars()
        )
        return [
            SearchResult(
                id=str(user.id),
                type="member",
                title=user.full_name,
                key=user.username,
                description=user.email,
                avatar_url=user.avatar_url or "",
            )
            for user in users
        ]

    async def _search_sprints(
        self, project_ids: set[str], query: str
    ) -> list[SearchResult]:
        if not project_ids:
            return []
        sprints = list(
            (
                await self.db.execute(
                    select(Sprint)
                    .where(
                        Sprint.project_id.in_(project_ids),
                        Sprint.deleted_at.is_(None),
                        self._search_predicate(query, Sprint.name, Sprint.goal),
                    )
                    .options(selectinload(Sprint.project))
                    .limit(20)
                )
            ).scalars()
        )
        return [
            SearchResult(
                id=str(sprint.id),
                type="sprint",
                title=sprint.name,
                description=sprint.goal or "",
                status=sprint.status or "",
                project_id=str(sprint.project_id),
                project_name=sprint.project.name if sprint.project else "",
                project_slug=sprint.project.slug if sprint.project else "",
            )
            for sprint in sprints
        ]
