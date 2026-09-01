from __future__ import annotations

import asyncio
import io
import math
import ntpath
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from typing import Iterable, Sequence

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.audit.models import AuditLogType
from src.audit.service import AuditService
from src.auth.models import User
from src.custom_status.models import CustomStatus
from src.favorite.models import Favorite
from src.label.models import Label
from src.organization.models import OrphanedFile, Role
from src.project.models import Project, ProjectMember
from src.sprint.models import Sprint
from src.task.models import (
    DEFAULT_STATUS_COLORS,
    DEFAULT_STATUS_IS_FINAL,
    Task,
    TaskAttachment,
    normalize_task_status,
    task_labels,
)
from src.task.schema import (
    AttachmentResponse,
    BulkDeleteTasksResponse,
    BulkUpdateTaskItem,
    BulkUpdateTasksResponse,
    CloneTaskRequest,
    CreateTaskRequest,
    FavoriteResponse,
    LabelResponse,
    PaginationResponse,
    RemoveFavoriteResponse,
    TaskResponse,
    UpdateTaskRequest,
    UserSummary,
)
from src.user_story.models import UserStory
from src.user_story_status.models import UserStoryStatus
from src.utils.setting import get_settings


class TaskServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


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


def _role_name(name: str | None) -> str:
    aliases = {
        "member": "developer",
        "user": "developer",
        "tester": "qa",
        "viewer": "stakeholder",
    }
    normalized = (name or "").lower()
    return aliases.get(normalized, normalized)


def _has_default_permission(role: str | None, resource: str, action: str) -> bool:
    return f"{resource}:{action}" in DEFAULT_ROLE_PERMISSIONS.get(_role_name(role), set())


def _is_nil(value: object | None) -> bool:
    if value is None:
        return True
    try:
        return uuid.UUID(str(value)).int == 0
    except (ValueError, TypeError, AttributeError):
        return False


def _uuid_string(value: object | None) -> str | None:
    return None if value is None else str(value)


def _is_fibonacci(value: int) -> bool:
    if value < 0:
        return False
    if value in {0, 1}:
        return True
    left, right = 1, 1
    while right < value:
        left, right = right, left + right
    return right == value


def _is_backdated(value: datetime) -> bool:
    local_now = datetime.now().astimezone()
    if value.tzinfo is None:
        local_value = value.replace(tzinfo=local_now.tzinfo)
    else:
        local_value = value.astimezone(local_now.tzinfo)
    return local_value.date() < local_now.date()


def _project_prefix(name: str) -> str:
    name = name.strip()
    if not name:
        return "TASK"
    parts = [part for part in re.split(r"[\s_-]+", name) if part]
    prefix = "".join(part[0].upper() for part in parts) if len(parts) > 1 else name.upper()[:3]
    prefix = "".join(char for char in prefix if char.isascii() and char.isalnum())
    if len(prefix) < 2:
        prefix = "WP"
    return prefix[:10]


class _HTMLSanitizer(HTMLParser):
    allowed_tags = {
        "a", "abbr", "acronym", "b", "blockquote", "br", "code", "div",
        "em", "i", "img", "li", "ol", "p", "pre", "span", "strike",
        "strong", "s", "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
    }
    void_tags = {"br", "img"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.allowed_tags:
            return
        allowed: list[tuple[str, str]] = []
        for key, value in attrs:
            value = value or ""
            if tag == "a" and key in {"href", "title"}:
                if key != "href" or not value.lower().startswith(("javascript:", "data:")):
                    allowed.append((key, value))
            elif tag == "img" and key in {"src", "alt", "title"}:
                if key != "src" or not value.lower().startswith(("javascript:", "data:")):
                    allowed.append((key, value))
        if tag == "a":
            allowed.append(("rel", "nofollow"))
        rendered = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in allowed)
        self.parts.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.allowed_tags and tag not in self.void_tags:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data, quote=False))


