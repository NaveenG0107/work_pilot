# src/response.py
"""
Response envelope helpers.
"""

from datetime import datetime, date
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


def success(message: str, data: Any = None, meta: Optional[dict] = None,
            status_code: int = 200) -> JSONResponse:
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


def error(message: str, status_code: int = 400, code: Optional[str] = None) -> JSONResponse:
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
