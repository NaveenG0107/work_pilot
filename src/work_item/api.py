from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.database import get_db
from src.utils.core import bearer_scheme, require_jwt
from src.utils.user_story_helper import GoValidationRoute, dumped, failure, success
from src.work_item.service import WorkItemService, WorkItemServiceError

logger = get_logger(__name__)

router = APIRouter(
    prefix="/projects",
    route_class=GoValidationRoute,
    dependencies=[Depends(bearer_scheme)],
    default_response_class=JSONResponse,
)


def get_work_item_service(db: AsyncSession = Depends(get_db)) -> WorkItemService:
    return WorkItemService(db)


@router.get("/{project_id}/work-items/task/{key}", tags=["Work Items"])
@require_jwt
async def get_task_by_key(
    project_id: str,
    key: str,
    request: Request,
    service: WorkItemService = Depends(get_work_item_service),
):
    try:
        user_id = request.state.user_id
        logger.info("Handling GET task by key for project_id=%s, key=%s", project_id, key)
        res = await service.get_task_by_key(project_id, key, user_id)
        logger.info("Successfully fetched task for key=%s", key)
        return success("Task retrieved successfully", dumped(res))
    except WorkItemServiceError as exc:
        logger.warning("Work item service error: %s", exc.message)
        return failure(exc)
    except Exception as exc:
        logger.error("Unexpected error fetching task by key: %s", exc, exc_info=True)
        return failure(exc)


@router.get("/{project_id}/work-items/us/{key}", tags=["Work Items"])
@require_jwt
async def get_user_story_by_key(
    project_id: str,
    key: str,
    request: Request,
    service: WorkItemService = Depends(get_work_item_service),
):
    try:
        user_id = request.state.user_id
        logger.info("Handling GET user story by key for project_id=%s, key=%s", project_id, key)
        res = await service.get_user_story_by_key(project_id, key, user_id)
        logger.info("Successfully fetched user story for key=%s", key)
        return success("User story retrieved successfully", dumped(res))
    except WorkItemServiceError as exc:
        logger.warning("Work item service error: %s", exc.message)
        return failure(exc)
    except Exception as exc:
        logger.error("Unexpected error fetching user story by key: %s", exc, exc_info=True)
        return failure(exc)


@router.get("/{project_id}/work-items/{serial_id}", tags=["Work Items"])
@require_jwt
async def get_work_item_by_serial_number(
    project_id: str,
    serial_id: str,
    request: Request,
    service: WorkItemService = Depends(get_work_item_service),
):
    try:
        user_id = request.state.user_id

        logger.info("Handling GET work item request for project_id=%s, serial_id=%s", project_id, serial_id)

        res = await service.get_work_item_by_serial_number(project_id, serial_id, user_id)

        logger.info("Successfully fetched work item for serial_id=%s", serial_id)

        return success("Work item retrieved successfully", dumped(res))

    except WorkItemServiceError as exc:
        logger.warning("Work item service error: %s", exc.message)
        return failure(exc)

    except Exception as exc:
        logger.error("Unexpected error fetching work item by serial number: %s", exc, exc_info=True)
        return failure(exc)
