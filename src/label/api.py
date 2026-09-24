import uuid
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.deps import get_current_user
from src.database import get_db
from src.label.schemas import CreateLabelRequest, UpdateLabelRequest
from src.label.service import LabelService
from src.response import error, success

router = APIRouter(prefix="/projects", tags=["Label"])

# pyrefly: ignore [missing-import]
def get_label_service(db: AsyncSession = Depends(get_db)) -> LabelService:
    return LabelService(db)


def validate_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID format",
        )


@router.post("/{project_id}/labels", status_code=status.HTTP_201_CREATED)
async def create_label(
    payload: CreateLabelRequest,
    project_id: str = Path(..., description="Project ID"),
    current_user: dict = Depends(get_current_user),
    service: LabelService = Depends(get_label_service),
):
    """
    Create a new label for a project.
    """
    valid_project_id = validate_uuid(project_id)
    user_id = current_user["user_id"]

    try:
        res = await service.create_label(
            project_id=valid_project_id,
            user_id=user_id,
            payload=payload,
        )
        return success(
            message="Label created successfully",
            data=res,
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException as exc:
        return error(
            message=exc.detail,
            status_code=exc.status_code,
        )
    except Exception as exc:
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{project_id}/labels", status_code=status.HTTP_200_OK)
async def get_labels(
    project_id: str = Path(..., description="Project ID"),
    current_user: dict = Depends(get_current_user),
    service: LabelService = Depends(get_label_service),
):
    """
    Get all labels for a project.
    """
    valid_project_id = validate_uuid(project_id)
    user_id = current_user["user_id"]

    try:
        res = await service.get_labels(
            project_id=valid_project_id,
            user_id=user_id,
        )
        return success(
            message="Labels retrieved successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.patch("/{project_id}/labels/{label_id}", status_code=status.HTTP_200_OK)
async def update_label(
    payload: UpdateLabelRequest,
    project_id: str = Path(..., description="Project ID"),
    label_id: str = Path(..., description="Label ID"),
    current_user: dict = Depends(get_current_user),
    service: LabelService = Depends(get_label_service),
):
    """
    Update an existing label for a project.
    """
    valid_project_id = validate_uuid(project_id)
    valid_label_id = validate_uuid(label_id)
    user_id = current_user["user_id"]

    try:
        res = await service.update_label(
            project_id=valid_project_id,
            label_id=valid_label_id,
            user_id=user_id,
            payload=payload,
        )
        return success(
            message="Label updated successfully",
            data=res,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.delete("/{project_id}/labels/{label_id}", status_code=status.HTTP_200_OK)
async def delete_label(
    project_id: str = Path(..., description="Project ID"),
    label_id: str = Path(..., description="Label ID"),
    current_user: dict = Depends(get_current_user),
    service: LabelService = Depends(get_label_service),
):
    """
    Delete an existing label for a project.
    """
    valid_project_id = validate_uuid(project_id)
    valid_label_id = validate_uuid(label_id)
    user_id = current_user["user_id"]

    try:
        deleted_id = await service.delete_label(
            project_id=valid_project_id,
            label_id=valid_label_id,
            user_id=user_id,
        )
        return success(
            message="Label deleted successfully",
            data={"Label_id": deleted_id},
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
