from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Optional
from math import ceil

from sqlalchemy import select, update, func, text, delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.audit.models import AuditLog, AuditLogType
from src.auth.models import User
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.sprint.models import Sprint, SprintSnapshot
from src.task.models import Task
from src.custom_status.models import CustomStatus
from src.sprint.schema import (
    CreateSprintRequest, StartSprintRequest, UpdateSprintRequest)
from src.utils.core import ErrorCode, error_response


DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "org_admin": {
        "projects:view",
        "projects:add",
        "projects:modify",
        "projects:delete",
        "sprints:view",
        "sprints:add",
        "sprints:modify",
        "sprints:delete",
        "user_stories:view",
        "user_stories:add",
        "user_stories:modify",
        "user_stories:delete",
        "tasks:view",
        "tasks:add",
        "tasks:modify",
        "tasks:delete",
        "comments:view",
        "comments:add",
        "comments:modify",
        "comments:delete",
        "attachments:view",
        "attachments:add",
        "attachments:delete",
        "custom_statuses:view",
        "custom_statuses:modify",
    },
    "project_manager": {
        "projects:view",
        "projects:modify",
        "sprints:view",
        "sprints:add",
        "sprints:modify",
        "sprints:delete",
        "user_stories:view",
        "user_stories:add",
        "user_stories:modify",
        "user_stories:delete",
        "tasks:view",
        "tasks:add",
        "tasks:modify",
        "tasks:delete",
        "comments:view",
        "comments:add",
        "comments:modify",
        "comments:delete",
        "attachments:view",
        "attachments:add",
        "attachments:delete",
        "custom_statuses:view",
        "custom_statuses:modify",
    },
    "developer": {
        "projects:view",
        "sprints:view",
        "user_stories:view",
        "user_stories:add",
        "user_stories:modify",
        "tasks:view",
        "tasks:add",
        "tasks:modify",
        "tasks:delete",
        "comments:view",
        "comments:add",
        "comments:modify",
        "comments:delete",
        "attachments:view",
        "attachments:add",
        "attachments:delete",
        "custom_statuses:view",
    },
    "qa": {
        "projects:view",
        "sprints:view",
        "user_stories:view",
        "user_stories:modify",
        "tasks:view",
        "tasks:add",
        "tasks:modify",
        "comments:view",
        "comments:add",
        "attachments:view",
        "attachments:add",
        "custom_statuses:view",
    },
    "stakeholder": {
        "projects:view",
        "sprints:view",
        "user_stories:view",
        "tasks:view",
        "comments:view",
        "comments:add",
        "attachments:view",
        "custom_statuses:view",
    },
}


