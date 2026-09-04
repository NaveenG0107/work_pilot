import asyncio
import html.parser
import math
import re
from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy import String, and_, case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi import HTTPException, UploadFile

from src.audit.models import AuditLog, AuditLogType
from src.auth.models import User
from src.comments.models import Comments, CommentAttachment
from src.custom_status.models import CustomStatus
from src.favorite.models import Favorite
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.sprint.models import Sprint
from src.task.models import (
    DEFAULT_STATUS_COLORS,
    DEFAULT_STATUS_IS_FINAL,
    Task,
    normalize_task_status,
)
from src.config import get_logger
from src.utils.setting import get_settings
from src.utils.storage import (
    StorageConfigurationError,
    build_attachment_key,
    delete_s3_object,
    get_s3_object,
    upload_s3_object,
)
from src.user_story.models import UserStory, UserStoryAttachment
from src.user_story_status.models import UserStoryStatus
from src.user_story.schema import (
    CommentResponse,
    CreateCommentRequest,
    CreateUserStoryRequest,
    FavoriteResponse,
    PaginationResponse,
    RemoveFavoriteResponse,
    ReorderUserStoriesRequest,
    TaskSummary,
    UpdateCommentRequest,
    UpdateUserStoryRequest,
    UpdateUserStoryStatusAssignmentRequest,
    UserStoryAttachmentResponse,
    UserStoryFilter,
    UserStoryResponse,
    user_summary_from_user,
)
from src.utils.core import ErrorCode

logger = get_logger(__name__)


class UserStoryServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


DEFAULT_ROLE_PERMISSIONS = {
    "org_admin": {
        "projects": {"view", "add", "modify", "delete"},
        "sprints": {"view", "add", "modify", "delete"},
        "user_stories": {"view", "add", "modify", "delete"},
        "tasks": {"view", "add", "modify", "delete"},
        "comments": {"view", "add", "modify", "delete"},
        "attachments": {"view", "add", "delete"},
        "custom_statuses": {"view", "modify"},
    },
    "project_manager": {
        "projects": {"view", "modify"},
        "sprints": {"view", "add", "modify", "delete"},
        "user_stories": {"view", "add", "modify", "delete"},
        "tasks": {"view", "add", "modify", "delete"},
        "comments": {"view", "add", "modify", "delete"},
        "attachments": {"view", "add", "delete"},
        "custom_statuses": {"view", "modify"},
    },
    "developer": {
        "projects": {"view"},
        "sprints": {"view"},
        "user_stories": {"view", "add", "modify"},
        "tasks": {"view", "add", "modify", "delete"},
        "comments": {"view", "add", "modify", "delete"},
        "attachments": {"view", "add", "delete"},
        "custom_statuses": {"view"},
    },
    "qa": {
        "projects": {"view"},
        "sprints": {"view"},
        "user_stories": {"view", "modify"},
        "tasks": {"view", "add", "modify"},
        "comments": {"view", "add"},
        "attachments": {"view", "add"},
        "custom_statuses": {"view"},
    },
    "stakeholder": {
        "projects": {"view"},
        "sprints": {"view"},
        "user_stories": {"view"},
        "tasks": {"view"},
        "comments": {"view", "add"},
        "attachments": {"view"},
        "custom_statuses": {"view"},
    },
}


def _normalize_role_name(role_name: str | None) -> str:
    name = (role_name or "").lower()
    if name in ("member", "user"):
        return "developer"
    if name == "tester":
        return "qa"
    if name == "viewer":
        return "stakeholder"
    return name


def _has_default_permission(role_name: str, resource: str, action: str) -> bool:
    perms = DEFAULT_ROLE_PERMISSIONS.get(_normalize_role_name(role_name))
    if not perms:
        return False
    return action in perms.get(resource, set())


