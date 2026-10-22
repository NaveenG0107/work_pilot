# src/utils/response.py
"""
Response envelope helpers for WorkPilot backend.
Provides standardized JSON envelopes for API success and error responses.
"""

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "RESOURCE_NOT_FOUND",
    409: "CONFLICT",
    410: "GONE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_SERVER_ERROR",
}


def _serialize(value: Any) -> Any:
    """Convert incoming response data into a JSON-serializable structure.

    Uses FastAPI's ``jsonable_encoder`` so Pydantic models, dataclasses,
    UUIDs, datetime and other non-JSON types are handled automatically.
    """
    if value is None:
        return None

    if isinstance(value, JSONResponse):
        return value

    return jsonable_encoder(value)


def success(
    message: str,
    data: Any = None,
    meta: Optional[dict] = None,
    status_code: int = 200,
) -> JSONResponse:
    """Standard success response envelope: {success: true, status_code, message, data, meta}."""
    body: dict = {
        "success": True,
        "status_code": status_code,
        "message": message,
    }
    data = _serialize(data)
    if data is not None:
        body["data"] = data
    if meta is not None:
        body["meta"] = _serialize(meta)
    return JSONResponse(status_code=status_code, content=body)


def error(
    message: str,
    status_code: int = 400,
    code: Optional[str] = None,
) -> JSONResponse:
    """Standard error response envelope: {success: false, error: {code, status_code, message}}."""
    if code is None:
        code = ERROR_CODES.get(status_code, "INTERNAL_SERVER_ERROR")
    body = {
        "success": False,
        "error": {
            "code": code,
            "status_code": status_code,
            "message": message,
        },
    }
    return JSONResponse(status_code=status_code, content=body)
