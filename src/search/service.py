from __future__ import annotations

import re
import time

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.auth.models import User
from src.config import get_logger
from src.label.models import Label as _Label  # noqa: F401
from src.organization.models import Role
from src.project.models import Project, ProjectKeyHistory, ProjectMember
from src.search.schema import (
    CategoryPaginationMap,
    CategoryPaginationMeta,
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
    VALID_CATEGORIES = {
        "all",
        "tasks",
        "user_stories",
        "projects",
        "members",
        "sprints",
    }

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
        category: str = "all",
    ) -> tuple[bool, set[str]]:
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

        is_org_admin = (user.role.name if user.role else "").strip().lower() == "org_admin"
        if is_org_admin:
            if category == "members":
                return True, set()
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
            return True, project_ids

        memberships = list(
            (
                await self.db.execute(
                    select(ProjectMember)
                    .join(Project, Project.id == ProjectMember.project_id)
                    .where(
                        ProjectMember.user_id == user_id,
                        Project.organization_id == organization_id,
                        Project.deleted_at.is_(None),
                        ProjectMember.deleted_at.is_(None),
                    )
                    .options(
                        joinedload(ProjectMember.role).selectinload(Role.permissions)
                    )
                )
            ).scalars()
        )
        accessible_ids = {
            str(member.project_id)
            for member in memberships
            if self._role_can_view_projects(member.role)
        }
        return False, accessible_ids

    @classmethod
    def _build_empty_result(
        cls, category: str, page: int, page_size: int
    ) -> GlobalSearchResult:
        empty_cat_meta = CategoryPaginationMeta(
            page=page, page_size=page_size, has_next=False, total=0
        )
        if category == "all":
            empty_map = CategoryPaginationMap(
                tasks=CategoryPaginationMeta(page=page, page_size=page_size, has_next=False, total=0),
                user_stories=CategoryPaginationMeta(page=page, page_size=page_size, has_next=False, total=0),
                projects=CategoryPaginationMeta(page=page, page_size=page_size, has_next=False, total=0),
                members=CategoryPaginationMeta(page=page, page_size=page_size, has_next=False, total=0),
                sprints=CategoryPaginationMeta(page=page, page_size=page_size, has_next=False, total=0),
            )
            data = GlobalSearchData(
                tasks=[],
                user_stories=[],
                projects=[],
                members=[],
                sprints=[],
            )
            meta = SearchMeta(
                total=0,
                page=page,
                page_size=page_size,
                has_next=False,
                total_tasks=0,
                total_user_stories=0,
                total_projects=0,
                total_members=0,
                total_sprints=0,
                pagination=empty_map,
            )
        elif category == "tasks":
            data = GlobalSearchData(tasks=[])
            meta = SearchMeta(
                total=0,
                page=page,
                page_size=page_size,
                has_next=False,
                total_tasks=0,
                pagination=CategoryPaginationMap(tasks=empty_cat_meta),
            )
        elif category == "user_stories":
            data = GlobalSearchData(user_stories=[])
            meta = SearchMeta(
                total=0,
                page=page,
                page_size=page_size,
                has_next=False,
                total_user_stories=0,
                pagination=CategoryPaginationMap(user_stories=empty_cat_meta),
            )
        elif category == "projects":
            data = GlobalSearchData(projects=[])
            meta = SearchMeta(
                total=0,
                page=page,
                page_size=page_size,
                has_next=False,
                total_projects=0,
                pagination=CategoryPaginationMap(projects=empty_cat_meta),
            )
        elif category == "members":
            data = GlobalSearchData(members=[])
            meta = SearchMeta(
                total=0,
                page=page,
                page_size=page_size,
                has_next=False,
                total_members=0,
                pagination=CategoryPaginationMap(members=empty_cat_meta),
            )
        elif category == "sprints":
            data = GlobalSearchData(sprints=[])
            meta = SearchMeta(
                total=0,
                page=page,
                page_size=page_size,
                has_next=False,
                total_sprints=0,
                pagination=CategoryPaginationMap(sprints=empty_cat_meta),
            )
        else:
            raise SearchServiceError(400, "BAD_REQUEST", f"Invalid category '{category}'")

        return GlobalSearchResult(data=data, meta=meta)

    async def global_search(
        self,
        user_id: str,
        organization_id: str,
        query: str,
        category: str = "all",
        page: int = 1,
        page_size: int = 10,
        request_id: str | None = None,
    ) -> GlobalSearchResult:
        category = (category or "all").strip().lower()
        if category not in self.VALID_CATEGORIES:
            raise SearchServiceError(
                400,
                "BAD_REQUEST",
                f"Invalid category '{category}'. Allowed categories: {', '.join(sorted(self.VALID_CATEGORIES))}",
            )

        if page < 1:
            raise SearchServiceError(
                400, "BAD_REQUEST", "Page number must be greater than or equal to 1"
            )
        if page_size < 1 or page_size > 100:
            raise SearchServiceError(
                400, "BAD_REQUEST", "Page size must be between 1 and 100"
            )

        start_time = time.perf_counter()
        query = query.strip()

        logger.info(
            "Global search started: category=%s, page=%d, page_size=%d, user_id=%s, org_id=%s, query_len=%d, req_id=%s",
            category,
            page,
            page_size,
            user_id,
            organization_id,
            len(query),
            request_id or "-",
        )

        if not query:
            return self._build_empty_result(category, page, page_size)

        try:
            is_org_admin, project_ids = await self._accessible_project_ids(
                user_id, organization_id, category=category
            )

            t_items, t_total = [], 0
            s_items, s_total = [], 0
            p_items, p_total = [], 0
            m_items, m_total = [], 0
            sp_items, sp_total = [], 0

            if category in ("all", "tasks"):
                t_items, t_total = await self._search_tasks(
                    project_ids, query, page=page, page_size=page_size
                )

            if category in ("all", "user_stories"):
                s_items, s_total = await self._search_user_stories(
                    project_ids, query, page=page, page_size=page_size
                )

            if category in ("all", "projects"):
                p_items, p_total = await self._search_projects(
                    project_ids, query, page=page, page_size=page_size
                )

            if category in ("all", "members"):
                m_items, m_total = await self._search_members(
                    organization_id,
                    user_id,
                    is_org_admin,
                    project_ids,
                    query,
                    page=page,
                    page_size=page_size,
                )

            if category in ("all", "sprints"):
                sp_items, sp_total = await self._search_sprints(
                    project_ids, query, page=page, page_size=page_size
                )

        except SearchServiceError:
            raise
        except SQLAlchemyError as exc:
            await self.db.rollback()
            logger.error(
                "Database error in global search (category=%s, query_len=%d): %s",
                category,
                len(query),
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
                "Unexpected error in global search (category=%s, query_len=%d): %s",
                category,
                len(query),
                exc,
                exc_info=True,
            )
            raise

        if category == "all":
            t_has_next = (page * page_size) < t_total
            s_has_next = (page * page_size) < s_total
            p_has_next = (page * page_size) < p_total
            m_has_next = (page * page_size) < m_total
            sp_has_next = (page * page_size) < sp_total

            overall_total = t_total + s_total + p_total + m_total + sp_total
            overall_has_next = t_has_next or s_has_next or p_has_next or m_has_next or sp_has_next

            p_map = CategoryPaginationMap(
                tasks=CategoryPaginationMeta(page=page, page_size=page_size, has_next=t_has_next, total=t_total),
                user_stories=CategoryPaginationMeta(page=page, page_size=page_size, has_next=s_has_next, total=s_total),
                projects=CategoryPaginationMeta(page=page, page_size=page_size, has_next=p_has_next, total=p_total),
                members=CategoryPaginationMeta(page=page, page_size=page_size, has_next=m_has_next, total=m_total),
                sprints=CategoryPaginationMeta(page=page, page_size=page_size, has_next=sp_has_next, total=sp_total),
            )

            meta = SearchMeta(
                total=overall_total,
                page=page,
                page_size=page_size,
                has_next=overall_has_next,
                total_tasks=t_total,
                total_user_stories=s_total,
                total_projects=p_total,
                total_members=m_total,
                total_sprints=sp_total,
                pagination=p_map,
            )
            data = GlobalSearchData(
                tasks=t_items,
                user_stories=s_items,
                projects=p_items,
                members=m_items,
                sprints=sp_items,
            )
        elif category == "tasks":
            t_has_next = (page * page_size) < t_total
            meta = SearchMeta(
                total=t_total,
                page=page,
                page_size=page_size,
                has_next=t_has_next,
                total_tasks=t_total,
                pagination=CategoryPaginationMap(
                    tasks=CategoryPaginationMeta(page=page, page_size=page_size, has_next=t_has_next, total=t_total)
                ),
            )
            data = GlobalSearchData(tasks=t_items)
        elif category == "user_stories":
            s_has_next = (page * page_size) < s_total
            meta = SearchMeta(
                total=s_total,
                page=page,
                page_size=page_size,
                has_next=s_has_next,
                total_user_stories=s_total,
                pagination=CategoryPaginationMap(
                    user_stories=CategoryPaginationMeta(page=page, page_size=page_size, has_next=s_has_next, total=s_total)
                ),
            )
            data = GlobalSearchData(user_stories=s_items)
        elif category == "projects":
            p_has_next = (page * page_size) < p_total
            meta = SearchMeta(
                total=p_total,
                page=page,
                page_size=page_size,
                has_next=p_has_next,
                total_projects=p_total,
                pagination=CategoryPaginationMap(
                    projects=CategoryPaginationMeta(page=page, page_size=page_size, has_next=p_has_next, total=p_total)
                ),
            )
            data = GlobalSearchData(projects=p_items)
        elif category == "members":
            m_has_next = (page * page_size) < m_total
            meta = SearchMeta(
                total=m_total,
                page=page,
                page_size=page_size,
                has_next=m_has_next,
                total_members=m_total,
                pagination=CategoryPaginationMap(
                    members=CategoryPaginationMeta(page=page, page_size=page_size, has_next=m_has_next, total=m_total)
                ),
            )
            data = GlobalSearchData(members=m_items)
        elif category == "sprints":
            sp_has_next = (page * page_size) < sp_total
            meta = SearchMeta(
                total=sp_total,
                page=page,
                page_size=page_size,
                has_next=sp_has_next,
                total_sprints=sp_total,
                pagination=CategoryPaginationMap(
                    sprints=CategoryPaginationMeta(page=page, page_size=page_size, has_next=sp_has_next, total=sp_total)
                ),
            )
            data = GlobalSearchData(sprints=sp_items)
        else:
            raise SearchServiceError(400, "BAD_REQUEST", f"Invalid category '{category}'")

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        if duration_ms > 1000.0:
            logger.warning(
                "Slow global search completed: category=%s, duration=%.2fms, total=%d, req_id=%s",
                category,
                duration_ms,
                meta.total,
                request_id or "-",
            )
        else:
            logger.info(
                "Global search completed: category=%s, duration=%.2fms, total=%d, req_id=%s",
                category,
                duration_ms,
                meta.total,
                request_id or "-",
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
        total_stmt = (
            select(func.count(Task.id))
            .where(
                Task.project_id.in_(project_ids),
                Task.deleted_at.is_(None),
                predicate,
            )
        )
        total = int((await self.db.execute(total_stmt)).scalar() or 0)
        offset = max(0, (page - 1) * page_size)
        if total == 0 or offset >= total:
            return [], total

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
                    .order_by(Task.created_at.desc(), Task.id.desc())
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
            total,
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
        total_stmt = (
            select(func.count(UserStory.id))
            .where(
                UserStory.project_id.in_(project_ids),
                UserStory.deleted_at.is_(None),
                predicate,
            )
        )
        total = int((await self.db.execute(total_stmt)).scalar() or 0)
        offset = max(0, (page - 1) * page_size)
        if total == 0 or offset >= total:
            return [], total

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
                    .order_by(UserStory.created_at.desc(), UserStory.id.desc())
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
            total,
        )

    async def _search_projects(
        self, project_ids: set[str], query: str, page: int = 1, page_size: int = 10
    ) -> tuple[list[SearchResult], int]:
        if not project_ids:
            return [], 0
        predicate = self._search_predicate(
            query, Project.name, self._clean_rich_text(Project.description), Project.slug
        )
        total_stmt = (
            select(func.count(Project.id))
            .where(
                Project.id.in_(project_ids),
                Project.deleted_at.is_(None),
                predicate,
            )
        )
        total = int((await self.db.execute(total_stmt)).scalar() or 0)
        offset = max(0, (page - 1) * page_size)
        if total == 0 or offset >= total:
            return [], total

        projects = list(
            (
                await self.db.execute(
                    select(Project)
                    .where(
                        Project.id.in_(project_ids),
                        Project.deleted_at.is_(None),
                        predicate,
                    )
                    .order_by(Project.created_at.desc(), Project.id.desc())
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
            total,
        )

    async def _search_members(
        self,
        organization_id: str,
        user_id: str,
        is_org_admin: bool,
        project_ids: set[str],
        query: str,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[MemberSearchResult], int]:
        predicate = self._search_predicate(
            query,
            User.full_name,
            User.username,
            User.email,
            Role.name,
        )
        base_conditions = [
            User.organization_id == organization_id,
            User.deleted_at.is_(None),
            predicate,
        ]

        if not is_org_admin:
            allowed_criteria = [
                User.id == user_id,
                func.lower(func.coalesce(Role.name, "")) == "org_admin",
            ]
            if project_ids:
                allowed_criteria.append(
                    User.id.in_(
                        select(ProjectMember.user_id).where(
                            ProjectMember.project_id.in_(project_ids),
                            ProjectMember.deleted_at.is_(None),
                        )
                    )
                )
            base_conditions.append(or_(*allowed_criteria))

        total_stmt = (
            select(func.count(User.id))
            .outerjoin(Role, User.role_id == Role.id)
            .where(*base_conditions)
        )
        total = int((await self.db.execute(total_stmt)).scalar() or 0)
        offset = max(0, (page - 1) * page_size)
        if total == 0 or offset >= total:
            return [], total

        users = list(
            (
                await self.db.execute(
                    select(User)
                    .outerjoin(Role, User.role_id == Role.id)
                    .where(*base_conditions)
                    .options(joinedload(User.role))
                    .order_by(User.created_at.desc(), User.id.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            )
            .scalars()
            .unique()
        )
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
        return results, total

    async def _search_sprints(
        self, project_ids: set[str], query: str, page: int = 1, page_size: int = 10
    ) -> tuple[list[SearchResult], int]:
        if not project_ids:
            return [], 0
        predicate = self._search_predicate(
            query, Sprint.name, self._clean_rich_text(Sprint.goal)
        )
        total_stmt = (
            select(func.count(Sprint.id))
            .where(
                Sprint.project_id.in_(project_ids),
                Sprint.deleted_at.is_(None),
                predicate,
            )
        )
        total = int((await self.db.execute(total_stmt)).scalar() or 0)
        offset = max(0, (page - 1) * page_size)
        if total == 0 or offset >= total:
            return [], total

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
                    .order_by(Sprint.created_at.desc(), Sprint.id.desc())
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
            total,
        )
