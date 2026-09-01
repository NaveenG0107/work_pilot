import logging
from datetime import datetime, timezone
from typing import Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid6 import uuid7

from src.audit.models import AuditLog
from src.auth.models import User
from src.label.models import Label
from src.label.schemas import CreateLabelRequest, LabelResponse, UpdateLabelRequest
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.middleware.rbac import has_default_permission


logger = logging.getLogger(__name__)


class LabelService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _check_permission(
        self, user: User, project: Project, resource: str, action: str
    ) -> bool:
        # Super admins cannot perform project activities
        if user.role and user.role.name == "super_admin":
            return False

        # 1. Check project-level role first
        stmt = (
            select(ProjectMember)
            .where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
                ProjectMember.deleted_at.is_(None),
            )
            .options(selectinload(ProjectMember.role).selectinload(Role.permissions))
        )
        result = await self.db.execute(stmt)
        member = result.scalar_one_or_none()

        if member and member.role:
            for perm in member.role.permissions or []:
                if perm.resource == resource and perm.action == action:
                    return True
            if has_default_permission(member.role.name, resource, action):
                return True

        # 2. Check organization-level role if user belongs to project's org (Org Admin only)
        if user.organization_id and str(user.organization_id) == str(project.organization_id):
            if user.role and user.role.name == "org_admin":
                for perm in (user.role.permissions or []):
                    if perm.resource == resource and perm.action == action:
                        return True
                if has_default_permission(user.role.name, resource, action):
                    return True

        return False

    async def check_admin_or_pm(self, project_id: str, user_id: str) -> Tuple[Project, User]:
        """Verify project exists, user exists, and user has 'projects:modify' permission."""
        # Fetch project
        proj_stmt = select(Project).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

        # Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        authorized = await self._check_permission(user, project, "projects", "modify")
        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to manage labels in this project",
            )

        return project, user

    async def create_label(
        self, project_id: str, user_id: str, payload: CreateLabelRequest
    ) -> LabelResponse:
        # 1. Authorization
        project, user = await self.check_admin_or_pm(project_id, user_id)

        # 2. Name normalization
        name = payload.name.strip().lower()
        color = payload.color.strip()

        # 3. Uniqueness Check
        stmt = select(Label).where(
            Label.project_id == project_id,
            func.lower(Label.name) == name,
            Label.deleted_at.is_(None),
        )
        res = await self.db.execute(stmt)
        if res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Label name already exists in this project",
            )

        # 4. Create label
        now = datetime.now(timezone.utc)
        label = Label(
            id=str(uuid7()),
            project_id=project_id,
            name=name,
            color=color,
            created_at=now,
            updated_at=now,
        )
        self.db.add(label)

        # 5. Audit Logging
        project_name = project.name or project_id
        user_name = user.full_name or user.username or user_id
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=str(project.organization_id),
            project_id=project_id,
            action="created",
            resource_type="label",
            resource_id=label.id,
            details=f"Label '{label.name}' created for project '{project_name}' by {user_name}",
            type="activity",
            created_at=now,
        )
        self.db.add(audit_log)

        try:
            await self.db.commit()
            await self.db.refresh(label)
        except Exception as exc:
            await self.db.rollback()
            logger.error("Failed to commit create label and audit log: %s", exc)
            raise exc

        return LabelResponse.model_validate(label)

    async def check_project_member(self, project_id: str, user_id: str) -> Tuple[Project, User]:
        """Verify project exists, user exists, and user has 'projects:view' permission."""
        proj_stmt = select(Project).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        authorized = await self._check_permission(user, project, "projects", "view")
        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view labels in this project",
            )

        return project, user

    async def get_labels(self, project_id: str, user_id: str) -> list[LabelResponse]:
        """Fetch all non-deleted labels for a project."""
        project, user = await self.check_project_member(project_id, user_id)

        stmt = (
            select(Label)
            .where(Label.project_id == project_id, Label.deleted_at.is_(None))
            .order_by(Label.created_at.asc())
        )
        res = await self.db.execute(stmt)
        labels = res.scalars().all()

        # Audit Logging
        now = datetime.now(timezone.utc)
        project_name = project.name or project_id
        user_name = user.full_name or user.username or user_id
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=str(project.organization_id),
            project_id=project_id,
            action="viewed",
            resource_type="label",
            details=f"Labels for project '{project_name}' viewed by {user_name}",
            type="audit",
            created_at=now,
        )
        self.db.add(audit_log)
        try:
            await self.db.commit()
        except Exception as exc:
            logger.warning("Failed to create audit log for get labels: %s", exc)

        return [LabelResponse.model_validate(l) for l in labels]

    async def update_label(
        self,
        project_id: str,
        label_id: str,
        user_id: str,
        payload: UpdateLabelRequest,
    ) -> LabelResponse:
        """Update an existing label for a project."""
        # 1. Authorization
        project, user = await self.check_admin_or_pm(project_id, user_id)

        # 2. Fetch existing label
        stmt = select(Label).where(
            Label.id == label_id,
            Label.project_id == project_id,
            Label.deleted_at.is_(None),
        )
        res = await self.db.execute(stmt)
        label = res.scalar_one_or_none()
        if not label:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Label not found",
            )

        old_name = label.name
        old_color = label.color
        changes = []
        updated = False

        # 3. Name update & duplicate check
        if payload.name is not None:
            normalized_name = payload.name.strip().lower()
            if not normalized_name or len(normalized_name) > 30:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Label name must be between 1 and 30 characters",
                )

            if normalized_name != label.name:
                dup_stmt = select(Label).where(
                    Label.project_id == project_id,
                    func.lower(Label.name) == normalized_name,
                    Label.id != label_id,
                    Label.deleted_at.is_(None),
                )
                dup_res = await self.db.execute(dup_stmt)
                if dup_res.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Label name already exists in this project",
                    )

                changes.append(f"name changed from '{old_name}' to '{normalized_name}'")
                label.name = normalized_name
                updated = True

        # 4. Color update
        if payload.color is not None:
            color = payload.color.strip()
            if color != label.color:
                changes.append(f"color changed from '{old_color}' to '{color}'")
                label.color = color
                updated = True

        # 5. Save & Audit Logging
        if updated:
            now = datetime.now(timezone.utc)
            label.updated_at = now

            project_name = project.name or project_id
            user_name = user.full_name or user.username or user_id
            detail_str = (
                f"Label '{label.name}' updated for project '{project_name}' by {user_name}: {', '.join(changes)}"
                if changes
                else f"Label '{label.name}' updated for project '{project_name}' by {user_name}"
            )

            audit_log = AuditLog(
                id=str(uuid7()),
                user_id=user_id,
                organization_id=str(project.organization_id),
                project_id=project_id,
                action="updated",
                resource_type="label",
                resource_id=label.id,
                details=detail_str,
                type="activity",
                created_at=now,
            )
            self.db.add(audit_log)

            try:
                await self.db.commit()
                await self.db.refresh(label)
            except Exception as exc:
                await self.db.rollback()
                logger.error("Failed to commit update label: %s", exc)
                raise exc

        return LabelResponse.model_validate(label)

    async def delete_label(
        self,
        project_id: str,
        label_id: str,
        user_id: str,
    ) -> str:
        """Soft-delete an existing label for a project."""
        # 1. Authorization
        project, user = await self.check_admin_or_pm(project_id, user_id)

        # 2. Fetch existing label
        stmt = select(Label).where(
            Label.id == label_id,
            Label.project_id == project_id,
            Label.deleted_at.is_(None),
        )
        res = await self.db.execute(stmt)
        label = res.scalar_one_or_none()
        if not label:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Label not found",
            )

        # 3. Soft delete
        now = datetime.now(timezone.utc)
        label.deleted_at = now
        label.updated_at = now

        # 4. Audit Logging
        project_name = project.name or project_id
        user_name = user.full_name or user.username or user_id
        audit_log = AuditLog(
            id=str(uuid7()),
            user_id=user_id,
            organization_id=str(project.organization_id),
            project_id=project_id,
            action="deleted",
            resource_type="label",
            resource_id=label.id,
            details=f"Label '{label.name}' deleted for project '{project_name}' by {user_name}",
            type="activity",
            created_at=now,
        )
        self.db.add(audit_log)

        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            logger.error("Failed to commit delete label: %s", exc)
            raise exc

        return label.id