def _sanitize_html(value: str) -> str:
    if not value:
        return ""
    parser = _HTMLSanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def pagination(page: int, page_size: int, total: int) -> PaginationResponse:
        total_pages = math.ceil(total / page_size) if total else 1
        return PaginationResponse(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        )

    async def _project(self, project_id: str) -> Project:
        project = (
            await self.db.execute(
                select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if project is None:
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Project not found")
        return project

    async def _user(self, user_id: str) -> User:
        user = (
            await self.db.execute(
                select(User)
                .where(User.id == user_id, User.deleted_at.is_(None))
                .options(selectinload(User.role).selectinload(Role.permissions))
            )
        ).scalar_one_or_none()
        if user is None:
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "User not found")
        return user

    @staticmethod
    def _role_allows(role: Role | None, resource: str, action: str) -> bool:
        if role is None:
            return False
        if any(p.resource == resource and p.action == action for p in role.permissions):
            return True
        return _has_default_permission(role.name, resource, action)

    async def _has_permission(
        self,
        project: Project,
        user: User,
        resource: str,
        action: str,
    ) -> bool:
        if _role_name(getattr(user.role, "name", None)) == "super_admin":
            return False

        member = (
            await self.db.execute(
                select(ProjectMember)
                .where(
                    ProjectMember.project_id == str(project.id),
                    ProjectMember.user_id == str(user.id),
                    ProjectMember.deleted_at.is_(None),
                )
                .options(selectinload(ProjectMember.role).selectinload(Role.permissions))
            )
        ).scalars().first()
        if member is not None and self._role_allows(member.role, resource, action):
            return True

        if (
            _role_name(getattr(user.role, "name", None)) == "org_admin"
            and user.organization_id == project.organization_id
        ):
            return self._role_allows(user.role, resource, action)
        return False

    async def _check_authorization(
        self,
        project_id: str,
        user_id: str,
        denied_message: str,
    ) -> tuple[Project, User]:
        project = await self._project(project_id)
        user = await self._user(user_id)
        if _role_name(getattr(user.role, "name", None)) == "super_admin":
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "Super admins are not allowed to perform organization-level activities",
            )
        if not await self._has_permission(project, user, "tasks", "view"):
            raise TaskServiceError(403, "FORBIDDEN", denied_message)
        return project, user

    async def _is_project_member(self, project: Project, user: User) -> bool:
        member_id = (
            await self.db.execute(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == str(project.id),
                    ProjectMember.user_id == str(user.id),
                    ProjectMember.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if member_id is not None:
            return True
        return (
            _role_name(getattr(user.role, "name", None)) == "org_admin"
            and user.organization_id == project.organization_id
        )

    @staticmethod
    def _task_options():
        return (
            selectinload(Task.project),
            selectinload(Task.sprint),
            selectinload(Task.user_story),
            selectinload(Task.assignee).selectinload(User.role),
            selectinload(Task.reporter).selectinload(User.role),
            selectinload(Task.labels),
        )

    async def _task(
        self,
        task_id: str,
        project_id: str | None = None,
        *,
        include_deleted: bool = False,
    ) -> Task:
        conditions = [Task.id == task_id]
        if project_id is not None:
            conditions.append(Task.project_id == project_id)
        if not include_deleted:
            conditions.append(Task.deleted_at.is_(None))
        task = (
            await self.db.execute(
                select(Task).where(*conditions).options(*self._task_options())
            )
        ).scalar_one_or_none()
        if task is None:
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Task not found")
        return task

    async def _statuses(self, project_id: str) -> list[CustomStatus]:
        statuses = list(
            (
                await self.db.execute(
                    select(CustomStatus)
                    .where(
                        CustomStatus.project_id == project_id,
                        CustomStatus.deleted_at.is_(None),
                    )
                    .order_by(CustomStatus.display_order.asc())
                )
            ).scalars()
        )
        if not statuses:
            await self._seed_default_statuses(project_id)
            statuses = list(
                (
                    await self.db.execute(
                        select(CustomStatus)
                        .where(
                            CustomStatus.project_id == project_id,
                            CustomStatus.deleted_at.is_(None),
                        )
                        .order_by(CustomStatus.display_order.asc())
                    )
                ).scalars()
            )
        return statuses

    async def _seed_default_statuses(self, project_id: str) -> None:
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

    async def _resolve_status(
        self,
        project_id: str,
        status_id: object | None,
        status_name: str | None,
        statuses: Sequence[CustomStatus] | None = None,
    ) -> tuple[str, str]:
        statuses = list(statuses) if statuses is not None else await self._statuses(project_id)
        if status_id is not None and not _is_nil(status_id):
            wanted = str(status_id)
            status = next((item for item in statuses if str(item.id) == wanted), None)
            if status is None:
                raise TaskServiceError(
                    422,
                    "VALIDATION_ERROR",
                    "Invalid task status_id: status does not exist or does not belong to this project",
                )
            return str(status.id), status.name

        if status_name:
            normalized = normalize_task_status(status_name)
            status = next(
                (item for item in statuses if normalize_task_status(item.name) == normalized),
                None,
            )
            if status is None:
                raise TaskServiceError(
                    422,
                    "VALIDATION_ERROR",
                    "Invalid task status value: status name not found in this project",
                )
            return str(status.id), status.name

        default = next((item for item in statuses if item.is_default), None)
        if default is None and statuses:
            default = statuses[0]
        if default is None:
            raise TaskServiceError(500, "INTERNAL_SERVER_ERROR", "Project has no defined statuses")
        return str(default.id), default.name

    @staticmethod
    def _status_maps(statuses: Sequence[CustomStatus]) -> tuple[dict[str, str], dict[str, bool]]:
        colors = dict(DEFAULT_STATUS_COLORS)
        finals = dict(DEFAULT_STATUS_IS_FINAL)
        for status in statuses:
            key = normalize_task_status(status.name)
            colors[key] = status.color
            finals[key] = bool(status.is_final)
        return colors, finals

    @staticmethod
    def _user_summary(user: User | None) -> UserSummary | None:
        if user is None:
            return None
        return UserSummary(
            id=str(user.id),
            full_name=user.full_name or "",
            email=user.email or "",
            avatar_url=user.avatar_url or None,
            color=user.color or "",
            role=getattr(user.role, "name", None) if user.role else None,
        )

    def _build_response(
        self,
        task: Task,
        colors: dict[str, str],
        finals: dict[str, bool],
        *,
        is_favourite: bool = False,
        include_relations: bool = True,
    ) -> TaskResponse:
        project = task.project if include_relations else None
        sprint = task.sprint if include_relations else None
        story = task.user_story if include_relations else None
        reporter_model = task.reporter if include_relations else None
        assignee_model = task.assignee if include_relations else None
        reporter = self._user_summary(reporter_model)
        assignee = self._user_summary(assignee_model)
        status_key = normalize_task_status(task.status or "")
        now = datetime.now(timezone.utc)
        labels = (
            [LabelResponse(id=str(label.id), name=label.name, color=label.color) for label in task.labels]
            if include_relations else []
        )
        return TaskResponse(
            id=str(task.id),
            project_id=str(task.project_id),
            project_name=project.name if project else "",
            sprint_id=_uuid_string(task.sprint_id),
            sprint_name=sprint.name if sprint else "",
            user_story_id=_uuid_string(task.user_story_id),
            user_story_title=story.title if story else "",
            key=task.key,
            serial_number=int(task.serial_number or 0),
            formatted_serial_number=task.formatted_serial_number,
            title=task.title,
            description=task.description or "",
            type=task.type,
            priority=task.priority,
            status_id=str(task.status_id),
            status=task.status or "",
            status_color=colors.get(status_key) or "#808080",
            is_final=finals.get(status_key, DEFAULT_STATUS_IS_FINAL.get(status_key, False)),
            is_favourite=is_favourite,
            assignee_id=_uuid_string(task.assignee_id),
            reporter_id=_uuid_string(task.reporter_id),
            reporter_name=reporter.full_name if reporter else "",
            assignee_name=assignee.full_name if assignee else "",
            story_points=int(task.story_points or 0),
            due_date=task.due_date,
            estimated_hours=task.estimated_hours,
            actual_hours=task.actual_hours,
            blocked_reason=task.blocked_reason or "",
            created_at=task.created_at or now,
            updated_at=task.updated_at or now,
            labels=labels,
            reporter=reporter,
            assignee=assignee,
        )

    async def _favorite_task_ids(self, user_id: str, task_ids: Iterable[str]) -> set[str]:
        ids = list(task_ids)
        if not ids:
            return set()
        rows = (
            await self.db.execute(
                select(Favorite.task_id).where(
                    Favorite.user_id == user_id,
                    Favorite.item_type == "task",
                    Favorite.task_id.in_(ids),
                    Favorite.deleted_at.is_(None),
                )
            )
        ).scalars()
        return {str(item) for item in rows if item is not None}

    async def _next_sequence(self, project_id: str) -> int:
        value = (
            await self.db.execute(
                select(func.coalesce(func.max(Task.sequence_number), 0)).where(
                    Task.project_id == project_id
                )
            )
        ).scalar_one()
        return int(value) + 1

    async def _next_global_serial(self) -> int:
        """Allocate a work-item serial without relying on the optional DB sequence.

        Older databases do not contain ``global_work_item_serial_seq``. Supplying
        the serial before SQLAlchemy's model hook runs avoids attempting that
        missing sequence (which would abort the PostgreSQL transaction).
        """
        max_task = (
            await self.db.execute(
                select(func.coalesce(func.max(Task.serial_number), 0))
            )
        ).scalar_one()
        max_story = (
            await self.db.execute(
                select(func.coalesce(func.max(UserStory.serial_number), 0))
            )
        ).scalar_one()
        return max(int(max_task), int(max_story)) + 1

    async def _verify_labels(self, project_id: str, label_ids: Sequence[object]) -> list[Label]:
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for value in label_ids:
            label_id = str(value)
            if label_id not in seen:
                seen.add(label_id)
                ordered_ids.append(label_id)
        if not ordered_ids:
            return []
        labels = list(
            (
                await self.db.execute(
                    select(Label).where(
                        Label.project_id == project_id,
                        Label.id.in_(ordered_ids),
                        Label.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        by_id = {str(label.id): label for label in labels}
        if any(label_id not in by_id for label_id in ordered_ids):
            raise TaskServiceError(
                400,
                "BAD_REQUEST",
                "One or more labels do not exist or do not belong to the project",
            )
        return [by_id[label_id] for label_id in ordered_ids]

    async def _validate_assignee(
        self,
        project: Project,
        assignee_id: object,
        organization_id: str,
    ) -> User:
        try:
            assignee = await self._user(str(assignee_id))
        except TaskServiceError as exc:
            if exc.status_code == 404:
                raise TaskServiceError(400, "BAD_REQUEST", "Assignee user not found") from exc
            raise
        if not assignee.is_active:
            raise TaskServiceError(400, "BAD_REQUEST", "Assignee must be an active user")
        if not await self._is_project_member(project, assignee):
            raise TaskServiceError(400, "BAD_REQUEST", "Assignee must be a member of the project")
        return assignee

    async def _validate_sprint(self, sprint_id: str, project_id: str | None = None) -> Sprint:
        conditions = [Sprint.id == sprint_id, Sprint.deleted_at.is_(None)]
        if project_id is not None:
            conditions.append(Sprint.project_id == project_id)
        sprint = (await self.db.execute(select(Sprint).where(*conditions))).scalar_one_or_none()
        if sprint is None:
            if project_id is None:
                raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Sprint not found")
            raise TaskServiceError(400, "BAD_REQUEST", "Sprint must belong to the project")
        return sprint

    async def _validate_story(self, story_id: str, project_id: str) -> None:
        exists = (
            await self.db.execute(
                select(UserStory.id).where(
                    UserStory.id == story_id,
                    UserStory.project_id == project_id,
                    UserStory.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise TaskServiceError(400, "BAD_REQUEST", "User story must belong to the same project")

    async def _recalculate_stories(self, story_ids: Iterable[str | None]) -> None:
        ids = {str(item) for item in story_ids if item}
        if not ids:
            return
        changed = False
        for story_id in ids:
            story = (
                await self.db.execute(
                    select(UserStory).where(
                        UserStory.id == story_id,
                        UserStory.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if story is None:
                continue
            status_ids = list(
                (
                    await self.db.execute(
                        select(Task.status_id).where(
                            Task.user_story_id == story_id,
                            Task.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )
            if status_ids:
                final_ids = set(
                    (
                        await self.db.execute(
                            select(CustomStatus.id).where(
                                CustomStatus.id.in_(status_ids),
                                CustomStatus.is_final.is_(True),
                                CustomStatus.deleted_at.is_(None),
                            )
                        )
                    ).scalars()
                )
                is_closed = all(status_id in final_ids for status_id in status_ids)
            else:
                story_status = (
                    await self.db.execute(
                        select(UserStoryStatus).where(
                            UserStoryStatus.id == story.status_id,
                            UserStoryStatus.deleted_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                is_closed = bool(story_status and (story_status.is_closed or story_status.is_final))
            if bool(story.is_closed) != is_closed:
                story.is_closed = is_closed
                changed = True
        if changed:
            await self.db.commit()

    async def _audit(
        self,
        *,
        user_id: str,
        organization_id: str,
        project_id: str,
        action: str,
        resource_type: str = "task",
        resource_id: str | None = None,
        task_id: str | None = None,
        details: str = "",
        audit_type: str = AuditLogType.ACTIVITY,
    ) -> None:
        await AuditService(self.db).create_audit_log(
            action=action,
            resource_type=resource_type,
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            resource_id=resource_id,
            details=details,
            audit_type=audit_type,
        )

    # ------------------------------------------------------------------
    # CRUD and list
    # ------------------------------------------------------------------

    async def create(
        self,
        project_id: str,
        body: CreateTaskRequest,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> str:
        await self.db.rollback()
        project, actor = await self._check_authorization(
            project_id, user_id, "You do not have permission to view tasks in this project"
        )
        if not await self._has_permission(project, actor, "tasks", "add"):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to create tasks in this project"
            )
        if not 3 <= len(body.title) <= 200:
            raise TaskServiceError(
                400, "VALIDATION_ERROR", "Task title must be between 3 and 200 characters"
            )
        if not _is_fibonacci(body.story_points):
            raise TaskServiceError(
                400, "VALIDATION_ERROR", "Story points must follow the Fibonacci scale"
            )
        if body.assignee_id is not None and not _is_nil(body.assignee_id):
            await self._validate_assignee(project, body.assignee_id, organization_id)
        if body.due_date is not None and _is_backdated(body.due_date):
            if not await self._has_permission(project, actor, "projects", "modify"):
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    "Due date cannot be backdated unless set by a PM or Admin",
                )
        if body.sprint_id is not None and not _is_nil(body.sprint_id):
            sprint = await self._validate_sprint(str(body.sprint_id))
            if sprint.status == "completed":
                raise TaskServiceError(
                    400, "VALIDATION_ERROR", "Cannot assign a task to a completed sprint"
                )
        if body.user_story_id is not None and not _is_nil(body.user_story_id):
            await self._validate_story(str(body.user_story_id), project_id)

        statuses = await self._statuses(project_id)
        status_id, status_name = await self._resolve_status(
            project_id,
            body.status_id,
            body.status or None,
            statuses,
        )
        labels = await self._verify_labels(project_id, body.label_ids)
        project_name = project.name
        sequence = await self._next_sequence(project_id)
        serial_number = await self._next_global_serial()
        task = Task(
            project_id=project_id,
            sprint_id=_uuid_string(body.sprint_id),
            user_story_id=_uuid_string(body.user_story_id),
            key=f"{_project_prefix(project_name)}-{sequence}",
            sequence_number=sequence,
            serial_number=serial_number,
            title=body.title,
            description=_sanitize_html(body.description),
            type=body.type,
            priority=body.priority,
            status_id=status_id,
            status=status_name,
            assignee_id=_uuid_string(body.assignee_id),
            reporter_id=user_id,
            story_points=body.story_points,
            due_date=body.due_date,
            estimated_hours=body.estimated_hours,
            actual_hours=body.actual_hours,
            labels=labels,
        )
        try:
            self.db.add(task)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise TaskServiceError(409, "CONFLICT", "Task key already exists") from exc
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(500, "INTERNAL_SERVER_ERROR", "Failed to create task") from exc

        await self._recalculate_stories([task.user_story_id])
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=str(task.id),
            resource_id=str(task.id),
            action="created",
            details=f"The task '{task.title}' was created by {actor.username}",
        )
        return str(task.id)

    async def list(
        self,
        project_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
        *,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "created_at",
        sort_order: str = "DESC",
        status_id: Sequence[str] | None = None,
        assignee_id: Sequence[str] | None = None,
        reporter_id: Sequence[str] | None = None,
        sprint_id: Sequence[str] | None = None,
        user_story_id: Sequence[str] | None = None,
        type: Sequence[str] | None = None,
        priority: Sequence[str] | None = None,
        search: str = "",
        labels: Sequence[str] | None = None,
        is_deleted: bool = False,
        unassigned_task: bool = False,
        match: str = "",
        sequence_number: int | None = None,
        serial_number: int | None = None,
    ) -> tuple[list[TaskResponse], PaginationResponse]:
        await self._check_authorization(
            project_id, user_id, "You do not have permission to view tasks in this project"
        )
        page = page if page >= 1 else 1
        page_size = page_size if page_size >= 1 else 10
        sort_by = sort_by.strip() or "created_at"
        order_value = sort_order.strip().upper()
        sort_order = order_value if order_value in {"ASC", "DESC"} else "DESC"

        conditions = [Task.project_id == project_id]
        conditions.append(Task.deleted_at.isnot(None) if is_deleted else Task.deleted_at.is_(None))

        statuses = await self._statuses(project_id)
        if status_id:
            by_id = {str(item.id): item for item in statuses}
            by_name = {normalize_task_status(item.name): item for item in statuses}
            resolved: list[str] = []
            for raw in status_id:
                try:
                    parsed = str(uuid.UUID(raw))
                    status = by_id.get(parsed)
                    if status is None:
                        raise TaskServiceError(
                            422,
                            "VALIDATION_ERROR",
                            "Invalid task status_id: status does not exist or does not belong to this project",
                        )
                except ValueError:
                    status = by_name.get(normalize_task_status(raw))
                    if status is None:
                        raise TaskServiceError(
                            422,
                            "VALIDATION_ERROR",
                            "Invalid task status value: status name not found in this project",
                        )
                value = str(status.id)
                if value not in resolved:
                    resolved.append(value)
            conditions.append(Task.status_id.in_(resolved))

        def nullable_ids(values: Sequence[str] | None, column):
            values = values or []
            has_null = any(value in {"none", "null"} for value in values)
            valid: list[str] = []
            for value in values:
                try:
                    valid.append(str(uuid.UUID(value)))
                except ValueError:
                    pass
            if has_null and valid:
                return or_(column.is_(None), column.in_(valid))
            if has_null:
                return column.is_(None)
            if valid:
                return column.in_(valid)
            return None

        for values, column in (
            (assignee_id, Task.assignee_id),
            (reporter_id, Task.reporter_id),
            (sprint_id, Task.sprint_id),
            (user_story_id, Task.user_story_id),
        ):
            condition = nullable_ids(values, column)
            if condition is not None:
                conditions.append(condition)
        if unassigned_task:
            conditions.extend((Task.sprint_id.is_(None), Task.user_story_id.is_(None)))
        if type:
            conditions.append(Task.type.in_(list(type)))
        if priority:
            conditions.append(Task.priority.in_(list(priority)))
        if sequence_number is not None:
            conditions.append(
                or_(
                    Task.sequence_number == sequence_number,
                    Task.serial_number == sequence_number,
                )
            )
        if serial_number is not None:
            conditions.append(Task.serial_number == serial_number)
        if search:
            cleaned = search.strip().removeprefix("#")
            conditions.append(
                or_(
                    Task.title.ilike(f"%{search}%"),
                    Task.description.ilike(f"%{search}%"),
                    Task.key.ilike(f"%{search}%"),
                    cast(Task.serial_number, String).ilike(f"%{cleaned}%"),
                )
            )

        if labels:
            unique_items = list(dict.fromkeys(value.lower() for value in labels))
            label_uuid_values: list[str] = []
            label_names: list[str] = []
            for value in labels:
                try:
                    label_uuid_values.append(str(uuid.UUID(value)))
                except ValueError:
                    label_names.append(value.lower())
            label_conditions = []
            if label_uuid_values:
                label_conditions.append(Label.id.in_(label_uuid_values))
            if label_names:
                label_conditions.append(func.lower(Label.name).in_(label_names))
            resolved_ids = list(
                (
                    await self.db.execute(
                        select(Label.id).where(
                            Label.project_id == project_id,
                            Label.deleted_at.is_(None),
                            or_(*label_conditions) if label_conditions else False,
                        )
                    )
                ).scalars()
            )
            if not resolved_ids or (
                match.lower() == "all" and len(set(resolved_ids)) < len(unique_items)
            ):
                conditions.append(False)
            elif match.lower() == "all":
                subquery = (
                    select(task_labels.c.task_id)
                    .where(task_labels.c.label_id.in_(resolved_ids))
                    .group_by(task_labels.c.task_id)
                    .having(func.count(func.distinct(task_labels.c.label_id)) == len(set(resolved_ids)))
                )
                conditions.append(Task.id.in_(subquery))
            else:
                conditions.append(
                    Task.id.in_(
                        select(task_labels.c.task_id).where(task_labels.c.label_id.in_(resolved_ids))
                    )
                )

        total = int(
            (await self.db.execute(select(func.count(Task.id)).where(*conditions))).scalar_one()
        )
        columns = {
            "title": Task.title,
            "created_at": Task.created_at,
            "updated_at": Task.updated_at,
            "priority": Task.priority,
            "status": Task.status,
            "serial_number": Task.serial_number,
        }
        sort_column = columns.get(sort_by, Task.created_at)
        order = sort_column.desc() if sort_order == "DESC" else sort_column.asc()
        tasks = list(
            (
                await self.db.execute(
                    select(Task)
                    .where(*conditions)
                    .options(*self._task_options())
                    .order_by(order)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars()
        )
        colors, finals = self._status_maps(statuses)
        favorite_ids = await self._favorite_task_ids(user_id, (str(task.id) for task in tasks))
        responses = [
            self._build_response(
                task,
                colors,
                finals,
                is_favourite=str(task.id) in favorite_ids,
            )
            for task in tasks
        ]
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="viewed",
            resource_id=project_id,
            details="tasks viewed",
            audit_type=AuditLogType.AUDIT,
        )
        return responses, self.pagination(page, page_size, total)

    async def get(
        self,
        project_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> TaskResponse:
        _, actor = await self._check_authorization(
            project_id, user_id, "You do not have permission to view tasks in this project"
        )
        task = await self._task(task_id, project_id)
        statuses = await self._statuses(project_id)
        colors, finals = self._status_maps(statuses)
        favorite = bool(await self._favorite_task_ids(user_id, [task_id]))
        response = self._build_response(task, colors, finals, is_favourite=favorite)
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            resource_id=task_id,
            action="viewed",
            details=f"The task '{task.title}' was viewed by {actor.username}",
            audit_type=AuditLogType.VIEW,
        )
        return response

    async def update(
        self,
        project_id: str,
        task_id: str,
        body: UpdateTaskRequest,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> str:
        project, actor = await self._check_authorization(
            project_id, user_id, "You do not have permission to view tasks in this project"
        )
        if not await self._has_permission(project, actor, "tasks", "modify"):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to update tasks in this project"
            )
        is_pm_or_admin = await self._has_permission(project, actor, "projects", "modify")
        task = await self._task(task_id, project_id)
        original_story_id = task.user_story_id

        if body.title is not None and not 3 <= len(body.title) <= 200:
            raise TaskServiceError(
                400, "VALIDATION_ERROR", "Task title must be between 3 and 200 characters"
            )
        if body.story_points is not None and not _is_fibonacci(body.story_points):
            raise TaskServiceError(
                400, "VALIDATION_ERROR", "Story points must follow the Fibonacci scale"
            )
        if (
            body.explicitly_set("assignee_id")
            and body.assignee_id is not None
            and not _is_nil(body.assignee_id)
        ):
            await self._validate_assignee(project, body.assignee_id, organization_id)
        if (
            body.explicitly_set("user_story_id")
            and body.user_story_id is not None
            and not _is_nil(body.user_story_id)
        ):
            await self._validate_story(str(body.user_story_id), project_id)
        if (
            body.explicitly_set("due_date")
            and body.due_date is not None
            and _is_backdated(body.due_date)
            and not is_pm_or_admin
        ):
            raise TaskServiceError(
                400,
                "VALIDATION_ERROR",
                "Due date cannot be backdated unless set by a PM or Admin",
            )

        if body.actual_hours is not None:
            status_changing_to_active = body.status is not None and body.status != "completed"
            if task.status == "completed" and not status_changing_to_active:
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    "Actual hours can only be updated while the task is not completed",
                )
            if not is_pm_or_admin:
                if task.assignee_id != user_id:
                    raise TaskServiceError(
                        403,
                        "FORBIDDEN",
                        "Only the task assignee or a PM/Admin can update actual hours",
                    )
                if task.actual_hours is not None and body.actual_hours <= task.actual_hours:
                    raise TaskServiceError(400, "BAD_REQUEST", "Actual hours can only be incremented")
                if task.actual_hours is None and body.actual_hours <= 0:
                    raise TaskServiceError(400, "BAD_REQUEST", "Actual hours must be greater than 0")
            elif body.actual_hours < 0:
                raise TaskServiceError(400, "BAD_REQUEST", "Actual hours cannot be negative")

        sprint_changing = False
        target_sprint_id: str | None = task.sprint_id
        if body.explicitly_set("sprint_id"):
            target_sprint_id = (
                None if body.sprint_id is None or _is_nil(body.sprint_id) else str(body.sprint_id)
            )
            sprint_changing = target_sprint_id != task.sprint_id
        if sprint_changing:
            if task.sprint is not None and task.sprint.status == "completed":
                raise TaskServiceError(
                    400,
                    "VALIDATION_ERROR",
                    "Changing the sprint of a task in a completed sprint is blocked",
                )
            if target_sprint_id is not None:
                sprint = await self._validate_sprint(target_sprint_id, project_id)
                if sprint.status == "completed":
                    raise TaskServiceError(
                        400, "VALIDATION_ERROR", "Cannot assign a task to a completed sprint"
                    )

        statuses = await self._statuses(project_id)
        status_changing = False
        new_status_id = str(task.status_id)
        new_status_name = task.status
        if body.status_id is not None and str(body.status_id) != str(task.status_id):
            status_changing = True
        elif body.status is not None and body.status != task.status:
            status_changing = True
        if status_changing:
            new_status_id, new_status_name = await self._resolve_status(
                project_id, body.status_id, body.status, statuses
            )
            status_changing = new_status_id != str(task.status_id)
        if status_changing and normalize_task_status(new_status_name) == "blocked":
            if body.blocked_reason is None or not body.blocked_reason.strip():
                raise TaskServiceError(
                    400, "BAD_REQUEST", "Moving to Blocked requires a blocked reason"
                )
        if status_changing and not is_pm_or_admin:
            old_status = normalize_task_status(task.status)
            new_status = normalize_task_status(new_status_name)
            transition_map = {
                "todo": {"in_progress", "blocked"},
                "in_progress": {"in_review", "blocked"},
                "in_review": {"testing", "blocked"},
                "testing": {"completed", "blocked"},
                "completed": {"blocked"},
                "blocked": {"in_progress", "todo"},
            }
            both_default = old_status in DEFAULT_STATUS_COLORS and new_status in DEFAULT_STATUS_COLORS
            if both_default and new_status not in transition_map.get(old_status, set()):
                raise TaskServiceError(
                    400,
                    "INVALID_STATUS_TRANSITION",
                    f"Invalid status transition from {old_status} to {new_status} for developers",
                )

        changes: list[str] = []
        if body.title is not None and body.title != task.title:
            changes.append(f"title changed from '{task.title}' to '{body.title}'")
            task.title = body.title
        if body.description is not None and body.description != task.description:
            changes.append("description changed")
            task.description = _sanitize_html(body.description)
        if body.type is not None and body.type != task.type:
            changes.append(f"type changed from '{task.type}' to '{body.type}'")
            task.type = body.type
        if body.priority is not None and body.priority != task.priority:
            changes.append(f"priority changed from '{task.priority}' to '{body.priority}'")
            task.priority = body.priority
        if status_changing:
            changes.append(f"status changed from '{task.status}' to '{new_status_name}'")
            task.status_id = new_status_id
            task.status = new_status_name
            task.blocked_reason = (
                body.blocked_reason
                if normalize_task_status(new_status_name) == "blocked"
                else ""
            )
        if body.explicitly_set("assignee_id"):
            target = None if body.assignee_id is None or _is_nil(body.assignee_id) else str(body.assignee_id)
            if target != task.assignee_id:
                changes.append(f"assignee changed from {task.assignee_id or 'nil'} to {target or 'nil'}")
                task.assignee_id = target
        if sprint_changing:
            changes.append(f"sprint changed from {task.sprint_id or 'nil'} to {target_sprint_id or 'nil'}")
            task.sprint_id = target_sprint_id
        if body.explicitly_set("user_story_id"):
            target_story = (
                None
                if body.user_story_id is None or _is_nil(body.user_story_id)
                else str(body.user_story_id)
            )
            if target_story != task.user_story_id:
                changes.append(
                    f"user story changed from {task.user_story_id or 'nil'} to {target_story or 'nil'}"
                )
                task.user_story_id = target_story
        if body.story_points is not None and body.story_points != task.story_points:
            changes.append(f"story points changed from {task.story_points} to {body.story_points}")
            task.story_points = body.story_points
        if body.explicitly_set("due_date"):
            target_due = body.due_date
            if target_due != task.due_date:
                changes.append("due date changed")
                task.due_date = target_due
        if body.explicitly_set("estimated_hours"):
            if body.estimated_hours != task.estimated_hours:
                changes.append("estimated hours changed")
                task.estimated_hours = body.estimated_hours
        if body.explicitly_set("actual_hours"):
            if body.actual_hours != task.actual_hours:
                changes.append("actual hours changed")
                task.actual_hours = body.actual_hours
        if body.reporter_id is not None:
            task.reporter_id = str(body.reporter_id)

        labels_supplied = body.label_ids is not None
        if labels_supplied:
            labels = await self._verify_labels(project_id, body.label_ids or [])
            old_ids = {str(label.id) for label in task.labels}
            new_ids = {str(label.id) for label in labels}
            if old_ids != new_ids:
                changes.append("labels changed")
            task.labels = labels

        if not changes and not labels_supplied and body.reporter_id is None:
            return str(task.id)
        task.updated_at = datetime.now(timezone.utc)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise TaskServiceError(409, "CONFLICT", "Task key already exists") from exc
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(500, "INTERNAL_SERVER_ERROR", "Failed to update task") from exc

        await self._recalculate_stories([original_story_id, task.user_story_id])
        changed_by = actor.username or actor.full_name or actor.email or user_id
        details = (
            f"Task '{task.title}' updated by {changed_by}: {', '.join(changes)}"
            if changes
            else f"Task '{task.title}' details updated by {changed_by}"
        )
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            resource_id=task_id,
            action="updated",
            details=details,
        )
        return str(task.id)

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def bulk_update(
        self,
        project_id: str,
        items: Sequence[BulkUpdateTaskItem],
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> BulkUpdateTasksResponse:
        project = await self._project(project_id)
        actor = await self._user(user_id)
        if not await self._has_permission(project, actor, "projects", "modify"):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "You do not have permission to bulk update tasks in this project",
            )
        statuses = await self._statuses(project_id)
        updated_count = 0
        failed_ids: list[str] = []
        reasons: dict[str, str] = {}
        affected_stories: set[str] = set()

        for item in items:
            item_id = str(item.task_id)
            try:
                task = await self._task(item_id, project_id)
                if item.assignee_id is not None and not _is_nil(item.assignee_id):
                    try:
                        await self._validate_assignee(project, item.assignee_id, organization_id)
                    except TaskServiceError as exc:
                        if exc.message not in {
                            "Assignee user not found",
                            "Assignee must be an active user",
                            "Assignee must be a member of the project",
                        }:
                            raise TaskServiceError(
                                400, "BAD_REQUEST", "Failed to validate assignee membership"
                            ) from exc
                        raise

                target_sprint = task.sprint_id
                sprint_changing = item.sprint_id is not None and (
                    None if _is_nil(item.sprint_id) else str(item.sprint_id)
                ) != task.sprint_id
                if sprint_changing:
                    target_sprint = None if _is_nil(item.sprint_id) else str(item.sprint_id)
                    if task.sprint is not None and task.sprint.status == "completed":
                        raise TaskServiceError(
                            400,
                            "VALIDATION_ERROR",
                            "Changing the sprint of a task in a completed sprint is blocked",
                        )
                    if target_sprint is not None:
                        sprint = await self._validate_sprint(target_sprint, project_id)
                        if sprint.status == "completed":
                            raise TaskServiceError(
                                400,
                                "VALIDATION_ERROR",
                                "Cannot assign a task to a completed sprint",
                            )

                status_changing = False
                new_status_id = str(task.status_id)
                new_status_name = task.status
                if item.status_id is not None and str(item.status_id) != str(task.status_id):
                    status_changing = True
                elif item.status is not None and item.status != task.status:
                    status_changing = True
                if status_changing:
                    new_status_id, new_status_name = await self._resolve_status(
                        project_id, item.status_id, item.status, statuses
                    )
                    status_changing = new_status_id != str(task.status_id)
                if status_changing and normalize_task_status(new_status_name) == "blocked":
                    if item.blocked_reason is None or not item.blocked_reason.strip():
                        raise TaskServiceError(
                            400, "BAD_REQUEST", "Moving to Blocked requires a blocked reason"
                        )

                changes: list[str] = []
                if status_changing:
                    changes.append(f"status changed from '{task.status}' to '{new_status_name}'")
                    task.status_id = new_status_id
                    task.status = new_status_name
                    task.blocked_reason = (
                        item.blocked_reason
                        if normalize_task_status(new_status_name) == "blocked"
                        else ""
                    )
                if item.assignee_id is not None:
                    target = None if _is_nil(item.assignee_id) else str(item.assignee_id)
                    if target != task.assignee_id:
                        changes.append("assignee changed")
                        task.assignee_id = target
                if sprint_changing:
                    changes.append("sprint changed")
                    task.sprint_id = target_sprint
                if changes:
                    task.updated_at = datetime.now(timezone.utc)
                    await self.db.commit()
                    await self._audit(
                        user_id=user_id,
                        organization_id=organization_id,
                        project_id=project_id,
                        task_id=item_id,
                        resource_id=item_id,
                        action="updated",
                        details=f"Task {task.key} updated in bulk: {', '.join(changes)}",
                        audit_type=AuditLogType.AUDIT,
                    )
                if task.user_story_id:
                    affected_stories.add(task.user_story_id)
                updated_count += 1
            except TaskServiceError as exc:
                await self.db.rollback()
                failed_ids.append(item_id)
                reasons[item_id] = exc.message
            except SQLAlchemyError:
                await self.db.rollback()
                failed_ids.append(item_id)
                reasons[item_id] = "Failed to update task"

        await self._recalculate_stories(affected_stories)
        details = (
            f"Bulk update completed. Successfully updated {updated_count} tasks. "
            f"Failed tasks: {len(failed_ids)}."
            if updated_count
            else f"Bulk update executed but 0 tasks were updated. Failed tasks: {len(failed_ids)}."
        )
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="bulk_updated",
            resource_id=project_id,
            details=details,
            audit_type=AuditLogType.AUDIT,
        )
        return BulkUpdateTasksResponse(
            updated_count=updated_count,
            failed_task_ids=failed_ids,
            failure_reasons=reasons,
        )

    async def bulk_delete(
        self,
        project_id: str,
        task_ids: Sequence[str],
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> BulkDeleteTasksResponse:
        project, actor = await self._check_authorization(
            project_id, user_id, "You do not have permission to view tasks in this project"
        )
        if not await self._has_permission(project, actor, "tasks", "delete"):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to delete tasks in this project"
            )
        deleted_ids: list[str] = []
        failed_ids: list[str] = []
        reasons: dict[str, str] = {}
        affected_stories: set[str] = set()
        for task_id in task_ids:
            try:
                task = await self._task(task_id, project_id, include_deleted=True)
                if task.deleted_at is not None:
                    raise TaskServiceError(400, "BAD_REQUEST", "Task is already deleted")
                task.deleted_at = datetime.now(timezone.utc)
                task.updated_at = datetime.now(timezone.utc)
                await self.db.commit()
                deleted_ids.append(task_id)
                if task.user_story_id:
                    affected_stories.add(task.user_story_id)
                await self._audit(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    task_id=task_id,
                    resource_id=task_id,
                    action="deleted",
                    details=f"Task {task.key} soft deleted in bulk",
                    audit_type=AuditLogType.AUDIT,
                )
            except TaskServiceError as exc:
                await self.db.rollback()
                failed_ids.append(task_id)
                reasons[task_id] = exc.message
            except SQLAlchemyError:
                await self.db.rollback()
                failed_ids.append(task_id)
                reasons[task_id] = "Failed to delete task"
        await self._recalculate_stories(affected_stories)
        details = (
            f"Bulk deletion completed. Successfully deleted {len(deleted_ids)} tasks. "
            f"Failed tasks: {len(failed_ids)}."
            if deleted_ids
            else f"Bulk deletion executed but 0 tasks were deleted. Failed tasks: {len(failed_ids)}."
        )
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="bulk_deleted",
            resource_id=project_id,
            details=details,
            audit_type=AuditLogType.AUDIT,
        )
        return BulkDeleteTasksResponse(
            deleted_count=len(deleted_ids),
            deleted_task_ids=deleted_ids,
            failed_task_ids=failed_ids,
            failure_reasons=reasons,
        )

    # ------------------------------------------------------------------
    # Restore, clone, assignment, and labels
    # ------------------------------------------------------------------

    async def restore(
        self,
        project_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> None:
        _, actor = await self._check_authorization(
            project_id, user_id, "You do not have permission to restore tasks in this project"
        )
        try:
            task = await self._task(task_id, project_id, include_deleted=True)
        except TaskServiceError as exc:
            if exc.status_code == 404:
                raise TaskServiceError(
                    410,
                    "TASK_PERMANENTLY_DELETED",
                    "Task is permanently deleted and cannot be restored",
                ) from exc
            raise
        if task.deleted_at is not None:
            deleted_at = task.deleted_at
            if deleted_at.tzinfo is None:
                deleted_at = deleted_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - deleted_at > timedelta(days=30):
                raise TaskServiceError(
                    410,
                    "TASK_PERMANENTLY_DELETED",
                    "Task is permanently deleted and cannot be restored",
                )
        task.deleted_at = None
        task.updated_at = datetime.now(timezone.utc)
        try:
            await self.db.commit()
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(500, "INTERNAL_SERVER_ERROR", "Failed to restore task") from exc
        await self._recalculate_stories([task.user_story_id])
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            resource_id=task_id,
            action="restored",
            details=f"Task '{task.title}' restored by {actor.username}",
        )

    async def clone(
        self,
        project_id: str,
        task_id: str,
        body: CloneTaskRequest,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> TaskResponse:
        project, actor = await self._check_authorization(
            project_id, user_id, "You do not have permission to view tasks in this project"
        )
        if not await self._has_permission(project, actor, "tasks", "add"):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to clone tasks in this project"
            )
        original = await self._task(task_id, project_id, include_deleted=True)
        statuses = await self._statuses(project_id)
        try:
            status_id, status_name = await self._resolve_status(
                project_id, None, "todo", statuses
            )
        except TaskServiceError:
            status_id, status_name = await self._resolve_status(
                project_id, None, None, statuses
            )
        sequence = await self._next_sequence(project_id)
        serial_number = await self._next_global_serial()
        title = f"{original.title} (Cloned)"[:200]
        cloned = Task(
            project_id=project_id,
            sprint_id=original.sprint_id,
            user_story_id=original.user_story_id,
            key=f"{_project_prefix(project.name)}-{sequence}",
            sequence_number=sequence,
            serial_number=serial_number,
            title=title,
            description=original.description,
            type=original.type,
            priority=original.priority,
            status_id=status_id,
            status=status_name,
            assignee_id=original.assignee_id if body.keep_assignee else None,
            reporter_id=original.reporter_id,
            story_points=original.story_points,
            due_date=original.due_date,
            estimated_hours=original.estimated_hours,
            actual_hours=original.actual_hours,
        )
        try:
            self.db.add(cloned)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise TaskServiceError(409, "CONFLICT", "Task key already exists") from exc
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(500, "INTERNAL_SERVER_ERROR", "Failed to create task") from exc
        await self._recalculate_stories([cloned.user_story_id])
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=str(cloned.id),
            resource_id=str(cloned.id),
            action="cloned",
            details=(
                f"Task '{cloned.title}'-'{cloned.key}' cloned from "
                f"'{original.title}'-'{original.key}' by {actor.username}"
            ),
        )
        colors, finals = self._status_maps(statuses)
        return self._build_response(
            cloned, colors, finals, include_relations=False
        )

    async def assign_to_me(
        self,
        project_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> TaskResponse:
        task = await self._task(task_id)
        if task.project_id != project_id:
            raise TaskServiceError(
                403, "FORBIDDEN", "Task does not belong to the specified project"
            )
        project, actor = await self._check_authorization(
            project_id, user_id, "You do not have permission to view tasks in this project"
        )
        if not await self._has_permission(project, actor, "tasks", "modify"):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to update tasks in this project"
            )
        if not actor.is_active or not await self._is_project_member(project, actor):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "You must be an active project member to assign tasks to yourself",
            )
        if task.assignee_id != user_id:
            old_assignee = task.assignee_id or "nil"
            task.assignee_id = user_id
            task.updated_at = datetime.now(timezone.utc)
            try:
                await self.db.commit()
            except SQLAlchemyError as exc:
                await self.db.rollback()
                raise TaskServiceError(500, "INTERNAL_SERVER_ERROR", "Failed to update task") from exc
            task = await self._task(task_id, project_id)
            changed_by = actor.username or actor.full_name or actor.email or user_id
            await self._audit(
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                task_id=task_id,
                resource_id=task_id,
                action="updated",
                details=(
                    f"Task '{task.title}' updated by {changed_by}: assignee changed "
                    f"from {old_assignee} to {user_id}"
                ),
            )
        statuses = await self._statuses(project_id)
        colors, finals = self._status_maps(statuses)
        return self._build_response(task, colors, finals)

    async def _label(self, project_id: str, label_id: str) -> Label:
        labels = await self._verify_labels(project_id, [label_id])
        return labels[0]

    async def attach_label(
        self,
        project_id: str,
        task_id: str,
        label_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> None:
        project = await self._project(project_id)
        actor = await self._user(user_id)
        if not await self._has_permission(project, actor, "tasks", "modify"):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "You do not have permission to modify task labels in this project",
            )
        task = await self._task(task_id, project_id)
        label = await self._label(project_id, label_id)
        if label_id in {str(item.id) for item in task.labels}:
            return
        task.labels.append(label)
        try:
            await self.db.commit()
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to append task label association"
            ) from exc
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            resource_id=task_id,
            action="updated",
            resource_type="label",
            details=f"Task {task.key} updated: labels changed (attached '{label.name}')",
        )

    async def remove_label(
        self,
        project_id: str,
        task_id: str,
        label_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> str:
        project = await self._project(project_id)
        actor = await self._user(user_id)
        if not await self._has_permission(project, actor, "tasks", "modify"):
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "You do not have permission to modify task labels in this project",
            )
        task = await self._task(task_id, project_id)
        label = await self._label(project_id, label_id)
        attached = next((item for item in task.labels if str(item.id) == label_id), None)
        if attached is None:
            return label_id
        task.labels.remove(attached)
        try:
            await self.db.commit()
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to delete task label association"
            ) from exc
        await self._audit(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            task_id=task_id,
            resource_id=task_id,
            action="removed",
            resource_type="label",
            details=f"Task {task.key} updated: labels changed (removed '{label.name}')",
        )
        return label_id

    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------

    async def favorite(
        self,
        project_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> FavoriteResponse:
        task = await self._task(task_id, project_id)
        existing = (
            await self.db.execute(
                select(Favorite.id).where(
                    Favorite.user_id == user_id,
                    Favorite.item_type == "task",
                    Favorite.task_id == task_id,
                    Favorite.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise TaskServiceError(409, "CONFLICT", "Item is already added to favorites")
        favorite = Favorite(
            user_id=user_id,
            item_type="task",
            task_id=task_id,
        )
        try:
            self.db.add(favorite)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise TaskServiceError(409, "CONFLICT", "Item is already added to favorites") from exc
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to add item to favorites"
            ) from exc
        statuses = await self._statuses(project_id)
        colors, finals = self._status_maps(statuses)
        task_response = self._build_response(task, colors, finals)
        story_title = task_response.user_story_title
        return FavoriteResponse(
            id=str(favorite.id),
            user_id=user_id,
            item_type="task",
            task_id=task_id,
            project_id=project_id,
            project_name=task_response.project_name,
            user_story_name=story_title,
            user_story_title=story_title,
            task_name=task.title,
            task_title=task.title,
            task=task_response,
            created_at=favorite.created_at,
        )

    async def unfavorite(
        self,
        project_id: str,
        task_id: str,
        user_id: str,
        organization_id: str,
        role: str = "",
    ) -> RemoveFavoriteResponse:
        await self._task(task_id, project_id)
        favorite = (
            await self.db.execute(
                select(Favorite).where(
                    Favorite.user_id == user_id,
                    Favorite.item_type == "task",
                    Favorite.task_id == task_id,
                    Favorite.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if favorite is None:
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Favorite record not found")
        favorite.deleted_at = datetime.now(timezone.utc)
        try:
            await self.db.commit()
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to remove item from favorites"
            ) from exc
        return RemoveFavoriteResponse(id=str(favorite.id))

    # ------------------------------------------------------------------
    # Task attachments
    # ------------------------------------------------------------------

    @staticmethod
    def attachment_limits() -> tuple[int, int]:
        settings = get_settings()
        return settings.s3_max_file_size_mb, settings.attachment_max_files_count

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        base = ntpath.basename(filename.replace("/", "\\"))
        stem, dot, extension = base.rpartition(".")
        if not dot:
            stem, extension = base, ""
        sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", stem)
        sanitized = re.sub(r"_+", "_", sanitized).strip("_-") or "attachment"
        sanitized = sanitized[:100]
        return f"{sanitized}.{extension.lower()}" if extension else sanitized

    @staticmethod
    def _sniff_mime(data: bytes) -> str:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"%PDF-"):
            return "application/pdf"
        if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            return "application/zip"
        sample = data[:512]
        lowered = sample.lstrip().lower()
        if lowered.startswith((b"<!doctype html", b"<html", b"<script")):
            return "text/html"
        if b"\x00" not in sample:
            try:
                sample.decode("utf-8")
                return "text/plain"
            except UnicodeDecodeError:
                pass
        return "application/octet-stream"

    @classmethod
    def _validate_attachment(cls, filename: str, data: bytes, max_size_mb: int) -> tuple[str, str]:
        if len(data) > max_size_mb * 1024 * 1024:
            raise TaskServiceError(
                413,
                "PAYLOAD_TOO_LARGE",
                f"File exceeds the maximum allowed size of {max_size_mb} MB.",
            )
        extension = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
        allowed = {".png", ".jpg", ".jpeg", ".pdf", ".docx", ".xlsx", ".zip", ".txt"}
        if extension not in allowed:
            raise TaskServiceError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "Unsupported file type. Only PNG, JPG/JPEG, PDF, DOCX, XLSX, ZIP, and TXT files are accepted.",
            )
        detected = cls._sniff_mime(data)
        final_mime = ""
        if extension in {".docx", ".xlsx", ".zip"}:
            if detected != "application/zip":
                raise TaskServiceError(
                    415,
                    "UNSUPPORTED_MEDIA_TYPE",
                    "Invalid file format. File is not a valid ZIP container.",
                )
            try:
                archive = zipfile.ZipFile(io.BytesIO(data))
                entries = archive.infolist()
            except (zipfile.BadZipFile, OSError) as exc:
                raise TaskServiceError(
                    415, "UNSUPPORTED_MEDIA_TYPE", "Invalid zip file structure."
                ) from exc
            if len(entries) > 1000:
                raise TaskServiceError(
                    415, "UNSUPPORTED_MEDIA_TYPE", "ZIP archive contains too many files."
                )
            total = 0
            names: set[str] = set()
            for entry in entries:
                normalized = entry.filename.replace("\\", "/")
                parts = normalized.split("/")
                if normalized.startswith("/") or ".." in parts:
                    raise TaskServiceError(
                        415,
                        "UNSUPPORTED_MEDIA_TYPE",
                        "ZIP archive contains unsafe file paths.",
                    )
                if entry.file_size > 10 * 1024 * 1024:
                    raise TaskServiceError(
                        413,
                        "PAYLOAD_TOO_LARGE",
                        "ZIP archive contains an entry that exceeds the maximum size limit.",
                    )
                total += entry.file_size
                if total > 50 * 1024 * 1024:
                    raise TaskServiceError(
                        413,
                        "PAYLOAD_TOO_LARGE",
                        "ZIP archive uncompressed size exceeds maximum limit.",
                    )
                if entry.compress_size and entry.file_size / entry.compress_size > 100:
                    raise TaskServiceError(
                        415,
                        "UNSUPPORTED_MEDIA_TYPE",
                        "ZIP archive contains excessively compressed files (potential zip-bomb).",
                    )
                names.add(normalized)
            archive.close()
            if extension == ".docx":
                valid = "[Content_Types].xml" in names and "word/document.xml" in names
                final_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif extension == ".xlsx":
                valid = "[Content_Types].xml" in names and "xl/workbook.xml" in names
                final_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                valid = True
                final_mime = "application/zip"
            if not valid:
                raise TaskServiceError(
                    415,
                    "UNSUPPORTED_MEDIA_TYPE",
                    f"Invalid OOXML document structure for {extension} extension.",
                )
        else:
            expected = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".pdf": "application/pdf",
                ".txt": "text/plain",
            }[extension]
            valid = detected == expected or (
                extension == ".txt" and detected == "application/octet-stream"
            )
            if not valid:
                raise TaskServiceError(
                    415, "UNSUPPORTED_MEDIA_TYPE", "Unsupported file content type."
                )
            final_mime = expected
        return cls._sanitize_filename(filename), final_mime

    @staticmethod
    def _storage_client():
        settings = get_settings()
        if not (
            settings.s3_endpoint
            and settings.s3_access_key_id
            and settings.s3_secret_access_key
            and settings.s3_bucket
        ):
            raise TaskServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Supabase S3 storage is not configured.",
            )
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region or None,
            config=Config(s3={"addressing_style": "path"}),
        )

    @staticmethod
    def _attachment_url(key: str) -> str:
        settings = get_settings()
        endpoint = (settings.s3_public_endpoint or settings.s3_endpoint).rstrip("/")
        if endpoint.endswith("/s3"):
            endpoint = endpoint[:-3] + "/object/public"
        elif "/s3/" in endpoint:
            endpoint = endpoint.replace("/s3/", "/object/public/", 1)
        elif endpoint and "/object/public" not in endpoint:
            endpoint += "/object/public"
        return f"{endpoint}/{settings.s3_bucket}/{key}"

    async def _can_access_attachment_task(
        self,
        task: Task,
        user: User,
    ) -> bool:
        if _role_name(getattr(user.role, "name", None)) == "super_admin":
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "Super admins are not allowed to perform organization-level activities",
            )
        return await self._has_permission(task.project, user, "tasks", "view")

    @staticmethod
    def _attachment_response(attachment: TaskAttachment) -> AttachmentResponse:
        return AttachmentResponse(
            id=str(attachment.id),
            task_id=str(attachment.task_id),
            original_filename=attachment.original_filename,
            mime_type=attachment.mime_type,
            file_size=int(attachment.file_size),
            url=attachment.url or "",
            uploaded_by=str(attachment.uploaded_by),
            uploaded_at=attachment.uploaded_at,
        )

    async def upload_attachments(
        self,
        project_id: str,
        task_id: str,
        user_id: str,
        files: Sequence[tuple[str, bytes]],
    ) -> list[AttachmentResponse]:
        max_size_mb, max_files = self.attachment_limits()
        if len(files) > max_files:
            raise TaskServiceError(
                400,
                "BAD_REQUEST",
                f"Maximum of {max_files} files can be uploaded per request.",
            )
        user = await self._user(user_id)
        task = await self._task(task_id)
        if task.project_id != project_id:
            raise TaskServiceError(
                400, "BAD_REQUEST", "Task does not belong to the specified project"
            )
        if not await self._can_access_attachment_task(task, user):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to access this project"
            )
        prepared = [
            (filename, data, *self._validate_attachment(filename, data, max_size_mb))
            for filename, data in files
        ]
        client = self._storage_client()
        settings = get_settings()
        uploaded_keys: list[str] = []
        attachments: list[TaskAttachment] = []
        try:
            for original, data, sanitized, mime_type in prepared:
                key = f"tasks/{task_id}/attachments/{uuid.uuid4()}-{sanitized}"
                await asyncio.to_thread(
                    client.put_object,
                    Bucket=settings.s3_bucket,
                    Key=key,
                    Body=data,
                    ContentType=mime_type,
                    ContentLength=len(data),
                )
                uploaded_keys.append(key)
                attachment = TaskAttachment(
                    task_id=task_id,
                    original_filename=original,
                    stored_filename=sanitized,
                    mime_type=mime_type,
                    file_size=len(data),
                    storage_path=key,
                    url=self._attachment_url(key),
                    uploaded_by=user_id,
                    uploaded_at=datetime.now(timezone.utc),
                )
                self.db.add(attachment)
                attachments.append(attachment)
            await self.db.commit()
        except TaskServiceError:
            await self.db.rollback()
            raise
        except Exception as exc:
            await self.db.rollback()
            for storage_path in uploaded_keys:
                try:
                    await asyncio.to_thread(
                        client.delete_object,
                        Bucket=settings.s3_bucket,
                        Key=storage_path,
                    )
                except Exception:
                    pass
            if isinstance(exc, SQLAlchemyError):
                raise TaskServiceError(
                    500, "INTERNAL_SERVER_ERROR", "Failed to save attachment metadata"
                ) from exc
            raise TaskServiceError(
                500,
                "INTERNAL_SERVER_ERROR",
                "Failed to upload file. Please try again later.",
            ) from exc
        for attachment in attachments:
            await self._audit(
                user_id=user_id,
                organization_id=str(task.project.organization_id),
                project_id=project_id,
                task_id=task_id,
                resource_id=task_id,
                action="attachment_uploaded",
                resource_type="task_attachment",
                details=(
                    f"User {user.email} uploaded attachment "
                    f"{attachment.original_filename} to task {task.key}"
                ),
            )
        return [self._attachment_response(item) for item in attachments]

    async def get_attachments(
        self,
        project_id: str,
        task_id: str,
        user_id: str,
    ) -> list[AttachmentResponse]:
        user = await self._user(user_id)
        task = await self._task(task_id)
        if task.project_id != project_id:
            raise TaskServiceError(
                400, "BAD_REQUEST", "Task does not belong to the specified project"
            )
        if not await self._can_access_attachment_task(task, user):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to access this project"
            )
        attachments = list(
            (
                await self.db.execute(
                    select(TaskAttachment)
                    .where(TaskAttachment.task_id == task_id)
                    .order_by(TaskAttachment.uploaded_at.asc())
                )
            ).scalars()
        )
        await self._audit(
            user_id=user_id,
            organization_id=str(task.project.organization_id),
            project_id=project_id,
            resource_id=task_id,
            action="viewed",
            resource_type="task_attachment",
            details=f"User {user.email} viewed attachments from task {task.key}",
            audit_type=AuditLogType.AUDIT,
        )
        return [self._attachment_response(item) for item in attachments]

    async def _attachment(self, attachment_id: str) -> TaskAttachment:
        attachment = (
            await self.db.execute(
                select(TaskAttachment).where(TaskAttachment.id == attachment_id)
            )
        ).scalar_one_or_none()
        if attachment is None:
            raise TaskServiceError(404, "RESOURCE_NOT_FOUND", "Attachment not found")
        return attachment

    async def download_attachment(
        self,
        project_id: str,
        attachment_id: str,
        user_id: str,
    ) -> tuple[bytes, str, str, int]:
        user = await self._user(user_id)
        attachment = await self._attachment(attachment_id)
        task = await self._task(str(attachment.task_id))
        if task.project_id != project_id:
            raise TaskServiceError(
                400, "BAD_REQUEST", "Attachment does not belong to the specified project"
            )
        if not await self._can_access_attachment_task(task, user):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to access this project"
            )
        client = self._storage_client()
        settings = get_settings()
        try:
            result = await asyncio.to_thread(
                client.get_object,
                Bucket=settings.s3_bucket,
                Key=attachment.storage_path,
            )
            body = result["Body"]
            try:
                data = await asyncio.to_thread(body.read)
            finally:
                await asyncio.to_thread(body.close)
        except Exception as exc:
            raise TaskServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to retrieve file from storage."
            ) from exc
        await self._audit(
            user_id=user_id,
            organization_id=str(task.project.organization_id),
            project_id=project_id,
            resource_id=attachment_id,
            action="downloaded",
            resource_type="task_attachment",
            details=(
                f"User {user.email} downloaded attachment "
                f"{attachment.original_filename} from task {task.key}"
            ),
            audit_type=AuditLogType.AUDIT,
        )
        return data, attachment.original_filename, attachment.mime_type, len(data)

    async def delete_attachment(
        self,
        project_id: str,
        attachment_id: str,
        user_id: str,
    ) -> None:
        user = await self._user(user_id)
        attachment = await self._attachment(attachment_id)
        task = await self._task(str(attachment.task_id))
        if task.project_id != project_id:
            raise TaskServiceError(
                400, "BAD_REQUEST", "Attachment does not belong to the specified project"
            )
        if not await self._can_access_attachment_task(task, user):
            raise TaskServiceError(
                403, "FORBIDDEN", "You do not have permission to access this project"
            )
        allowed = attachment.uploaded_by == user_id or await self._has_permission(
            task.project, user, "tasks", "modify"
        )
        if not allowed:
            raise TaskServiceError(
                403,
                "FORBIDDEN",
                "Only the uploader, Project Managers, or Organization Administrators can delete this attachment",
            )
        filename = attachment.original_filename
        storage_path = attachment.storage_path
        task_id = str(attachment.task_id)
        client = self._storage_client()
        settings = get_settings()
        orphaned_file = OrphanedFile(
            storage_path=storage_path,
            available_at=datetime.now(timezone.utc),
        )
        try:
            await self.db.delete(attachment)
            self.db.add(orphaned_file)
            await self.db.commit()
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise TaskServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to delete attachment metadata"
            ) from exc
        try:
            await asyncio.to_thread(
                client.delete_object,
                Bucket=settings.s3_bucket,
                Key=storage_path,
            )
        except Exception:
            # Keep the orphan record so a cleanup process can retry the S3 deletion.
            pass
        else:
            try:
                await self.db.delete(orphaned_file)
                await self.db.commit()
            except SQLAlchemyError:
                # The Supabase object is already gone; a stale orphan record is safe.
                await self.db.rollback()
        await self._audit(
            user_id=user_id,
            organization_id=str(task.project.organization_id),
            project_id=project_id,
            task_id=task_id,
            resource_id=attachment_id,
            action="attachment_deleted",
            resource_type="task_attachment",
            details=f"Attachment {filename} deleted from task {task.key}",
        )
