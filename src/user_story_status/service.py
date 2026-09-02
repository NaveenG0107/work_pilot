import uuid
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid6 import uuid7

from src.audit.models import AuditLog, AuditLogType
from src.auth.models import User
from src.config import get_logger
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.user_story.models import UserStory
from src.user_story_status.models import UserStoryStatus
from src.user_story_status.schema import (
    CreateUserStoryStatusRequest,
    UpdateUserStoryStatusRequest,
    UserStoryStatusResponse,
)

logger = get_logger(__name__)


class UserStoryStatusServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val).strip().strip('"').strip("'"))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class UserStoryStatusService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_project(self, project_id_or_slug: str) -> Project:
        if _is_valid_uuid(project_id_or_slug):
            query = select(Project).where(
                Project.id == str(project_id_or_slug),
                Project.deleted_at.is_(None)
            )
        else:
            query = select(Project).where(
                Project.slug == str(project_id_or_slug),
                Project.deleted_at.is_(None)
            )

        project = (await self.db.execute(query)).scalar_one_or_none()

        if not project:
            logger.warning("Project not found for project_id_or_slug=%s", project_id_or_slug)
            raise UserStoryStatusServiceError(404, "NOT_FOUND", "Project not found")

        return project

    async def _get_user(self, user_id: str) -> User:
        user_query = select(User).where(
            User.id == str(user_id),
            User.deleted_at.is_(None)
        ).options(
            selectinload(User.role).selectinload(Role.permissions)
        )

        user = (await self.db.execute(user_query)).scalar_one_or_none()

        if not user:
            logger.warning("User not found for user_id=%s", user_id)
            raise UserStoryStatusServiceError(404, "NOT_FOUND", "User not found")

        return user

    async def _check_authorization(self, project: Project, user_id: str) -> User:
        user = await self._get_user(user_id)

        role_name = getattr(user.role, "name", "") or ""
        if role_name.lower() in ("superadmin", "super_admin"):
            logger.warning("Super admin user_id=%s blocked from project activity", user_id)
            raise UserStoryStatusServiceError(
                403,
                "FORBIDDEN",
                "Super admins are not allowed to perform organization-level activities"
            )

        member = (await self.db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == str(project.id),
                ProjectMember.user_id == str(user.id),
                ProjectMember.deleted_at.is_(None)
            )
        )).scalars().first()

        if member is not None or role_name.lower() == "org_admin":
            return user

        logger.warning("User user_id=%s forbidden from project_id=%s", user_id, project.id)
        raise UserStoryStatusServiceError(403, "FORBIDDEN", "You do not have permission to view User Story statuses in this project")

    async def _create_audit_log(
        self,
        user_id: str,
        organization_id: str,
        project_id: str,
        action: str,
        resource_id: str | None = None,
        details: str | None = None,
    ):
        try:
            audit_log = AuditLog(
                user_id=str(user_id),
                organization_id=str(organization_id) if organization_id else None,
                project_id=str(project_id),
                action=action,
                resource_type="user_story_status",
                resource_id=str(resource_id) if resource_id else None,
                details=details,
                type=AuditLogType.AUDIT if hasattr(AuditLogType, "AUDIT") else "audit",
                created_at=datetime.now(timezone.utc),
            )
            self.db.add(audit_log)
            await self.db.commit()
        except Exception as exc:
            logger.warning("Failed to create audit log: %s", exc)

    async def create_status(
        self,
        request: CreateUserStoryStatusRequest,
        project_id_or_slug: str,
        user_id: str,
        organization_id: str | None = None,
    ) -> UserStoryStatusResponse:
        logger.info("Creating User Story status for project=%s, name=%s", project_id_or_slug, request.name)

        project = await self._get_project(project_id_or_slug)
        await self._check_authorization(project, user_id)

        name = request.name.strip()
        if not name or len(name) > 50:
            raise UserStoryStatusServiceError(422, "VALIDATION_ERROR", "Status name must be between 1 and 50 characters")

        existing = (await self.db.execute(
            select(UserStoryStatus).where(
                UserStoryStatus.project_id == str(project.id),
                func.lower(UserStoryStatus.name) == name.lower(),
                UserStoryStatus.deleted_at.is_(None)
            )
        )).scalar_one_or_none()

        if existing is not None:
            logger.warning("Duplicate status name=%s in project_id=%s", name, project.id)
            raise UserStoryStatusServiceError(409, "CONFLICT", "Status name already exists in this project")

        is_closed = False
        is_final = False

        if request.is_final is not None:
            is_final = bool(request.is_final)
            is_closed = is_final
        elif request.is_closed is not None:
            is_closed = bool(request.is_closed)
            is_final = is_closed

        new_status = UserStoryStatus(
            id=str(uuid7()),
            project_id=str(project.id),
            name=name,
            color=request.color,
            display_order=request.display_order,
            is_closed=is_closed,
            is_final=is_final,
            is_default=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        self.db.add(new_status)
        await self.db.commit()

        await self._create_audit_log(
            user_id=user_id,
            organization_id=organization_id or getattr(project, "organization_id", None),
            project_id=str(project.id),
            action="created",
            resource_id=new_status.id,
            details=f"User Story Status '{new_status.name}' created",
        )

        logger.info("Successfully created User Story status ID=%s", new_status.id)
        return UserStoryStatusResponse.model_validate(new_status)

    async def get_statuses(
        self,
        project_id_or_slug: str,
        user_id: str,
        organization_id: str | None = None,
    ) -> list[UserStoryStatusResponse]:
        logger.info("Retrieving User Story statuses for project=%s", project_id_or_slug)

        project = await self._get_project(project_id_or_slug)
        await self._check_authorization(project, user_id)

        statuses_query = select(UserStoryStatus).where(
            UserStoryStatus.project_id == str(project.id),
            UserStoryStatus.deleted_at.is_(None)
        ).order_by(UserStoryStatus.display_order.asc())

        statuses = list((await self.db.execute(statuses_query)).scalars())

        response_list = [
            UserStoryStatusResponse.model_validate(st)
            for st in statuses
        ]

        for idx, res_item in enumerate(response_list):
            res_item.display_order = idx

        await self._create_audit_log(
            user_id=user_id,
            organization_id=organization_id or getattr(project, "organization_id", None),
            project_id=str(project.id),
            action="viewed",
            details="User Story statuses viewed",
        )

        logger.info("Retrieved %d statuses for project_id=%s", len(response_list), project.id)
        return response_list

    async def update_status(
        self,
        request: UpdateUserStoryStatusRequest,
        project_id_or_slug: str,
        status_id: str,
        user_id: str,
        organization_id: str | None = None,
    ) -> UserStoryStatusResponse:
        logger.info("Updating User Story status ID=%s for project=%s", status_id, project_id_or_slug)

        project = await self._get_project(project_id_or_slug)
        await self._check_authorization(project, user_id)

        status_model = (await self.db.execute(
            select(UserStoryStatus).where(
                UserStoryStatus.id == str(status_id),
                UserStoryStatus.project_id == str(project.id),
                UserStoryStatus.deleted_at.is_(None)
            )
        )).scalar_one_or_none()

        if status_model is None:
            logger.warning("Status ID=%s not found in project_id=%s", status_id, project.id)
            raise UserStoryStatusServiceError(404, "NOT_FOUND", "User story status not found")

        updated = False

        if request.name is not None:
            trimmed_name = request.name.strip()
            if not trimmed_name or len(trimmed_name) > 50:
                raise UserStoryStatusServiceError(422, "VALIDATION_ERROR", "Status name must be between 1 and 50 characters")

            if trimmed_name.lower() != status_model.name.lower():
                existing = (await self.db.execute(
                    select(UserStoryStatus).where(
                        UserStoryStatus.project_id == str(project.id),
                        func.lower(UserStoryStatus.name) == trimmed_name.lower(),
                        UserStoryStatus.deleted_at.is_(None)
                    )
                )).scalar_one_or_none()

                if existing is not None:
                    raise UserStoryStatusServiceError(409, "CONFLICT", "Status name already exists in this project")

            if trimmed_name != status_model.name:
                status_model.name = trimmed_name
                updated = True

        if request.color is not None and request.color != status_model.color:
            status_model.color = request.color
            updated = True

        if request.display_order is not None and request.display_order != status_model.display_order:
            status_model.display_order = request.display_order
            updated = True

        if request.is_closed is not None and request.is_closed != status_model.is_closed:
            status_model.is_closed = bool(request.is_closed)
            status_model.is_final = bool(request.is_closed)
            updated = True

        if request.is_final is not None and request.is_final != status_model.is_final:
            status_model.is_final = bool(request.is_final)
            status_model.is_closed = bool(request.is_final)
            updated = True

        if updated:
            status_model.updated_at = datetime.now(timezone.utc)
            await self.db.commit()

            await self._create_audit_log(
                user_id=user_id,
                organization_id=organization_id or getattr(project, "organization_id", None),
                project_id=str(project.id),
                action="updated",
                resource_id=status_model.id,
                details=f"User Story Status '{status_model.name}' updated",
            )

        logger.info("Successfully updated User Story status ID=%s", status_model.id)
        return UserStoryStatusResponse.model_validate(status_model)

    async def delete_status(
        self,
        status_id: str,
        project_id_or_slug: str,
        user_id: str,
        organization_id: str | None = None,
    ) -> dict:
        logger.info("Deleting User Story status ID=%s from project=%s", status_id, project_id_or_slug)

        project = await self._get_project(project_id_or_slug)
        await self._check_authorization(project, user_id)

        status_model = (await self.db.execute(
            select(UserStoryStatus).where(
                UserStoryStatus.id == str(status_id),
                UserStoryStatus.project_id == str(project.id),
                UserStoryStatus.deleted_at.is_(None)
            )
        )).scalar_one_or_none()

        if status_model is None:
            logger.warning("Status ID=%s not found for deletion in project_id=%s", status_id, project.id)
            raise UserStoryStatusServiceError(404, "NOT_FOUND", "User story status not found")

        if status_model.is_default:
            logger.warning("Attempt to delete default status ID=%s", status_id)
            raise UserStoryStatusServiceError(400, "BUSINESS_RULE_VIOLATION", "A default status cannot be deleted")

        statuses = list((await self.db.execute(
            select(UserStoryStatus).where(
                UserStoryStatus.project_id == str(project.id),
                UserStoryStatus.deleted_at.is_(None)
            )
        )).scalars())

        if len(statuses) <= 1:
            logger.warning("Attempt to delete only status ID=%s in project_id=%s", status_id, project.id)
            raise UserStoryStatusServiceError(400, "BUSINESS_RULE_VIOLATION", "The project's only status cannot be deleted")

        stories_count = (await self.db.execute(
            select(func.count(UserStory.id)).where(
                UserStory.project_id == str(project.id),
                UserStory.status_id == str(status_id),
                UserStory.deleted_at.is_(None)
            )
        )).scalar() or 0

        if stories_count > 0:
            logger.warning("Status ID=%s is assigned to %d active stories", status_id, stories_count)
            raise UserStoryStatusServiceError(
                400,
                "BUSINESS_RULE_VIOLATION",
                "A status cannot be deleted while it is assigned to existing User Stories"
            )

        status_model.deleted_at = datetime.now(timezone.utc)
        await self.db.commit()

        await self._create_audit_log(
            user_id=user_id,
            organization_id=organization_id or getattr(project, "organization_id", None),
            project_id=str(project.id),
            action="deleted",
            resource_id=status_model.id,
            details=f"User Story Status '{status_model.name}' deleted",
        )

        logger.info("Successfully deleted User Story status ID=%s", status_id)
        return {"status_id": str(status_id)}
