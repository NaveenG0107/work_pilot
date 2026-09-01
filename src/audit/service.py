import math
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.audit.models import AuditLog, AuditLogType
from src.audit.schema import (
    AuditFilter,
    AuditLogResponse,
    AuditLogResponseWrapper,
    PaginationResponse,
    UserSummary,
)
from src.config import get_logger

from src.auth.models import User
from src.project.models import Project
from src.sprint.models import Sprint
from src.task.models import Task
from src.user_story.models import UserStory


logger = get_logger(__name__)


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_audit_logs(
        self,
        filters: AuditFilter,
    ) -> tuple[AuditLogResponseWrapper, PaginationResponse]:

        try:
            conditions = []

            if filters.organization_id:
                conditions.append(AuditLog.organization_id == filters.organization_id)

            if filters.project_id:
                conditions.append(AuditLog.project_id == filters.project_id)

            if filters.task_id:
                conditions.append(
                    or_(
                        AuditLog.task_id == filters.task_id,
                        (
                            func.lower(AuditLog.resource_type).in_(
                                [
                                    "task",
                                    "task_attachment",
                                    "comment",
                                ]
                            )
                            & (AuditLog.resource_id == filters.task_id)
                        ),
                    )
                )

            if filters.user_story_id:
                conditions.append(
                    or_(
                        AuditLog.user_story_id == filters.user_story_id,
                        (
                            func.lower(AuditLog.resource_type).in_(
                                [
                                    "user_story",
                                    "userstory",
                                    "user_story_attachment",
                                    "comment",
                                ]
                            )
                            & (AuditLog.resource_id == filters.user_story_id)
                        ),
                    )
                )

            if filters.resource_type:
                resource_type = filters.resource_type.strip().lower()

                conditions.append(func.lower(AuditLog.resource_type) == resource_type)

            if filters.resource_id:
                conditions.append(
                    or_(
                        AuditLog.resource_id == filters.resource_id,
                        AuditLog.task_id == filters.resource_id,
                        AuditLog.user_story_id == filters.resource_id,
                    )
                )

            if filters.type:
                audit_type = filters.type.strip().lower()

                if audit_type != "all":
                    if audit_type == AuditLogType.VIEW:
                        conditions.append(
                            or_(
                                func.lower(AuditLog.type) == AuditLogType.VIEW,
                                (
                                    AuditLog.type.is_(None)
                                    & func.lower(AuditLog.action).like("%view%")
                                ),
                            )
                        )

                    elif audit_type == AuditLogType.ACTIVITY:
                        conditions.append(
                            or_(
                                func.lower(AuditLog.type) == AuditLogType.ACTIVITY,
                                (
                                    AuditLog.type.is_(None)
                                    & ~func.lower(AuditLog.action).like("%view%")
                                ),
                            )
                        )

                    else:
                        conditions.append(func.lower(AuditLog.type) == audit_type)

            count_stmt = select(func.count(AuditLog.id)).where(*conditions)

            total_items = (
                await self.db.execute(count_stmt)
            ).scalar_one()

            offset = (filters.page - 1) * filters.page_size

            stmt = (
                select(AuditLog)
                .where(*conditions)
                .order_by(
                    AuditLog.created_at.desc(),
                    AuditLog.id.desc(),
                )
                .limit(filters.page_size)
                .offset(offset)
            )

            audits = list(
                (await self.db.execute(stmt)).scalars().all()
            )

            total_pages = max(
                1,
                math.ceil(total_items / filters.page_size),
            )

            pagination = PaginationResponse(
                page=filters.page,
                page_size=filters.page_size,
                total_items=total_items,
                total_pages=total_pages,
                has_next=(filters.page < total_pages),
                has_previous=(filters.page > 1),
            )

            activities = await self._build_audit_responses(audits)

            user = await self._get_user_summary(filters.user_id)

            wrapper = AuditLogResponseWrapper(
                user=user,
                activities=activities,
            )

            if filters.user_id:
                await self.create_audit_log(
                    user_id=filters.user_id,
                    organization_id=(filters.organization_id),
                    action="viewed",
                    resource_type="audits",
                    resource_id=filters.user_id,
                    audit_type=AuditLogType.AUDIT,
                    details=(f"view audits by user {filters.user_id}"),
                )

            logger.info(
                "Audit logs retrieved: count=%s user_id=%s",
                len(activities),
                filters.user_id,
            )

            return wrapper, pagination

        except SQLAlchemyError as exc:
            logger.exception("Database error while retrieving audit logs")

            raise RuntimeError("Something went wrong. Please try again later.") from exc

        except RuntimeError:
            raise

        except Exception as exc:
            logger.exception("Unexpected error while processing audit logs")

            raise RuntimeError("Failed to process audit logs") from exc

    async def create_audit_log(
        self,
        *,
        action: str,
        resource_type: str,
        user_id: str | None = None,
        organization_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        sprint_id: str | None = None,
        user_story_id: str | None = None,
        resource_id: str | None = None,
        details: str | None = None,
        audit_type: str = AuditLogType.ACTIVITY,
    ) -> None:

        try:
            audit = AuditLog(
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                task_id=task_id,
                sprint_id=sprint_id,
                user_story_id=user_story_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                type=audit_type,
                created_at=datetime.now(timezone.utc),
            )

            self.db.add(audit)
            await self.db.commit()

        except SQLAlchemyError:
            await self.db.rollback()

            logger.exception("Failed to create audit log")

    async def _get_user_summary(
        self,
        user_id: str | None,
    ) -> UserSummary | None:

        if not user_id:
            return None

        try:
            user = (
                await self.db.execute(
                    select(User).where(User.id == user_id).options(selectinload(User.role))
                )
            ).scalar_one_or_none()

            if user is None:
                return None

            role_name = None

            if getattr(user, "role", None):
                role_name = getattr(
                    user.role,
                    "name",
                    None,
                )

            return UserSummary(
                id=str(user.id),
                full_name=(getattr(user, "full_name", None) or ""),
                email=(getattr(user, "email", None) or ""),
                avatar_url=(getattr(user, "avatar_url", None) or ""),
                color=(getattr(user, "color", None) or ""),
                role=role_name,
            )

        except SQLAlchemyError:
            logger.exception("Failed to retrieve audit user")

            return None

    async def _build_audit_responses(
        self,
        audits: list[AuditLog],
    ) -> list[AuditLogResponse]:

        if not audits:
            return []

        task_ids: set[str] = set()
        story_ids: set[str] = set()
        project_ids: set[str] = set()
        sprint_ids: set[str] = set()
        user_ids: set[str] = set()

        for audit in audits:
            if audit.user_id:
                user_ids.add(audit.user_id)

            if audit.task_id:
                task_ids.add(audit.task_id)

            if audit.user_story_id:
                story_ids.add(audit.user_story_id)

            if audit.project_id:
                project_ids.add(audit.project_id)

            if audit.sprint_id:
                sprint_ids.add(audit.sprint_id)

            resource_id = audit.resource_id

            if not resource_id:
                continue

            resource_type = (audit.resource_type or "").lower()

            if resource_type in {
                "task",
                "task_attachment",
            }:
                task_ids.add(resource_id)

            elif resource_type in {
                "user_story",
                "userstory",
                "user_story_attachment",
            }:
                story_ids.add(resource_id)

            elif resource_type in {
                "project",
                "project_member",
            }:
                project_ids.add(resource_id)

            elif resource_type == "sprint":
                sprint_ids.add(resource_id)

        task_map = await self._get_task_map(task_ids)
        story_map = await self._get_story_map(story_ids)
        project_map = await self._get_project_map(project_ids)
        sprint_map = await self._get_sprint_map(sprint_ids)
        user_map = await self._get_user_map(user_ids)

        responses = []

        for audit in audits:
            resource_id = audit.resource_id

            audit_type = audit.type

            if not audit_type:
                audit_type = (
                    AuditLogType.VIEW
                    if "view" in (audit.action or "").lower()
                    else AuditLogType.ACTIVITY
                )

            title = None
            task_key = None
            task_name = None
            user_story_name = None
            sprint_name = None

            project_name = (
                project_map.get(audit.project_id) if audit.project_id else None
            )

            if not project_name and resource_id:
                project_name = project_map.get(resource_id)

            task = None

            if resource_id:
                task = task_map.get(resource_id)

            if task is None and audit.task_id:
                task = task_map.get(audit.task_id)

            if task:
                title = task["title"]
                task_name = task["title"]
                task_key = task["key"]

            story = None

            if not task:
                if resource_id:
                    story = story_map.get(resource_id)

                if story is None and audit.user_story_id:
                    story = story_map.get(audit.user_story_id)

                if story:
                    title = story["title"]
                    user_story_name = story["title"]

            if not title:
                if resource_id and resource_id in project_map:
                    title = project_map[resource_id]

                elif audit.project_id:
                    title = project_map.get(audit.project_id)

            if audit.sprint_id:
                sprint_name = sprint_map.get(audit.sprint_id)

            if resource_id and resource_id in sprint_map:
                sprint_name = sprint_map[resource_id]

                if not title:
                    title = sprint_name

            # Go omits blank optional strings through `omitempty`.
            details = audit.details or None

            if (
                audit.resource_type.lower() == "comment"
                and "deleted" in audit.action.lower()
            ):
                details = "Comment deleted"

            responses.append(
                AuditLogResponse(
                    id=str(audit.id),
                    project_id=audit.project_id,
                    project_name=project_name,
                    organization_id=(audit.organization_id),
                    user=user_map.get(audit.user_id),
                    action=audit.action,
                    resource_type=(audit.resource_type),
                    resource_id=(audit.resource_id or None),
                    details=details,
                    created_at=audit.created_at,
                    task_key=task_key,
                    task_id=audit.task_id,
                    user_story_id=(audit.user_story_id),
                    title=title,
                    task_name=task_name,
                    user_story_name=(user_story_name),
                    sprint_name=sprint_name,
                    type=audit_type,
                )
            )

        return responses

    async def _get_task_map(
        self,
        ids: set[str],
    ) -> dict[str, dict]:

        if not ids:
            return {}

        rows = (
            await self.db.execute(
                select(
                    Task.id,
                    Task.title,
                    Task.key,
                ).where(Task.id.in_(ids))
            )
        ).all()

        return {
            str(row.id): {
                "title": row.title,
                "key": row.key,
            }
            for row in rows
        }

    async def _get_story_map(
        self,
        ids: set[str],
    ) -> dict[str, dict]:

        if not ids:
            return {}

        rows = (
            await self.db.execute(
                select(
                    UserStory.id,
                    UserStory.title,
                ).where(UserStory.id.in_(ids))
            )
        ).all()

        return {
            str(row.id): {
                "title": row.title,
            }
            for row in rows
        }

    async def _get_project_map(
        self,
        ids: set[str],
    ) -> dict[str, str]:

        if not ids:
            return {}

        rows = (
            await self.db.execute(
                select(
                    Project.id,
                    Project.name,
                ).where(Project.id.in_(ids))
            )
        ).all()

        return {str(row.id): row.name for row in rows}

    async def _get_sprint_map(
        self,
        ids: set[str],
    ) -> dict[str, str]:

        if not ids:
            return {}

        rows = (
            await self.db.execute(
                select(
                    Sprint.id,
                    Sprint.name,
                ).where(Sprint.id.in_(ids))
            )
        ).all()

        return {str(row.id): row.name for row in rows}

    async def _get_user_map(
        self,
        ids: set[str],
    ) -> dict[str, UserSummary]:

        if not ids:
            return {}

        users = (
            await self.db.execute(
                select(User)
                .where(User.id.in_(ids))
                .options(selectinload(User.role))
            )
        ).scalars().all()

        result = {}

        for user in users:
            role_name = None

            if getattr(user, "role", None):
                role_name = getattr(
                    user.role,
                    "name",
                    None,
                )

            result[str(user.id)] = UserSummary(
                id=str(user.id),
                full_name=getattr(
                    user,
                    "full_name",
                    None,
                ),
                email=getattr(
                    user,
                    "email",
                    None,
                ),
                avatar_url=getattr(
                    user,
                    "avatar_url",
                    None,
                ),
                color=getattr(
                    user,
                    "color",
                    None,
                ),
                role=role_name,
            )

        return result
