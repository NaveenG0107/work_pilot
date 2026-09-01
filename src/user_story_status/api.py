from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .schema import (
    CreateUserStoryStatusRequest,
    UpdateUserStoryStatusRequest,
    SuccessResponse,
)
from .service import UserStoryStatusService
from src.database import get_db


router = APIRouter(
    prefix="/projects/{project_id}/user-story-statuses",
    tags=["UserStoryStatus"],
)

from uuid import UUID 


async def get_current_user():
    return {
        "user_id": UUID("6a3b86a9-e93f-4a60-8cc7-e432b59bd2dc"),
        "organization_id": UUID("6a3b86a9-e93f-4a60-8cc7-e432b59bd2dd"),
    }

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
)
async def create_status(
    project_id: UUID,
    payload: CreateUserStoryStatusRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):

    user_id = current_user["user_id"]
    organization_id = current_user["organization_id"]

    service = UserStoryStatusService(db)

    result = await service.create_status(
        request=payload,
        project_id=project_id,
        user_id=user_id,
        organization_id=organization_id,
    )

    return {
        "success": True,
        "status_code": status.HTTP_201_CREATED,
        "message": "User Story status created successfully",
        "data": result,
    }

@router.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def get_statuses(
    project_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):

    user_id = current_user["user_id"]
    organization_id = current_user["organization_id"]

    service = UserStoryStatusService(db)

    result = await service.get_statuses(
        project_id=project_id,
        user_id=user_id,
        organization_id=organization_id,
    )

    return {
        "success": True,
        "status_code": status.HTTP_200_OK,
        "message": (
            "User Story statuses retrieved successfully"
        ),
        "data": result,
    }

@router.patch(
    "/{status_id}",
    status_code=status.HTTP_200_OK,
)
async def update_status(
    project_id: UUID,
    status_id: UUID,
    payload: UpdateUserStoryStatusRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):

    user_id = current_user["user_id"]
    organization_id = current_user["organization_id"]

    service = UserStoryStatusService(db)

    result = await service.update_status(
        request=payload,
        project_id=project_id,
        status_id=status_id,
        user_id=user_id,
        organization_id=organization_id,
    )

    return {
        "success": True,
        "status_code": status.HTTP_200_OK,
        "message": "User Story status updated successfully",
        "data": result,
    }


@router.delete(
    "/{status_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_status(
    project_id: UUID,
    status_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):

    user_id = current_user["user_id"]
    organization_id = current_user["organization_id"]

    service = UserStoryStatusService(db)

    await service.delete_status(
        status_id=status_id,
        project_id=project_id,
        user_id=user_id,
        organization_id=organization_id,
    )

    return {
        "success": True,
        "status_code": status.HTTP_200_OK,
        "message": "User Story status deleted successfully",
        "data": {
            "status_id": status_id,
        },
    }