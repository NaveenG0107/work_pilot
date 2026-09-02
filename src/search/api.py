from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request  # type: ignore
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.search.schema import ErrorResponse, SearchSuccessResponse
from src.search.service import SearchService, SearchServiceError
from src.utils.core import GoJSONResponse as JSONResponse
from src.utils.core import require_jwt

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
    else:
        status_code = 500
        code = "INTERNAL_SERVER_ERROR"
        message = "Failed to execute search queries"
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
        401: {"model": ErrorResponse},
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
    service: SearchService = Depends(get_search_service),
):
    try:
        user_id = getattr(request.state, "user_id", None)
        organization_id = getattr(request.state, "organization_id", None)
        if not user_id or not organization_id:
            raise SearchServiceError(
                401, "UNAUTHORIZED", "Authentication required"
            )
        result = await service.global_search(
            str(user_id), str(organization_id), q
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "status_code": 200,
                "message": "Search results retrieved successfully",
                "data": dump(result),
            },
        )
    except Exception as exc:
        return failure(exc)
