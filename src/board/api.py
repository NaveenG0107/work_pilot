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


def parse_uuid_list(name: str, raw_values: list[str] | str | None) -> list[str]:
    if raw_values is None:
        return []
    candidates = [raw_values] if isinstance(raw_values, str) else raw_values
    items: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        for part in str(candidate).split(","):
            part_str = part.strip().strip('"').strip("'")
            if part_str:
                items.append(part_str)

    validated: list[str] = []
    seen: set[str] = set()
    for item in items:
        try:
            valid_id = str(uuid.UUID(item))
            if valid_id not in seen:
                seen.add(valid_id)
                validated.append(valid_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise BoardServiceError(400, "BAD_REQUEST", f"Invalid {name} format") from exc
    return validated


def parse_string_list(raw_values: list[str] | str | None) -> list[str]:
    if raw_values is None:
        return []
    candidates = [raw_values] if isinstance(raw_values, str) else raw_values
    items: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        for part in str(candidate).split(","):
            part_str = part.strip().strip('"').strip("'")
            if part_str and part_str.lower() not in seen:
                seen.add(part_str.lower())
                items.append(part_str)
    return items


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
    tasks_per_status: int = Query(default=5, ge=0, le=20, description="Tasks per status column in board (default 5)"),
    user_story_id: Optional[str] = Query(default=None, description="User story ID for story expansion or status pagination"),
    story_id: Optional[str] = Query(default=None, description="Alias for user_story_id", include_in_schema=False),
    status_id: Optional[str] = Query(default=None, description="Status ID for loading more tasks within a story or status filtering"),
    storyless_tasks: bool = Query(default=False, description="Whether to fetch storyless tasks grouped by status"),
    sprint_id: Optional[str] = Query(default=None, description="Optional sprint filter"),
    story_assignee_id: Optional[str] = Query(
        default=None,
        description="Optional assignee filter for user stories (single UUID, comma-separated UUIDs, or repeated param)",
    ),
    story_assignee_ids: Optional[str] = Query(default=None, include_in_schema=False),
    task_assignee_id: Optional[str] = Query(
        default=None,
        description="Optional assignee filter for tasks (single UUID, comma-separated UUIDs, or repeated param)",
    ),
    task_assignee_ids: Optional[str] = Query(default=None, include_in_schema=False),
    task_status_id: Optional[str] = Query(
        default=None,
        description="Optional status filter for tasks (supports single UUID, comma-separated UUIDs, or repeated param)",
    ),
    task_status_ids: Optional[str] = Query(default=None, include_in_schema=False),
    priority: Optional[str] = Query(
        default=None,
        description="Optional priority filter (low, medium, high, critical - single value, comma-separated, or repeated param)",
    ),
    priorities: Optional[str] = Query(default=None, include_in_schema=False),
    work_type: Optional[str] = Query(
        default=None,
        description="Optional task type filter (bug, feature, task, etc. - single value, comma-separated, or repeated param)",
    ),
    work_types: Optional[str] = Query(default=None, include_in_schema=False),
    type: Optional[str] = Query(default=None, include_in_schema=False),
    types: Optional[str] = Query(default=None, include_in_schema=False),
    label_id: Optional[str] = Query(
        default=None,
        description="Optional label filter (single UUID, comma-separated UUIDs, or repeated param)",
    ),
    label_ids: Optional[str] = Query(default=None, include_in_schema=False),
    labels: Optional[str] = Query(default=None, include_in_schema=False),
    # Hidden backward-compatible query aliases:
    storyless: Optional[bool] = Query(default=None, include_in_schema=False),
    include_storyless: Optional[bool] = Query(default=None, include_in_schema=False),
    group_by_status: Optional[bool] = Query(default=None, include_in_schema=False),
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

        # Resolve storyless_tasks vs legacy storyless
        effective_storyless = storyless_tasks or (storyless is True)

        # Resolve user_story_id (support user_story_id, plus backward-compatible story_id alias)
        effective_story_id = user_story_id or story_id
        is_storyless_id = bool(effective_story_id and effective_story_id.strip().lower() == "storyless")

        # Ambiguous combination validations
        if effective_storyless and effective_story_id and not is_storyless_id:
            raise BoardServiceError(400, "BAD_REQUEST", "Cannot provide user_story_id when storyless_tasks is true")

        # UUID validations
        clean_project_id = validate_uuid_param("project_id", project_id)
        clean_user_story_id = "storyless" if is_storyless_id else validate_uuid_param("user_story_id", effective_story_id)
        clean_sprint_id = validate_uuid_param("sprint_id", sprint_id)

        # Multi-value parsing for story_assignee_id
        raw_story_assignees = (
            request.query_params.getlist("story_assignee_id")
            + request.query_params.getlist("story_assignee_ids")
        )
        if not raw_story_assignees and story_assignee_id:
            raw_story_assignees = [story_assignee_id]
        if story_assignee_ids:
            raw_story_assignees.append(story_assignee_ids)
        clean_story_assignee_ids = parse_uuid_list("story_assignee_id", raw_story_assignees)
        clean_story_assignee_filter = (
            clean_story_assignee_ids
            if len(clean_story_assignee_ids) > 1
            else (clean_story_assignee_ids[0] if clean_story_assignee_ids else None)
        )

        # Multi-value parsing for task_assignee_id
        raw_task_assignees = (
            request.query_params.getlist("task_assignee_id")
            + request.query_params.getlist("task_assignee_ids")
        )
        if not raw_task_assignees and task_assignee_id:
            raw_task_assignees = [task_assignee_id]
        if task_assignee_ids:
            raw_task_assignees.append(task_assignee_ids)
        clean_task_assignee_ids = parse_uuid_list("task_assignee_id", raw_task_assignees)
        clean_task_assignee_filter = (
            clean_task_assignee_ids
            if len(clean_task_assignee_ids) > 1
            else (clean_task_assignee_ids[0] if clean_task_assignee_ids else None)
        )

        # Multi-value parsing for label_id
        raw_labels = (
            request.query_params.getlist("label_id")
            + request.query_params.getlist("label_ids")
            + request.query_params.getlist("labels")
        )
        if not raw_labels and label_id:
            raw_labels = [label_id]
        if label_ids:
            raw_labels.append(label_ids)
        if labels:
            raw_labels.append(labels)
        clean_label_ids = parse_uuid_list("label_id", raw_labels)
        clean_label_filter = (
            clean_label_ids
            if len(clean_label_ids) > 1
            else (clean_label_ids[0] if clean_label_ids else None)
        )

        # Multi-value parsing for priority
        raw_priorities = (
            request.query_params.getlist("priority")
            + request.query_params.getlist("priorities")
        )
        if not raw_priorities and priority:
            raw_priorities = [priority]
        if priorities:
            raw_priorities.append(priorities)
        clean_priorities = parse_string_list(raw_priorities)
        clean_priority_filter = (
            clean_priorities
            if len(clean_priorities) > 1
            else (clean_priorities[0] if clean_priorities else None)
        )

        # Multi-value parsing for work_type
        raw_work_types = (
            request.query_params.getlist("work_type")
            + request.query_params.getlist("work_types")
            + request.query_params.getlist("type")
            + request.query_params.getlist("types")
        )
        if not raw_work_types and work_type:
            raw_work_types = [work_type]
        if work_types:
            raw_work_types.append(work_types)
        if type:
            raw_work_types.append(type)
        if types:
            raw_work_types.append(types)
        clean_work_types = parse_string_list(raw_work_types)
        clean_work_type_filter = (
            clean_work_types
            if len(clean_work_types) > 1
            else (clean_work_types[0] if clean_work_types else None)
        )

        # Multi-value parsing for task_status_id / task_status_ids
        raw_status_params = (
            request.query_params.getlist("task_status_id")
            + request.query_params.getlist("task_status_ids")
        )
        if not raw_status_params and task_status_id:
            raw_status_params = [task_status_id]
        if task_status_ids:
            raw_status_params.append(task_status_ids)

        clean_task_status_ids = parse_uuid_list("task_status_id", raw_status_params)

        clean_status_id = None
        if clean_user_story_id or effective_storyless:
            if status_id:
                # Mode C: status pagination for a specific story column or storyless column
                clean_status_id = validate_uuid_param("status_id", status_id)
                if effective_storyless and not clean_user_story_id:
                    clean_user_story_id = "storyless"
        else:
            # When status_id is provided without story reference, treat status_id as task_status_id filter across board
            raw_board_status = (
                request.query_params.getlist("status_id")
                + request.query_params.getlist("status_ids")
            )
            if not raw_board_status and status_id:
                raw_board_status = [status_id]
            if raw_board_status:
                fallback_status_ids = parse_uuid_list("status_id", raw_board_status)
                if not clean_task_status_ids:
                    clean_task_status_ids = fallback_status_ids

        clean_task_status_filter = (
            clean_task_status_ids
            if len(clean_task_status_ids) > 1
            else (clean_task_status_ids[0] if clean_task_status_ids else None)
        )

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
        if clean_user_story_id and clean_status_id:
            mode = "story_status_tasks"
            response = await service.get_board_status_tasks(
                clean_project_id,
                clean_user_story_id,
                clean_status_id,
                page=page,
                page_size=page_size,
                task_assignee_id=clean_task_assignee_filter,
                sprint_id=clean_sprint_id,
                priority=clean_priority_filter,
                work_type=clean_work_type_filter,
                label_id=clean_label_filter,
                current_user_id=str(user_id) if user_id else None,
            )
        elif effective_storyless:
            mode = "storyless_tasks"
            response = await service.get_board_storyless_tasks(
                clean_project_id,
                page=page,
                page_size=page_size,
                tasks_per_status=tasks_per_status,
                sprint_id=clean_sprint_id,
                task_assignee_id=clean_task_assignee_filter,
                task_status_id=clean_task_status_filter,
                priority=clean_priority_filter,
                work_type=clean_work_type_filter,
                label_id=clean_label_filter,
                current_user_id=str(user_id) if user_id else None,
                group_by_status=(group_by_status is not False),
            )
        elif clean_user_story_id:
            mode = "story"
            response = await service.get_board_story_details(
                clean_project_id,
                clean_user_story_id,
                page=page,
                page_size=page_size,
                tasks_per_status=tasks_per_status,
                task_assignee_id=clean_task_assignee_filter,
                task_status_id=clean_task_status_filter,
                priority=clean_priority_filter,
                work_type=clean_work_type_filter,
                label_id=clean_label_filter,
                current_user_id=str(user_id) if user_id else None,
            )
        else:
            mode = "stories"
            response = await service.get_board_stories(
                clean_project_id,
                page=page,
                page_size=page_size,
                sprint_id=clean_sprint_id,
                story_assignee_id=clean_story_assignee_filter,
                task_assignee_id=clean_task_assignee_filter,
                task_status_id=clean_task_status_filter,
                priority=clean_priority_filter,
                work_type=clean_work_type_filter,
                label_id=clean_label_filter,
                tasks_per_status=tasks_per_status,
                current_user_id=str(user_id) if user_id else None,
                include_storyless=bool(include_storyless),
            )

        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Board request completed: mode=%s, project_id=%s, user_story_id=%s, status_id=%s, page=%s, page_size=%s, duration_ms=%.2f",
            mode,
            clean_project_id,
            clean_user_story_id,
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
                "status_code": exc.status_code,
                "message": exc.message,
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
                "status_code": 500,
                "message": "Internal server error. Please try again later.",
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "status_code": 500,
                    "message": "Internal server error. Please try again later.",
                },
            },
        )
