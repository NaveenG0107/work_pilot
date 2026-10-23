from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request  # type: ignore
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.database import get_db
from src.search.schema import ErrorResponse, SearchSuccessResponse
from src.search.service import SearchService, SearchServiceError
from src.utils.core import GoJSONResponse as JSONResponse
from src.utils.core import require_jwt

logger = get_logger(__name__)

router = APIRouter(
    prefix="/search",
    tags=["Search"],
    default_response_class=JSONResponse,
)


def get_search_service(db: AsyncSession = Depends(get_db)) -> SearchService:
    return SearchService(db)


def failure(exc: Exception) -> JSONResponse:
    if isinstance(exc, SearchServiceError):
        status_code = exc.status_code
        code = exc.code
        message = exc.message
        logger.warning(
            "SearchServiceError: status_code=%d, code=%s, message=%s",
            status_code,
            code,
            message,
        )
    else:
        status_code = 500
        code = "INTERNAL_SERVER_ERROR"
        message = "Failed to execute search queries"
        logger.error("Unhandled exception in search API: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": code,
                "status_code": status_code,
                "message": message,
            },
        },
    )


def dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


@router.get(
    "",
    response_model=SearchSuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Global Search",
    description=(
        "Search across all Tasks, User Stories, Projects, Members, and Sprints "
        "in the current user's organization"
    ),
)
@require_jwt
async def global_search(
    request: Request,
    q: str = Query(default="", description="Search query string"),
    category: str = Query(
        default="all",
        description="Search category: all, tasks, user_stories, projects, members, sprints",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(
        default=10, ge=1, le=100, description="Items per category per page"
    ),
    service: SearchService = Depends(get_search_service),
):
    try:
        user_id = getattr(request.state, "user_id", None)
        organization_id = getattr(request.state, "organization_id", None)
        request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
        logger.info(
            "Global search requested: category=%s, page=%d, page_size=%d, user_id=%s, organization_id=%s, req_id=%s",
            category,
            page,
            page_size,
            user_id,
            organization_id,
            request_id or "-",
        )
        if not user_id:
            raise SearchServiceError(
                401, "UNAUTHORIZED", "Authentication required"
            )
        if not organization_id:
            raise SearchServiceError(
                403, "ORGANIZATION_REQUIRED", "Organization is required"
            )
        result = await service.global_search(
            str(user_id),
            str(organization_id),
            q,
            category=category,
            page=page,
            page_size=page_size,
            request_id=str(request_id) if request_id else None,
        )
        logger.info(
            "Global search response ready: category=%s, user_id=%s, org_id=%s, total=%d, req_id=%s",
            category,
            user_id,
            organization_id,
            result.meta.total,
            request_id or "-",
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "status_code": 200,
                "message": "Search results retrieved successfully",
                "data": dump(result.data),
                "meta": dump(result.meta),
            },
        )
    except Exception as exc:
        return failure(exc)
