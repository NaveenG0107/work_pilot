from uuid import UUID
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from .models import UserStoryStatus
from .schema import (
    CreateUserStoryStatusRequest,
    UpdateUserStoryStatusRequest,
    UserStoryStatusResponse,
)


# =========================================================
# Repository
# =========================================================

class UserStoryStatusRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    # -----------------------------------------------------
    # Create
    # -----------------------------------------------------

    async def create_status(
        self,
        status_model: UserStoryStatus,
    ) -> UserStoryStatus:

        self.db.add(status_model)
        await self.db.flush()

        return status_model

    # -----------------------------------------------------
    # Get all statuses for project
    # -----------------------------------------------------

    async def get_statuses_by_project_id(
        self,
        project_id: str,
    ) -> list[UserStoryStatus]:

        result = await self.db.execute(
            select(UserStoryStatus)
            .where(
                UserStoryStatus.project_id == project_id,
                UserStoryStatus.deleted_at.is_(None),
            )
            .order_by(UserStoryStatus.display_order)
        )

        return list(result.scalars().all())

    # -----------------------------------------------------
    # Get single status
    # -----------------------------------------------------

    async def get_status_by_id(
        self,
        status_id: str,
        project_id: str,
    ) -> UserStoryStatus | None:

        result = await self.db.execute(
            select(UserStoryStatus)
            .where(
                UserStoryStatus.id == status_id,
                UserStoryStatus.project_id == project_id,
                UserStoryStatus.deleted_at.is_(None),
            )
        )

        return result.scalar_one_or_none()

    # -----------------------------------------------------
    # Check duplicate status name
    # -----------------------------------------------------

    async def is_status_name_exists(
        self,
        project_id: str,
        name: str,
    ) -> bool:

        result = await self.db.execute(
            select(UserStoryStatus.id)
            .where(
                UserStoryStatus.project_id == project_id,
                func.lower(UserStoryStatus.name) == name.lower(),
                UserStoryStatus.deleted_at.is_(None),
            )
            .limit(1)
        )

        return result.scalar_one_or_none() is not None

    # -----------------------------------------------------
    # Update
    # -----------------------------------------------------

    async def update_status(
        self,
        status_model: UserStoryStatus,
    ) -> UserStoryStatus:

        await self.db.flush()

        return status_model

    # -----------------------------------------------------
    # Delete
    # -----------------------------------------------------

    async def delete_status(
        self,
        status_id: str,
        project_id: str,
    ) -> bool:

        status_model = await self.get_status_by_id(
            status_id,
            project_id,
        )

        if status_model is None:
            return False

        # Soft delete
        status_model.deleted_at = datetime.now(timezone.utc)

        await self.db.flush()

        return True


# =========================================================
# Service
# =========================================================

