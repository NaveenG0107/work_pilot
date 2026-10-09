from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.auth.models import User
from src.config import get_logger
from src.custom_status.models import CustomStatus
from src.favorite.models import Favorite
from src.project.models import Project, ProjectMember
from src.organization.models import Organization, Role
from src.sprint.models import Sprint
from src.task.models import (
    DEFAULT_STATUS_COLORS,
    DEFAULT_STATUS_IS_FINAL,
    Task,
    normalize_task_status,
    task_labels,
)
from src.task.schema import LabelResponse, TaskResponse, UserSummary
from src.user_story.models import UserStory
from src.board.schemas import (
    BoardPagination,
    BoardResponse,
    BoardStatusGroup,
    BoardStatusInfo,
    BoardStoryDetail,
    BoardStoryDetailData,
    BoardStorySummary,
)

logger = get_logger(__name__)

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "org_admin": {
        "projects:view", "projects:add", "projects:modify", "projects:delete",
        "sprints:view", "sprints:add", "sprints:modify", "sprints:delete",
        "user_stories:view", "user_stories:add", "user_stories:modify",
        "user_stories:delete", "tasks:view", "tasks:add", "tasks:modify",
        "tasks:delete", "comments:view", "comments:add", "comments:modify",
        "comments:delete", "attachments:view", "attachments:add",
        "attachments:delete", "custom_statuses:view", "custom_statuses:modify",
    },
    "project_manager": {
        "projects:view", "projects:modify", "sprints:view", "sprints:add",
        "sprints:modify", "sprints:delete", "user_stories:view",
        "user_stories:add", "user_stories:modify", "user_stories:delete",
        "tasks:view", "tasks:add", "tasks:modify", "tasks:delete",
        "comments:view", "comments:add", "comments:modify", "comments:delete",
        "attachments:view", "attachments:add", "attachments:delete",
        "custom_statuses:view", "custom_statuses:modify",
    },
    "developer": {
        "projects:view", "sprints:view", "user_stories:view", "user_stories:add",
        "user_stories:modify", "tasks:view", "tasks:add", "tasks:modify",
        "tasks:delete", "comments:view", "comments:add", "comments:modify",
        "comments:delete", "attachments:view", "attachments:add",
        "attachments:delete", "custom_statuses:view",
    },
    "qa": {
        "projects:view", "sprints:view", "user_stories:view",
        "user_stories:modify", "tasks:view", "tasks:add", "tasks:modify",
        "comments:view", "comments:add", "attachments:view", "attachments:add",
        "custom_statuses:view",
    },
    "stakeholder": {
        "projects:view", "sprints:view", "user_stories:view", "tasks:view",
        "comments:view", "comments:add", "attachments:view", "custom_statuses:view",
    },
}


def _normalize_role_name(name: str | None) -> str:
    aliases = {
        "member": "developer",
        "user": "developer",
        "tester": "qa",
        "viewer": "stakeholder",
    }
    normalized = (name or "").lower()
    return aliases.get(normalized, normalized)


def _has_default_permission(role: str | None, resource: str, action: str) -> bool:
    return f"{resource}:{action}" in DEFAULT_ROLE_PERMISSIONS.get(_normalize_role_name(role), set())


class BoardServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def user_summary_from_model(user: User | None) -> UserSummary | None:
    if user is None:
        return None
    role_obj = user.__dict__.get("role")
    role_name = getattr(role_obj, "name", None) if role_obj else None
    return UserSummary(
        id=str(user.id),
        full_name=user.full_name or "",
        email=user.email or "",
        avatar_url=user.avatar_url or None,
        color=user.color or "",
        role=role_name,
    )


def serialize_board_task(task: Task, is_favourite: bool = False) -> TaskResponse:
    now = datetime.now(timezone.utc)
    status_rel = task.status_rel
    status_color = status_rel.color if status_rel else (DEFAULT_STATUS_COLORS.get(task.status or "", "#808080"))
    is_final = status_rel.is_final if status_rel else (DEFAULT_STATUS_IS_FINAL.get(task.status or "", False))

    reporter = user_summary_from_model(task.reporter)
    assignee = user_summary_from_model(task.assignee)

    labels = [
        LabelResponse(id=str(label.id), name=label.name, color=label.color)
        for label in getattr(task, "labels", []) or []
    ]

    return TaskResponse(
        id=str(task.id),
        project_id=str(task.project_id),
        project_name=task.project.name if getattr(task, "project", None) else "",
        sprint_id=str(task.sprint_id) if task.sprint_id else None,
        sprint_name=task.sprint.name if getattr(task, "sprint", None) else "",
        user_story_id=str(task.user_story_id) if task.user_story_id else None,
        user_story_title=task.user_story.title if getattr(task, "user_story", None) else "",
        key=task.key,
        serial_number=int(task.serial_number or 0),
        formatted_serial_number=task.formatted_serial_number,
        title=task.title,
        description=task.description or "",
        type=task.type,
        priority=task.priority,
        status_id=str(task.status_id),
        status=task.status or (status_rel.name if status_rel else ""),
        status_color=status_color,
        is_final=is_final,
        is_favourite=is_favourite,
        assignee_id=str(task.assignee_id) if task.assignee_id else None,
        reporter_id=str(task.reporter_id) if task.reporter_id else None,
        reporter_name=reporter.full_name if reporter else "",
        assignee_name=assignee.full_name if assignee else "",
        story_points=int(task.story_points or 0),
        due_date=task.due_date,
        estimated_hours=float(task.estimated_hours) if task.estimated_hours is not None else None,
        actual_hours=float(task.actual_hours) if task.actual_hours is not None else None,
        created_at=task.created_at or now,
        updated_at=task.updated_at or now,
        labels=labels,
        reporter=reporter,
        assignee=assignee,
    )


