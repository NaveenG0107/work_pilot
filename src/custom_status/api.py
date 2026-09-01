# src/custom_status/api.py
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.deps import get_current_user
from src.custom_status.schemas import (
    CreateCustomStatusRequest,
    CustomStatusResponse,
    UpdateCustomStatusRequest,
)
from src.custom_status.service import CustomStatusService
from src.database import get_db
from src.response import error, success

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Custom Statuses"])


def get_custom_status_service(db: AsyncSession = Depends(get_db)) -> CustomStatusService:
    return CustomStatusService(db)


def validate_uuid(value: str, param_name: str = "ID") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {param_name} format",
        )


@router.post("/projects/{project_id}/custom-statuses", status_code=status.HTTP_201_CREATED)
async def create_custom_status(
    payload: CreateCustomStatusRequest,
    project_id: str = Path(..., description="Project ID (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: CustomStatusService = Depends(get_custom_status_service),
):
    """
    Create a new custom status for a project.
    Mirrors st.POST("", middleware.ValidateJWT(), statusHandler.CreateStatus)
    """
    logger.info("Received request to create custom status in project %s", project_id)
    valid_project_id = validate_uuid(project_id, "project_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for creating custom status")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for creating custom status")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.create_status(
            project_id=valid_project_id,
            user_id=user_id,
            organization_id=organization_id,
            payload=payload,
        )
        return success(
            message="Custom status created successfully",
            data=res,
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in create_custom_status: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in create_custom_status: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/projects/{project_id}/custom-statuses", status_code=status.HTTP_200_OK)
async def get_custom_statuses(
    project_id: str = Path(..., description="Project ID (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: CustomStatusService = Depends(get_custom_status_service),
):
    """
    Get all custom statuses for a project.
    Mirrors st.GET("", middleware.ValidateJWT(), statusHandler.GetStatuses)
    """
    logger.info("Received request to get custom statuses for project %s", project_id)
    valid_project_id = validate_uuid(project_id, "project_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for retrieving custom statuses")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for retrieving custom statuses")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.get_statuses(
            project_id=valid_project_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        return success(
            message="Custom statuses retrieved successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_custom_statuses: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_custom_statuses: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.patch("/projects/{project_id}/custom-statuses/{status_id}", status_code=status.HTTP_200_OK)
async def update_custom_status(
    payload: UpdateCustomStatusRequest,
    project_id: str = Path(..., description="Project ID (UUID)"),
    status_id: str = Path(..., description="Status ID (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: CustomStatusService = Depends(get_custom_status_service),
):
    """
    Update an existing custom status.
    Mirrors st.PATCH("/:status_id", middleware.ValidateJWT(), statusHandler.UpdateStatus)
    """
    logger.info("Received request to update custom status %s in project %s", status_id, project_id)
    valid_project_id = validate_uuid(project_id, "project_id")
    valid_status_id = validate_uuid(status_id, "status_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for updating custom status")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for updating custom status")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        res = await service.update_status(
            status_id=valid_status_id,
            project_id=valid_project_id,
            user_id=user_id,
            organization_id=organization_id,
            payload=payload,
        )
        return success(
            message="Custom status updated successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in update_custom_status: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in update_custom_status: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.delete("/projects/{project_id}/custom-statuses/{status_id}", status_code=status.HTTP_200_OK)
async def delete_custom_status(
    project_id: str = Path(..., description="Project ID (UUID)"),
    status_id: str = Path(..., description="Status ID (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: CustomStatusService = Depends(get_custom_status_service),
):
    """
    Delete an existing custom status.
    Mirrors st.DELETE("/:status_id", middleware.ValidateJWT(), statusHandler.DeleteStatus)
    """
    logger.info("Received request to delete custom status %s in project %s", status_id, project_id)
    valid_project_id = validate_uuid(project_id, "project_id")
    valid_status_id = validate_uuid(status_id, "status_id")
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        logger.error("Authentication required for deleting custom status")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    if not organization_id:
        logger.error("Organization context required for deleting custom status")
        return error("Organization context required", status_code=status.HTTP_403_FORBIDDEN, code="FORBIDDEN")

    try:
        await service.delete_status(
            status_id=valid_status_id,
            project_id=valid_project_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        return success(
            message="Custom status deleted successfully",
            data={"status_id": uuid.UUID(valid_status_id)},
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in delete_custom_status: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in delete_custom_status: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )



