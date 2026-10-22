from __future__ import annotations

import re

from sqlalchemy import String, and_, cast, distinct, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.auth.models import User
from src.config import get_logger
from src.label.models import Label as _Label  # noqa: F401
from src.organization.models import Role
from src.project.models import Project, ProjectKeyHistory, ProjectMember
from src.search.schema import (
    GlobalSearchData,
    GlobalSearchResponse,
    GlobalSearchResult,
    MemberSearchResult,
    SearchMeta,
    SearchResult,
)
from src.sprint.models import Sprint
from src.task.models import Task
from src.user_story.models import UserStory
from src.user_story_status.models import (
    UserStoryStatus as _UserStoryStatus,  # noqa: F401
)

logger = get_logger(__name__)


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
    def _clean_rich_text(column):
        return func.regexp_replace(
            func.regexp_replace(func.coalesce(column, ""), "<[^>]+>", " ", "g"),
            r"&[a-zA-Z0-9#]+;",
            " ",
            "g",
        )

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
                .options(joinedload(User.role).selectinload(Role.permissions))
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
                        joinedload(ProjectMember.role).selectinload(Role.permissions)
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
        page: int = 1,
        page_size: int = 10,
    ) -> GlobalSearchResult:
        query = query.strip()
        if not query:
            logger.debug("Empty query passed to global_search; returning empty result")
            return GlobalSearchResult(
                data=GlobalSearchData(),
                meta=SearchMeta(
                    page=page,
                    page_size=page_size,
                    has_next=False,
                    total=0,
                    total_tasks=0,
                    total_projects=0,
                    total_members=0,
                    total_sprints=0,
                    total_user_stories=0,
                ),
            )

        logger.info(
            "Executing global search for query=%r (page=%d, page_size=%d, user_id=%s, organization_id=%s)",
            query,
            page,
            page_size,
            user_id,
            organization_id,
        )

        try:
            project_ids = await self._accessible_project_ids(
                user_id, organization_id
            )
            tasks, tasks_total = await self._search_tasks(project_ids, query, page=page, page_size=page_size)
            stories, stories_total = await self._search_user_stories(project_ids, query, page=page, page_size=page_size)
            projects, projects_total = await self._search_projects(project_ids, query, page=page, page_size=page_size)
            members, members_total = await self._search_members(organization_id, query, page=page, page_size=page_size)
            sprints, sprints_total = await self._search_sprints(project_ids, query, page=page, page_size=page_size)
        except SearchServiceError as exc:
            logger.warning(
                "Search service error for query=%r: [%s] %s",
                query,
                exc.code,
                exc.message,
            )
            raise
        except SQLAlchemyError as exc:
            logger.error(
                "Database error executing search query=%r: %s",
                query,
                exc,
                exc_info=True,
            )
            raise SearchServiceError(
                500,
                "INTERNAL_SERVER_ERROR",
                "Failed to execute search queries",
            ) from exc
        except Exception as exc:
            logger.error(
                "Unexpected error during global search query=%r: %s",
                query,
                exc,
                exc_info=True,
            )
            raise

        overall_total = (
            tasks_total
            + stories_total
            + projects_total
            + members_total
            + sprints_total
        )
        has_next = (page * page_size) < overall_total
        meta = SearchMeta(
            page=page,
            page_size=page_size,
            has_next=has_next,
            total=overall_total,
            total_tasks=tasks_total,
            total_projects=projects_total,
            total_members=members_total,
            total_sprints=sprints_total,
            total_user_stories=stories_total,
        )

        logger.info(
            "Global search completed for query=%r: total=%d (tasks=%d, stories=%d, projects=%d, members=%d, sprints=%d)",
            query,
            overall_total,
            tasks_total,
            stories_total,
            projects_total,
            members_total,
            sprints_total,
        )

        data = GlobalSearchData(
            tasks=tasks,
            user_stories=stories,
            projects=projects,
            members=members,
            sprints=sprints,
        )
        return GlobalSearchResult(data=data, meta=meta)

    async def _search_tasks(
        self, project_ids: set[str], query: str, page: int = 1, page_size: int = 10
    ) -> tuple[list[SearchResult], int]:
        if not project_ids:
            return [], 0
        predicates = [
            self._search_predicate(
                query, Task.title, self._clean_rich_text(Task.description), Task.key
            ),
            cast(Task.serial_number, String) == query,
        ]
        m = re.match(r"^([A-Za-z0-9]+)-(\d+)$", query.strip())
        if m:
            s_prefix = m.group(1).upper()
            s_seq = int(m.group(2))
            matching_pids = list(
                (
                    await self.db.execute(
                        select(Project.id).where(
                            Project.id.in_(project_ids),
                            or_(
                                Project.key == s_prefix,
                                Project.id.in_(
                                    select(ProjectKeyHistory.project_id).where(
                                        ProjectKeyHistory.project_id.in_(project_ids),
                                        ProjectKeyHistory.key == s_prefix,
                                    )
                                ),
                            ),
                        )
                    )
                ).scalars()
            )
            if matching_pids:
                predicates.append(
                    and_(Task.project_id.in_(matching_pids), Task.sequence_number == s_seq)
                )
        predicate = or_(*predicates)
        offset = max(0, (page - 1) * page_size)
        total_stmt = (
            select(func.count(distinct(Task.id)))
            .where(
                Task.project_id.in_(project_ids),
                Task.deleted_at.is_(None),
                predicate,
            )
        )
        total = (await self.db.execute(total_stmt)).scalar() or 0
        tasks = list(
            (
                await self.db.execute(
                    select(Task)
                    .where(
                        Task.project_id.in_(project_ids),
                        Task.deleted_at.is_(None),
                        predicate,
                    )
                    .options(joinedload(Task.project))
                    .offset(offset)
                    .limit(page_size)
                )
            ).scalars()
        )
        return (
            [
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
            ],
            int(total),
        )

    async def _search_user_stories(
        self, project_ids: set[str], query: str, page: int = 1, page_size: int = 10
    ) -> tuple[list[SearchResult], int]:
        if not project_ids:
            return [], 0
        predicate = or_(
            self._search_predicate(
                query, UserStory.title, self._clean_rich_text(UserStory.description)
            ),
            cast(UserStory.serial_number, String) == query,
        )
        offset = max(0, (page - 1) * page_size)
        total_stmt = (
            select(func.count(distinct(UserStory.id)))
            .where(
                UserStory.project_id.in_(project_ids),
                UserStory.deleted_at.is_(None),
                predicate,
            )
        )
        total = (await self.db.execute(total_stmt)).scalar() or 0
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
                        joinedload(UserStory.project),
                        joinedload(UserStory.status),
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            ).scalars()
        )
        return (
            [
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
            ],
            int(total),
        )

    async def _search_projects(
        self, project_ids: set[str], query: str, page: int = 1, page_size: int = 10
    ) -> tuple[list[SearchResult], int]:
        if not project_ids:
            return [], 0
        predicate = self._search_predicate(
            query, Project.name, self._clean_rich_text(Project.description), Project.slug
        )
        offset = max(0, (page - 1) * page_size)
        total_stmt = (
            select(func.count(distinct(Project.id)))
            .where(
                Project.id.in_(project_ids),
                Project.deleted_at.is_(None),
                predicate,
            )
        )
        total = (await self.db.execute(total_stmt)).scalar() or 0
        projects = list(
            (
                await self.db.execute(
                    select(Project)
                    .where(
                        Project.id.in_(project_ids),
                        Project.deleted_at.is_(None),
                        predicate,
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            ).scalars()
        )
        return (
            [
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
            ],
            int(total),
        )

    async def _search_members(
        self, organization_id: str, query: str, page: int = 1, page_size: int = 10
    ) -> tuple[list[MemberSearchResult], int]:
        logger.debug(
            "Searching members in organization_id=%s with query=%r",
            organization_id,
            query,
        )
        predicate = self._search_predicate(
            query,
            User.full_name,
            User.username,
            User.email,
            Role.name,
        )
        offset = max(0, (page - 1) * page_size)
        total_stmt = (
            select(func.count(distinct(User.id)))
            .outerjoin(Role, User.role_id == Role.id)
            .where(
                User.organization_id == organization_id,
                User.deleted_at.is_(None),
                predicate,
            )
        )
        total = (await self.db.execute(total_stmt)).scalar() or 0
        users = list(
            (
                await self.db.execute(
                    select(User)
                    .outerjoin(Role, User.role_id == Role.id)
                    .where(
                        User.organization_id == organization_id,
                        User.deleted_at.is_(None),
                        predicate,
                    )
                    .options(joinedload(User.role))
                    .offset(offset)
                    .limit(page_size)
                )
            )
            .scalars()
            .unique()
        )
        logger.debug("Found %d members for query=%r (total: %d)", len(users), query, total)
        results: list[MemberSearchResult] = []
        for user in users:
            role_name = (
                user.role.name
                if user.role and hasattr(user.role, "name")
                else (str(user.role) if user.role else "")
            )
            results.append(
                MemberSearchResult(
                    id=str(user.id),
                    name=user.full_name or "",
                    user_name=user.username or "",
                    email=user.email or "",
                    role=role_name,
                    avatar_url=user.avatar_url or "",
                    color=user.color or "",
                )
            )
        return results, int(total)

    async def _search_sprints(
        self, project_ids: set[str], query: str, page: int = 1, page_size: int = 10
    ) -> tuple[list[SearchResult], int]:
        if not project_ids:
            return [], 0
        predicate = self._search_predicate(
            query, Sprint.name, self._clean_rich_text(Sprint.goal)
        )
        offset = max(0, (page - 1) * page_size)
        total_stmt = (
            select(func.count(distinct(Sprint.id)))
            .where(
                Sprint.project_id.in_(project_ids),
                Sprint.deleted_at.is_(None),
                predicate,
            )
        )
        total = (await self.db.execute(total_stmt)).scalar() or 0
        sprints = list(
            (
                await self.db.execute(
                    select(Sprint)
                    .where(
                        Sprint.project_id.in_(project_ids),
                        Sprint.deleted_at.is_(None),
                        predicate,
                    )
                    .options(joinedload(Sprint.project))
                    .offset(offset)
                    .limit(page_size)
                )
            ).scalars()
        )
        return (
            [
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
            ],
            int(total),
        )