class _HTMLSanitizer(html.parser.HTMLParser):
    ALLOWED_TAGS = {
        "p", "br", "strong", "b", "em", "i", "u", "s",
        "ul", "ol", "li", "blockquote", "code", "pre", "a", "img",
    }
    ALLOWED_ATTRS = {
        "a": {"href", "title"},
        "img": {"src", "alt", "title"},
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED_TAGS:
            return
        allowed = self.ALLOWED_ATTRS.get(tag, set())
        filtered = [(k, v) for k, v in attrs if k in allowed and v is not None]
        attrs_str = "".join(f' {k}="{self._escape(v)}"' for k, v in filtered)
        self.parts.append(f"<{tag}{attrs_str}>")

    def handle_endtag(self, tag):
        if tag in self.ALLOWED_TAGS and tag not in ("br", "img"):
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(self._escape(data))

    @staticmethod
    def _escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


def _sanitize_html(raw: str | None) -> str:
    if not raw:
        return ""
    parser = _HTMLSanitizer()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


class UserStoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _commit(self) -> None:
        try:
            await self.db.commit()
        except SQLAlchemyError as exc:
            logger.error("Database commit failed: %s", exc)
            await self.db.rollback()
            raise

    async def _flush(self) -> None:
        try:
            await self.db.flush()
        except SQLAlchemyError as exc:
            logger.error("Database flush failed: %s", exc)
            await self.db.rollback()
            raise

    @staticmethod
    def pagination(page: int, size: int, total: int) -> PaginationResponse:
        pages = max(1, math.ceil(total / size))

        return PaginationResponse(
            page=page,
            page_size=size,
            total_items=total,
            total_pages=pages,
            has_next=page < pages,
            has_previous=page > 1,
        )

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
            raise UserStoryServiceError(404, ErrorCode.ErrNotFound.value, "Project not found")

        return project

    async def _user(self, user_id: str) -> User:
        user = (
            await self.db.execute(
                select(User)
                .options(selectinload(User.role))
                .where(User.id == user_id, User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()

        if not user:
            logger.warning("User not found: user_id=%s", user_id)
            raise UserStoryServiceError(404, ErrorCode.ErrNotFound.value, "User not found")

        return user

    async def _story(self, story_id: str, project_id: str) -> UserStory:
        story = (
            await self.db.execute(
                select(UserStory)
                .options(
                    selectinload(UserStory.project),
                    selectinload(UserStory.sprint),
                    selectinload(UserStory.status),
                    selectinload(UserStory.assignee).selectinload(User.role),
                    selectinload(UserStory.reporter).selectinload(User.role),
                )
                .where(
                    UserStory.id == story_id,
                    UserStory.project_id == project_id,
                    UserStory.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not story:
            logger.warning("User story not found: story_id=%s, project_id=%s", story_id, project_id)
            raise UserStoryServiceError(404, ErrorCode.ErrNotFound.value, "User story not found")

        return story

    async def _member_role(self, user_id: str, project_id: str) -> Role | None:
        member = (
            await self.db.execute(
                select(ProjectMember)
                .options(selectinload(ProjectMember.role).selectinload(Role.permissions))
                .where(
                    ProjectMember.user_id == user_id,
                    ProjectMember.project_id == project_id,
                    ProjectMember.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if member:
            return member.role

        return None

    async def check_permission(self, user_id: str, project_id: str, resource: str, action: str) -> bool:
        user = await self._user(user_id)

        user_role_name = getattr(getattr(user, "role", None), "name", None)
        if user_role_name == "super_admin":
            return False

        member_role = await self._member_role(user_id, project_id)
        if member_role is not None:
            for perm in getattr(member_role, "permissions", []):
                if perm.resource == resource and perm.action == action:
                    return True
            if _has_default_permission(member_role.name, resource, action):
                return True

        project = await self._project(project_id)
        if user.organization_id and str(user.organization_id) == str(project.organization_id):
            if user_role_name == "org_admin":
                for perm in getattr(user.role, "permissions", []) or []:
                    if perm.resource == resource and perm.action == action:
                        return True
                if _has_default_permission(user_role_name, resource, action):
                    return True

        return False

    async def _check_authorization(self, project_id: str, user_id: str) -> tuple[Project, User, bool]:
        project = await self._project(project_id)
        user = await self._user(user_id)

        user_role_name = getattr(getattr(user, "role", None), "name", None)
        if user_role_name == "super_admin":
            logger.warning("Super admin attempted organization activity: user_id=%s", user_id)
            raise UserStoryServiceError(
                403,
                ErrorCode.ErrForbidden.value,
                "Super admins are not allowed to perform organization-level activities",
            )

        authorized = await self.check_permission(user_id, project_id, "user_stories", "view")

        return project, user, authorized

    async def _statuses_by_project(self, project_id: str) -> list[UserStoryStatus]:
        return list(
            (
                await self.db.execute(
                    select(UserStoryStatus).where(
                        UserStoryStatus.project_id == project_id,
                        UserStoryStatus.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )

    async def _status_by_id(self, status_id: str, project_id: str) -> UserStoryStatus:
        status = (
            await self.db.execute(
                select(UserStoryStatus).where(
                    UserStoryStatus.id == status_id,
                    UserStoryStatus.project_id == project_id,
                    UserStoryStatus.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not status:
            logger.warning("Status not found: status_id=%s, project_id=%s", status_id, project_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Status not found"
            )

        return status

    async def _status_by_name(self, project_id: str, name: str) -> UserStoryStatus:
        status = (
            await self.db.execute(
                select(UserStoryStatus).where(
                    UserStoryStatus.project_id == project_id,
                    func.lower(UserStoryStatus.name) == name.lower(),
                    UserStoryStatus.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not status:
            logger.warning("Status not found by name: name=%s, project_id=%s", name, project_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Status not found"
            )

        return status

    def _default_status(self, statuses: list[UserStoryStatus]) -> UserStoryStatus:
        default = None

        for s in statuses:
            if s.is_default:
                if default is None or s.display_order < default.display_order:
                    default = s

        if default is None and statuses:
            default = min(statuses, key=lambda s: s.display_order)

        if default is None:
            logger.error("Project has no defined user story statuses")
            raise UserStoryServiceError(
                500,
                ErrorCode.ErrInternalServerError.value,
                "Project has no defined statuses",
            )

        return default

    async def resolve_status_id_and_name(self, project_id: str, status_id: str | None = None, status_name: str | None = None) -> tuple[str, str]:
        if status_id:
            status = await self._status_by_id(status_id, project_id)
            return str(status.id), status.name

        if status_name and status_name.strip():
            normalized = normalize_task_status(status_name)
            status = await self._status_by_name(project_id, normalized)
            return str(status.id), status.name

        statuses = await self._statuses_by_project(project_id)
        default = self._default_status(statuses)

        return str(default.id), default.name

    async def _story_task_stats(self, project_id: str) -> dict[str, dict]:
        rows = (
            await self.db.execute(
                select(
                    Task.user_story_id,
                    func.count(Task.id).label("total"),
                    func.count(
                        case(
                            (CustomStatus.is_final.is_(True), 1)
                        )
                    ).label("completed"),
                )
                .outerjoin(CustomStatus, and_(
                    CustomStatus.id == Task.status_id,
                    CustomStatus.deleted_at.is_(None),
                ))
                .where(
                    Task.project_id == project_id,
                    Task.user_story_id.is_not(None),
                    Task.deleted_at.is_(None),
                )
                .group_by(Task.user_story_id)
            )
        ).all()

        return {
            str(row.user_story_id): {
                "total": int(row.total or 0),
                "completed": int(row.completed or 0),
            }
            for row in rows
        }

    async def _recalculate_is_closed(self, user_story_id: str) -> None:
        if not user_story_id:
            return

        row = (
            await self.db.execute(
                select(
                    func.count(Task.id).label("total"),
                    func.count(
                        case(
                            (CustomStatus.is_final.is_(True), 1)
                        )
                    ).label("completed"),
                )
                .outerjoin(CustomStatus, and_(
                    CustomStatus.id == Task.status_id,
                    CustomStatus.deleted_at.is_(None),
                ))
                .where(
                    Task.user_story_id == user_story_id,
                    Task.deleted_at.is_(None),
                )
            )
        ).one()

        total = int(row.total or 0)
        completed = int(row.completed or 0)
        is_closed = False

        if total > 0:
            is_closed = completed == total
        else:
            story = (
                await self.db.execute(
                    select(UserStory.id, UserStory.status_id).where(
                        UserStory.id == user_story_id,
                        UserStory.deleted_at.is_(None),
                    )
                )
            ).first()

            if story:
                status = (
                    await self.db.execute(
                        select(UserStoryStatus.is_closed, UserStoryStatus.is_final).where(
                            UserStoryStatus.id == story.status_id,
                            UserStoryStatus.deleted_at.is_(None),
                        )
                    )
                ).first()

                if status:
                    is_closed = bool(status.is_closed or status.is_final)

        await self.db.execute(
            UserStory.__table__.update()
            .where(UserStory.id == user_story_id)
            .values(is_closed=is_closed, updated_at=datetime.now(timezone.utc))
        )

    async def _get_favorite_story_map(self, user_id: str) -> dict[str, bool]:
        if not user_id:
            return {}

        rows = (
            await self.db.execute(
                select(Favorite.user_story_id).where(
                    Favorite.user_id == user_id,
                    Favorite.item_type == Favorite.USER_STORY,
                    Favorite.user_story_id.is_not(None),
                    Favorite.deleted_at.is_(None),
                )
            )
        ).all()

        return {str(row.user_story_id): True for row in rows}

    async def _get_favorite_task_map(self, user_id: str) -> dict[str, bool]:
        if not user_id:
            return {}

        rows = (
            await self.db.execute(
                select(Favorite.task_id).where(
                    Favorite.user_id == user_id,
                    Favorite.item_type == Favorite.TASK,
                    Favorite.task_id.is_not(None),
                    Favorite.deleted_at.is_(None),
                )
            )
        ).all()

        return {str(row.task_id): True for row in rows}

    async def _status_color_map(self, project_id: str) -> dict[str, str]:
        color_map = dict(DEFAULT_STATUS_COLORS)
        rows = (
            await self.db.execute(
                select(CustomStatus.name, CustomStatus.color).where(
                    CustomStatus.project_id == project_id,
                    CustomStatus.deleted_at.is_(None),
                )
            )
        ).all()

        for name, color in rows:
            color_map[normalize_task_status(name)] = color

        return color_map

    async def _status_is_final_map(self, project_id: str) -> dict[str, bool]:
        final_map = dict(DEFAULT_STATUS_IS_FINAL)
        rows = (
            await self.db.execute(
                select(CustomStatus.name, CustomStatus.is_final).where(
                    CustomStatus.project_id == project_id,
                    CustomStatus.deleted_at.is_(None),
                )
            )
        ).all()

        for name, is_final in rows:
            final_map[normalize_task_status(name)] = bool(is_final)

        return final_map

    async def _tasks_by_story(self, user_story_id: str) -> list[Task]:
        return list(
            (
                await self.db.execute(
                    select(Task)
                    .options(
                        selectinload(Task.assignee).selectinload(User.role),
                    )
                    .where(
                        Task.user_story_id == user_story_id,
                        Task.deleted_at.is_(None),
                    )
                    .order_by(Task.created_at.asc())
                )
            ).scalars()
        )

    def _story_color(self, story_status_id: str | None, status_name: str,
                     statuses: list[UserStoryStatus], color_map: dict[str, str]) -> str:
        norm_name = normalize_task_status(status_name)

        if story_status_id:
            for s in statuses:
                if str(s.id) == story_status_id:
                    return s.color

        for s in statuses:
            if normalize_task_status(s.name) == norm_name:
                return s.color

        return color_map.get(norm_name, "#808080")

    def _story_response(self, story: UserStory, statuses: list[UserStoryStatus],
                        total: int, completed: int, progress: float,
                        color_map: dict[str, str], *, is_favourite: bool = False,
                        tasks: list[TaskSummary] | None = None) -> UserStoryResponse:
        status_name = story.status.name if story.status else ""
        project_name = story.project.name if story.project else ""
        sprint_name = story.sprint.name if story.sprint else ""
        assignee_name = story.assignee.full_name if story.assignee else ""

        reporter_name = story.reporter.full_name if story.reporter else ""

        return UserStoryResponse(
            id=str(story.id),
            project_id=str(story.project_id),
            project_name=project_name or None,
            sprint_id=str(story.sprint_id) if story.sprint_id else None,
            sprint_name=sprint_name or None,
            serial_number=story.serial_number,
            formatted_serial_number=story.formatted_serial_number,
            title=story.title,
            description=story.description or None,
            priority=story.priority,
            status_id=str(story.status_id) if story.status_id else None,
            status=status_name,
            status_color=self._story_color(story.status_id, status_name, statuses, color_map),
            is_closed=story.is_closed,
            is_favourite=is_favourite,
            story_points=story.story_points,
            assignee_id=str(story.assignee_id) if story.assignee_id else None,
            assignee_name=assignee_name or None,
            reporter_id=str(story.reporter_id),
            reporter_name=reporter_name,
            reporter=user_summary_from_user(story.reporter),
            assignee=user_summary_from_user(story.assignee),
            backlog_order=story.backlog_order,
            total_tasks=total,
            completed_tasks=completed,
            progress=progress,
            created_at=story.created_at,
            updated_at=story.updated_at,
            tasks=tasks or [],
        )

    async def _task_summaries(self, tasks: list[Task], color_map: dict[str, str],
                              is_final_map: dict[str, bool],
                              fav_task_map: dict[str, bool]) -> list[TaskSummary]:
        result = []

        for t in tasks:
            norm = normalize_task_status(t.status)
            result.append(
                TaskSummary(
                    id=str(t.id),
                    title=t.title,
                    key=t.key,
                    type=t.type,
                    status=t.status,
                    status_color=color_map.get(norm, "#808080"),
                    status_is_final=is_final_map.get(norm, False),
                    priority=t.priority,
                    is_favourite=fav_task_map.get(str(t.id), False),
                    assignee_id=str(t.assignee_id) if t.assignee_id else None,
                    assignee_name=(t.assignee.full_name if t.assignee else None),
                    assignee=user_summary_from_user(t.assignee),
                )
            )

        return result

    async def _build_single_story(self, story: UserStory, user_id: str, project_id: str) -> UserStoryResponse:
        statuses = await self._statuses_by_project(project_id)
        color_map = await self._status_color_map(project_id)
        is_final_map = await self._status_is_final_map(project_id)
        stats = await self._story_task_stats(project_id)
        stat = stats.get(str(story.id), {"total": 0, "completed": 0})
        total, completed = stat["total"], stat["completed"]
        progress = (completed / total * 100.0) if total > 0 else 0.0

        is_fav = (await self._get_favorite_story_map(user_id)).get(str(story.id), False)
        tasks = await self._tasks_by_story(story.id)
        fav_task_map = await self._get_favorite_task_map(user_id)
        task_responses = await self._task_summaries(tasks, color_map, is_final_map, fav_task_map)

        return self._story_response(
            story, statuses, total, completed, progress, color_map,
            is_favourite=is_fav, tasks=task_responses,
        )

    async def _audit(self, *, user_id: str, organization_id: str, project_id: str,
                     action: str, resource_type: str, resource_id: str,
                     details: str, audit_type: str = AuditLogType.ACTIVITY,
                     user_story_id: str | None = None) -> None:
        try:
            self.db.add(
                AuditLog(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    user_story_id=user_story_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    details=details,
                    type=audit_type,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await self.db.commit()
            logger.debug("Audit log created: action=%s, resource_type=%s, resource_id=%s", action, resource_type, resource_id)

        except SQLAlchemyError as exc:
            await self.db.rollback()
            logger.warning("Failed to create audit log: %s", exc)

    async def create(
        self,
        req: CreateUserStoryRequest,
        project_id: str,
        reporter_id: str,
        organization_id: str,
    ) -> UserStoryResponse:
        logger.info("Service: Creating user story in project_id=%s by reporter_id=%s", project_id, reporter_id)

        project, user, authorized = await self._check_authorization(project_id, reporter_id)
        if not authorized:
            logger.warning("Service: Permission denied viewing user stories for user_id=%s in project_id=%s", reporter_id, project_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to view user stories in this project",
            )

        has_add = await self.check_permission(reporter_id, project_id, "user_stories", "add")
        if not has_add:
            logger.warning("Service: Permission denied adding user story for user_id=%s in project_id=%s", reporter_id, project_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to create user stories in this project",
            )

        title_length = len(req.title)
        if title_length < 3 or title_length > 250:
            raise UserStoryServiceError(
                400, ErrorCode.ErrValidation.value,
                "User story title must be between 3 and 250 characters",
            )

        if req.assignee_id:
            assignee = await self._user(req.assignee_id)
            if not assignee.is_active:
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value, "Assignee must be an active user"
                )

            is_member = await self._is_project_member(project_id, req.assignee_id)
            if not is_member:
                role_name = getattr(getattr(assignee, "role", None), "name", None)
                assignee_org_id = getattr(assignee, "organization_id", None)
                if (
                    role_name == "org_admin"
                    and assignee_org_id
                    and str(assignee_org_id) == str(organization_id)
                ):
                    is_member = True

            if not is_member:
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value,
                    "Assignee must be a member of the project",
                )

        if req.sprint_id:
            in_project = await self._sprint_in_project(req.sprint_id, project_id)
            if not in_project:
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value,
                    "Sprint must belong to the same project",
                )

        max_order = (
            await self.db.execute(
                select(func.coalesce(func.max(UserStory.backlog_order), 0)).where(
                    UserStory.project_id == project_id
                )
            )
        ).scalar_one() or 0

        status_id, status_name = await self.resolve_status_id_and_name(
            project_id=project_id,
            status_id=req.status_id,
            status_name=req.status,
        )

        story = UserStory(
            project_id=project_id,
            sprint_id=req.sprint_id if req.sprint_id else None,
            title=req.title,
            description=_sanitize_html(req.description),
            priority=req.priority,
            status_id=status_id,
            story_points=req.story_points,
            backlog_order=int(max_order) + 1,
            assignee_id=req.assignee_id if req.assignee_id else None,
            reporter_id=reporter_id,
        )

        self.db.add(story)
        await self._flush()

        await self._recalculate_is_closed(story.id)
        await self._commit()

        created_story = await self._story(story.id, project_id)
        response = await self._build_single_story(created_story, reporter_id, project_id)

        await self._audit(
            user_id=reporter_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=str(story.id),
            action="created",
            resource_type="user_story",
            resource_id=str(story.id),
            details=f"User Story '{story.title}' created by {user.username}",
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully created user story story_id=%s in project_id=%s", story.id, project_id)
        return response

    async def _is_project_member(self, project_id: str, user_id: str) -> bool:
        member = (
            await self.db.execute(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                    ProjectMember.deleted_at.is_(None),
                )
            )
        ).first()

        return member is not None

    async def _sprint_in_project(self, sprint_id: str, project_id: str) -> bool:
        sprint = (
            await self.db.execute(
                select(Sprint.id).where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.deleted_at.is_(None),
                )
            )
        ).first()

        return sprint is not None

    async def get_by_id(
        self, user_story_id: str, project_id: str, user_id: str, organization_id: str
    ) -> UserStoryResponse:
        logger.info("Service: Fetching user story_id=%s in project_id=%s", user_story_id, project_id)

        _, user, authorized = await self._check_authorization(project_id, user_id)
        if not authorized:
            logger.warning("Service: Permission denied viewing user story for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to view user stories in this project",
            )

        story = await self._story(user_story_id, project_id)
        response = await self._build_single_story(story, user_id, project_id)

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=str(story.id),
            action="viewed",
            resource_type="user_story",
            resource_id=str(story.id),
            details=f"User Story '{story.title}' viewed by {user.username}",
            audit_type=AuditLogType.VIEW,
        )

        return response

    async def update(
        self,
        req: UpdateUserStoryRequest,
        user_story_id: str,
        project_id: str,
        user_id: str,
        organization_id: str,
    ) -> UserStoryResponse:
        logger.info("Service: Updating user story_id=%s in project_id=%s by user_id=%s", user_story_id, project_id, user_id)

        _, user, _ = await self._check_authorization(project_id, user_id)

        authorized_update = await self.check_permission(user_id, project_id, "user_stories", "modify")
        if not authorized_update:
            logger.warning("Service: Permission denied updating user story for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to update user stories in this project",
            )

        existing = await self._story(user_story_id, project_id)

        changed_by = user.username or user.full_name or user.email or str(user_id)
        changes: list[str] = []
        updates: dict = {}

        payload = req.model_dump(exclude_unset=True)
        fields_to_update = {k: v for k, v in payload.items() if v is not None}

        if "title" in fields_to_update:
            new_title = fields_to_update["title"]
            title_length = len(new_title)
            if title_length < 3 or title_length > 250:
                raise UserStoryServiceError(
                    400, ErrorCode.ErrValidation.value,
                    "User story title must be between 3 and 250 characters",
                )
            if new_title != existing.title:
                changes.append(f"title changed from '{existing.title}' to '{new_title}'")
            updates["title"] = new_title

        if "description" in fields_to_update:
            sanitized = _sanitize_html(fields_to_update["description"]).strip()
            if sanitized != (existing.description or ""):
                changes.append("description changed")
            updates["description"] = sanitized

        if "priority" in fields_to_update:
            new_priority = fields_to_update["priority"]
            if new_priority != existing.priority:
                changes.append(f"priority changed from '{existing.priority}' to '{new_priority}'")
            updates["priority"] = new_priority

        if "status_id" in fields_to_update or "status" in fields_to_update:
            resolved_id, resolved_name = await self.resolve_status_id_and_name(
                project_id=project_id,
                status_id=fields_to_update.get("status_id"),
                status_name=fields_to_update.get("status"),
            )
            current_status_name = existing.status.name if existing.status else ""
            if resolved_name != current_status_name:
                changes.append(f"status changed from '{current_status_name}' to '{resolved_name}'")
            updates["status_id"] = resolved_id

        if "story_points" in fields_to_update:
            new_points = fields_to_update["story_points"]
            if new_points != existing.story_points:
                changes.append(f"story points changed from {existing.story_points} to {new_points}")
            updates["story_points"] = new_points

        assignee_id = req.assignee_id
        if assignee_id is None and "assignee_id" in payload and payload["assignee_id"] is None:
            if existing.assignee_id:
                changes.append(f"assignee changed from {existing.assignee_id} to nil")
            updates["assignee_id"] = None
        elif assignee_id:
            assignee = await self._user(assignee_id)
            if not assignee.is_active:
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value, "Assignee must be an active user"
                )

            is_member = await self._is_project_member(project_id, assignee_id)
            if not is_member:
                role_name = getattr(getattr(assignee, "role", None), "name", None)
                assignee_org_id = getattr(assignee, "organization_id", None)
                if (
                    role_name == "org_admin"
                    and assignee_org_id
                    and str(assignee_org_id) == str(organization_id)
                ):
                    is_member = True

            if not is_member:
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value,
                    "Assignee must be a member of the project",
                )

            old_assignee = existing.assignee_id or "nil"
            new_assignee = assignee_id
            if old_assignee != new_assignee:
                changes.append(f"assignee changed from {old_assignee} to {new_assignee}")
            updates["assignee_id"] = assignee_id

        if "is_closed" in fields_to_update:
            new_closed = fields_to_update["is_closed"]
            if new_closed != existing.is_closed:
                changes.append(f"is_closed changed from {existing.is_closed} to {new_closed}")
            updates["is_closed"] = new_closed

        sprint_id = req.sprint_id
        if sprint_id is None and "sprint_id" in payload and payload["sprint_id"] is None:
            if existing.sprint_id:
                changes.append(f"sprint changed from {existing.sprint_id} to nil")
            updates["sprint_id"] = None
        elif sprint_id:
            in_project = await self._sprint_in_project(sprint_id, project_id)
            if not in_project:
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value,
                    "Sprint must belong to the same project",
                )

            old_sprint = existing.sprint_id or "nil"
            new_sprint = sprint_id
            if old_sprint != new_sprint:
                changes.append(f"sprint changed from {old_sprint} to {new_sprint}")
            updates["sprint_id"] = sprint_id

        if updates:
            updates["updated_at"] = datetime.now(timezone.utc)
            await self.db.execute(
                UserStory.__table__.update()
                .where(UserStory.id == user_story_id)
                .values(**updates)
            )
            await self._flush()

        await self._recalculate_is_closed(user_story_id)
        await self._commit()

        updated_story = await self._story(user_story_id, project_id)
        response = await self._build_single_story(updated_story, user_id, project_id)

        if changes:
            detail = f"User Story '{updated_story.title}' updated by {changed_by}: " + ", ".join(changes)
        else:
            detail = f"User Story '{updated_story.title}' details updated by {changed_by}"

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=str(updated_story.id),
            action="updated",
            resource_type="user_story",
            resource_id=str(updated_story.id),
            details=detail,
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully updated user story_id=%s", user_story_id)
        return response

    async def delete(
        self, user_story_id: str, project_id: str, user_id: str, organization_id: str
    ) -> None:
        logger.info("Service: Deleting user story_id=%s in project_id=%s by user_id=%s", user_story_id, project_id, user_id)

        _, user, authorized = await self._check_authorization(project_id, user_id)
        if not authorized:
            logger.warning("Service: Permission denied deleting user story for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to delete user stories in this project",
            )

        has_delete = await self.check_permission(user_id, project_id, "user_stories", "delete")
        if not has_delete:
            logger.warning("Service: Permission denied deleting user story for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to delete user stories in this project",
            )

        existing = await self._story(user_story_id, project_id)

        await self.db.execute(
            UserStory.__table__.update()
            .where(
                UserStory.id == user_story_id,
                UserStory.project_id == project_id,
            )
            .values(deleted_at=datetime.now(timezone.utc))
        )
        await self._commit()

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=user_story_id,
            action="deleted",
            resource_type="user_story",
            resource_id=user_story_id,
            details=f"User Story '{existing.title}' deleted by {user.username}",
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully soft-deleted user story_id=%s", user_story_id)

    async def list(
        self, project_id: str, user_id: str, organization_id: str,
        filter_: UserStoryFilter
    ) -> tuple[list[UserStoryResponse], PaginationResponse]:
        logger.info("Service: Listing user stories in project_id=%s for user_id=%s", project_id, user_id)

        _, _, authorized = await self._check_authorization(project_id, user_id)
        if not authorized:
            logger.warning("Service: Permission denied listing user stories for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to view user stories in this project",
            )

        page = max(1, filter_.page)
        page_size = max(1, filter_.page_size)
        offset = (page - 1) * page_size

        conditions = [
            UserStory.project_id == project_id,
            UserStory.deleted_at.is_(None),
        ]

        if filter_.status:
            conditions.append(
                UserStory.status_id.in_(
                    select(UserStoryStatus.id).where(
                        UserStoryStatus.project_id == project_id,
                        func.lower(UserStoryStatus.name) == filter_.status.lower(),
                        UserStoryStatus.deleted_at.is_(None),
                    )
                )
            )

        if filter_.assignee_id:
            conditions.append(UserStory.assignee_id == filter_.assignee_id)

        if filter_.reporter_id:
            conditions.append(UserStory.reporter_id == filter_.reporter_id)

        if filter_.is_unassigned_story:
            conditions.append(UserStory.sprint_id.is_(None))
        elif filter_.sprint_id:
            if filter_.sprint_id in ("null", "none"):
                conditions.append(UserStory.sprint_id.is_(None))
            else:
                conditions.append(UserStory.sprint_id == filter_.sprint_id)

        if filter_.priority:
            conditions.append(func.lower(UserStory.priority) == filter_.priority.lower())

        if filter_.serial_number is not None:
            conditions.append(UserStory.serial_number == filter_.serial_number)

        if filter_.is_closed is not None:
            conditions.append(UserStory.is_closed == filter_.is_closed)

        if filter_.search:
            clean_search = filter_.search.strip().lstrip("#")
            search_term = f"%{filter_.search.lower()}%"
            clean_term = f"%{clean_search.lower()}%"
            conditions.append(
                or_(
                    func.lower(UserStory.title).like(search_term),
                    func.lower(UserStory.description).like(search_term),
                    func.cast(UserStory.serial_number, String).like(clean_term),
                )
            )

        total = (
            await self.db.execute(
                select(func.count(UserStory.id)).where(*conditions)
            )
        ).scalar_one()

        order_column = UserStory.created_at
        if filter_.sort_by == "title":
            order_column = UserStory.title
        elif filter_.sort_by == "updated_at":
            order_column = UserStory.updated_at
        elif filter_.sort_by == "priority":
            order_column = UserStory.priority
        elif filter_.sort_by == "serial_number":
            order_column = UserStory.serial_number

        stories = list(
            (
                await self.db.execute(
                    select(UserStory)
                    .options(
                        selectinload(UserStory.project),
                        selectinload(UserStory.sprint),
                        selectinload(UserStory.status),
                        selectinload(UserStory.assignee).selectinload(User.role),
                        selectinload(UserStory.reporter).selectinload(User.role),
                    )
                    .where(*conditions)
                    .order_by(
                        order_column.asc()
                        if filter_.sort_order == "ASC"
                        else order_column.desc()
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            ).scalars()
        )

        stats = await self._story_task_stats(project_id)
        statuses = await self._statuses_by_project(project_id)
        color_map = await self._status_color_map(project_id)
        is_final_map = await self._status_is_final_map(project_id)
        fav_story_map = await self._get_favorite_story_map(user_id)
        fav_task_map = await self._get_favorite_task_map(user_id)

        responses = []
        for story in stories:
            stat = stats.get(str(story.id), {"total": 0, "completed": 0})
            total_tasks, completed_tasks = stat["total"], stat["completed"]
            progress = (completed_tasks / total_tasks * 100.0) if total_tasks > 0 else 0.0
            tasks = await self._tasks_by_story(story.id)
            task_responses = await self._task_summaries(tasks, color_map, is_final_map, fav_task_map)
            responses.append(
                self._story_response(
                    story, statuses, total_tasks, completed_tasks, progress, color_map,
                    is_favourite=fav_story_map.get(str(story.id), False),
                    tasks=task_responses,
                )
            )

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="viewed",
            resource_type="user_story",
            resource_id=project_id,
            details="",
            audit_type=AuditLogType.AUDIT,
        )

        logger.info("Service: Successfully fetched %d user stories (total=%d)", len(responses), total)
        return responses, self.pagination(page, page_size, total)

    async def reorder(
        self, req: ReorderUserStoriesRequest, project_id: str, user_id: str,
        organization_id: str
    ) -> None:
        logger.info("Service: Reordering %d user stories in project_id=%s", len(req.story_ids), project_id)

        _, user, authorized = await self._check_authorization(project_id, user_id)
        if not authorized:
            logger.warning("Service: Permission denied reordering user stories for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to reorder user stories in this project",
            )

        for idx, story_id in enumerate(req.story_ids):
            await self.db.execute(
                UserStory.__table__.update()
                .where(
                    UserStory.id == story_id,
                    UserStory.project_id == project_id,
                    UserStory.deleted_at.is_(None),
                )
                .values(backlog_order=idx + 1)
            )

        await self._commit()

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="reordered",
            resource_type="user_story",
            resource_id=req.story_ids[0],
            details=f"User Story was reordered by {user.username}",
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully reordered user stories in project_id=%s", project_id)

    async def update_status(
        self, req: UpdateUserStoryStatusAssignmentRequest, user_story_id: str,
        project_id: str, user_id: str, organization_id: str
    ) -> UserStoryResponse:
        logger.info("Service: Updating status_id=%s for user_story_id=%s", req.status_id, user_story_id)

        _, _, authorized = await self._check_authorization(project_id, user_id)
        if not authorized:
            logger.warning("Service: Permission denied updating user story status for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to update user stories in this project",
            )

        await self._story(user_story_id, project_id)
        status = await self._status_by_id(req.status_id, project_id)

        await self.db.execute(
            UserStory.__table__.update()
            .where(
                UserStory.id == user_story_id,
                UserStory.project_id == project_id,
            )
            .values(status_id=req.status_id, updated_at=datetime.now(timezone.utc))
        )
        await self._flush()

        await self._recalculate_is_closed(user_story_id)
        await self._commit()

        updated_story = await self._story(user_story_id, project_id)
        response = await self._build_single_story(updated_story, user_id, project_id)

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=user_story_id,
            action="updated",
            resource_type="user_story",
            resource_id=user_story_id,
            details=f"User Story '{updated_story.title}' status updated to '{status.name}'",
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully updated status for user_story_id=%s to '%s'", user_story_id, status.name)
        return response

    async def add_favorite(
        self, user_id: str, project_id: str, user_story_id: str
    ) -> FavoriteResponse:
        logger.info("Service: Adding user_story_id=%s to favorites for user_id=%s", user_story_id, user_id)

        story = await self._story(user_story_id, project_id)

        existing = (
            await self.db.execute(
                select(Favorite).where(
                    Favorite.user_id == user_id,
                    Favorite.item_type == Favorite.USER_STORY,
                    Favorite.user_story_id == user_story_id,
                    Favorite.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if existing:
            logger.warning("Service: User story_id=%s is already favorited by user_id=%s", user_story_id, user_id)
            raise UserStoryServiceError(
                409, ErrorCode.ErrConflict.value, "Item is already added to favorites"
            )

        fav = Favorite(
            user_id=user_id,
            item_type=Favorite.USER_STORY,
            user_story_id=user_story_id,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(fav)
        await self._commit()
        await self.db.refresh(fav)

        logger.info("Service: Successfully added favorite_id=%s for user_story_id=%s", fav.id, user_story_id)

        project_obj = getattr(story, "project", None)
        project_name = getattr(project_obj, "name", None)

        return FavoriteResponse(
            id=str(fav.id),
            user_id=str(fav.user_id),
            item_type=fav.item_type,
            user_story_id=str(fav.user_story_id),
            project_id=str(story.project_id),
            project_name=project_name,
            user_story_name=story.title,
            user_story_title=story.title,
            created_at=fav.created_at,
        )

    async def remove_favorite(
        self, user_id: str, project_id: str, user_story_id: str
    ) -> RemoveFavoriteResponse:
        logger.info("Service: Removing user_story_id=%s from favorites for user_id=%s", user_story_id, user_id)

        await self._story(user_story_id, project_id)

        fav = (
            await self.db.execute(
                select(Favorite).where(
                    Favorite.user_id == user_id,
                    Favorite.item_type == Favorite.USER_STORY,
                    Favorite.user_story_id == user_story_id,
                    Favorite.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not fav:
            logger.warning("Service: Favorite record not found for user_story_id=%s, user_id=%s", user_story_id, user_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Favorite not found"
            )

        fav.deleted_at = datetime.now(timezone.utc)
        await self._commit()

        logger.info("Service: Successfully removed favorite for user_story_id=%s", user_story_id)

        return RemoveFavoriteResponse(id=str(fav.id))

    async def upload_attachments(
        self, user_story_id: str, project_id: str, user_id: str, organization_id: str,
        files: List[UploadFile]
    ) -> List[UserStoryAttachmentResponse]:
        logger.info("Service: Uploading %d attachment(s) for user_story_id=%s", len(files), user_story_id)

        story = await self._story(user_story_id, project_id)
        max_files = get_settings().attachment_max_files_count

        if len(files) > max_files:
            raise UserStoryServiceError(
                400,
                ErrorCode.ErrBadRequest.value,
                f"Maximum of {max_files} files can be uploaded per request.",
            )

        existing_count = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(UserStoryAttachment)
                    .where(UserStoryAttachment.user_story_id == user_story_id)
                )
            ).scalar_one()
        )
        if existing_count + len(files) > max_files:
            raise UserStoryServiceError(
                400,
                ErrorCode.ErrBadRequest.value,
                f"Maximum of {max_files} attachments are allowed per user story.",
            )

        max_size_mb = get_settings().attachment_max_file_size_mb
        max_size_bytes = max_size_mb * 1024 * 1024
        allowed_extensions = {".png", ".jpg", ".jpeg", ".pdf", ".docx", ".xlsx", ".zip", ".txt"}
        uploaded_keys: list[str] = []
        attachments: list[UserStoryAttachment] = []

        try:
            for file in files:
                filename = (file.filename or "file").replace("\\", "/").split("/")[-1]
                extension = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
                declared_type = (file.content_type or "").split(";", 1)[0].strip().lower()
                extension_by_mime = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "application/pdf": ".pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                    "application/zip": ".zip",
                    "text/plain": ".txt",
                }
                if not extension and declared_type in extension_by_mime:
                    extension = extension_by_mime[declared_type]
                    filename = f"{filename}{extension}"
                if extension not in allowed_extensions:
                    await file.close()
                    logger.warning(
                        "Rejected User Story attachment filename=%r content_type=%r extension=%r",
                        filename,
                        declared_type,
                        extension,
                    )
                    raise UserStoryServiceError(
                        415,
                        "UNSUPPORTED_MEDIA_TYPE",
                        "Unsupported file type. Only PNG, JPG/JPEG, PDF, DOCX, XLSX, ZIP, and TXT files are accepted.",
                    )

                content = await file.read(max_size_bytes + 1)
                await file.close()
                if len(content) > max_size_bytes:
                    raise UserStoryServiceError(
                        413,
                        "PAYLOAD_TOO_LARGE",
                        f"File {filename} exceeds the maximum allowed size of {max_size_mb} MB.",
                    )

                valid_content = {
                    ".png": content.startswith(b"\x89PNG\r\n\x1a\n"),
                    ".jpg": content.startswith(b"\xff\xd8\xff"),
                    ".jpeg": content.startswith(b"\xff\xd8\xff"),
                    ".pdf": content.startswith(b"%PDF-"),
                    ".docx": content.startswith(b"PK"),
                    ".xlsx": content.startswith(b"PK"),
                    ".zip": content.startswith(b"PK"),
                    ".txt": b"\x00" not in content[:512],
                }[extension]
                if not valid_content:
                    raise UserStoryServiceError(
                        415,
                        "UNSUPPORTED_MEDIA_TYPE",
                        f"Unsupported file content type for extension '{extension}'.",
                    )

                mime_type = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".pdf": "application/pdf",
                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ".zip": "application/zip",
                    ".txt": "text/plain",
                }[extension]
                storage_path = build_attachment_key("user_stories", user_story_id, filename)
                stored_name = storage_path.rsplit("/", 1)[-1]
                url = await asyncio.to_thread(
                    upload_s3_object,
                    content,
                    storage_path,
                    mime_type,
                )
                uploaded_keys.append(storage_path)

                attachment = UserStoryAttachment(
                    user_story_id=user_story_id,
                    original_filename=filename,
                    stored_filename=stored_name,
                    mime_type=mime_type,
                    file_size=len(content),
                    storage_path=storage_path,
                    url=url,
                    uploaded_by=user_id,
                    uploaded_at=datetime.now(timezone.utc),
                )
                self.db.add(attachment)
                attachments.append(attachment)

            await self._commit()
            for attachment in attachments:
                await self.db.refresh(attachment)
        except UserStoryServiceError:
            await self.db.rollback()
            for key in uploaded_keys:
                try:
                    await asyncio.to_thread(delete_s3_object, key)
                except Exception:
                    logger.exception("Failed to clean up User Story attachment key=%s", key)
            raise
        except StorageConfigurationError as exc:
            await self.db.rollback()
            raise UserStoryServiceError(
                503, "SERVICE_UNAVAILABLE", "Supabase S3 storage is not configured."
            ) from exc
        except Exception as exc:
            await self.db.rollback()
            for key in uploaded_keys:
                try:
                    await asyncio.to_thread(delete_s3_object, key)
                except Exception:
                    logger.exception("Failed to clean up User Story attachment key=%s", key)
            raise UserStoryServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to upload attachment. Please try again later."
            ) from exc

        responses = [
            UserStoryAttachmentResponse(
                id=str(attachment.id),
                user_story_id=str(attachment.user_story_id),
                original_filename=attachment.original_filename,
                stored_filename=attachment.stored_filename,
                mime_type=attachment.mime_type,
                file_size=attachment.file_size,
                url=attachment.url,
                uploaded_by=str(attachment.uploaded_by),
                uploaded_at=attachment.uploaded_at,
            )
            for attachment in attachments
        ]

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=user_story_id,
            action="uploaded",
            resource_type="user_story_attachment",
            resource_id=user_story_id,
            details=f"Uploaded {len(files)} attachment(s) to user story '{story.title}'",
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully uploaded %d attachment(s) for user_story_id=%s", len(responses), user_story_id)

        return responses

    async def get_attachments(
        self, user_story_id: str, project_id: str, user_id: str, organization_id: str
    ) -> List[UserStoryAttachmentResponse]:
        logger.info("Service: Fetching attachments for user_story_id=%s", user_story_id)

        await self._story(user_story_id, project_id)

        attachments = (
            await self.db.execute(
                select(UserStoryAttachment).where(
                    UserStoryAttachment.user_story_id == user_story_id
                )
            )
        ).scalars().all()

        logger.info("Service: Found %d attachment(s) for user_story_id=%s", len(attachments), user_story_id)

        return [
            UserStoryAttachmentResponse(
                id=str(a.id),
                user_story_id=str(a.user_story_id),
                original_filename=a.original_filename,
                stored_filename=a.stored_filename,
                mime_type=a.mime_type,
                file_size=a.file_size,
                url=a.url,
                uploaded_by=str(a.uploaded_by),
                uploaded_at=a.uploaded_at,
            )
            for a in attachments
        ]

    async def download_attachment(
        self, attachment_id: str, user_story_id: str, project_id: str, user_id: str
    ) -> Tuple[bytes, str, str]:
        logger.info("Service: Downloading attachment_id=%s for user_story_id=%s", attachment_id, user_story_id)

        await self._story(user_story_id, project_id)

        attachment = (
            await self.db.execute(
                select(UserStoryAttachment).where(
                    UserStoryAttachment.id == attachment_id,
                    UserStoryAttachment.user_story_id == user_story_id,
                )
            )
        ).scalar_one_or_none()

        if not attachment:
            logger.warning("Service: Attachment not found attachment_id=%s", attachment_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Attachment not found"
            )

        stream, _, stored_mime = await asyncio.to_thread(get_s3_object, attachment.storage_path)
        content = await asyncio.to_thread(stream.read)

        logger.info("Service: Successfully read attachment_id=%s ('%s')", attachment_id, attachment.original_filename)

        return content, attachment.original_filename, attachment.mime_type or stored_mime

    async def delete_attachment(
        self, attachment_id: str, user_story_id: str, project_id: str, user_id: str, organization_id: str
    ) -> None:
        logger.info("Service: Deleting attachment_id=%s from user_story_id=%s", attachment_id, user_story_id)

        story = await self._story(user_story_id, project_id)

        attachment = (
            await self.db.execute(
                select(UserStoryAttachment).where(
                    UserStoryAttachment.id == attachment_id,
                    UserStoryAttachment.user_story_id == user_story_id,
                )
            )
        ).scalar_one_or_none()

        if not attachment:
            logger.warning("Service: Attachment not found attachment_id=%s", attachment_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Attachment not found"
            )

        storage_path = attachment.storage_path

        await self.db.delete(attachment)
        await self._commit()

        try:
            await asyncio.to_thread(delete_s3_object, storage_path)
        except Exception:
            logger.exception("Failed to delete User Story attachment key=%s", storage_path)

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=user_story_id,
            action="deleted",
            resource_type="user_story_attachment",
            resource_id=attachment_id,
            details=f"Deleted attachment '{attachment.original_filename}' from user story '{story.title}'",
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully deleted attachment_id=%s", attachment_id)

    async def create_comment(
        self, req: CreateCommentRequest, user_story_id: str, project_id: str,
        user_id: str, organization_id: str
    ) -> CommentResponse:
        logger.info("Service: Creating comment for user_story_id=%s by user_id=%s", user_story_id, user_id)

        story = await self._story(user_story_id, project_id)

        has_comment = await self.check_permission(user_id, project_id, "comments", "comment")
        has_add = await self.check_permission(user_id, project_id, "comments", "add")
        if not (has_comment or has_add):
            logger.warning("Service: Permission denied adding comment for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to add comments to this project",
            )

        if req.parent_comment_id:
            parent = (
                await self.db.execute(
                    select(Comments).where(
                        Comments.id == req.parent_comment_id,
                        Comments.user_story_id == user_story_id,
                        Comments.project_id == project_id,
                    )
                )
            ).scalar_one_or_none()

            if not parent:
                logger.warning("Service: Parent comment not found parent_comment_id=%s", req.parent_comment_id)
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value,
                    "Parent comment belongs to a different user story or project",
                )

        sanitized_content = _sanitize_html(req.content).strip()
        if not sanitized_content:
            raise UserStoryServiceError(
                400, ErrorCode.ErrValidation.value, "Content cannot be empty"
            )

        comment = Comments(
            user_story_id=user_story_id,
            user_id=user_id,
            project_id=project_id,
            organization_id=organization_id,
            content=sanitized_content,
            parent_comment_id=req.parent_comment_id,
            is_deleted=False,
        )

        self.db.add(comment)
        await self._flush()

        if req.attachment_ids:
            drafts = list(
                (
                    await self.db.execute(
                        select(CommentAttachment).where(
                            CommentAttachment.id.in_(req.attachment_ids),
                            CommentAttachment.comment_id.is_(None),
                            CommentAttachment.user_story_id == user_story_id,
                            CommentAttachment.uploaded_by == user_id,
                        )
                    )
                ).scalars()
            )
            if len(drafts) != len(set(req.attachment_ids)):
                await self.db.rollback()
                raise UserStoryServiceError(
                    400, ErrorCode.ErrBadRequest.value,
                    "One or more attachments are invalid or do not belong to this user story",
                )
            for attachment in drafts:
                attachment.comment_id = str(comment.id)
        await self._commit()
        await self.db.refresh(comment)

        user = await self._user(user_id)

        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            user_story_id=user_story_id,
            action="created",
            resource_type="comment",
            resource_id=str(comment.id),
            details=f"{user.username} commented on the userstory: {story.title} as {comment.content}",
            audit_type=AuditLogType.ACTIVITY,
        )

        logger.info("Service: Successfully created comment_id=%s for user_story_id=%s", comment.id, user_story_id)

        return CommentResponse(
            id=str(comment.id),
            user_story_id=str(comment.user_story_id),
            user_id=str(comment.user_id),
            user_name=getattr(user, "username", None),
            full_name=getattr(user, "full_name", None),
            avatar_url=getattr(user, "avatar_url", None),
            color=getattr(user, "color", None),
            content=comment.content,
            parent_comment_id=str(comment.parent_comment_id) if comment.parent_comment_id else None,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            is_deleted=comment.is_deleted,
        )

    async def upload_comment_attachments(
        self, files: List[UploadFile], user_story_id: str, project_id: str,
        user_id: str, organization_id: str, comment_id: str | None = None,
    ):
        """Use the shared comments attachment implementation for user stories."""
        await self._story(user_story_id, project_id)
        if comment_id:
            comment = (
                await self.db.execute(
                    select(Comments).where(
                        Comments.id == comment_id,
                        Comments.user_story_id == user_story_id,
                        Comments.project_id == project_id,
                        Comments.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if not comment:
                raise UserStoryServiceError(404, ErrorCode.ErrNotFound.value, "Comment not found")

        from src.comments.service import CommentService
        try:
            return await CommentService(self.db).upload_comment_attachments(
                comment_id=comment_id,
                task_id=None,
                user_story_id=user_story_id,
                user_id=user_id,
                organization_id=organization_id,
                files=files,
            )
        except HTTPException as exc:
            raise UserStoryServiceError(
                exc.status_code,
                "BAD_REQUEST" if exc.status_code < 500 else "INTERNAL_SERVER_ERROR",
                str(exc.detail),
            ) from exc

    async def get_comment_attachments(
        self, comment_id: str, user_story_id: str, project_id: str, user_id: str,
    ) -> List[dict]:
        await self._story(user_story_id, project_id)
        if not await self.check_permission(user_id, project_id, "comments", "view"):
            raise UserStoryServiceError(403, ErrorCode.ErrForbidden.value, "You do not have permission to view comments in this project")
        comment = (
            await self.db.execute(
                select(Comments)
                .options(selectinload(Comments.attachments))
                .where(
                    Comments.id == comment_id,
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if not comment:
            raise UserStoryServiceError(404, ErrorCode.ErrNotFound.value, "Comment not found")
        return [
            {
                "id": str(a.id), "comment_id": str(a.comment_id),
                "original_filename": a.original_filename, "mime_type": a.mime_type,
                "file_size": a.file_size, "url": a.url,
                "uploaded_by": str(a.uploaded_by), "uploaded_at": a.uploaded_at,
            }
            for a in comment.attachments
        ]

    async def download_draft_comment_attachment(
        self, attachment_id: str, user_story_id: str, project_id: str, user_id: str,
    ):
        await self._story(user_story_id, project_id)
        attachment = (
            await self.db.execute(
                select(CommentAttachment).where(
                    CommentAttachment.id == attachment_id,
                    CommentAttachment.comment_id.is_(None),
                    CommentAttachment.user_story_id == user_story_id,
                    CommentAttachment.uploaded_by == user_id,
                )
            )
        ).scalar_one_or_none()
        if not attachment:
            raise UserStoryServiceError(404, ErrorCode.ErrNotFound.value, "Attachment not found")
        stream, size, stored_mime = await asyncio.to_thread(get_s3_object, attachment.storage_path)
        return stream, attachment.original_filename, attachment.mime_type or stored_mime, attachment.file_size or size

    async def delete_draft_comment_attachment(
        self, attachment_id: str, user_story_id: str, project_id: str, user_id: str,
    ) -> None:
        await self._story(user_story_id, project_id)
        attachment = (
            await self.db.execute(
                select(CommentAttachment).where(
                    CommentAttachment.id == attachment_id,
                    CommentAttachment.comment_id.is_(None),
                    CommentAttachment.user_story_id == user_story_id,
                    CommentAttachment.uploaded_by == user_id,
                )
            )
        ).scalar_one_or_none()
        if not attachment:
            raise UserStoryServiceError(404, ErrorCode.ErrNotFound.value, "Attachment not found")
        storage_path = attachment.storage_path
        await self.db.delete(attachment)
        await self._commit()
        try:
            await asyncio.to_thread(delete_s3_object, storage_path)
        except Exception:
            logger.exception("Failed to delete draft comment attachment key=%s", storage_path)

    async def get_comments(
        self, user_story_id: str, project_id: str, user_id: str, organization_id: str,
        page: int = 1, page_size: int = 10
    ) -> Tuple[List[CommentResponse], PaginationResponse]:
        logger.info("Service: Fetching comments for user_story_id=%s, page=%d", user_story_id, page)

        await self._story(user_story_id, project_id)

        has_perm = await self.check_permission(user_id, project_id, "comments", "view")
        if not has_perm:
            logger.warning("Service: Permission denied viewing comments for user_id=%s", user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value,
                "You do not have permission to view comments in this project",
            )

        offset = (max(1, page) - 1) * max(1, page_size)
        total = (
            await self.db.execute(
                select(func.count(Comments.id)).where(
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.parent_comment_id.is_(None),
                    Comments.deleted_at.is_(None),
                )
            )
        ).scalar_one() or 0

        comments = (
            await self.db.execute(
                select(Comments)
                .options(selectinload(Comments.user), selectinload(Comments.attachments))
                .where(
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.parent_comment_id.is_(None),
                    Comments.deleted_at.is_(None),
                )
                .order_by(Comments.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()

        reply_counts: dict[str, int] = {}
        if comments:
            rows = (
                await self.db.execute(
                    select(Comments.parent_comment_id, func.count(Comments.id))
                    .where(
                        Comments.parent_comment_id.in_([c.id for c in comments]),
                        Comments.is_deleted.is_(False),
                    )
                    .group_by(Comments.parent_comment_id)
                )
            ).all()
            reply_counts = {str(parent_id): count for parent_id, count in rows}

        responses = [
            CommentResponse(
                id=str(c.id),
                user_story_id=str(c.user_story_id),
                user_id=str(c.user_id),
                user_name=c.user.username if c.user else None,
                full_name=c.user.full_name if c.user else None,
                avatar_url=c.user.avatar_url if c.user else None,
                color=getattr(c.user, "color", None) if c.user else None,
                content=c.content,
                parent_comment_id=str(c.parent_comment_id) if c.parent_comment_id else None,
                created_at=c.created_at,
                updated_at=c.updated_at,
                is_deleted=c.is_deleted,
                replies_count=reply_counts.get(str(c.id), 0),
                attachments=[
                    {
                        "id": str(a.id),
                        "comment_id": str(a.comment_id) if a.comment_id else None,
                        "original_filename": a.original_filename,
                        "mime_type": a.mime_type,
                        "file_size": a.file_size,
                        "url": a.url,
                        "uploaded_by": str(a.uploaded_by),
                        "uploaded_at": a.uploaded_at,
                    }
                    for a in c.attachments
                ],
            )
            for c in comments
        ]

        logger.info("Service: Found %d comment(s) (total=%d) for user_story_id=%s", len(responses), total, user_story_id)

        return responses, self.pagination(page, page_size, total)

    async def get_comment_by_id(
        self, comment_id: str, user_story_id: str, project_id: str, user_id: str, organization_id: str
    ) -> CommentResponse:
        logger.info("Service: Fetching comment_id=%s for user_story_id=%s", comment_id, user_story_id)

        await self._story(user_story_id, project_id)

        comment = (
            await self.db.execute(
                select(Comments)
                .options(selectinload(Comments.user), selectinload(Comments.attachments))
                .where(
                    Comments.id == comment_id,
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not comment:
            logger.warning("Service: Comment not found comment_id=%s", comment_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Comment not found"
            )

        logger.info("Service: Successfully fetched comment_id=%s", comment_id)

        return CommentResponse(
            id=str(comment.id),
            user_story_id=str(comment.user_story_id),
            user_id=str(comment.user_id),
            user_name=comment.user.username if comment.user else None,
            full_name=comment.user.full_name if comment.user else None,
            avatar_url=comment.user.avatar_url if comment.user else None,
            color=getattr(comment.user, "color", None) if comment.user else None,
            content=comment.content,
            parent_comment_id=str(comment.parent_comment_id) if comment.parent_comment_id else None,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            is_deleted=comment.is_deleted,
            attachments=[
                {
                    "id": str(a.id),
                    "comment_id": str(a.comment_id) if a.comment_id else None,
                    "original_filename": a.original_filename,
                    "mime_type": a.mime_type,
                    "file_size": a.file_size,
                    "url": a.url,
                    "uploaded_by": str(a.uploaded_by),
                    "uploaded_at": a.uploaded_at,
                }
                for a in comment.attachments
            ],
        )

    async def get_comment_replies(
        self, parent_comment_id: str, user_story_id: str, project_id: str, user_id: str,
        organization_id: str, page: int = 1, page_size: int = 10
    ) -> Tuple[List[CommentResponse], PaginationResponse]:
        logger.info("Service: Fetching replies for parent_comment_id=%s, user_story_id=%s", parent_comment_id, user_story_id)

        await self._story(user_story_id, project_id)

        offset = (max(1, page) - 1) * max(1, page_size)
        total = (
            await self.db.execute(
                select(func.count(Comments.id)).where(
                    Comments.parent_comment_id == parent_comment_id,
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.deleted_at.is_(None),
                )
            )
        ).scalar_one() or 0

        comments = (
            await self.db.execute(
                select(Comments)
                .options(selectinload(Comments.user), selectinload(Comments.attachments))
                .where(
                    Comments.parent_comment_id == parent_comment_id,
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.deleted_at.is_(None),
                )
                .order_by(Comments.created_at.asc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()

        responses = [
            CommentResponse(
                id=str(c.id),
                user_story_id=str(c.user_story_id),
                user_id=str(c.user_id),
                user_name=c.user.username if c.user else None,
                full_name=c.user.full_name if c.user else None,
                avatar_url=c.user.avatar_url if c.user else None,
                color=getattr(c.user, "color", None) if c.user else None,
                content=c.content,
                parent_comment_id=str(c.parent_comment_id) if c.parent_comment_id else None,
                created_at=c.created_at,
                updated_at=c.updated_at,
                is_deleted=c.is_deleted,
                attachments=[
                    {
                        "id": str(a.id),
                        "comment_id": str(a.comment_id) if a.comment_id else None,
                        "original_filename": a.original_filename,
                        "mime_type": a.mime_type,
                        "file_size": a.file_size,
                        "url": a.url,
                        "uploaded_by": str(a.uploaded_by),
                        "uploaded_at": a.uploaded_at,
                    }
                    for a in c.attachments
                ],
            )
            for c in comments
        ]

        logger.info("Service: Found %d reply comment(s) (total=%d) for parent_comment_id=%s", len(responses), total, parent_comment_id)

        return responses, self.pagination(page, page_size, total)

    async def update_comment(
        self, req: UpdateCommentRequest, comment_id: str, user_story_id: str,
        project_id: str, user_id: str, organization_id: str
    ) -> CommentResponse:
        logger.info("Service: Updating comment_id=%s for user_story_id=%s by user_id=%s", comment_id, user_story_id, user_id)

        await self._story(user_story_id, project_id)

        comment = (
            await self.db.execute(
                select(Comments)
                .options(selectinload(Comments.user))
                .where(
                    Comments.id == comment_id,
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not comment:
            logger.warning("Service: Comment not found comment_id=%s", comment_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Comment not found"
            )

        if str(comment.user_id) != str(user_id):
            logger.warning("Service: User_id=%s attempted to update comment owned by user_id=%s", user_id, comment.user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value, "You can only update your own comments"
            )

        sanitized_content = _sanitize_html(req.content).strip()
        if not sanitized_content:
            raise UserStoryServiceError(
                400, ErrorCode.ErrValidation.value, "Content cannot be empty"
            )

        comment.content = sanitized_content
        comment.updated_at = datetime.now(timezone.utc)
        await self._commit()

        logger.info("Service: Successfully updated comment_id=%s", comment_id)

        return CommentResponse(
            id=str(comment.id),
            user_story_id=str(comment.user_story_id),
            user_id=str(comment.user_id),
            user_name=comment.user.username if comment.user else None,
            full_name=comment.user.full_name if comment.user else None,
            avatar_url=comment.user.avatar_url if comment.user else None,
            color=getattr(comment.user, "color", None) if comment.user else None,
            content=comment.content,
            parent_comment_id=str(comment.parent_comment_id) if comment.parent_comment_id else None,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            is_deleted=comment.is_deleted,
        )

    async def delete_comment(
        self, comment_id: str, user_story_id: str, project_id: str,
        user_id: str, organization_id: str
    ) -> None:
        logger.info("Service: Deleting comment_id=%s from user_story_id=%s by user_id=%s", comment_id, user_story_id, user_id)

        await self._story(user_story_id, project_id)

        comment = (
            await self.db.execute(
                select(Comments).where(
                    Comments.id == comment_id,
                    Comments.user_story_id == user_story_id,
                    Comments.project_id == project_id,
                    Comments.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not comment:
            logger.warning("Service: Comment not found comment_id=%s", comment_id)
            raise UserStoryServiceError(
                404, ErrorCode.ErrNotFound.value, "Comment not found"
            )

        if str(comment.user_id) != str(user_id):
            logger.warning("Service: User_id=%s attempted to delete comment owned by user_id=%s", user_id, comment.user_id)
            raise UserStoryServiceError(
                403, ErrorCode.ErrForbidden.value, "You can only delete your own comments"
            )

        comment.is_deleted = True
        comment.deleted_at = datetime.now(timezone.utc)
        await self._commit()

        logger.info("Service: Successfully soft-deleted comment_id=%s", comment_id)
