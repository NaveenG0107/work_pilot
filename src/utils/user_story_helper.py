import uuid
from typing import Any

from fastapi import Depends, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.user_story.service import UserStoryService, UserStoryServiceError
from src.utils.core import (
    GoJSONResponse as JSONResponse,
    authenticate_request,
)


class GoValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            auth_error = await authenticate_request(request)
            if auth_error is not None:
                return auth_error
            try:
                return await original(request)
            except RequestValidationError as exc:
                message = validation_message(exc, request.method)
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "status_code": 400,
                            "message": message,
                        },
                    },
                )

        return handler


def field_label(field: object) -> str:
    parts = str(field).split("_")
    return " ".join("ID" if part.lower() == "id" else part.capitalize() for part in parts)


def validation_message(exc: RequestValidationError, method: str) -> str:
    if method == "GET":
        return "Invalid query parameters"

    error = exc.errors()[0]
    error_type = error.get("type", "")
    field = field_label(error.get("loc", ("field",))[-1])
    context = error.get("ctx") or {}

    if error_type == "missing":
        return f"{field} is required."
    if error_type == "string_too_short":
        return f"{field} must be at least {context.get('min_length')} characters."
    if error_type == "string_too_long":
        return f"{field} must not exceed {context.get('max_length')} characters."
    if error_type == "too_short":
        return f"{field} must be at least {context.get('min_length')} characters."
    if error_type == "too_long":
        return f"{field} must not exceed {context.get('max_length')} characters."
    if error_type == "string_too_small":
        return f"{field} must be at least {context.get('ge')}."
    if error_type == "uuid_parsing":
        return f"{field} must be a valid UUID."
    if error_type == "json_invalid":
        return "Invalid JSON request body format."
    if error_type.endswith("_type"):
        expected = error_type.removesuffix("_type")
        return f"Invalid data type for {field}. Expected {expected}."
    return "Invalid request payload."


def get_user_story_service(db: AsyncSession = Depends(get_db)) -> UserStoryService:
    return UserStoryService(db)


def success(
    message: str,
    data: Any = None,
    *,
    code: int = status.HTTP_200_OK,
    meta: Any = None,
) -> JSONResponse:
    body: dict[str, Any] = {"success": True, "status_code": code, "message": message}
    if data is not None:
        body["data"] = data
    if meta is not None:
        body["meta"] = meta
    return JSONResponse(status_code=code, content=body)


def failure(exc: Exception) -> JSONResponse:
    if isinstance(exc, UserStoryServiceError):
        code, error_code, message = exc.status_code, exc.code, exc.message
    else:
        code, error_code, message = (
            500,
            "INTERNAL_SERVER_ERROR",
            "Something went wrong. Please try again later.",
        )
    return JSONResponse(
        status_code=code,
        content={
            "success": False,
            "error": {"code": error_code, "status_code": code, "message": message},
        },
    )


def validated_uuid(value: str) -> str:
    try:
        cleaned = str(value or "").strip().strip('"').strip("'")
        return str(uuid.UUID(cleaned))
    except (ValueError, TypeError, AttributeError) as exc:
        raise UserStoryServiceError(400, "BAD_REQUEST", "Invalid ID format") from exc


def dumped(value: Any) -> Any:
    if isinstance(value, list):
        return [item.model_dump(mode="json", by_alias=True) for item in value]
    return value.model_dump(mode="json", by_alias=True)