class BoardService:
    def __init__(self, db: AsyncSession, redis=None):
        self.db = db
        self.redis = redis

    async def _user(self, user_id: str) -> User:
        user = (
            await self.db.execute(
                select(User)
                .options(joinedload(User.role))
                .where(User.id == user_id, User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if not user:
            logger.warning("User not found: user_id=%s", user_id)
            raise BoardServiceError(404, "RESOURCE_NOT_FOUND", "User not found")
        return user

    async def _project(self, project_id: str) -> Project:
        project = (
            await self.db.execute(
                select(Project).where(
                    Project.id == project_id,
                    Project.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not project:
            logger.warning("Project not found: project_id=%s", project_id)
            raise BoardServiceError(404, "RESOURCE_NOT_FOUND", "Project not found")
        return project

    def _role_allows(self, role: Role | None, resource: str, action: str) -> bool:
        if role is None:
            return False
        if _has_default_permission(role.name, resource, action):
            return True
        for permission in getattr(role, "permissions", []) or []:
            if permission.resource == resource and permission.action == action:
                return True
        return False

    async def _has_permission(
        self,
        project: Project,
        user: User,
        resource: str,
        action: str,
    ) -> bool:
        user_role = _normalize_role_name(getattr(user.role, "name", None))
        if user_role == "org_admin" and str(user.organization_id) == str(project.organization_id):
            return self._role_allows(user.role, resource, action)

        cache_attr = f"_pm_{project.id}"
        member = getattr(user, cache_attr, None)
        if member is None:
            member = (
                await self.db.execute(
                    select(ProjectMember)
                    .where(
                        ProjectMember.project_id == str(project.id),
                        ProjectMember.user_id == str(user.id),
                        ProjectMember.deleted_at.is_(None),
                    )
                    .options(joinedload(ProjectMember.role))
                )
            ).scalars().first()
            setattr(user, cache_attr, member if member is not None else False)
        elif member is False:
            member = None

        if member is not None and self._role_allows(member.role, resource, action):
            return True

        return False

    async def check_authorization(
        self,
        project_id: str,
        user_id: str,
        organization_id: str,
    ) -> tuple[Project, User]:
        project = await self._project(project_id)
        user = await self._user(user_id)

        # Organization isolation
        if str(project.organization_id) != str(organization_id):
            logger.warning(
                "Cross-organization project access attempted: user_org=%s, project_org=%s",
                organization_id,
                project.organization_id,
            )
            raise BoardServiceError(403, "FORBIDDEN", "Access denied to this project")

        user_role = _normalize_role_name(getattr(user.role, "name", None))
        if user_role == "super_admin":
            raise BoardServiceError(
                403,
                "FORBIDDEN",
                "Super admins are not allowed to perform organization-level activities",
            )

        # Fast-path for organization admins within their own organization
        if user_role == "org_admin" and str(user.organization_id) == str(project.organization_id):
            return project, user

        has_task_perm = await self._has_permission(project, user, "tasks", "view")
        has_story_perm = await self._has_permission(project, user, "user_stories", "view")
        if not (has_task_perm or has_story_perm):
            logger.warning("User lacks permission to view board in project: user_id=%s, project_id=%s", user_id, project_id)
            raise BoardServiceError(403, "FORBIDDEN", "Access denied to this project")

        return project, user

    async def _statuses(self, project_id: str) -> list[CustomStatus]:
        statuses = list(
            (
                await self.db.execute(
                    select(CustomStatus)
                    .where(
                        CustomStatus.project_id == project_id,
                        CustomStatus.deleted_at.is_(None),
                    )
                    .order_by(CustomStatus.display_order.asc(), CustomStatus.created_at.asc())
                )
            ).scalars()
        )
        if not statuses:
            # Seed defaults if none exist
            defaults = [
                ("Todo", "todo", DEFAULT_STATUS_COLORS["todo"], DEFAULT_STATUS_IS_FINAL["todo"]),
                ("In Progress", "in_progress", DEFAULT_STATUS_COLORS["in_progress"], DEFAULT_STATUS_IS_FINAL["in_progress"]),
                ("In Review", "in_review", DEFAULT_STATUS_COLORS["in_review"], DEFAULT_STATUS_IS_FINAL["in_review"]),
                ("Testing", "testing", DEFAULT_STATUS_COLORS["testing"], DEFAULT_STATUS_IS_FINAL["testing"]),
                ("Completed", "completed", DEFAULT_STATUS_COLORS["completed"], DEFAULT_STATUS_IS_FINAL["completed"]),
                ("Blocked", "blocked", DEFAULT_STATUS_COLORS["blocked"], DEFAULT_STATUS_IS_FINAL["blocked"]),
            ]
            for index, (label, key, color, is_final) in enumerate(defaults):
                self.db.add(
                    CustomStatus(
                        project_id=project_id,
                        name=label,
                        color=color,
                        display_order=index,
                        is_default=(index == 0),
                        is_final=is_final,
                    )
                )
            await self.db.flush()
            statuses = list(
                (
                    await self.db.execute(
                        select(CustomStatus)
                        .where(
                            CustomStatus.project_id == project_id,
                            CustomStatus.deleted_at.is_(None),
                        )
                        .order_by(CustomStatus.display_order.asc(), CustomStatus.created_at.asc())
                    )
                ).scalars()
            )
        return statuses

    def _build_task_filters(
        self,
        *,
        task_assignee_id: str | None = None,
        task_status_id: str | None = None,
        priority: str | None = None,
        work_type: str | None = None,
        label_id: str | None = None,
    ) -> list[Any]:
        conditions = []
        if task_assignee_id:
            conditions.append(Task.assignee_id == task_assignee_id)
        if task_status_id:
            conditions.append(Task.status_id == task_status_id)
        if priority:
            conditions.append(func.lower(Task.priority) == priority.lower())
        if work_type:
            conditions.append(func.lower(Task.type) == work_type.lower())
        if label_id:
            conditions.append(
                Task.id.in_(
                    select(task_labels.c.task_id).where(task_labels.c.label_id == label_id)
                )
            )
        return conditions

    # -----------------------------------------------------------------------
    # Mode A — Story List
    # -----------------------------------------------------------------------
    async def get_board_stories(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 5,
        sprint_id: str | None = None,
        story_assignee_id: str | None = None,
        task_assignee_id: str | None = None,
        task_status_id: str | None = None,
        priority: str | None = None,
        work_type: str | None = None,
        label_id: str | None = None,
        tasks_per_status: int = 5,
        current_user_id: str | None = None,
        include_storyless: bool = False,
    ) -> BoardResponse:
        conditions = [
            UserStory.project_id == project_id,
            UserStory.deleted_at.is_(None),
        ]
        if sprint_id:
            conditions.append(UserStory.sprint_id == sprint_id)
        if story_assignee_id:
            conditions.append(UserStory.assignee_id == story_assignee_id)

        # 1. Total stories count query
        count_stmt = select(func.count(UserStory.id)).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        # 2. Paginated stories query with eager loaded relationships
        offset = (page - 1) * page_size
        fav_story_join = (
            and_(
                Favorite.user_story_id == UserStory.id,
                Favorite.user_id == current_user_id,
                Favorite.item_type == "user_story",
                Favorite.deleted_at.is_(None),
            )
            if current_user_id
            else None
        )
        stories_stmt = (
            select(UserStory)
            .where(*conditions)
            .options(
                joinedload(UserStory.assignee).joinedload(User.role),
                joinedload(UserStory.reporter).joinedload(User.role),
                joinedload(UserStory.sprint),
                joinedload(UserStory.status),
            )
        )
        if fav_story_join is not None:
            fav_story_priority = case((Favorite.id.is_not(None), 0), else_=1)
            stories_stmt = stories_stmt.outerjoin(Favorite, fav_story_join).order_by(
                fav_story_priority.asc(),
                UserStory.created_at.desc(),
                UserStory.id.desc(),
            )
        else:
            stories_stmt = stories_stmt.order_by(
                UserStory.created_at.desc(),
                UserStory.id.desc(),
            )
        stories_stmt = stories_stmt.limit(page_size).offset(offset)
        stories = (await self.db.execute(stories_stmt)).scalars().unique().all()

        story_summaries: list[BoardStorySummary] = []
        if stories:
            story_ids = [str(s.id) for s in stories]

            # 3. Project statuses
            statuses = await self._statuses(project_id)

            # 4. Total task count per story (unfiltered)
            task_counts_stmt = (
                select(Task.user_story_id, func.count(Task.id))
                .where(
                    Task.project_id == project_id,
                    Task.user_story_id.in_(story_ids),
                    Task.deleted_at.is_(None),
                )
                .group_by(Task.user_story_id)
            )
            task_counts = {str(r[0]): r[1] for r in (await self.db.execute(task_counts_stmt)).all()}

            final_status_ids = [str(st.id) for st in statuses if st.is_final]
            if final_status_ids:
                completed_counts_stmt = (
                    select(Task.user_story_id, func.count(Task.id))
                    .where(
                        Task.project_id == project_id,
                        Task.user_story_id.in_(story_ids),
                        Task.status_id.in_(final_status_ids),
                        Task.deleted_at.is_(None),
                    )
                    .group_by(Task.user_story_id)
                )
                completed_counts = {str(r[0]): r[1] for r in (await self.db.execute(completed_counts_stmt)).all()}
            else:
                completed_counts = {}

            # 5. Build task filters for status columns & preview tasks
            task_filters = self._build_task_filters(
                task_assignee_id=task_assignee_id,
                task_status_id=task_status_id,
                priority=priority,
                work_type=work_type,
                label_id=label_id,
            )

            # 6. Task counts per (user_story_id, status_id) with filters applied
            status_task_counts_stmt = (
                select(Task.user_story_id, Task.status_id, func.count(Task.id))
                .where(
                    Task.project_id == project_id,
                    Task.user_story_id.in_(story_ids),
                    Task.deleted_at.is_(None),
                    *task_filters,
                )
                .group_by(Task.user_story_id, Task.status_id)
            )
            status_counts_rows = (await self.db.execute(status_task_counts_stmt)).all()
            story_status_counts: dict[tuple[str, str], int] = {
                (str(r[0]), str(r[1])): r[2] for r in status_counts_rows
            }

            # 7. Window function query to fetch top tasks_per_status per (user_story_id, status_id)
            fav_task_join = (
                and_(
                    Favorite.task_id == Task.id,
                    Favorite.user_id == current_user_id,
                    Favorite.item_type == "task",
                    Favorite.deleted_at.is_(None),
                )
                if current_user_id
                else None
            )

            if fav_task_join is not None:
                fav_task_priority = case((Favorite.id.is_not(None), 0), else_=1)
                rn_col = func.row_number().over(
                    partition_by=(Task.user_story_id, Task.status_id),
                    order_by=(fav_task_priority.asc(), Task.created_at.desc(), Task.id.desc()),
                ).label("rn")
                ranked_subq = (
                    select(Task.id.label("task_id"), rn_col)
                    .outerjoin(Favorite, fav_task_join)
                    .where(
                        Task.project_id == project_id,
                        Task.user_story_id.in_(story_ids),
                        Task.deleted_at.is_(None),
                        *task_filters,
                    )
                    .subquery()
                )
            else:
                rn_col = func.row_number().over(
                    partition_by=(Task.user_story_id, Task.status_id),
                    order_by=(Task.created_at.desc(), Task.id.desc()),
                ).label("rn")
                ranked_subq = (
                    select(Task.id.label("task_id"), rn_col)
                    .where(
                        Task.project_id == project_id,
                        Task.user_story_id.in_(story_ids),
                        Task.deleted_at.is_(None),
                        *task_filters,
                    )
                    .subquery()
                )

            preview_stmt = (
                select(Task)
                .join(ranked_subq, Task.id == ranked_subq.c.task_id)
                .where(ranked_subq.c.rn <= tasks_per_status)
                .options(
                    joinedload(Task.assignee).joinedload(User.role),
                    joinedload(Task.reporter).joinedload(User.role),
                    joinedload(Task.status_rel),
                    joinedload(Task.project),
                    joinedload(Task.sprint),
                    joinedload(Task.user_story),
                    selectinload(Task.labels),
                )
                .order_by(Task.user_story_id, Task.status_id, ranked_subq.c.rn.asc())
            )
            preview_tasks = (await self.db.execute(preview_stmt)).scalars().unique().all()

            # 8. Batch favorites query for stories and preview tasks
            fav_task_ids: set[str] = set()
            fav_story_ids: set[str] = set()
            if current_user_id:
                fav_conditions = [
                    and_(Favorite.item_type == "user_story", Favorite.user_story_id.in_(story_ids))
                ]
                if preview_tasks:
                    t_ids = [str(t.id) for t in preview_tasks]
                    fav_conditions.append(
                        and_(Favorite.item_type == "task", Favorite.task_id.in_(t_ids))
                    )
                fav_stmt = select(Favorite.item_type, Favorite.task_id, Favorite.user_story_id).where(
                    Favorite.user_id == current_user_id,
                    Favorite.deleted_at.is_(None),
                    or_(*fav_conditions),
                )
                fav_rows = (await self.db.execute(fav_stmt)).all()
                for f_type, f_tid, f_sid in fav_rows:
                    if f_type == "task" and f_tid:
                        fav_task_ids.add(str(f_tid))
                    elif f_type == "user_story" and f_sid:
                        fav_story_ids.add(str(f_sid))

            # Group preview tasks by (user_story_id, status_id)
            grouped_tasks: dict[tuple[str, str], list[TaskResponse]] = {}
            for t in preview_tasks:
                key = (str(t.user_story_id), str(t.status_id))
                if key not in grouped_tasks:
                    grouped_tasks[key] = []
                grouped_tasks[key].append(
                    serialize_board_task(t, is_favourite=(str(t.id) in fav_task_ids))
                )

            for story in stories:
                sid_str = str(story.id)
                # Derive due_date from sprint end_date if available
                due_date = None
                if story.sprint and story.sprint.end_date:
                    due_date = datetime.combine(story.sprint.end_date, datetime.min.time(), tzinfo=timezone.utc)

                status_name = story.status.name if getattr(story, "status", None) else (story.status_text or "")
                status_color = story.status.color if getattr(story, "status", None) and story.status.color else "#4A90E2"

                # Construct status groups for this story
                story_statuses: list[BoardStatusGroup] = []
                for st in statuses:
                    st_id = str(st.id)
                    st_count = story_status_counts.get((sid_str, st_id), 0)
                    st_tasks = grouped_tasks.get((sid_str, st_id), [])
                    story_statuses.append(
                        BoardStatusGroup(
                            status_id=st_id,
                            status_name=st.name,
                            color=st.color or "",
                            display_order=st.display_order,
                            task_count=st_count,
                            tasks=st_tasks,
                            meta=BoardPagination(
                                page=1,
                                page_size=tasks_per_status,
                                total=st_count,
                                has_next=st_count > len(st_tasks),
                            ),
                        )
                    )

                t_count = task_counts.get(sid_str, 0)
                comp_t = completed_counts.get(sid_str, 0)
                prog = round((comp_t / t_count * 100.0), 2) if t_count > 0 else 0.0
                story_summaries.append(
                    BoardStorySummary(
                        id=sid_str,
                        project_id=str(story.project_id),
                        title=story.title,
                        total_tasks=t_count,
                        completed_tasks=comp_t,
                        progress=prog,
                        description=story.description,
                        assignee=user_summary_from_model(story.assignee),
                        reporter=user_summary_from_model(story.reporter),
                        due_date=due_date,
                        key=story.key,
                        serial_number=int(story.serial_number or story.sequence_number or 0),
                        priority=story.priority or "medium",
                        is_favourite=sid_str in fav_story_ids,
                        status_id=str(story.status_id) if story.status_id else None,
                        status=status_name,
                        status_color=status_color,
                        story_points=int(story.story_points or 0),
                        created_at=story.created_at,
                        updated_at=story.updated_at,
                        statuses=story_statuses,
                    )
                )

        if include_storyless:
            storyless_summary = await self.get_board_storyless_summary(
                project_id,
                sprint_id=sprint_id,
                task_assignee_id=task_assignee_id,
                task_status_id=task_status_id,
                priority=priority,
                work_type=work_type,
                label_id=label_id,
                tasks_per_status=tasks_per_status,
                current_user_id=current_user_id,
            )
            if storyless_summary and storyless_summary.total_tasks > 0:
                story_summaries.append(storyless_summary)
                total += 1

        has_next = (page * page_size) < total
        pagination = BoardPagination(
            page=page,
            page_size=page_size,
            total=total,
            has_next=has_next,
        )

        return BoardResponse(
            success=True,
            status_code=200,
            message="Board stories retrieved successfully",
            data=story_summaries,
            meta=pagination.model_dump(),
        )

    # -----------------------------------------------------------------------
    # Mode B — Story Details & Initial Status Expansion
    # -----------------------------------------------------------------------
    async def get_board_story_details(
        self,
        project_id: str,
        user_story_id: str,
        *,
        page: int = 1,
        page_size: int = 5,
        tasks_per_status: int = 5,
        task_assignee_id: str | None = None,
        task_status_id: str | None = None,
        priority: str | None = None,
        work_type: str | None = None,
        label_id: str | None = None,
        current_user_id: str | None = None,
    ) -> BoardResponse:
        # 1. Fetch story with eager-loaded single-value relationships
        if user_story_id == "storyless":
            summary = await self.get_board_storyless_summary(
                project_id,
                task_assignee_id=task_assignee_id,
                task_status_id=task_status_id,
                priority=priority,
                work_type=work_type,
                label_id=label_id,
                tasks_per_status=tasks_per_status,
                current_user_id=current_user_id,
            )
            return BoardResponse(
                success=True,
                status_code=200,
                message="Storyless tasks retrieved successfully",
                data=summary,
            )

        story_stmt = (
            select(UserStory)
            .where(
                UserStory.id == user_story_id,
                UserStory.project_id == project_id,
                UserStory.deleted_at.is_(None),
            )
            .options(
                joinedload(UserStory.assignee).joinedload(User.role),
                joinedload(UserStory.reporter).joinedload(User.role),
                joinedload(UserStory.sprint),
                joinedload(UserStory.status),
            )
        )
        story = (await self.db.execute(story_stmt)).scalar_one_or_none()
        if not story:
            logger.warning("User story not found in project: user_story_id=%s, project_id=%s", user_story_id, project_id)
            raise BoardServiceError(404, "RESOURCE_NOT_FOUND", "User story not found")

        # 2. Fetch all project statuses
        statuses = await self._statuses(project_id)

        # 3. Status task counts query for this story (Single SQL aggregation, Zero N+1)
        task_filters = self._build_task_filters(
            task_assignee_id=task_assignee_id,
            task_status_id=task_status_id,
            priority=priority,
            work_type=work_type,
            label_id=label_id,
        )

        counts_stmt = (
            select(Task.status_id, func.count(Task.id))
            .where(
                Task.project_id == project_id,
                Task.user_story_id == user_story_id,
                Task.deleted_at.is_(None),
                *task_filters,
            )
            .group_by(Task.status_id)
        )
        status_counts = {str(r[0]): r[1] for r in (await self.db.execute(counts_stmt)).all()}

        total_stmt = (
            select(func.count(Task.id))
            .where(
                Task.project_id == project_id,
                Task.user_story_id == user_story_id,
                Task.deleted_at.is_(None),
            )
        )
        original_total = (await self.db.execute(total_stmt)).scalar_one()

        final_status_ids = [str(st.id) for st in statuses if st.is_final]
        if final_status_ids:
            completed_stmt = (
                select(func.count(Task.id))
                .where(
                    Task.project_id == project_id,
                    Task.user_story_id == user_story_id,
                    Task.status_id.in_(final_status_ids),
                    Task.deleted_at.is_(None),
                )
            )
            original_completed = (await self.db.execute(completed_stmt)).scalar_one()
        else:
            original_completed = 0
        story_progress = round((original_completed / original_total * 100.0), 2) if original_total > 0 else 0.0

        # 4. Batch query preview tasks (top 5 per status) using ROW_NUMBER() window function
        fav_task_join = (
            and_(
                Favorite.task_id == Task.id,
                Favorite.user_id == current_user_id,
                Favorite.item_type == "task",
                Favorite.deleted_at.is_(None),
            )
            if current_user_id
            else None
        )

        if fav_task_join is not None:
            fav_task_priority = case((Favorite.id.is_not(None), 0), else_=1)
            rn_col = func.row_number().over(
                partition_by=Task.status_id,
                order_by=(fav_task_priority.asc(), Task.created_at.desc(), Task.id.desc()),
            ).label("rn")
            ranked_subq = (
                select(Task.id.label("task_id"), rn_col)
                .outerjoin(Favorite, fav_task_join)
                .where(
                    Task.project_id == project_id,
                    Task.user_story_id == user_story_id,
                    Task.deleted_at.is_(None),
                    *task_filters,
                )
                .subquery()
            )
        else:
            rn_col = func.row_number().over(
                partition_by=Task.status_id,
                order_by=(Task.created_at.desc(), Task.id.desc()),
            ).label("rn")
            ranked_subq = (
                select(Task.id.label("task_id"), rn_col)
                .where(
                    Task.project_id == project_id,
                    Task.user_story_id == user_story_id,
                    Task.deleted_at.is_(None),
                    *task_filters,
                )
                .subquery()
            )

        preview_stmt = (
            select(Task)
            .join(ranked_subq, Task.id == ranked_subq.c.task_id)
            .where(ranked_subq.c.rn <= tasks_per_status)
            .options(
                joinedload(Task.assignee).joinedload(User.role),
                joinedload(Task.reporter).joinedload(User.role),
                joinedload(Task.status_rel),
                joinedload(Task.project),
                joinedload(Task.sprint),
                joinedload(Task.user_story),
                selectinload(Task.labels),
            )
            .order_by(Task.status_id, ranked_subq.c.rn.asc())
        )
        preview_tasks = (await self.db.execute(preview_stmt)).scalars().unique().all()

        # Consolidated batch favorites query for story and preview tasks (Single round-trip, Zero N+1)
        fav_task_ids: set[str] = set()
        is_fav = False
        if current_user_id:
            fav_conditions = [
                and_(Favorite.item_type == "user_story", Favorite.user_story_id == user_story_id)
            ]
            if preview_tasks:
                t_ids = [str(t.id) for t in preview_tasks]
                fav_conditions.append(
                    and_(Favorite.item_type == "task", Favorite.task_id.in_(t_ids))
                )
            fav_stmt = select(Favorite.item_type, Favorite.task_id, Favorite.user_story_id).where(
                Favorite.user_id == current_user_id,
                Favorite.deleted_at.is_(None),
                or_(*fav_conditions),
            )
            fav_rows = (await self.db.execute(fav_stmt)).all()
            for f_type, f_tid, f_sid in fav_rows:
                if f_type == "task" and f_tid:
                    fav_task_ids.add(str(f_tid))
                elif f_type == "user_story" and str(f_sid) == str(user_story_id):
                    is_fav = True

        # Group tasks by status_id in-memory
        grouped_tasks: dict[str, list[TaskResponse]] = {}
        for t in preview_tasks:
            sid = str(t.status_id)
            if sid not in grouped_tasks:
                grouped_tasks[sid] = []
            grouped_tasks[sid].append(
                serialize_board_task(t, is_favourite=(str(t.id) in fav_task_ids))
            )

        # Build status groups
        status_groups: list[BoardStatusGroup] = []
        for st in statuses:
            st_id = str(st.id)
            count = status_counts.get(st_id, 0)
            status_groups.append(
                BoardStatusGroup(
                    status_id=st_id,
                    status_name=st.name,
                    color=st.color or "",
                    display_order=st.display_order,
                    task_count=count,
                    tasks=grouped_tasks.get(st_id, []),
                    meta=BoardPagination(
                        page=1,
                        page_size=tasks_per_status,
                        total=count,
                        has_next=count > len(grouped_tasks.get(st_id, [])),
                    ),
                )
            )

        due_date = None
        if story.sprint and story.sprint.end_date:
            due_date = datetime.combine(story.sprint.end_date, datetime.min.time(), tzinfo=timezone.utc)

        status_name = story.status.name if getattr(story, "status", None) else (story.status_text or "")
        status_color = story.status.color if getattr(story, "status", None) and story.status.color else "#4A90E2"

        story_detail_data = BoardStoryDetailData(
            id=str(story.id),
            project_id=str(story.project_id),
            title=story.title,
            key=story.key,
            serial_number=int(story.serial_number or story.sequence_number or 0),
            description=story.description,
            priority=story.priority or "medium",
            is_favourite=is_fav,
            story_points=int(story.story_points or 0),
            total_tasks=original_total,
            completed_tasks=original_completed,
            progress=story_progress,
            assignee=user_summary_from_model(story.assignee),
            reporter=user_summary_from_model(story.reporter),
            due_date=due_date,
            status_id=str(story.status_id) if story.status_id else None,
            status=status_name,
            status_color=status_color,
            created_at=story.created_at,
            updated_at=story.updated_at,
            statuses=status_groups,
        )

        pagination = BoardPagination(
            page=1,
            page_size=1,
            total=1,
            has_next=False,
        )

        return BoardResponse(
            success=True,
            status_code=200,
            message="Board story details retrieved successfully",
            data=story_detail_data,
            meta=pagination.model_dump(),
        )

    # -----------------------------------------------------------------------
    # Mode C — Status Task Pagination
    # -----------------------------------------------------------------------
    async def get_board_status_tasks(
        self,
        project_id: str,
        user_story_id: str,
        status_id: str,
        page: int = 1,
        page_size: int = 5,
        *,
        task_assignee_id: str | None = None,
        sprint_id: str | None = None,
        priority: str | None = None,
        work_type: str | None = None,
        label_id: str | None = None,
        current_user_id: str | None = None,
    ) -> BoardResponse:
        # 1. Validate story belongs to project & load details
        if user_story_id == "storyless":
            story = None
        else:
            story_stmt = (
                select(UserStory)
                .where(
                    UserStory.id == user_story_id,
                    UserStory.project_id == project_id,
                    UserStory.deleted_at.is_(None),
                )
                .options(
                    joinedload(UserStory.assignee).joinedload(User.role),
                    joinedload(UserStory.reporter).joinedload(User.role),
                    joinedload(UserStory.sprint),
                    joinedload(UserStory.status),
                )
            )
            story = (await self.db.execute(story_stmt)).scalars().first()
            if not story:
                logger.warning("User story not found for status tasks: user_story_id=%s, project_id=%s", user_story_id, project_id)
                raise BoardServiceError(404, "RESOURCE_NOT_FOUND", "User story not found")

        # 2. Validate status belongs to project
        status_obj = (
            await self.db.execute(
                select(CustomStatus).where(
                    CustomStatus.id == status_id,
                    CustomStatus.project_id == project_id,
                    CustomStatus.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not status_obj:
            logger.warning("Status not found for status tasks: status_id=%s, project_id=%s", status_id, project_id)
            raise BoardServiceError(404, "RESOURCE_NOT_FOUND", "Status not found")

        # 3. Build conditions
        task_filters = self._build_task_filters(
            task_assignee_id=task_assignee_id,
            priority=priority,
            work_type=work_type,
            label_id=label_id,
        )
        conditions = [
            Task.project_id == project_id,
            Task.user_story_id.is_(None) if user_story_id == "storyless" else (Task.user_story_id == user_story_id),
            Task.status_id == status_id,
            Task.deleted_at.is_(None),
            *task_filters,
        ]
        if sprint_id:
            conditions.append(Task.sprint_id == sprint_id)
        elif user_story_id == "storyless":
            conditions.append(Task.sprint_id.isnot(None))

        # 4. Total count query
        count_stmt = select(func.count(Task.id)).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        # 5. Paginated tasks query
        offset = (page - 1) * page_size
        fav_task_join = (
            and_(
                Favorite.task_id == Task.id,
                Favorite.user_id == current_user_id,
                Favorite.item_type == "task",
                Favorite.deleted_at.is_(None),
            )
            if current_user_id
            else None
        )
        tasks_stmt = (
            select(Task)
            .where(*conditions)
            .options(
                joinedload(Task.assignee).joinedload(User.role),
                joinedload(Task.reporter).joinedload(User.role),
                joinedload(Task.status_rel),
                joinedload(Task.project),
                joinedload(Task.sprint),
                joinedload(Task.user_story),
                selectinload(Task.labels),
            )
        )
        if fav_task_join is not None:
            fav_task_priority = case((Favorite.id.is_not(None), 0), else_=1)
            tasks_stmt = tasks_stmt.outerjoin(Favorite, fav_task_join).order_by(
                fav_task_priority.asc(),
                Task.created_at.desc(),
                Task.id.desc(),
            )
        else:
            tasks_stmt = tasks_stmt.order_by(
                Task.created_at.desc(),
                Task.id.desc(),
            )
        tasks_stmt = tasks_stmt.limit(page_size).offset(offset)
        tasks = (await self.db.execute(tasks_stmt)).scalars().unique().all()

        # Consolidated batch favorites query for story and tasks (Single round-trip, Zero N+1)
        fav_task_ids: set[str] = set()
        is_fav = False
        if current_user_id:
            fav_conditions = []
            if user_story_id != "storyless":
                fav_conditions.append(
                    and_(Favorite.item_type == "user_story", Favorite.user_story_id == user_story_id)
                )
            if tasks:
                t_ids = [str(t.id) for t in tasks]
                fav_conditions.append(
                    and_(Favorite.item_type == "task", Favorite.task_id.in_(t_ids))
                )
            if fav_conditions:
                fav_stmt = select(Favorite.item_type, Favorite.task_id, Favorite.user_story_id).where(
                    Favorite.user_id == current_user_id,
                    Favorite.deleted_at.is_(None),
                    or_(*fav_conditions),
                )
                fav_rows = (await self.db.execute(fav_stmt)).all()
                for f_type, f_tid, f_sid in fav_rows:
                    if f_type == "task" and f_tid:
                        fav_task_ids.add(str(f_tid))
                    elif f_type == "user_story" and str(f_sid) == str(user_story_id):
                        is_fav = True

        serialized_tasks = [
            serialize_board_task(t, is_favourite=(str(t.id) in fav_task_ids))
            for t in tasks
        ]

        has_next = (page * page_size) < total
        pagination = BoardPagination(
            page=page,
            page_size=page_size,
            total=total,
            has_next=has_next,
        )
        status_group = BoardStatusGroup(
            status_id=str(status_obj.id),
            status_name=status_obj.name,
            color=status_obj.color or "",
            display_order=status_obj.display_order,
            task_count=total,
            tasks=serialized_tasks,
            meta=pagination,
        )

        statuses = await self._statuses(project_id)
        final_status_ids = [str(st.id) for st in statuses if st.is_final]

        if story is not None:
            story_total_stmt = (
                select(func.count(Task.id))
                .where(
                    Task.project_id == project_id,
                    Task.user_story_id == user_story_id,
                    Task.deleted_at.is_(None),
                )
            )
            story_total = (await self.db.execute(story_total_stmt)).scalar_one()
            if final_status_ids:
                story_comp_stmt = (
                    select(func.count(Task.id))
                    .where(
                        Task.project_id == project_id,
                        Task.user_story_id == user_story_id,
                        Task.status_id.in_(final_status_ids),
                        Task.deleted_at.is_(None),
                    )
                )
                story_completed = (await self.db.execute(story_comp_stmt)).scalar_one()
            else:
                story_completed = 0
            story_prog = round((story_completed / story_total * 100.0), 2) if story_total > 0 else 0.0

            due_date = None
            if story.sprint and story.sprint.end_date:
                due_date = datetime.combine(story.sprint.end_date, datetime.min.time(), tzinfo=timezone.utc)

            status_name = story.status.name if getattr(story, "status", None) else (story.status_text or "")
            status_color = story.status.color if getattr(story, "status", None) and story.status.color else "#4A90E2"

            story_detail_data = BoardStoryDetailData(
                id=str(story.id),
                project_id=str(story.project_id),
                title=story.title,
                key=story.key,
                serial_number=int(story.serial_number or story.sequence_number or 0),
                description=story.description,
                priority=story.priority or "medium",
                is_favourite=is_fav,
                story_points=int(story.story_points or 0),
                total_tasks=story_total,
                completed_tasks=story_completed,
                progress=story_prog,
                assignee=user_summary_from_model(story.assignee),
                reporter=user_summary_from_model(story.reporter),
                due_date=due_date,
                status_id=str(story.status_id) if story.status_id else None,
                status=status_name,
                status_color=status_color,
                created_at=story.created_at,
                updated_at=story.updated_at,
                statuses=[status_group],
            )
        else:
            storyless_base = [
                Task.project_id == project_id,
                Task.user_story_id.is_(None),
                Task.deleted_at.is_(None),
            ]
            if sprint_id:
                storyless_base.append(Task.sprint_id == sprint_id)
            else:
                storyless_base.append(Task.sprint_id.isnot(None))

            story_total = (await self.db.execute(select(func.count(Task.id)).where(*storyless_base))).scalar_one()
            if final_status_ids:
                story_completed = (await self.db.execute(
                    select(func.count(Task.id)).where(*storyless_base, Task.status_id.in_(final_status_ids))
                )).scalar_one()
            else:
                story_completed = 0
            story_prog = round((story_completed / story_total * 100.0), 2) if story_total > 0 else 0.0

            story_detail_data = BoardStoryDetailData(
                id="storyless",
                project_id=str(project_id),
                title="Storyless Tasks",
                key="STORYLESS",
                serial_number=0,
                description="Tasks not linked to any user story",
                priority="medium",
                is_favourite=False,
                story_points=0,
                total_tasks=story_total,
                completed_tasks=story_completed,
                progress=story_prog,
                assignee=None,
                reporter=None,
                due_date=None,
                status_id=None,
                status=None,
                status_color="",
                created_at=None,
                updated_at=None,
                statuses=[status_group],
            )

        return BoardResponse(
            success=True,
            status_code=200,
            message="Status tasks retrieved successfully",
            data=story_detail_data,
            meta=pagination.model_dump(),
        )

    # -----------------------------------------------------------------------
    # Mode D — Story-less Tasks
    # -----------------------------------------------------------------------
    async def get_board_storyless_tasks(
        self,
        project_id: str,
        page: int = 1,
        page_size: int = 5,
        tasks_per_status: int = 5,
        *,
        sprint_id: str | None = None,
        task_assignee_id: str | None = None,
        task_status_id: str | None = None,
        priority: str | None = None,
        work_type: str | None = None,
        label_id: str | None = None,
        current_user_id: str | None = None,
        group_by_status: bool = True,
    ) -> BoardResponse:
        if group_by_status:
            summary = await self.get_board_storyless_summary(
                project_id,
                sprint_id=sprint_id,
                task_assignee_id=task_assignee_id,
                task_status_id=task_status_id,
                priority=priority,
                work_type=work_type,
                label_id=label_id,
                tasks_per_status=tasks_per_status,
                current_user_id=current_user_id,
            )
            return BoardResponse(
                success=True,
                status_code=200,
                message="Storyless tasks retrieved successfully",
                data=summary,
            )

        # Condition: user_story_id IS NULL AND sprint_id IS NOT NULL
        task_filters = self._build_task_filters(
            task_assignee_id=task_assignee_id,
            task_status_id=task_status_id,
            priority=priority,
            work_type=work_type,
            label_id=label_id,
        )
        conditions = [
            Task.project_id == project_id,
            Task.user_story_id.is_(None),
            Task.sprint_id.isnot(None),
            Task.deleted_at.is_(None),
            *task_filters,
        ]
        if sprint_id:
            conditions.append(Task.sprint_id == sprint_id)

        # 1. Total count query
        count_stmt = select(func.count(Task.id)).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        # 2. Paginated tasks query
        offset = (page - 1) * page_size
        fav_task_join = (
            and_(
                Favorite.task_id == Task.id,
                Favorite.user_id == current_user_id,
                Favorite.item_type == "task",
                Favorite.deleted_at.is_(None),
            )
            if current_user_id
            else None
        )
        tasks_stmt = (
            select(Task)
            .where(*conditions)
            .options(
                joinedload(Task.assignee).joinedload(User.role),
                joinedload(Task.reporter).joinedload(User.role),
                joinedload(Task.status_rel),
                joinedload(Task.project),
                joinedload(Task.sprint),
                selectinload(Task.labels),
            )
        )
        if fav_task_join is not None:
            fav_task_priority = case((Favorite.id.is_not(None), 0), else_=1)
            tasks_stmt = tasks_stmt.outerjoin(Favorite, fav_task_join).order_by(
                fav_task_priority.asc(),
                Task.created_at.desc(),
                Task.id.desc(),
            )
        else:
            tasks_stmt = tasks_stmt.order_by(
                Task.created_at.desc(),
                Task.id.desc(),
            )
        tasks_stmt = tasks_stmt.limit(page_size).offset(offset)
        tasks = (await self.db.execute(tasks_stmt)).scalars().unique().all()

        # Batch task favorites query for current user (Zero N+1)
        fav_task_ids: set[str] = set()
        if current_user_id and tasks:
            t_ids = [str(t.id) for t in tasks]
            fav_task_stmt = select(Favorite.task_id).where(
                Favorite.user_id == current_user_id,
                Favorite.item_type == "task",
                Favorite.task_id.in_(t_ids),
                Favorite.deleted_at.is_(None),
            )
            fav_task_rows = (await self.db.execute(fav_task_stmt)).scalars().all()
            fav_task_ids = {str(fid) for fid in fav_task_rows}

        serialized_tasks = [
            serialize_board_task(t, is_favourite=(str(t.id) in fav_task_ids))
            for t in tasks
        ]

        has_next = (page * page_size) < total
        pagination = BoardPagination(
            page=page,
            page_size=page_size,
            total=total,
            has_next=has_next,
        )

        return BoardResponse(
            success=True,
            status_code=200,
            message="Storyless tasks retrieved successfully",
            data=serialized_tasks,
            meta=pagination.model_dump(),
        )

    async def get_board_storyless_summary(
        self,
        project_id: str,
        *,
        sprint_id: str | None = None,
        task_assignee_id: str | None = None,
        task_status_id: str | None = None,
        priority: str | None = None,
        work_type: str | None = None,
        label_id: str | None = None,
        tasks_per_status: int = 5,
        current_user_id: str | None = None,
    ) -> BoardStorySummary:
        # Base conditions: user_story_id IS NULL
        base_conditions = [
            Task.project_id == project_id,
            Task.user_story_id.is_(None),
            Task.deleted_at.is_(None),
        ]
        if sprint_id:
            base_conditions.append(Task.sprint_id == sprint_id)
        else:
            base_conditions.append(Task.sprint_id.isnot(None))

        # Unfiltered total tasks
        total_stmt = select(func.count(Task.id)).where(*base_conditions)
        total_tasks = (await self.db.execute(total_stmt)).scalar_one()

        statuses = await self._statuses(project_id)
        final_status_ids = [str(st.id) for st in statuses if st.is_final]
        if final_status_ids:
            completed_stmt = select(func.count(Task.id)).where(
                *base_conditions,
                Task.status_id.in_(final_status_ids),
            )
            completed_tasks = (await self.db.execute(completed_stmt)).scalar_one()
        else:
            completed_tasks = 0
        progress = (completed_tasks / total_tasks * 100.0) if total_tasks > 0 else 0.0

        # Filtered conditions for status columns
        task_filters = self._build_task_filters(
            task_assignee_id=task_assignee_id,
            task_status_id=task_status_id,
            priority=priority,
            work_type=work_type,
            label_id=label_id,
        )
        filtered_conditions = [*base_conditions, *task_filters]

        # Status task counts
        counts_stmt = (
            select(Task.status_id, func.count(Task.id))
            .where(*filtered_conditions)
            .group_by(Task.status_id)
        )
        status_counts = {str(r[0]): r[1] for r in (await self.db.execute(counts_stmt)).all()}

        # Top tasks_per_status preview per status
        fav_task_join = (
            and_(
                Favorite.task_id == Task.id,
                Favorite.user_id == current_user_id,
                Favorite.item_type == "task",
                Favorite.deleted_at.is_(None),
            )
            if current_user_id
            else None
        )

        if fav_task_join is not None:
            fav_task_priority = case((Favorite.id.is_not(None), 0), else_=1)
            rn_col = func.row_number().over(
                partition_by=Task.status_id,
                order_by=(fav_task_priority.asc(), Task.created_at.desc(), Task.id.desc()),
            ).label("rn")
            ranked_subq = (
                select(Task.id.label("task_id"), rn_col)
                .outerjoin(Favorite, fav_task_join)
                .where(*filtered_conditions)
                .subquery()
            )
        else:
            rn_col = func.row_number().over(
                partition_by=Task.status_id,
                order_by=(Task.created_at.desc(), Task.id.desc()),
            ).label("rn")
            ranked_subq = (
                select(Task.id.label("task_id"), rn_col)
                .where(*filtered_conditions)
                .subquery()
            )

        preview_stmt = (
            select(Task)
            .join(ranked_subq, Task.id == ranked_subq.c.task_id)
            .where(ranked_subq.c.rn <= tasks_per_status)
            .options(
                joinedload(Task.assignee).joinedload(User.role),
                joinedload(Task.reporter).joinedload(User.role),
                joinedload(Task.status_rel),
                joinedload(Task.project),
                joinedload(Task.sprint),
                selectinload(Task.labels),
            )
            .order_by(Task.status_id, ranked_subq.c.rn.asc())
        )
        preview_tasks = (await self.db.execute(preview_stmt)).scalars().unique().all()

        fav_task_ids: set[str] = set()
        if current_user_id and preview_tasks:
            t_ids = [str(t.id) for t in preview_tasks]
            fav_task_stmt = select(Favorite.task_id).where(
                Favorite.user_id == current_user_id,
                Favorite.item_type == "task",
                Favorite.task_id.in_(t_ids),
                Favorite.deleted_at.is_(None),
            )
            fav_task_rows = (await self.db.execute(fav_task_stmt)).scalars().all()
            fav_task_ids = {str(fid) for fid in fav_task_rows}

        grouped_tasks: dict[str, list[TaskResponse]] = {}
        for t in preview_tasks:
            sid = str(t.status_id)
            if sid not in grouped_tasks:
                grouped_tasks[sid] = []
            grouped_tasks[sid].append(
                serialize_board_task(t, is_favourite=(str(t.id) in fav_task_ids))
            )

        storyless_statuses: list[BoardStatusGroup] = []
        for st in statuses:
            st_id = str(st.id)
            st_count = status_counts.get(st_id, 0)
            st_tasks = grouped_tasks.get(st_id, [])
            storyless_statuses.append(
                BoardStatusGroup(
                    status_id=st_id,
                    status_name=st.name,
                    color=st.color or "",
                    display_order=st.display_order,
                    task_count=st_count,
                    tasks=st_tasks,
                    meta=BoardPagination(
                        page=1,
                        page_size=tasks_per_status,
                        total=st_count,
                        has_next=st_count > len(st_tasks),
                    ),
                )
            )

        return BoardStorySummary(
            id="storyless",
            project_id=project_id,
            title="Storyless Tasks",
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            progress=progress,
            description="Tasks not linked to any user story",
            key="STORYLESS",
            serial_number=0,
            priority="medium",
            is_favourite=False,
            statuses=storyless_statuses,
        )
