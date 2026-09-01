from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.favorite.schema import (
    FAVORITE_TYPES,
    AddFavoriteRequest,
    ErrorResponse,
    FavoriteListSuccessResponse,
    FavoriteSuccessResponse,
    FavoriteType,
    RemoveFavoriteSuccessResponse,
    SuccessResponse,
)
from src.favorite.service import FavoriteService, FavoriteServiceError
from src.utils.core import GoJSONResponse as JSONResponse
from src.utils.core import authenticate_request, bearer_scheme


class GoValidationRoute(APIRoute):
    """Apply JWT middleware before FastAPI parses the request, like Gin."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            auth_error = await authenticate_request(request)
            if auth_error is not None:
                return auth_error
            try:
                return await original(request)
            except RequestValidationError as exc:
                return failure_response(
                    400,
                    "VALIDATION_ERROR",
                    validation_message(exc, request.method),
                )

        return handler


router = APIRouter(
    prefix="/favorites",
    route_class=GoValidationRoute,
    dependencies=[Depends(bearer_scheme)],
    default_response_class=JSONResponse,
)


def get_favorite_service(db: AsyncSession = Depends(get_db)) -> FavoriteService:
    return FavoriteService(db)


def field_label(field: object) -> str:
    return " ".join(
        "ID" if part.lower() == "id" else part.capitalize()
        for part in str(field).split("_")
    )


def validation_message(exc: RequestValidationError, method: str) -> str:
    if method == "GET":
        return "Invalid filter parameters"
    error = exc.errors()[0]
    location = error.get("loc", ())
    error_type = error.get("type", "")
    field_name = str(location[-1]) if location else "field"
    label = field_label(field_name)
    context = error.get("ctx") or {}
    value = error.get("input")

    if location == ("body",) and error_type == "missing":
        return "Invalid JSON request body format."
    if error_type == "json_invalid":
        return "Invalid JSON request body format."
    if error_type == "missing" or (value == "" and field_name in {"item_id", "item_type"}):
        return f"{label} is required."
    if error_type == "literal_error":
        if field_name == "item_type":
            return f"{label} must be one of {', '.join(FAVORITE_TYPES)}."
        return f"{label} must be one of {', '.join(FAVORITE_TYPES)}."
    if error_type.endswith("_type"):
        expected = error_type.removesuffix("_type")
        return f"Invalid data type for {label}. Expected {expected}."
    return "Invalid request payload."


_MISSING = object()


def success_response(
    message: str,
    data: Any = _MISSING,
    *,
    http_status: int = 200,
    body_status: int | None = None,
    success: bool = True,
    meta: Any = _MISSING,
) -> JSONResponse:
    body: dict[str, Any] = {
        "success": success,
        "status_code": http_status if body_status is None else body_status,
        "message": message,
    }
    if data is not _MISSING:
        body["data"] = data
    if meta is not _MISSING:
        body["meta"] = meta
    return JSONResponse(status_code=http_status, content=body)


def failure_response(code: int, error_code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={
            "success": False,
            "error": {
                "code": error_code,
                "status_code": code,
                "message": message,
            },
        },
    )


def failure(exc: Exception) -> JSONResponse:
    if isinstance(exc, FavoriteServiceError):
        return failure_response(exc.status_code, exc.code, exc.message)
    return failure_response(
        500,
        "INTERNAL_SERVER_ERROR",
        "Something went wrong. Please try again later.",
    )


def auth_context(request: Request) -> tuple[str, str, str]:
    user_id = getattr(request.state, "user_id", None)
    organization_id = getattr(request.state, "organization_id", None)
    role = getattr(request.state, "role", "") or ""
    if user_id is None:
        raise FavoriteServiceError(
            500,
            "UNAUTHORIZED",
            "Internal server error: missing user context",
        )
    if organization_id is None:
        raise FavoriteServiceError(
            500,
            "UNAUTHORIZED",
            "Internal server error: missing organization context",
        )
    return parse_uuid(str(user_id)), parse_uuid(str(organization_id)), str(role)


def parse_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise FavoriteServiceError(400, "BAD_REQUEST", "Invalid ID format") from exc


def dump(value: Any) -> Any:
    if isinstance(value, list):
        return [dump(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


@router.get(
    "",
    tags=["Favorites"],
    response_model=FavoriteListSuccessResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def get_favorites(
    request: Request,
    item_type: str | None = Query(default=None),
    search: str = Query(default=""),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="DESC"),
    page: int = Query(default=1),
    page_size: int = Query(default=10),
    service: FavoriteService = Depends(get_favorite_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        result = await service.list_favorites(
            user_id,
            item_type=item_type,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
        )
        return success_response(
            "Favorites retrieved successfully",
            dump(result),
        )
    except Exception as exc:
        return failure(exc)


@router.post(
    "",
    tags=["Favorites"],
    status_code=status.HTTP_201_CREATED,
    response_model=FavoriteSuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def add_favorite(
    request: Request,
    body: AddFavoriteRequest,
    service: FavoriteService = Depends(get_favorite_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        favorite = await service.add_favorite(
            user_id, parse_uuid(body.item_id), body.item_type
        )
        return success_response(
            "Favorite added successfully",
            dump(favorite),
            http_status=201,
        )
    except Exception as exc:
        return failure(exc)


@router.delete(
    "",
    tags=["Favorites"],
    response_model=RemoveFavoriteSuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def remove_favorite(
    request: Request,
    item_type: FavoriteType = Query(...),
    item_id: str = Query(...),
    service: FavoriteService = Depends(get_favorite_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        parsed_item_id = parse_uuid(item_id)
        result = await service.remove_favorite(
            user_id, item_type, parsed_item_id
        )
        return success_response(
            "Favorite removed successfully",
            dump(result),
        )
    except Exception as exc:
        return failure(exc)
