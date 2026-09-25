import re
import math
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

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

UUID_REGEX = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def clean_val(val: str, id_map: dict | None = None) -> str:
    val = val.strip()
    m = re.match(r"^(.+?)\s*\([0-9a-f-]{36}\)$", val, re.I)
    if m:
        val = m.group(1).strip()
    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
        val = val[1:-1].strip()
    if id_map and val in id_map:
        val = id_map[val]
    return val


def format_audit_details(
    details: str | None,
    user_map: dict | None = None,
    story_map: dict | None = None,
    sprint_map: dict | None = None,
) -> str | None:
    if not details:
        return details

    def resolve_story(v: str) -> str:
        c = clean_val(v)
        if story_map and c in story_map:
            st = story_map[c]
            return st.get("title") if isinstance(st, dict) else str(st)
        if UUID_REGEX.match(c):
            return "user story"
        return c

    def resolve_sprint(v: str) -> str:
        c = clean_val(v)
        if sprint_map and c in sprint_map:
            sp = sprint_map[c]
            return sp.get("name") if isinstance(sp, dict) else str(sp)
        if UUID_REGEX.match(c):
            return "sprint"
        return c

    def resolve_user(v: str) -> str:
        c = clean_val(v)
        if user_map and c in user_map:
            u = user_map[c]
            if hasattr(u, "full_name") and u.full_name:
                return u.full_name
            if isinstance(u, dict):
                return u.get("full_name") or u.get("name") or u.get("username") or c
            if isinstance(u, str):
                return u
        if UUID_REGEX.match(c):
            return "user"
        return c

    def _sub_story(m):
        f_val, t_val = m.group(1).strip(), m.group(2).strip()
        f_is_nil = f_val.lower() in ("nil", "none", "null")
        t_is_nil = t_val.lower() in ("nil", "none", "null")
        if f_is_nil and not t_is_nil:
            name = resolve_story(t_val)
            return f"assigned to user story '{name}'"
        elif not f_is_nil and t_is_nil:
            name = resolve_story(f_val)
            return f"removed from user story '{name}'"
        elif not f_is_nil and not t_is_nil:
            name_f = resolve_story(f_val)
            name_t = resolve_story(t_val)
            return f"user story changed from '{name_f}' to '{name_t}'"
        return m.group(0)

    details = re.sub(
        r"user story changed from\s+(.+?)\s+to\s+([^,]+)",
        _sub_story,
        details,
        flags=re.IGNORECASE,
    )

    def _sub_sprint(m):
        f_val, t_val = m.group(1).strip(), m.group(2).strip()
        f_is_nil = f_val.lower() in ("nil", "none", "null")
        t_is_nil = t_val.lower() in ("nil", "none", "null")
        if f_is_nil and not t_is_nil:
            name = resolve_sprint(t_val)
            return f"assigned to sprint '{name}'"
        elif not f_is_nil and t_is_nil:
            name = resolve_sprint(f_val)
            return f"removed from sprint '{name}'"
        elif not f_is_nil and not t_is_nil:
            name_f = resolve_sprint(f_val)
            name_t = resolve_sprint(t_val)
            return f"sprint changed from '{name_f}' to '{name_t}'"
        return m.group(0)

    details = re.sub(
        r"sprint changed from\s+(.+?)\s+to\s+([^,]+)",
        _sub_sprint,
        details,
        flags=re.IGNORECASE,
    )

    def _sub_assignee(m):
        f_val, t_val = m.group(1).strip(), m.group(2).strip()
        f_is_nil = f_val.lower() in ("nil", "none", "null")
        t_is_nil = t_val.lower() in ("nil", "none", "null")
        if f_is_nil and not t_is_nil:
            name = resolve_user(t_val)
            return f"assigned to '{name}'"
        elif not f_is_nil and t_is_nil:
            name = resolve_user(f_val)
            return f"unassigned from '{name}'"
        elif not f_is_nil and not t_is_nil:
            name_f = resolve_user(f_val)
            name_t = resolve_user(t_val)
            return f"assignee changed from '{name_f}' to '{name_t}'"
        return m.group(0)

    details = re.sub(
        r"assignee changed from\s+(.+?)\s+to\s+([^,]+)",
        _sub_assignee,
        details,
        flags=re.IGNORECASE,
    )

    details = re.sub(
        r"\b(actual hours|estimated hours|story points) changed from (?:nil|none|null) to ([^,]+)",
        r"\1 set to \2",
        details,
        flags=re.IGNORECASE,
    )

    details = re.sub(r"\s*\([0-9a-f-]{36}\)", "", details, flags=re.IGNORECASE)

    return details




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
                    select(User).where(User.id == user_id).options(joinedload(User.role))
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
                user_ids.add(str(audit.user_id))

            if audit.task_id:
                task_ids.add(str(audit.task_id))

            if audit.user_story_id:
                story_ids.add(str(audit.user_story_id))

            if audit.project_id:
                project_ids.add(str(audit.project_id))

            if audit.sprint_id:
                sprint_ids.add(str(audit.sprint_id))

            resource_id = str(audit.resource_id) if audit.resource_id else None

            if resource_id:
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

                elif resource_type in {"sprint", "sprints"}:
                    sprint_ids.add(resource_id)

            if audit.details:
                for sm in re.finditer(r"user story changed from\s+(.+?)\s+to\s+([^,]+)", audit.details, re.I):
                    for uid in UUID_REGEX.findall(sm.group(0)):
                        story_ids.add(uid)
                for spm in re.finditer(r"sprint changed from\s+(.+?)\s+to\s+([^,]+)", audit.details, re.I):
                    for uid in UUID_REGEX.findall(spm.group(0)):
                        sprint_ids.add(uid)
                for am in re.finditer(r"assignee changed from\s+(.+?)\s+to\s+([^,]+)", audit.details, re.I):
                    for uid in UUID_REGEX.findall(am.group(0)):
                        user_ids.add(uid)
                if "sprint" in audit.details.lower():
                    for uid in UUID_REGEX.findall(audit.details):
                        sprint_ids.add(uid)

        task_map = await self._get_task_map(task_ids)
        for t in task_map.values():
            if t.get("user_story_id"):
                story_ids.add(t["user_story_id"])

        story_map = await self._get_story_map(story_ids)
        project_map = await self._get_project_map(project_ids)

        for t in task_map.values():
            if t.get("sprint_id"):
                sprint_ids.add(t["sprint_id"])
        for s in story_map.values():
            if s.get("sprint_id"):
                sprint_ids.add(s["sprint_id"])

        sprint_map = await self._get_sprint_map(sprint_ids)
        user_map = await self._get_user_map(user_ids)

        responses = []

        for audit in audits:
            resource_id = str(audit.resource_id) if audit.resource_id else None

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
            user_story_key = None
            sprint_name = None
            sprint_id = None
            task_id = str(audit.task_id) if audit.task_id else None
            user_story_id = str(audit.user_story_id) if audit.user_story_id else None

            project_name = (
                project_map.get(str(audit.project_id)) if audit.project_id else None
            )

            if not project_name and resource_id:
                project_name = project_map.get(resource_id)

            task = None

            if resource_id:
                task = task_map.get(resource_id)
                if task and not task_id:
                    task_id = resource_id

            if task is None and audit.task_id:
                task = task_map.get(str(audit.task_id))

            if task:
                title = task["title"]
                task_name = task["title"]
                task_key = task["key"]
                if not task_id:
                    task_id = str(audit.task_id) if audit.task_id else resource_id
                if not sprint_id and task.get("sprint_id"):
                    sprint_id = task["sprint_id"]
                if not user_story_id and task.get("user_story_id"):
                    user_story_id = task["user_story_id"]

            story = None

            if resource_id:
                story = story_map.get(resource_id)
                if story and not user_story_id:
                    user_story_id = resource_id

            if story is None and user_story_id:
                story = story_map.get(user_story_id)

            if story:
                user_story_name = story["title"]
                user_story_key = story.get("key")
                if not title:
                    title = story["title"]
                if not sprint_id and story.get("sprint_id"):
                    sprint_id = story["sprint_id"]

            if not title:
                if resource_id and resource_id in project_map:
                    title = project_map[resource_id]

                elif audit.project_id:
                    title = project_map.get(str(audit.project_id))

            if audit.sprint_id:
                sprint_id = str(audit.sprint_id)

            if not sprint_id and (audit.resource_type or "").lower() in {"sprint", "sprints"} and resource_id:
                sprint_id = resource_id

            if sprint_id:
                sprint_name = sprint_map.get(sprint_id)

            if not sprint_name and resource_id and resource_id in sprint_map:
                sprint_name = sprint_map[resource_id]
                if not sprint_id:
                    sprint_id = resource_id

            if not title and (audit.resource_type or "").lower() in {"sprint", "sprints"}:
                title = sprint_name

            # Go omits blank optional strings through `omitempty`.
            details = audit.details or None

            if (
                (audit.resource_type or "").lower() == "comment"
                and "deleted" in (audit.action or "").lower()
            ):
                details = "Comment deleted"
            elif details:
                details = format_audit_details(
                    details,
                    user_map=user_map,
                    story_map=story_map,
                    sprint_map=sprint_map,
                )

            responses.append(
                AuditLogResponse(
                    id=str(audit.id),
                    project_id=str(audit.project_id) if audit.project_id else None,
                    project_name=project_name,
                    organization_id=str(audit.organization_id) if audit.organization_id else None,
                    user=user_map.get(str(audit.user_id)) if audit.user_id else None,
                    action=audit.action,
                    resource_type=audit.resource_type,
                    resource_id=resource_id,
                    details=details,
                    created_at=audit.created_at,
                    task_key=task_key,
                    task_id=task_id,
                    user_story_id=user_story_id,
                    user_story_key=user_story_key,
                    title=title,
                    task_name=task_name,
                    user_story_name=user_story_name,
                    sprint_id=sprint_id,
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
                    Task.sprint_id,
                    Task.user_story_id,
                ).where(Task.id.in_(ids))
            )
        ).all()

        return {
            str(row.id): {
                "title": row.title,
                "key": row.key,
                "sprint_id": str(row.sprint_id) if row.sprint_id else None,
                "user_story_id": str(row.user_story_id) if row.user_story_id else None,
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
                    UserStory.key,
                    UserStory.sprint_id,
                ).where(UserStory.id.in_(ids))
            )
        ).all()

        return {
            str(row.id): {
                "title": row.title,
                "key": row.key,
                "sprint_id": str(row.sprint_id) if row.sprint_id else None,
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
                .options(joinedload(User.role))
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