class UserStoryStatusService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = UserStoryStatusRepository(db)

    # -----------------------------------------------------
    # Authorization
    # -----------------------------------------------------

    async def check_admin_or_pm(
        self,
        project_id: UUID,
        user_id: UUID,
    ) -> bool:

        # Temporary testing
        return True

    async def check_project_member(
        self,
        project_id: UUID,
        user_id: UUID,
    ) -> bool:

        # Temporary testing
        return True

    # -----------------------------------------------------
    # Audit
    # -----------------------------------------------------

    async def create_audit_log(
        self,
        user_id: UUID,
        organization_id: UUID,
        project_id: UUID,
        action: str,
        resource_id: str | None = None,
        details: str | None = None,
    ):
        # TODO: connect audit repository
        pass

    # =====================================================
    # CREATE STATUS
    # =====================================================

    async def create_status(
        self,
        request: CreateUserStoryStatusRequest,
        project_id: UUID,
        user_id: UUID,
        organization_id: UUID,
    ) -> UserStoryStatusResponse:

        # -------------------------------------------------
        # Authorization
        # -------------------------------------------------

        authorized = await self.check_admin_or_pm(
            project_id,
            user_id,
        )

        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "You do not have permission to manage "
                    "User Story statuses in this project"
                ),
            )

        # -------------------------------------------------
        # Validate name
        # -------------------------------------------------

        name = request.name.strip()

        if not name or len(name) > 50:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Status name must be between 1 and 50 characters",
            )

        # -------------------------------------------------
        # Check duplicate name
        # -------------------------------------------------

        exists = await self.repository.is_status_name_exists(
            str(project_id),
            name,
        )

        if exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Status name already exists in this project",
            )

        # -------------------------------------------------
        # is_final and is_closed stay synchronized
        # -------------------------------------------------

        is_closed = False
        is_final = False

        if request.is_final is not None:
            is_final = request.is_final
            is_closed = is_final

        elif request.is_closed is not None:
            is_closed = request.is_closed
            is_final = is_closed

        # -------------------------------------------------
        # Create model
        # -------------------------------------------------

        status_model = UserStoryStatus(
            id=str(uuid7()),
            project_id=str(project_id),
            name=name,
            color=request.color,
            display_order=request.display_order,
            is_default=False,
            is_closed=is_closed,
            is_final=is_final,
        )

        # -------------------------------------------------
        # Save
        # -------------------------------------------------

        try:

            await self.repository.create_status(
                status_model
            )

            await self.db.commit()

            await self.db.refresh(
                status_model
            )

        except Exception:

            await self.db.rollback()

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user story status",
            )

        # -------------------------------------------------
        # Audit
        # -------------------------------------------------

        try:

            await self.create_audit_log(
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                action="created",
                resource_id=status_model.id,
                details=(
                    f"User Story Status "
                    f"'{status_model.name}' created"
                ),
            )

        except Exception:
            pass

        # -------------------------------------------------
        # Response
        # -------------------------------------------------

        return UserStoryStatusResponse.model_validate(
            status_model
        )

    # =====================================================
    # GET STATUSES
    # =====================================================

    async def get_statuses(
        self,
        project_id: UUID,
        user_id: UUID,
        organization_id: UUID,
    ) -> list[UserStoryStatusResponse]:

        # -------------------------------------------------
        # Authorization
        # -------------------------------------------------

        authorized = await self.check_project_member(
            project_id,
            user_id,
        )

        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "You do not have permission to view "
                    "User Story statuses in this project"
                ),
            )

        # -------------------------------------------------
        # Get statuses
        # -------------------------------------------------

        statuses = await self.repository.get_statuses_by_project_id(
            str(project_id)
        )

        # -------------------------------------------------
        # Convert to response
        # -------------------------------------------------

        responses = [
            UserStoryStatusResponse.model_validate(item)
            for item in statuses
        ]

        # -------------------------------------------------
        # Sort
        # -------------------------------------------------

        responses.sort(
            key=lambda item: item.display_order
        )

        # -------------------------------------------------
        # Normalize display order
        # -------------------------------------------------

        for index, item in enumerate(responses):
            item.display_order = index

        # -------------------------------------------------
        # Audit
        # -------------------------------------------------

        try:

            await self.create_audit_log(
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                action="viewed",
            )

        except Exception:
            pass

        return responses

    # =====================================================
    # UPDATE STATUS
    # =====================================================

    async def update_status(
        self,
        request: UpdateUserStoryStatusRequest,
        project_id: UUID,
        status_id: UUID,
        user_id: UUID,
        organization_id: UUID,
    ) -> UserStoryStatusResponse:

        # -------------------------------------------------
        # Authorization
        # -------------------------------------------------

        authorized = await self.check_admin_or_pm(
            project_id,
            user_id,
        )

        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "You do not have permission to manage "
                    "User Story statuses in this project"
                ),
            )

        # -------------------------------------------------
        # Get existing status
        # -------------------------------------------------

        existing = await self.repository.get_status_by_id(
            str(status_id),
            str(project_id),
        )

        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Status not found",
            )

        updated = False

        # -------------------------------------------------
        # Name
        # -------------------------------------------------

        if request.name is not None:

            trimmed_name = request.name.strip()

            if not trimmed_name or len(trimmed_name) > 50:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "Status name must be between "
                        "1 and 50 characters"
                    ),
                )

            if trimmed_name.lower() != existing.name.lower():

                exists = await self.repository.is_status_name_exists(
                    str(project_id),
                    trimmed_name,
                )

                if exists:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "Status name already exists "
                            "in this project"
                        ),
                    )

            if trimmed_name != existing.name:

                existing.name = trimmed_name
                updated = True

        # -------------------------------------------------
        # Color
        # -------------------------------------------------

        if request.color is not None:

            if request.color != existing.color:

                existing.color = request.color
                updated = True

        # -------------------------------------------------
        # Display order
        # -------------------------------------------------

        if request.display_order is not None:

            if request.display_order < 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "Display order must be greater "
                        "than or equal to 0"
                    ),
                )

            if request.display_order != existing.display_order:

                existing.display_order = request.display_order
                updated = True

        # -------------------------------------------------
        # Is closed
        # -------------------------------------------------

        if request.is_closed is not None:

            if request.is_closed != existing.is_closed:

                existing.is_closed = request.is_closed
                existing.is_final = request.is_closed

                updated = True

        # -------------------------------------------------
        # Is final
        # -------------------------------------------------

        if request.is_final is not None:

            if request.is_final != existing.is_final:

                existing.is_final = request.is_final
                existing.is_closed = request.is_final

                updated = True

        # -------------------------------------------------
        # Save
        # -------------------------------------------------

        if updated:

            try:

                await self.repository.update_status(
                    existing
                )

                await self.db.commit()

                await self.db.refresh(
                    existing
                )

            except Exception:

                await self.db.rollback()

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update user story status",
                )

            # -------------------------------------------------
            # Audit
            # -------------------------------------------------

            try:

                await self.create_audit_log(
                    user_id=user_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    action="updated",
                    resource_id=existing.id,
                    details=(
                        f"User Story Status "
                        f"'{existing.name}' updated"
                    ),
                )

            except Exception:
                pass

        return UserStoryStatusResponse.model_validate(
            existing
        )

    # =====================================================
    # DELETE STATUS
    # =====================================================

    async def delete_status(
        self,
        status_id: UUID,
        project_id: UUID,
        user_id: UUID,
        organization_id: UUID,
    ):

        # -------------------------------------------------
        # Authorization
        # -------------------------------------------------

        authorized = await self.check_admin_or_pm(
            project_id,
            user_id,
        )

        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "You do not have permission to manage "
                    "User Story statuses in this project"
                ),
            )

        # -------------------------------------------------
        # Get status
        # -------------------------------------------------

        existing = await self.repository.get_status_by_id(
            str(status_id),
            str(project_id),
        )

        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Status not found",
            )

        # -------------------------------------------------
        # Cannot delete default status
        # -------------------------------------------------

        if existing.is_default:

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A default status cannot be deleted",
            )

        # -------------------------------------------------
        # Get all statuses
        # -------------------------------------------------

        statuses = await self.repository.get_statuses_by_project_id(
            str(project_id)
        )

        # -------------------------------------------------
        # Cannot delete only status
        # -------------------------------------------------

        if len(statuses) <= 1:

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "The project's only status "
                    "cannot be deleted"
                ),
            )

        # -------------------------------------------------
        # Check assigned stories
        # -------------------------------------------------

        count = await self.count_stories_by_status_id(
            project_id,
            status_id,
        )

        if count > 0:

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "A status cannot be deleted while "
                    "it is assigned to existing User Stories"
                ),
            )

        # -------------------------------------------------
        # Delete
        # -------------------------------------------------

        try:

            deleted = await self.repository.delete_status(
                str(status_id),
                str(project_id),
            )

            if not deleted:

                await self.db.rollback()

                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Status not found",
                )

            await self.db.commit()

        except HTTPException:
            raise

        except Exception:

            await self.db.rollback()

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete user story status",
            )

        # -------------------------------------------------
        # Audit
        # -------------------------------------------------

        try:

            await self.create_audit_log(
                user_id=user_id,
                organization_id=organization_id,
                project_id=project_id,
                action="deleted",
                resource_id=str(status_id),
                details=(
                    f"User Story Status "
                    f"'{existing.name}' deleted"
                ),
            )

        except Exception:
            pass

    # =====================================================
    # COUNT STORIES
    # =====================================================

    async def count_stories_by_status_id(
        self,
        project_id: UUID,
        status_id: UUID,
    ) -> int:

        # TODO:
        # Add UserStory query here.
        return 0