class SprintService:

    def __init__(self, db: AsyncSession):
        self.db = db

    # COMMON HELPERS

    @staticmethod
    def _has_default_permission(
        role_name: str,
        resource: str,
        action: str,
    ) -> bool:
        name = (role_name or "").lower()

        aliases = {
            "member": "developer",
            "user": "developer",
            "tester": "qa",
            "viewer": "stakeholder",
        }

        name = aliases.get(name, name)

        permission = f"{resource}:{action}"

        return permission in DEFAULT_ROLE_PERMISSIONS.get(
            name,
            set(),
        )

    @staticmethod
    def _has_explicit_permission(
        role: Optional[Role],
        resource: str,
        action: str,
    ) -> bool:
        if role is None:
            return False

        return any(
            permission.resource == resource
            and permission.action == action
            for permission in role.permissions
        )

    async def _get_user_by_id(
        self,
        user_id: str,
    ) -> Optional[User]:
        result = await self.db.execute(
            select(User)
            .where(
                User.id == user_id,
                User.deleted_at.is_(None),
            )
            .options(
                selectinload(User.organization),
                selectinload(User.role).selectinload(
                    Role.permissions
                ),
            )
        )

        user = result.scalar_one_or_none()

        # user without role gets global developer role.
        if user is not None and (
            user.role_id is None
            or user.role is None
        ):
            role_result = await self.db.execute(
                select(Role).where(
                    Role.name == "developer",
                    Role.organization_id.is_(None),
                )
            )

            developer_role = (
                role_result.scalar_one_or_none()
            )

            if developer_role is not None:
                user.role_id = str(
                    developer_role.id
                )
                user.role = developer_role

                await self.db.commit()

        return user

    async def _get_project_by_id(
        self,
        project_id: str,
    ) -> Optional[Project]:
        result = await self.db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.deleted_at.is_(None),
            )
        )

        return result.scalar_one_or_none()

    async def _get_project_member(
        self,
        user_id: str,
        project_id: str,
    ) -> Optional[ProjectMember]:
        result = await self.db.execute(
            select(ProjectMember)
            .where(
                ProjectMember.user_id == user_id,
                ProjectMember.project_id == project_id,
                ProjectMember.deleted_at.is_(None),
            )
            .options(
                selectinload(
                    ProjectMember.role
                ).selectinload(
                    Role.permissions
                ),
            )
        )

        return result.scalar_one_or_none()

    async def _get_sprint_by_id(
        self,
        sprint_id: str,
        project_id: str,
    ) -> Optional[Sprint]:

        result = await self.db.execute(
            select(Sprint).where(
                Sprint.id == sprint_id,
                Sprint.project_id == project_id,
                Sprint.deleted_at.is_(None),
            )
        )

        return result.scalar_one_or_none()

    async def _check_permission(
        self,
        user: User,
        project_id: str,
        resource: str,
        action: str,
    ):
        if (
            user.role is not None
            and user.role.name == "super_admin"
        ):
            return False, None

        member = await self._get_project_member(
            str(user.id),
            project_id,
        )

        if (
            member is not None
            and member.role is not None
        ):
            if self._has_explicit_permission(
                member.role,
                resource,
                action,
            ):
                return True, None

            if self._has_default_permission(
                member.role.name,
                resource,
                action,
            ):
                return True, None

        project = await self._get_project_by_id(
            project_id
        )

        if project is None:
            return False, error_response(
                ErrorCode.ErrNotFound,
                "Project not found",
                status_code=404,
            )

        if (
            user.organization_id is not None
            and str(user.organization_id)
            == str(project.organization_id)
            and user.role is not None
            and user.role.name == "org_admin"
        ):
            if self._has_explicit_permission(
                user.role,
                resource,
                action,
            ):
                return True, None

            if self._has_default_permission(
                user.role.name,
                resource,
                action,
            ):
                return True, None

        return False, None

    @staticmethod
    def _parse_date_string(
        value: str,
    ) -> date:
        """
        Go parseDateString equivalent.

        Accepted:
        - RFC3339
        - RFC3339Nano
        - YYYY-MM-DD
        - YYYY-MM-DD HH:MM:SS
        """

        raw = value.strip()

        try:
            if raw.endswith("Z"):
                return datetime.fromisoformat(
                    raw[:-1] + "+00:00"
                ).date()

            if "T" in raw:
                return datetime.fromisoformat(
                    raw
                ).date()

        except ValueError:
            pass

        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                return datetime.strptime(
                    raw,
                    fmt,
                ).date()

            except ValueError:
                continue

        raise ValueError(
            "expected format: YYYY-MM-DD"
        )


    # CREATE SPRINT

    async def create_sprint(
        self,
        req: CreateSprintRequest,
        project_id: str,
        user_id: str,
        organization_id: Optional[str],
    ):
        try:

            user = await self._get_user_by_id(
                user_id
            )

            if user is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )


            if (
                user.organization_id is None
                or not organization_id
            ):
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    (
                        "You do not have permission "
                        "to perform this action"
                    ),
                    status_code=403,
                )

            if (
                str(user.organization_id)
                != str(organization_id)
            ):
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    (
                        "You do not have permission "
                        "to perform this action"
                    ),
                    status_code=403,
                )


            (
                authorized,
                permission_error,
            ) = await self._check_permission(
                user=user,
                project_id=project_id,
                resource="sprints",
                action="add",
            )

            if permission_error is not None:
                return None, permission_error

            if not authorized:
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    (
                        "You do not have permission "
                        "to create sprints for this project"
                    ),
                    status_code=403,
                )


            parsed_dates: list[
                tuple[
                    Optional[date],
                    Optional[date],
                ]
            ] = []

            for sprint_request in req.sprints:
                start_raw = (
                    sprint_request.start_date
                )

                end_raw = (
                    sprint_request.end_date
                )

                has_start = (
                    start_raw is not None
                    and start_raw != ""
                    and start_raw != "null"
                )

                has_end = (
                    end_raw is not None
                    and end_raw != ""
                    and end_raw != "null"
                )

                if (
                    (has_start and not has_end)
                    or
                    (not has_start and has_end)
                ):
                    return None, error_response(
                        ErrorCode.ErrBadRequest,
                        (
                            "Both start_date and end_date "
                            "must be provided if either "
                            "is specified"
                        ),
                        status_code=400,
                    )

                parsed_start_date = None
                parsed_end_date = None

                if has_start and has_end:
                    try:
                        parsed_start_date = (
                            self._parse_date_string(
                                start_raw
                            )
                        )

                    except ValueError as exc:
                        return None, error_response(
                            ErrorCode.ErrBadRequest,
                            (
                                f"Invalid start_date: "
                                f"{exc}"
                            ),
                            status_code=400,
                        )

                    try:
                        parsed_end_date = (
                            self._parse_date_string(
                                end_raw
                            )
                        )

                    except ValueError as exc:
                        return None, error_response(
                            ErrorCode.ErrBadRequest,
                            (
                                f"Invalid end_date: "
                                f"{exc}"
                            ),
                            status_code=400,
                        )

                    if (
                        parsed_end_date
                        < parsed_start_date
                    ):
                        return None, error_response(
                            ErrorCode.ErrBadRequest,
                            (
                                "end_date cannot be "
                                "before start_date"
                            ),
                            status_code=400,
                        )

                parsed_dates.append(
                    (
                        parsed_start_date,
                        parsed_end_date,
                    )
                )


            last_sprint_id: Optional[str] = None

            for (
                sprint_request,
                dates,
            ) in zip(
                req.sprints,
                parsed_dates,
            ):
                (
                    parsed_start_date,
                    parsed_end_date,
                ) = dates

                sprint = Sprint(
                    name=sprint_request.name,
                    goal=sprint_request.goal,
                    status="planned",
                    start_date=parsed_start_date,
                    end_date=parsed_end_date,
                    project_id=project_id,
                    created_by_id=user_id,
                )

                self.db.add(
                    sprint
                )

                await self.db.flush()

                last_sprint_id = str(
                    sprint.id
                )

                await self.db.commit()

                try:
                    audit_log = AuditLog(
                        user_id=user_id,
                        organization_id=(
                            organization_id
                        ),
                        project_id=project_id,
                        sprint_id=str(
                            sprint.id
                        ),
                        action="created",
                        resource_type="sprint",
                        resource_id=str(
                            sprint.id
                        ),
                        details=(
                            f"The sprint "
                            f"'{sprint.name}' "
                            f"was created by "
                            f"{user.username}"
                        ),
                        type=(
                            AuditLogType.ACTIVITY
                        ),
                        created_at=datetime.now(
                            timezone.utc
                        ),
                    )

                    self.db.add(
                        audit_log
                    )

                    await self.db.commit()

                except Exception:
                    await self.db.rollback()

            return last_sprint_id, None

        except IntegrityError:
            await self.db.rollback()

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                (
                    "Something went wrong. "
                    "Please try again later."
                ),
                status_code=500,
            )

        except SQLAlchemyError:
            await self.db.rollback()

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                (
                    "Something went wrong. "
                    "Please try again later."
                ),
                status_code=500,
            )

        except Exception:
            await self.db.rollback()

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                (
                    "Something went wrong. "
                    "Please try again later."
                ),
                status_code=500,
            )


    async def start_sprint(
        self,
        req: StartSprintRequest,
        project_id: str,
        sprint_id: str,
        user_id: str,
    ):

        try:

            user = await self._get_user_by_id(
                user_id
            )

            if user is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            if user.organization_id is None:
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    (
                        "You do not have permission "
                        "to start this sprint"
                    ),
                    status_code=403,
                )

            sprint = await self._get_sprint_by_id(
                sprint_id=sprint_id,
                project_id=project_id,
            )

            if sprint is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            member = await self._get_project_member(
                user_id=user_id,
                project_id=str(
                    sprint.project_id
                ),
            )

            is_org_admin = (
                user.role is not None
                and user.role.name == "org_admin"
            )

            # User is not project member.
            if member is None:
                if not is_org_admin:
                    return None, error_response(
                        ErrorCode.ErrForbidden,
                        (
                            "You do not have permission "
                            "to start this sprint"
                        ),
                        status_code=403,
                    )

            else:
                project_role_name = (
                    member.role.name
                    if member.role is not None
                    else ""
                )

                allowed_project_roles = {
                    "org_admin",
                    "project_manager",
                }

                if (
                    project_role_name
                    not in allowed_project_roles
                    and not is_org_admin
                ):
                    return None, error_response(
                        ErrorCode.ErrForbidden,
                        (
                            "You do not have permission "
                            "to start this sprint"
                        ),
                        status_code=403,
                    )

            if sprint.status != "planned":
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    (
                        "Only planned sprints "
                        "can be started"
                    ),
                    status_code=400,
                )

            if not req.start_date:
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "start_date must be provided",
                    status_code=400,
                )

            if not req.end_date:
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "end_date must be provided",
                    status_code=400,
                )

            try:
                parsed_start_date = (
                    self._parse_date_string(
                        req.start_date
                    )
                )

            except ValueError as exc:
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    f"Invalid start_date: {exc}",
                    status_code=400,
                )

            try:
                parsed_end_date = (
                    self._parse_date_string(
                        req.end_date
                    )
                )

            except ValueError as exc:
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    f"Invalid end_date: {exc}",
                    status_code=400,
                )

            if parsed_end_date <= parsed_start_date:
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    (
                        "end_date cannot be before "
                        "or equal to start_date"
                    ),
                    status_code=400,
                )

            result = await self.db.execute(
                update(Sprint)
                .where(
                    Sprint.id == sprint_id,
                    Sprint.status == "planned",
                )
                .values(
                    status="active",
                    start_date=parsed_start_date,
                    end_date=parsed_end_date,
                    updated_at=datetime.now(
                        timezone.utc
                    ),
                )
            )

            if result.rowcount == 0:
                await self.db.rollback()

                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    (
                        "Only planned sprints "
                        "can be started"
                    ),
                    status_code=400,
                )

            await self.db.commit()


            response_data = {
                "id": str(sprint.id),
                "name": sprint.name,
                "goal": sprint.goal,
                "status": "active",
                "start_date": (
                    parsed_start_date.isoformat()
                    + "T00:00:00Z"
                ),
                "end_date": (
                    parsed_end_date.isoformat()
                    + "T00:00:00Z"
                ),
            }

            return response_data, None

        except SQLAlchemyError:
            await self.db.rollback()

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Failed to start sprint",
                status_code=500,
            )

    async def complete_sprint(
        self,
        project_id: str,
        sprint_id: str,
        user_id: str,
    ):
        try:
            user = await self._get_user_by_id(user_id)
            if user is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            if user.organization_id is None:
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to complete this sprint",
                    status_code=403,
                )

            sprint = await self._get_sprint_by_id(
                sprint_id=sprint_id,
                project_id=project_id,
            )
            if sprint is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            authorized, permission_error = await self._check_permission(
                user,
                str(sprint.project_id),
                "sprints",
                "modify",
            )
            if permission_error:
                return None, permission_error
            if not authorized:
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to complete this sprint",
                    status_code=403,
                )

            if sprint.status != "active":
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Only active sprints can be completed",
                    status_code=400,
                )

            final_status_ids = select(CustomStatus.id).where(
                CustomStatus.is_final.is_(True),
                CustomStatus.deleted_at.is_(None),
            )
            unfinished_status_ids = select(CustomStatus.id).where(
                CustomStatus.is_final.is_(False),
                CustomStatus.deleted_at.is_(None),
            )

            velocity_result = await self.db.execute(
                select(func.coalesce(func.sum(Task.story_points), 0)).where(
                    Task.sprint_id == sprint_id,
                    Task.deleted_at.is_(None),
                    Task.status_id.in_(final_status_ids),
                )
            )
            velocity = int(velocity_result.scalar_one() or 0)

            await self.db.execute(
                update(Task)
                .where(
                    Task.sprint_id == sprint_id,
                    Task.deleted_at.is_(None),
                    Task.status_id.in_(unfinished_status_ids),
                )
                .values(sprint_id=None)
            )

            actual_end_date = datetime.now(timezone.utc)
            result = await self.db.execute(
                update(Sprint)
                .where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.status == "active",
                    Sprint.deleted_at.is_(None),
                )
                .values(
                    status="completed",
                    actual_end_date=actual_end_date,
                    velocity=velocity,
                    updated_at=actual_end_date,
                )
            )
            if result.rowcount == 0:
                await self.db.rollback()
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Only active sprints can be completed",
                    status_code=400,
                )

            await self.db.commit()

            return {
                "id": str(sprint.id),
                "name": sprint.name,
                "goal": sprint.goal or "",
                "status": "completed",
                "start_date": (
                    sprint.start_date.isoformat() + "T00:00:00Z"
                    if sprint.start_date else None
                ),
                "end_date": (
                    sprint.end_date.isoformat() + "T00:00:00Z"
                    if sprint.end_date else None
                ),
                "actual_end_date": actual_end_date.isoformat().replace(
                    "+00:00", "Z"
                ),
            }, None

        except SQLAlchemyError:
            await self.db.rollback()
            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Failed to complete sprint",
                status_code=500,
            )

        except Exception:
            await self.db.rollback()

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                (
                    "Something went wrong. "
                    "Please try again later."
                ),
                status_code=500,
            )

    async def get_sprints(
        self,
        project_id: str,
        user_id: str,
        organization_id: str | None,
        page: int = 1,
        page_size: int = 10,
        status: str | None = None,
        search: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ):
        try:
            if page < 1:
                page = 1

            if page_size < 1:
                page_size = 10

            user = await self._get_user_by_id(user_id)

            if user is None:
                return None, None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            # Organization validation
            if (
                user.organization_id is None
                or not organization_id
            ):
                return None, None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            if str(user.organization_id) != str(organization_id):
                return None, None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            if (
                start_date is not None
                and end_date is not None
                and start_date > end_date
            ):
                return None, None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Start date must be before or equal to end date",
                    status_code=400,
                )

            # Base query
            conditions = [
                Sprint.project_id == project_id,
                Sprint.deleted_at.is_(None),
            ]

            # Search by sprint name
            if search:
                conditions.append(
                    func.lower(Sprint.name).like(
                        f"%{search.lower()}%"
                    )
                )

            # Status filter
            if status:
                conditions.append(
                    Sprint.status == status
                )

            if (
                start_date is not None
                and end_date is not None
            ):
                conditions.extend(
                    [
                        Sprint.start_date >= start_date,
                        Sprint.end_date <= end_date,
                    ]
                )

            # Count total records
            count_result = await self.db.execute(
                select(func.count(Sprint.id)).where(
                    *conditions
                )
            )

            total_items = count_result.scalar() or 0

            offset = (page - 1) * page_size

            # Fetch sprints
            result = await self.db.execute(
                select(Sprint)
                .where(*conditions)
                .order_by(Sprint.created_at.desc())
                .limit(page_size)
                .offset(offset)
            )

            sprints = result.scalars().all()

            total_pages = (
                ceil(total_items / page_size)
                if total_items > 0
                else 0
            )

            pagination = {
                "page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1,
            }

            sprint_data = []

            for sprint in sprints:
                sprint_data.append(
                    {
                        "id": str(sprint.id),
                        "name": sprint.name,
                        "goal": sprint.goal,
                        "status": sprint.status,
                        "start_date": (
                            f"{sprint.start_date.isoformat()}T00:00:00Z"
                            if sprint.start_date
                            else None
                        ),
                        "end_date": (
                            f"{sprint.end_date.isoformat()}T00:00:00Z"
                            if sprint.end_date
                            else None
                        ),
                    }
                )

            try:
                audit_log = AuditLog(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    action="viewed",
                    resource_type="sprint",
                    details="sprint viewed",
                    type=AuditLogType.AUDIT,
                    created_at=datetime.now(timezone.utc),
                )

                self.db.add(audit_log)
                await self.db.commit()

            except Exception:
                await self.db.rollback()

            return sprint_data, pagination, None

        except SQLAlchemyError:
            await self.db.rollback()

            return None, None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

        except Exception:
            await self.db.rollback()

            return None, None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

    async def get_sprint_by_id(
        self,
        project_id: str,
        sprint_id: str,
        user_id: str,
        organization_id: str | None,
    ):
        try:
            # Get current user
            user = await self._get_user_by_id(user_id)

            if user is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            if (
                user.organization_id is None
                or not organization_id
            ):
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            if str(user.organization_id) != str(organization_id):
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            # Find sprint using BOTH sprint_id and project_id
            result = await self.db.execute(
                select(Sprint).where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.deleted_at.is_(None),
                )
            )

            sprint = result.scalar_one_or_none()

            if sprint is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            sprint_data = {
                "id": str(sprint.id),
                "name": sprint.name,
                "goal": sprint.goal,
                "status": sprint.status,
                "start_date": (
                    f"{sprint.start_date.isoformat()}T00:00:00Z"
                    if sprint.start_date
                    else None
                ),
                "end_date": (
                    f"{sprint.end_date.isoformat()}T00:00:00Z"
                    if sprint.end_date
                    else None
                ),
            }

            try:
                audit_log = AuditLog(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    sprint_id=sprint_id,
                    action="viewed",
                    resource_type="sprint",
                    resource_id=sprint_id,
                    details=f"Sprint details viewed by {user.username}",
                    type=AuditLogType.VIEW,
                    created_at=datetime.now(timezone.utc),
                )

                self.db.add(audit_log)
                await self.db.commit()

            except Exception:
                await self.db.rollback()

            return sprint_data, None

        except SQLAlchemyError:
            await self.db.rollback()

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

        except Exception:
            await self.db.rollback()

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

    async def update_sprint(
        self,
        req: UpdateSprintRequest,
        project_id: str,
        sprint_id: str,
        user_id: str,
        organization_id: str | None,
    ):
        try:
            # 1. Get user
            user = await self._get_user_by_id(user_id)

            if user is None:
                return error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            # 2. Organization validation
            if user.organization_id is None or not organization_id:
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            if str(user.organization_id) != str(organization_id):
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            # 3.CheckPermission(..., "sprints", "modify")
            authorized, permission_error = await self._check_permission(
                user,
                project_id,
                "sprints",
                "modify",
            )

            if permission_error:
                return permission_error

            if not authorized:
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to update sprints for this project",
                    status_code=403,
                )

            # 4. Get existing sprint using BOTH sprint and project
            existing_sprint = await self._get_sprint_by_id(
                sprint_id,
                project_id,
            )

            if existing_sprint is None:
                return error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            updates: dict = {}
            changes: list[str] = []

            start_date = existing_sprint.start_date
            end_date = existing_sprint.end_date

            today = datetime.now().date()

            if req.start_date is not None:

                if not req.start_date.strip():
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "start_date cannot be empty",
                        status_code=400,
                    )

                try:
                    parsed_start_date = datetime.strptime(
                        req.start_date,
                        "%Y-%m-%d",
                    ).date()

                except ValueError:
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "Invalid start_date. Expected format: YYYY-MM-DD",
                        status_code=400,
                    )

                if parsed_start_date < today:
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "Past start date is not allowed",
                        status_code=400,
                    )

                if (
                    end_date is not None
                    and parsed_start_date > end_date
                ):
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "Start date cannot be after end date",
                        status_code=400,
                    )

                old_date = (
                    existing_sprint.start_date.isoformat()
                    if existing_sprint.start_date
                    else "NULL"
                )

                new_date = parsed_start_date.isoformat()

                if old_date != new_date:
                    changes.append(
                        f"start date changed from '{old_date}' "
                        f"to '{new_date}'"
                    )

                start_date = parsed_start_date
                updates["start_date"] = parsed_start_date

            if req.end_date is not None:

                if not req.end_date.strip():
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "end_date cannot be empty",
                        status_code=400,
                    )

                try:
                    parsed_end_date = datetime.strptime(
                        req.end_date,
                        "%Y-%m-%d",
                    ).date()

                except ValueError:
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "Invalid end_date. Expected format: YYYY-MM-DD",
                        status_code=400,
                    )

                if parsed_end_date < today:
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "Past end date is not allowed",
                        status_code=400,
                    )

                if (
                    start_date is not None
                    and parsed_end_date < start_date
                ):
                    return error_response(
                        ErrorCode.ErrBadRequest,
                        "End date cannot be before start date",
                        status_code=400,
                    )

                old_date = (
                    existing_sprint.end_date.isoformat()
                    if existing_sprint.end_date
                    else "NULL"
                )

                new_date = parsed_end_date.isoformat()

                if old_date != new_date:
                    changes.append(
                        f"end date changed from '{old_date}' "
                        f"to '{new_date}'"
                    )

                end_date = parsed_end_date
                updates["end_date"] = parsed_end_date

            if req.name is not None:
                updates["name"] = req.name

                if req.name != existing_sprint.name:
                    changes.append(
                        f"name changed from '{existing_sprint.name}' "
                        f"to '{req.name}'"
                    )

            if req.goal is not None:
                updates["goal"] = req.goal

                if req.goal != existing_sprint.goal:
                    changes.append(
                        f"goal changed from '{existing_sprint.goal}' "
                        f"to '{req.goal}'"
                    )

            new_status = existing_sprint.status

            if req.status is not None:
                new_status = req.status
                updates["status"] = new_status

                if new_status != existing_sprint.status:
                    changes.append(
                        f"status changed from "
                        f"'{existing_sprint.status}' "
                        f"to '{new_status}'"
                    )

            if (
                req.status is not None
                and new_status == "completed"
                and existing_sprint.status != "completed"
            ):

                completed_result = await self.db.execute(
                    text(
                        """
                        SELECT COALESCE(SUM(t.story_points), 0)
                        FROM tasks t
                        WHERE t.sprint_id = :sprint_id
                        AND t.deleted_at IS NULL
                        AND t.status_id IN (
                            SELECT cs.id
                            FROM custom_statuses cs
                            WHERE cs.is_final = true
                                AND cs.deleted_at IS NULL
                        )
                        """
                    ),
                    {
                        "sprint_id": sprint_id,
                    },
                )

                velocity = completed_result.scalar() or 0

                updates["velocity"] = int(velocity)
                updates["actual_end_date"] = datetime.now(
                    timezone.utc
                )

                await self.db.execute(
                    text(
                        """
                        UPDATE tasks
                        SET sprint_id = NULL
                        WHERE sprint_id = :sprint_id
                        AND deleted_at IS NULL
                        AND status_id IN (
                            SELECT cs.id
                            FROM custom_statuses cs
                            WHERE cs.is_final = false
                                AND cs.deleted_at IS NULL
                        )
                        """
                    ),
                    {
                        "sprint_id": sprint_id,
                    },
                )

            if not updates:
                return None

            changed_by = (
                getattr(user, "user_name", None)
                or getattr(user, "username", None)
                or getattr(user, "full_name", None)
                or getattr(user, "email", None)
                or str(user_id)
            )

            if changes:
                detail = (
                    f"Sprint updated by {changed_by}: "
                    + ", ".join(changes)
                )
            else:
                detail = (
                    f"Sprint details updated by {changed_by}"
                )

            # Perform update
            result = await self.db.execute(
                update(Sprint)
                .where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                )
                .values(**updates)
            )

            if result.rowcount == 0:
                await self.db.rollback()

                return error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            # Audit log
            audit_log = AuditLog(
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                sprint_id=sprint_id,
                action="updated",
                resource_type="sprint",
                resource_id=sprint_id,
                details=detail,
                type=AuditLogType.ACTIVITY,
                created_at=datetime.now(timezone.utc),
            )

            self.db.add(audit_log)

            await self.db.commit()

            return None

        except IntegrityError as exc:
            await self.db.rollback()

            error_text = str(exc).lower()

            if (
                "idx_project_sprint_name" in error_text
                or (
                    "project_id" in error_text
                    and "name" in error_text
                )
            ):
                return error_response(
                    ErrorCode.ErrConflict,
                    "Sprint name already exists in this project",
                    status_code=409,
                )

            return error_response(
                ErrorCode.ErrConflict,
                "Sprint already exists",
                status_code=409,
            )

        except SQLAlchemyError:
            await self.db.rollback()

            return error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

        except Exception:
            await self.db.rollback()

            return error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

    async def delete_sprint(
        self,
        project_id: str,
        sprint_id: str,
        user_id: str,
        organization_id: str | None,
    ):
        try:
            # 1. validates sprint ID
            if not sprint_id:
                return error_response(
                    ErrorCode.ErrBadRequest,
                    "Invalid sprint id",
                    status_code=400,
                )

            # 2. Get user
            user = await self._get_user_by_id(user_id)

            if user is None:
                return error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            # 3. Organization validation
            if (
                user.organization_id is None
                or not organization_id
            ):
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            if str(user.organization_id) != str(organization_id):
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            # 4.CheckPermission:
            # resource = "sprints"
            # action   = "delete"
            authorized, permission_error = await self._check_permission(
                user,
                project_id,
                "sprints",
                "delete",
            )

            if permission_error:
                return permission_error

            if not authorized:
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to delete sprints from this project",
                    status_code=403,
                )

            # 5. Get sprint name for audit logging.
            sprint_name = sprint_id

            try:
                sprint = await self._get_sprint_by_id(
                    sprint_id,
                    project_id,
                )

                if sprint is not None and sprint.name:
                    sprint_name = sprint.name

            except Exception:
                pass

            # 6. Delete sprint.
            result = await self.db.execute(
                update(Sprint)
                .where(
                    Sprint.id == sprint_id,
                    Sprint.deleted_at.is_(None),
                )
                .values(
                    deleted_at=datetime.now(timezone.utc)
                )
            )

            if result.rowcount == 0:
                await self.db.rollback()

                return error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            await self.db.commit()

            # 7. Audit logging.
            try:
                user_name = (
                    getattr(user, "user_name", None)
                    or getattr(user, "username", None)
                    or getattr(user, "full_name", None)
                    or getattr(user, "email", None)
                    or str(user_id)
                )

                audit_log = AuditLog(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    sprint_id=sprint_id,
                    action="deleted",
                    resource_type="sprint",
                    resource_id=sprint_id,
                    details=(
                        f"The sprint '{sprint_name}' "
                        f"was deleted by {user_name}"
                    ),
                    type=AuditLogType.ACTIVITY,
                    created_at=datetime.now(timezone.utc),
                )

                self.db.add(audit_log)
                await self.db.commit()

            except Exception:
                await self.db.rollback()

            return None

        except SQLAlchemyError:
            await self.db.rollback()

            return error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

        except Exception:
            await self.db.rollback()

            return error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

    async def get_sprint_burndown(
        self,
        sprint_id: str,
        project_id: str,
        user_id: str,
        organization_id: str | None,
    ):
        try:
            # 1. Get user
            user = await self._get_user_by_id(user_id)

            if user is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            # 2. Organization validation 
            if user.organization_id is None or not organization_id:
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            if str(user.organization_id) != str(organization_id):
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            # 3. Get sprint by BOTH sprint ID and project ID
            sprint_result = await self.db.execute(
                select(Sprint).where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.deleted_at.is_(None),
                )
            )

            sprint = sprint_result.scalar_one_or_none()

            if sprint is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            if sprint.start_date is None or sprint.end_date is None:
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Burndown chart cannot be generated for sprints without start and end dates",
                    status_code=400,
                )

            # 4. Fetch snapshots ordered by date ASC
            snapshot_result = await self.db.execute(
                select(SprintSnapshot)
                .where(
                    SprintSnapshot.sprint_id == sprint_id
                )
                .order_by(
                    SprintSnapshot.date.asc()
                )
            )

            snapshots = snapshot_result.scalars().all()

            # 5. Calculate total story points dynamically
            total_result = await self.db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(story_points), 0)
                    FROM tasks
                    WHERE sprint_id = :sprint_id
                    AND deleted_at IS NULL
                    """
                ),
                {
                    "sprint_id": sprint_id,
                },
            )

            total_story_points = int(
                total_result.scalar() or 0
            )

            # 6. Calculate remaining story points dynamically
            remaining_result = await self.db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(story_points), 0)
                    FROM tasks
                    WHERE sprint_id = :sprint_id
                    AND deleted_at IS NULL
                    AND status_id IN (
                        SELECT id
                        FROM custom_statuses
                        WHERE is_final = false
                            AND deleted_at IS NULL
                    )
                    """
                ),
                {
                    "sprint_id": sprint_id,
                },
            )

            remaining_points_now = int(
                remaining_result.scalar() or 0
            )

            # 7. Map snapshots by YYYY-MM-DD
            snapshot_map = {
                snapshot.date.isoformat(): snapshot
                for snapshot in snapshots
            }

            start_date = sprint.start_date
            end_date = sprint.end_date

            total_days = (
                end_date - start_date
            ).days


            if total_days <= 0:
                total_days = 1

            today = datetime.now(
                timezone.utc
            ).date()

            burndown_data = []

            for i in range(total_days + 1):
                current_date = (
                    start_date
                    + timedelta(days=i)
                )

                date_string = (
                    current_date.isoformat()
                )


                ideal_value = (
                    float(total_story_points)
                    * (
                        1.0
                        - (
                            float(i)
                            / float(total_days)
                        )
                    )
                )

                if ideal_value < 0:
                    ideal_value = 0

                remaining_points = None

                if current_date <= today:

                    snapshot = snapshot_map.get(
                        date_string
                    )

                    if snapshot is not None:

                        remaining_points = (
                            snapshot.remaining_story_points
                        )

                    elif current_date == today:

                        remaining_points = (
                            remaining_points_now
                        )

                    else:

                        previous_value = (
                            total_story_points
                        )

                        if burndown_data:
                            previous_remaining = (
                                burndown_data[-1][
                                    "remaining_points"
                                ]
                            )

                            if previous_remaining is not None:
                                previous_value = (
                                    previous_remaining
                                )

                        remaining_points = (
                            previous_value
                        )

                burndown_data.append(
                    {
                        "date": date_string,
                        "remaining_points": (
                            remaining_points
                        ),
                        "ideal_value": (
                            ideal_value
                        ),
                    }
                )

            # 8. Audit log is best effort 
            try:
                audit_log = AuditLog(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    sprint_id=sprint_id,
                    action="viewed",
                    resource_type="sprint",
                    resource_id=sprint_id,
                    details="Sprint burndown chart viewed",
                    type=AuditLogType.AUDIT,
                    created_at=datetime.now(
                        timezone.utc
                    ),
                )

                self.db.add(audit_log)
                await self.db.commit()

            except Exception:
                await self.db.rollback()

            # 9. Exact data structure
            data = {
                "sprint_id": str(sprint.id),
                "sprint_name": sprint.name,
                "total_story_points": (
                    total_story_points
                ),
                "burndown_data": (
                    burndown_data
                ),
            }

            return data, None

        except SQLAlchemyError as exc:
            await self.db.rollback()

            logger.exception(
                "GET SPRINT BURNDOWN SQL ERROR: %s",
                exc,
            )

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

        except Exception as exc:
            await self.db.rollback()

            logger.exception(
                "GET SPRINT BURNDOWN ERROR: %s",
                exc,
            )

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

    async def trigger_daily_snapshots(
        self,
        project_id: str,
        user_id: str,
        organization_id: str,
    ):
        try:
            # 1. Get user
            user = await self._get_user_by_id(user_id)

            if user is None:
                return error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            # 2. Get project
            project = await self._get_project_by_id(project_id)

            if project is None:
                return error_response(
                    ErrorCode.ErrNotFound,
                    "Project not found",
                    status_code=404,
                )

            # 3. Organization check
            if (
                user.organization_id is None
                or str(user.organization_id) != str(project.organization_id)
            ):
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to perform this action",
                    status_code=403,
                )

            # 4. Permission: sprints:modify
            authorized, permission_error = await self._check_permission(
                user,
                project_id,
                "sprints",
                "modify",
            )

            if permission_error:
                return permission_error

            if not authorized:
                return error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to modify sprints for this project",
                    status_code=403,
                )

            result = await self.db.execute(
                select(Sprint).where(
                    Sprint.status == "active",
                    Sprint.deleted_at.is_(None),
                )
            )

            active_sprints = result.scalars().all()

            now = datetime.now(timezone.utc)
            today = now.date()

            for sprint in active_sprints:

                # 5. Total story points
                try:
                    total_result = await self.db.execute(
                        text(
                            """
                            SELECT COALESCE(SUM(story_points), 0)
                            FROM tasks
                            WHERE sprint_id = :sprint_id
                            AND deleted_at IS NULL
                            """
                        ),
                        {
                            "sprint_id": sprint.id,
                        },
                    )

                    total_points = int(
                        total_result.scalar() or 0
                    )

                except Exception:
                    continue

                # 6. Remaining story points
                try:
                    remaining_result = await self.db.execute(
                        text(
                            """
                            SELECT COALESCE(SUM(story_points), 0)
                            FROM tasks
                            WHERE sprint_id = :sprint_id
                            AND deleted_at IS NULL
                            AND status_id IN (
                                SELECT id
                                FROM custom_statuses
                                WHERE is_final = false
                                    AND deleted_at IS NULL
                            )
                            """
                        ),
                        {
                            "sprint_id": sprint.id,
                        },
                    )

                    remaining_points = int(
                        remaining_result.scalar() or 0
                    )

                except Exception:
                    continue

                existing_result = await self.db.execute(
                    select(SprintSnapshot).where(
                        SprintSnapshot.sprint_id == sprint.id,
                        SprintSnapshot.date == today,
                    )
                )

                snapshot = existing_result.scalar_one_or_none()

                try:
                    if snapshot is None:
                        snapshot = SprintSnapshot(
                            sprint_id=sprint.id,
                            date=today,
                            total_story_points=total_points,
                            remaining_story_points=remaining_points,
                            created_at=now,
                        )

                        self.db.add(snapshot)

                    else:
                        snapshot.total_story_points = total_points
                        snapshot.remaining_story_points = remaining_points

                    await self.db.commit()

                except Exception:
                    await self.db.rollback()
                    continue

            # 7. Audit log - best effort
            try:
                audit_log = AuditLog(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    action="created",
                    resource_type="sprint",
                    details="Sprint snapshot triggered",
                    type=AuditLogType.AUDIT,
                    created_at=datetime.now(timezone.utc),
                )

                self.db.add(audit_log)
                await self.db.commit()

            except Exception:
                await self.db.rollback()

            return None

        except SQLAlchemyError:
            await self.db.rollback()

            return error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

        except Exception:
            await self.db.rollback()

            return error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )
   

    async def complete_sprint(
        self,
        project_id: str,
        sprint_id: str,
        user_id: str,
    ):
        try:
            # Get user
            user = await self._get_user_by_id(user_id)

            if user is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "User not found",
                    status_code=404,
                )

            # Get sprint using sprint_id + project_id
            sprint_result = await self.db.execute(
                select(Sprint).where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.deleted_at.is_(None),
                )
            )

            sprint = sprint_result.scalar_one_or_none()

            if sprint is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            # Go checks user organization exists
            if user.organization_id is None:
                return None, error_response(
                    ErrorCode.ErrForbidden,
                    "You do not have permission to complete this sprint",
                    status_code=403,
                )

            # Get project membership
            member = await self._get_project_member(
                user_id,
                sprint.project_id,
            )

            role_name = getattr(
                getattr(user, "role", None),
                "name",
                None,
            )

            is_org_admin = role_name == "org_admin"

            # User is not project member
            if member is None:
                if not is_org_admin:
                    return None, error_response(
                        ErrorCode.ErrForbidden,
                        "You do not have permission to complete this sprint",
                        status_code=403,
                    )

            else:
                member_role = getattr(
                    getattr(member, "role", None),
                    "name",
                    None,
                )

                if (
                    member_role not in (
                        "org_admin",
                        "project_manager",
                    )
                    and not is_org_admin
                ):
                    return None, error_response(
                        ErrorCode.ErrForbidden,
                        "You do not have permission to complete this sprint",
                        status_code=403,
                    )

            # Only ACTIVE sprint can be completed
            if sprint.status != "active":
                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Only active sprints can be completed",
                    status_code=400,
                )

            # Backend generates actual end date
            actual_end_date = datetime.now(timezone.utc)

            # Calculate velocity from COMPLETED tasks only
            velocity_result = await self.db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(story_points), 0)
                    FROM tasks
                    WHERE sprint_id = :sprint_id
                    AND deleted_at IS NULL
                    AND status_id IN (
                        SELECT id
                        FROM custom_statuses
                        WHERE is_final = true
                            AND deleted_at IS NULL
                    )
                    """
                ),
                {
                    "sprint_id": sprint_id,
                },
            )

            velocity = int(
                velocity_result.scalar() or 0
            )

            # Move INCOMPLETE tasks back to backlog
            await self.db.execute(
                text(
                    """
                    UPDATE tasks
                    SET sprint_id = NULL
                    WHERE sprint_id = :sprint_id
                    AND deleted_at IS NULL
                    AND status_id IN (
                        SELECT id
                        FROM custom_statuses
                        WHERE is_final = false
                            AND deleted_at IS NULL
                    )
                    """
                ),
                {
                    "sprint_id": sprint_id,
                },
            )

            # Complete sprint
            update_result = await self.db.execute(
                update(Sprint)
                .where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.status == "active",
                    Sprint.deleted_at.is_(None),
                )
                .values(
                    status="completed",
                    actual_end_date=actual_end_date,
                    velocity=velocity,
                )
            )

            if update_result.rowcount == 0:
                await self.db.rollback()

                return None, error_response(
                    ErrorCode.ErrBadRequest,
                    "Only active sprints can be completed",
                    status_code=400,
                )

            await self.db.commit()

            #  Fetch updated sprint
            updated_result = await self.db.execute(
                select(Sprint).where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.deleted_at.is_(None),
                )
            )

            updated_sprint = updated_result.scalar_one_or_none()

            if updated_sprint is None:
                return None, error_response(
                    ErrorCode.ErrNotFound,
                    "Sprint not found",
                    status_code=404,
                )

            data = {
                "id": str(updated_sprint.id),
                "name": updated_sprint.name,
                "goal": updated_sprint.goal,
                "status": updated_sprint.status,
                "start_date": (
                    f"{updated_sprint.start_date.isoformat()}T00:00:00Z"
                    if updated_sprint.start_date
                    else None
                ),
                "end_date": (
                    f"{updated_sprint.end_date.isoformat()}T00:00:00Z"
                    if updated_sprint.end_date
                    else None
                ),
                "actual_end_date": (
                    updated_sprint.actual_end_date.isoformat()
                    if updated_sprint.actual_end_date
                    else None
                ),
            }

            return data, None

        except SQLAlchemyError as exc:
            await self.db.rollback()

            logger.exception(
                "COMPLETE SPRINT SQL ERROR: %s",
                exc,
            )

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )

        except Exception as exc:
            await self.db.rollback()

            logger.exception(
                "COMPLETE SPRINT ERROR: %s",
                exc,
            )

            return None, error_response(
                ErrorCode.ErrInternalServerError,
                "Something went wrong. Please try again later.",
                status_code=500,
            )