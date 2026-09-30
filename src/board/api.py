from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.board.schemas import BoardResponse
from src.board.service import BoardService, BoardServiceError
from src.config import get_logger
from src.database import get_db, get_redis
from src.utils.core import bearer_scheme, require_jwt

logger = get_logger(__name__)

router = APIRouter(
    prefix="/projects",
    dependencies=[Depends(bearer_scheme)],
)

MAX_PAGE_SIZE = 50


def validate_uuid_param(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    val = str(value).strip().strip('"').strip("'")
    if not val:
        return None
    try:
        return str(uuid.UUID(val))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BoardServiceError(400, "BAD_REQUEST", f"Invalid {name} format") from exc


def get_board_service(
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> BoardService:
    return BoardService(db, redis)


@router.get(
    "/{project_id}/board",
    response_model=BoardResponse,
    tags=["Board"],
    summary="Unified Kanban Board Endpoint",
    description="Single consolidated endpoint supporting stories list, story expansion, status task pagination, and story-less tasks.",
)
@require_jwt
async def get_board(
    project_id: str,
    request: Request,
    page: int = Query(default=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=5, description="Page size (max 50)"),
    story_id: Optional[str] = Query(default=None, description="User story ID for story expansion or status pagination"),
    status_id: Optional[str] = Query(default=None, description="Status ID for loading more tasks within a story"),
    storyless: bool = Query(default=False, description="Whether to fetch story-less tasks (user_story_id IS NULL AND sprint_id IS NOT NULL)"),
    sprint_id: Optional[str] = Query(default=None, description="Optional sprint filter"),
    story_assignee_id: Optional[str] = Query(default=None, description="Optional assignee filter for user stories"),
    task_assignee_id: Optional[str] = Query(default=None, description="Optional assignee filter for tasks"),
    task_status_id: Optional[str] = Query(default=None, description="Optional status filter for tasks"),
    priority: Optional[str] = Query(default=None, description="Optional priority filter (low, medium, high, critical)"),
    work_type: Optional[str] = Query(default=None, description="Optional task type filter (bug, feature, task, etc.)"),
    label_id: Optional[str] = Query(default=None, description="Optional label filter"),
    service: BoardService = Depends(get_board_service),
):
    start_time = time.perf_counter()

    try:
        # 1. Parameter Validation
        if page < 1:
            raise BoardServiceError(400, "BAD_REQUEST", "page must be greater than or equal to 1")
        if page_size <= 0:
            raise BoardServiceError(400, "BAD_REQUEST", "page_size must be greater than 0")
        if page_size > MAX_PAGE_SIZE:
            raise BoardServiceError(400, "BAD_REQUEST", f"page_size must not exceed {MAX_PAGE_SIZE}")

        # Ambiguous combination validations
        if storyless and story_id:
            raise BoardServiceError(400, "BAD_REQUEST", "Cannot provide story_id when storyless is true")
        if storyless and status_id:
            raise BoardServiceError(400, "BAD_REQUEST", "Cannot provide status_id when storyless is true")
        if status_id and not story_id:
            raise BoardServiceError(400, "BAD_REQUEST", "status_id requires story_id to be specified")

        # UUID validations
        clean_project_id = validate_uuid_param("project_id", project_id)
        clean_story_id = validate_uuid_param("story_id", story_id)
        clean_status_id = validate_uuid_param("status_id", status_id)
        clean_sprint_id = validate_uuid_param("sprint_id", sprint_id)
        clean_story_assignee_id = validate_uuid_param("story_assignee_id", story_assignee_id)
        clean_task_assignee_id = validate_uuid_param("task_assignee_id", task_assignee_id)
        clean_task_status_id = validate_uuid_param("task_status_id", task_status_id)
        clean_label_id = validate_uuid_param("label_id", label_id)

        # 2. Authentication & Authorization context
        user_id = getattr(request.state, "user_id", None)
        org_id = getattr(request.state, "organization_id", None)
        if not user_id:
            raise BoardServiceError(401, "UNAUTHORIZED", "Authentication required")
        if not org_id:
            raise BoardServiceError(403, "ORGANIZATION_REQUIRED", "Organization is required")

        # Verify project existence, org isolation, and permissions
        await service.check_authorization(clean_project_id, str(user_id), str(org_id))

        # 3. Determine request mode & dispatch
        if storyless:
            mode = "storyless_tasks"
            response = await service.get_board_storyless_tasks(
                clean_project_id,
                page=page,
                page_size=page_size,
                sprint_id=clean_sprint_id,
                task_assignee_id=clean_task_assignee_id,
                task_status_id=clean_task_status_id,
                priority=priority,
                work_type=work_type,
                label_id=clean_label_id,
                current_user_id=str(user_id) if user_id else None,
            )
        elif clean_story_id and clean_status_id:
            mode = "story_status_tasks"
            response = await service.get_board_status_tasks(
                clean_project_id,
                clean_story_id,
                clean_status_id,
                page=page,
                page_size=page_size,
                task_assignee_id=clean_task_assignee_id,
                sprint_id=clean_sprint_id,
                priority=priority,
                work_type=work_type,
                label_id=clean_label_id,
                current_user_id=str(user_id) if user_id else None,
            )
        elif clean_story_id:
            mode = "story"
            response = await service.get_board_story_details(
                clean_project_id,
                clean_story_id,
                task_assignee_id=clean_task_assignee_id,
                task_status_id=clean_task_status_id,
                priority=priority,
                work_type=work_type,
                label_id=clean_label_id,
                current_user_id=str(user_id) if user_id else None,
            )
        else:
            mode = "stories"
            response = await service.get_board_stories(
                clean_project_id,
                page=page,
                page_size=page_size,
                sprint_id=clean_sprint_id,
                story_assignee_id=clean_story_assignee_id,
                current_user_id=str(user_id) if user_id else None,
            )

        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Board request completed: mode=%s, project_id=%s, story_id=%s, status_id=%s, page=%s, page_size=%s, duration_ms=%.2f",
            mode,
            clean_project_id,
            clean_story_id,
            clean_status_id,
            page,
            page_size,
            duration_ms,
        )

        return response

    except BoardServiceError as exc:
        logger.warning(
            "Board service error: status_code=%s, code=%s, message=%s, project_id=%s",
            exc.status_code,
            exc.code,
            exc.message,
            project_id,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": exc.code,
                    "status_code": exc.status_code,
                    "message": exc.message,
                },
            },
        )
    except Exception as exc:
        logger.exception("Unexpected error in board endpoint: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "status_code": 500,
                    "message": "Internal server error. Please try again later.",
                },
            },
        )
