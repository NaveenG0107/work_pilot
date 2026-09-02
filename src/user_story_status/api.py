from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.deps import security as bearer_scheme
from src.config import get_logger
from src.database import get_db
from src.user_story_status.schema import (
    CreateUserStoryStatusRequest,
    UpdateUserStoryStatusRequest,
)
from src.user_story_status.service import (
    UserStoryStatusService,
    UserStoryStatusServiceError,
)
from src.utils.core import require_jwt
from src.utils.user_story_helper import GoValidationRoute, dumped, failure, success

logger = get_logger(__name__)

router = APIRouter(
    prefix="/projects",
    route_class=GoValidationRoute,
    dependencies=[Depends(bearer_scheme)],
    default_response_class=JSONResponse,
)


def get_user_story_status_service(db: AsyncSession = Depends(get_db)) -> UserStoryStatusService:
    return UserStoryStatusService(db)


@router.post(
    "/{project_id}/user-story-statuses",
    status_code=status.HTTP_201_CREATED,
    tags=["User Story Statuses"],
)
@require_jwt
async def create_status(
    project_id: str,
    payload: CreateUserStoryStatusRequest,
    request: Request,
    service: UserStoryStatusService = Depends(get_user_story_status_service),
):
    try:
        user_id = request.state.user_id
        org_id = getattr(request.state, "organization_id", None)

        logger.info("Handling POST create status for project_id=%s", project_id)

        res = await service.create_status(
            request=payload,
            project_id_or_slug=project_id,
            user_id=user_id,
            organization_id=org_id,
        )

        logger.info("Successfully created status ID=%s", res.id)

        return success("User Story status created successfully", dumped(res), code=status.HTTP_201_CREATED)

    except UserStoryStatusServiceError as exc:
        logger.warning("User story status service error: %s", exc.message)
        return failure(exc)

    except Exception as exc:
        logger.error("Unexpected error creating status: %s", exc, exc_info=True)
        return failure(exc)


@router.get(
    "/{project_id}/user-story-statuses",
    status_code=status.HTTP_200_OK,
    tags=["User Story Statuses"],
)
@require_jwt
async def get_statuses(
    project_id: str,
    request: Request,
    service: UserStoryStatusService = Depends(get_user_story_status_service),
):
    try:
        user_id = request.state.user_id
        org_id = getattr(request.state, "organization_id", None)

        logger.info("Handling GET statuses for project_id=%s", project_id)

        res = await service.get_statuses(
            project_id_or_slug=project_id,
            user_id=user_id,
            organization_id=org_id,
        )

        logger.info("Successfully retrieved %d statuses", len(res))

        return success("User Story statuses retrieved successfully", dumped(res))

    except UserStoryStatusServiceError as exc:
        logger.warning("User story status service error: %s", exc.message)
        return failure(exc)

    except Exception as exc:
        logger.error("Unexpected error getting statuses: %s", exc, exc_info=True)
        return failure(exc)


@router.patch(
    "/{project_id}/user-story-statuses/{status_id}",
    status_code=status.HTTP_200_OK,
    tags=["User Story Statuses"],
)
@require_jwt
async def update_status(
    project_id: str,
    status_id: str,
    payload: UpdateUserStoryStatusRequest,
    request: Request,
    service: UserStoryStatusService = Depends(get_user_story_status_service),
):
    try:
        user_id = request.state.user_id
        org_id = getattr(request.state, "organization_id", None)

        logger.info("Handling PATCH status ID=%s for project_id=%s", status_id, project_id)

        res = await service.update_status(
            request=payload,
            project_id_or_slug=project_id,
            status_id=status_id,
            user_id=user_id,
            organization_id=org_id,
        )

        logger.info("Successfully updated status ID=%s", status_id)

        return success("User Story status updated successfully", dumped(res))

    except UserStoryStatusServiceError as exc:
        logger.warning("User story status service error: %s", exc.message)
        return failure(exc)

    except Exception as exc:
        logger.error("Unexpected error updating status: %s", exc, exc_info=True)
        return failure(exc)


@router.delete(
    "/{project_id}/user-story-statuses/{status_id}",
    status_code=status.HTTP_200_OK,
    tags=["User Story Statuses"],
)
@require_jwt
async def delete_status(
    project_id: str,
    status_id: str,
    request: Request,
    service: UserStoryStatusService = Depends(get_user_story_status_service),
):
    try:
        user_id = request.state.user_id
        org_id = getattr(request.state, "organization_id", None)

        logger.info("Handling DELETE status ID=%s for project_id=%s", status_id, project_id)

        res = await service.delete_status(
            status_id=status_id,
            project_id_or_slug=project_id,
            user_id=user_id,
            organization_id=org_id,
        )

        logger.info("Successfully deleted status ID=%s", status_id)

        return success("User Story status deleted successfully", res)

    except UserStoryStatusServiceError as exc:
        logger.warning("User story status service error: %s", exc.message)
        return failure(exc)

    except Exception as exc:
        logger.error("Unexpected error deleting status: %s", exc, exc_info=True)
        return failure(exc)