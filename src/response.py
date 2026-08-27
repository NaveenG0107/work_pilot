# src/response.py
"""
Response envelope helpers mirroring internal/pkg/response in Go.
"""

from typing import Any, Optional

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


def success(message: str, data: Any = None, meta: Optional[dict] = None,
            status_code: int = 200) -> JSONResponse:
    body: dict = {
        "success": True,
        "status_code": status_code,
        "message": message,
    }
    if data is not None:
        body["data"] = data
    if meta is not None:
        body["meta"] = meta
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
