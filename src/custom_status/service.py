# src/custom_status/service.py
import logging
from datetime import datetime, timezone
from typing import Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid6 import uuid7

from src.audit.models import AuditLog
from src.auth.models import User
from src.custom_status.models import CustomStatus
from src.custom_status.schemas import (
    CreateCustomStatusRequest,
    CustomStatusResponse,
    UpdateCustomStatusRequest,
)
from src.middleware.rbac import has_default_permission
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.task.models import Task

logger = logging.getLogger(__name__)


def normalize_task_status(status: str) -> str:
    """Normalizes a status name by lowering, trimming, and replacing spaces with underscores."""
    s = status.strip().lower()
    return s.replace(" ", "_")


class CustomStatusService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _check_permission(
        self, user: User, project_id: str, resource: str, action: str
    ) -> bool:
        """
        Evaluates project permissions considering project-level member role overrides
        and falling back to the organization-level role permissions.
        """
        if user.role and user.role.name == "super_admin":
            return False

        # 1. Check project-level member role first
        stmt = (
            select(ProjectMember)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
            )
            .options(selectinload(ProjectMember.role).selectinload(Role.permissions))
        )
        res = await self.db.execute(stmt)
        pm = res.scalar_one_or_none()

        if pm and pm.role:
            for p in pm.role.permissions:
                if p.resource == resource and p.action == action:
                    return True
            return False

        # 2. Fall back to organization role permissions
        if user.role and user.role.permissions:
            for p in user.role.permissions:
                if p.resource == resource and p.action == action:
                    return True
            if has_default_permission(user.role.name, resource, action):
                return True

        return False

    async def create_status(
        self,
        project_id: str,
        user_id: str,
        organization_id: str,
        payload: CreateCustomStatusRequest,
    ) -> CustomStatusResponse:
        """
        Creates a new custom status for a project.
        """
        logger.info(
            "Creating custom status '%s' in project %s by user %s",
            payload.name,
            project_id,
            user_id,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:modify)
        can_manage = await self._check_permission(user, project_id, "projects", "modify")
        if not can_manage:
            logger.error("User %s does not have permission to manage custom statuses in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to manage custom statuses in this project",
            )

        # 4. Check uniqueness in project
        trimmed_name = payload.name.strip()
        normalized_name = normalize_task_status(trimmed_name)

        existing_stmt = (
            select(CustomStatus)
            .where(
                CustomStatus.project_id == project_id,
                func.lower(CustomStatus.name) == trimmed_name.lower(),
                CustomStatus.deleted_at.is_(None),
            )
        )
        existing_res = await self.db.execute(existing_stmt)
        if existing_res.scalar_one_or_none():
            logger.warning("Custom status '%s' already exists in project %s", trimmed_name, project_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Status name already exists in this project",
            )

        # 5. Create new custom status
        now = datetime.now(timezone.utc)
        status_id = str(uuid7())
        custom_status = CustomStatus(
            id=status_id,
            project_id=project_id,
            name=trimmed_name,
            color=payload.color.strip(),
            display_order=payload.display_order,
            is_default=False,
            is_final=bool(payload.is_final),
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self.db.add(custom_status)

        # 6. Audit Logging
        project_name = project.name or "project"
        user_name = user.full_name or user.username or str(user_id)
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="created",
            resource_type="custom_status",
            resource_id=status_id,
            details=f"Custom Status '{trimmed_name}' created for project '{project_name}' by {user_name}",
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)

        # 7. Commit transaction
        try:
            await self.db.commit()
            await self.db.refresh(custom_status)
            logger.info("Successfully created custom status '%s' (ID: %s) for project %s", trimmed_name, status_id, project_id)
        except Exception as exc:
            await self.db.rollback()
            logger.exception("Failed to create custom status in database: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create custom status",
            ) from exc

        return CustomStatusResponse(
            id=UUID(custom_status.id),
            project_id=UUID(custom_status.project_id),
            name=custom_status.name,
            color=custom_status.color,
            display_order=custom_status.display_order,
            is_default=custom_status.is_default,
            is_final=custom_status.is_final,
        )

    async def get_statuses(
        self,
        project_id: str,
        user_id: str,
        organization_id: str,
    ) -> list[CustomStatusResponse]:
        """
        Retrieves all custom statuses for a project.
        """
        logger.info(
            "Fetching custom statuses for project %s by user %s",
            project_id,
            user_id,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:view)
        can_view = await self._check_permission(user, project_id, "projects", "view")
        if not can_view:
            logger.error("User %s does not have permission to view custom statuses in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view custom statuses in this project",
            )

        # 4. Fetch custom statuses
        statuses_stmt = (
            select(CustomStatus)
            .where(
                CustomStatus.project_id == project_id,
                CustomStatus.deleted_at.is_(None),
            )
            .order_by(CustomStatus.display_order.asc())
        )
        statuses_res = await self.db.execute(statuses_stmt)
        custom_statuses = statuses_res.scalars().all()

        # 5. Build responses and re-assign display_order dynamically to be strictly sequential (0 to N-1)
        res = [
            CustomStatusResponse(
                id=UUID(cs.id),
                project_id=UUID(cs.project_id),
                name=cs.name,
                color=cs.color,
                display_order=idx,
                is_default=cs.is_default,
                is_final=cs.is_final,
            )
            for idx, cs in enumerate(custom_statuses)
        ]

        # 6. Audit Logging (best-effort)
        project_name = project.name or "project"
        user_name = user.full_name or user.username or str(user_id)
        now = datetime.now(timezone.utc)
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="viewed",
            resource_type="custom_status",
            resource_id=project_id,
            details=f"Custom Statuses for project '{project_name}' viewed by {user_name}",
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)

        try:
            await self.db.commit()
        except Exception as audit_err:
            await self.db.rollback()
            logger.warning("Failed to save audit log for get_statuses (best-effort): %s", audit_err)

        logger.info("Retrieved %d custom statuses for project %s", len(res), project_id)
        return res

    async def update_status(
        self,
        status_id: str,
        project_id: str,
        user_id: str,
        organization_id: str,
        payload: UpdateCustomStatusRequest,
    ) -> CustomStatusResponse:
        """
        Updates an existing custom status.
        """
        logger.info(
            "Updating custom status %s in project %s by user %s",
            status_id,
            project_id,
            user_id,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:modify)
        can_manage = await self._check_permission(user, project_id, "projects", "modify")
        if not can_manage:
            logger.error("User %s does not have permission to manage custom statuses in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to manage custom statuses in this project",
            )

        # 4. Fetch existing status
        status_stmt = (
            select(CustomStatus)
            .where(
                CustomStatus.id == status_id,
                CustomStatus.project_id == project_id,
                CustomStatus.deleted_at.is_(None),
            )
        )
        status_res = await self.db.execute(status_stmt)
        custom_status = status_res.scalar_one_or_none()
        if not custom_status:
            logger.error("Custom status %s not found in project %s", status_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Status not found",
            )

        old_name = custom_status.name
        old_color = custom_status.color
        old_display_order = custom_status.display_order
        changes = []
        updated = False

        # 5. Apply partial updates
        if payload.name is not None:
            trimmed_name = payload.name.strip()
            if not trimmed_name or len(trimmed_name) > 50:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Status name must be between 1 and 50 characters",
                )

            # Check uniqueness if name is changing
            if trimmed_name.lower() != custom_status.name.lower():
                dup_stmt = (
                    select(CustomStatus)
                    .where(
                        CustomStatus.project_id == project_id,
                        CustomStatus.id != status_id,
                        func.lower(CustomStatus.name) == trimmed_name.lower(),
                        CustomStatus.deleted_at.is_(None),
                    )
                )
                dup_res = await self.db.execute(dup_stmt)
                if dup_res.scalar_one_or_none():
                    logger.warning("Custom status name '%s' already exists in project %s", trimmed_name, project_id)
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Status name already exists in this project",
                    )

            if trimmed_name != custom_status.name:
                changes.append(f"name changed from '{old_name}' to '{trimmed_name}'")
                custom_status.name = trimmed_name
                updated = True

        if payload.color is not None:
            new_color = payload.color.strip()
            if new_color != custom_status.color:
                changes.append(f"color changed from '{old_color}' to '{new_color}'")
                custom_status.color = new_color
                updated = True

        if payload.display_order is not None:
            if payload.display_order < 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Display order must be greater than or equal to 0",
                )

            # Fetch existing custom statuses for the project to calculate display order and reorder
            existing_stmt = (
                select(CustomStatus)
                .where(
                    CustomStatus.project_id == project_id,
                    CustomStatus.deleted_at.is_(None),
                )
                .order_by(CustomStatus.display_order.asc())
            )
            existing_res = await self.db.execute(existing_stmt)
            existing_statuses = list(existing_res.scalars().all())

            old_idx = -1
            for idx, cs in enumerate(existing_statuses):
                if str(cs.id) == str(status_id):
                    old_idx = idx
                    break

            target_idx = payload.display_order
            if existing_statuses and target_idx > len(existing_statuses) - 1:
                target_idx = len(existing_statuses) - 1

            if old_idx != -1 and old_idx != target_idx:
                # Remove from current position
                moving_status = existing_statuses.pop(old_idx)
                # Insert at target index
                existing_statuses.insert(target_idx, moving_status)

                for idx, cs in enumerate(existing_statuses):
                    if str(cs.id) == str(status_id):
                        if custom_status.display_order != idx:
                            changes.append(f"display order changed from {old_display_order} to {idx}")
                            custom_status.display_order = idx
                            updated = True
                    else:
                        if cs.display_order != idx:
                            cs.display_order = idx
                            cs.updated_at = datetime.now(timezone.utc)

        if payload.is_final is not None:
            if payload.is_final != custom_status.is_final:
                changes.append(f"is_final changed from {custom_status.is_final} to {payload.is_final}")
                custom_status.is_final = payload.is_final
                updated = True

        now = datetime.now(timezone.utc)
        if updated:
            custom_status.updated_at = now

            # If name changed, update status field for all active tasks in this project
            if old_name != custom_status.name:
                task_update_stmt = (
                    update(Task)
                    .where(
                        Task.project_id == project_id,
                        func.lower(Task.status) == old_name.lower(),
                        Task.deleted_at.is_(None),
                    )
                    .values(status=custom_status.name, updated_at=now)
                )
                await self.db.execute(task_update_stmt)
                logger.info("Updated task statuses in project %s from '%s' to '%s'", project_id, old_name, custom_status.name)

            # Audit log
            project_name = project.name or "project"
            user_name = user.full_name or user.username or str(user_id)
            detail = (
                f"Custom Status '{custom_status.name}' updated for project '{project_name}' by {user_name}: {', '.join(changes)}"
                if changes
                else f"Custom Status '{custom_status.name}' updated for project '{project_name}' by {user_name}"
            )
            audit_log = AuditLog(
                id=str(uuid7()),
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                action="updated",
                resource_type="custom_status",
                resource_id=custom_status.id,
                details=detail,
                type="audit",
                created_at=now,
            )
            self.db.add(audit_log)

            try:
                await self.db.commit()
                await self.db.refresh(custom_status)
                logger.info("Successfully updated custom status %s", status_id)
            except Exception as exc:
                await self.db.rollback()
                logger.exception("Failed to update custom status %s: %s", status_id, exc)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update custom status",
                ) from exc

        return CustomStatusResponse(
            id=UUID(custom_status.id),
            project_id=UUID(custom_status.project_id),
            name=custom_status.name,
            color=custom_status.color,
            display_order=custom_status.display_order,
            is_default=custom_status.is_default,
            is_final=custom_status.is_final,
        )

    async def delete_status(
        self,
        status_id: str,
        project_id: str,
        user_id: str,
        organization_id: str,
    ) -> None:
        """
        Deletes a custom status if authorized and not currently in use by active tasks.
        """
        logger.info(
            "Deleting custom status %s in project %s by user %s",
            status_id,
            project_id,
            user_id,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:modify)
        can_manage = await self._check_permission(user, project_id, "projects", "modify")
        if not can_manage:
            logger.error("User %s does not have permission to manage custom statuses in project %s", user_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to manage custom statuses in this project",
            )

        # 4. Fetch status
        status_stmt = (
            select(CustomStatus)
            .where(
                CustomStatus.id == status_id,
                CustomStatus.project_id == project_id,
                CustomStatus.deleted_at.is_(None),
            )
        )
        status_res = await self.db.execute(status_stmt)
        custom_status = status_res.scalar_one_or_none()
        if not custom_status:
            logger.error("Custom status %s not found in project %s", status_id, project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Status not found",
            )

        # 5. Validation: status cannot be deleted while assigned to active tasks
        task_count_stmt = (
            select(func.count(Task.id))
            .where(
                Task.project_id == project_id,
                Task.status == custom_status.name,
                Task.deleted_at.is_(None),
            )
        )
        task_count_res = await self.db.execute(task_count_stmt)
        task_count = task_count_res.scalar() or 0
        if task_count > 0:
            logger.warning(
                "Cannot delete custom status '%s' in project %s because %d task(s) are assigned to it",
                custom_status.name,
                project_id,
                task_count,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A status cannot be deleted while it is assigned to existing Tasks",
            )

        status_name = custom_status.name
        now = datetime.now(timezone.utc)

        # 6. Delete the custom status
        await self.db.delete(custom_status)
        await self.db.flush()

        # 7. Reorder remaining statuses to be strictly sequential (0 to N-1)
        remaining_stmt = (
            select(CustomStatus)
            .where(
                CustomStatus.project_id == project_id,
                CustomStatus.deleted_at.is_(None),
            )
            .order_by(CustomStatus.display_order.asc())
        )
        remaining_res = await self.db.execute(remaining_stmt)
        remaining_statuses = remaining_res.scalars().all()

        for idx, cs in enumerate(remaining_statuses):
            if cs.display_order != idx:
                cs.display_order = idx
                cs.updated_at = now

        # 8. Audit logging
        project_name = project.name or "project"
        user_name = user.full_name or user.username or str(user_id)
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            action="deleted",
            resource_type="custom_status",
            resource_id=status_id,
            details=f"Custom Status '{status_name}' deleted for project '{project_name}' by {user_name}",
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)

        try:
            await self.db.commit()
            logger.info("Successfully deleted custom status '%s' (%s) in project %s", status_name, status_id, project_id)
        except Exception as exc:
            await self.db.rollback()
            logger.exception("Failed to delete custom status %s: %s", status_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete custom status",
            ) from exc



