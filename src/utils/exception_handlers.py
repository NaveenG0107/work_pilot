from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

ERROR_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "RESOURCE_NOT_FOUND",
    409: "CONFLICT",
    410: "GONE",
    500: "INTERNAL_SERVER_ERROR",
}


def field_label(field: str) -> str:
    parts = str(field).split("_")
    return " ".join("ID" if part.lower() == "id" else part.capitalize() for part in parts)


def extract_validation_message(exc: RequestValidationError, method: str) -> str:
    if method == "GET":
        return "Invalid query parameters"

    errors = exc.errors()
    if not errors:
        return "Invalid request payload."

    error = errors[0]
    error_type = error.get("type", "")
    msg = str(error.get("msg", ""))

    if msg.startswith("Value error, "):
        return msg[13:]

    field = field_label(error.get("loc", ("field",))[-1])
    context = error.get("ctx") or {}

    if error_type == "missing":
        return f"{field} is required."
    if error_type in ("string_too_short", "too_short"):
        return f"{field} must be at least {context.get('min_length')} characters."
    if error_type in ("string_too_long", "too_long"):
        return f"{field} must not exceed {context.get('max_length')} characters."
    if error_type == "json_invalid":
        return "Invalid JSON request body format."
    if error_type.endswith("_type"):
        expected = error_type.removesuffix("_type")
        return f"Invalid data type for {field}. Expected {expected}."

    return msg or "Invalid request payload."


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    message = extract_validation_message(exc, request.method)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "status_code": 400,
                "message": message,
            },
        },
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = ERROR_CODES.get(exc.status_code, "BAD_REQUEST")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": code,
                "status_code": exc.status_code,
                "message": str(exc.detail),
            },
        },
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "status_code": 500,
                "message": "Something went wrong. Please try again later.",
            },
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
