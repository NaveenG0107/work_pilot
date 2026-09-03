from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from src.database import get_db
from src.task.schema import (
    TASK_PRIORITIES,
    TASK_TYPES,
    BulkDeleteTasksRequest,
    BulkUpdateTasksRequest,
    CloneTaskRequest,
    CreateTaskRequest,
    ErrorResponse,
    SuccessResponse,
    UpdateTaskRequest,
)
from src.task.service import TaskService, TaskServiceError
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
    prefix="/projects/{project_id}/tasks",
    route_class=GoValidationRoute,
    dependencies=[Depends(bearer_scheme)],
    default_response_class=JSONResponse,
)


def get_task_service(db: AsyncSession = Depends(get_db)) -> TaskService:
    return TaskService(db)


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
    if error_type == "missing" or (
        value == "" and field_name in {"title", "type", "priority"}
    ):
        return f"{label} is required."
    if error_type == "string_too_short":
        return f"{label} must be at least {context.get('min_length')} characters."
    if error_type == "string_too_long":
        return f"{label} must not exceed {context.get('max_length')} characters."
    if error_type == "too_short":
        return f"{label} must be at least {context.get('min_length')} characters."
    if error_type == "literal_error":
        allowed = {
            "type": TASK_TYPES,
            "priority": TASK_PRIORITIES,
        }.get(field_name)
        if allowed:
            return f"{label} must be one of {', '.join(allowed)}."
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
    if isinstance(exc, TaskServiceError):
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
        raise TaskServiceError(
            500,
            "UNAUTHORIZED",
            "Internal server error: missing user context",
        )
    if organization_id is None:
        raise TaskServiceError(
            500,
            "UNAUTHORIZED",
            "Internal server error: missing organization context",
        )
    return parse_uuid(str(user_id)), parse_uuid(str(organization_id)), str(role)


def parse_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TaskServiceError(400, "BAD_REQUEST", "Invalid ID format") from exc


def dump(value: Any) -> Any:
    if isinstance(value, list):
        return [dump(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def multi_query(request: Request, key: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in request.query_params.getlist(key):
        for part in raw.split(","):
            value = part.strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def hidden_int_query(request: Request, key: str) -> int | None:
    value = request.query_params.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise TaskServiceError(
            400, "VALIDATION_ERROR", "Invalid filter parameters"
        ) from exc


# ---------------------------------------------------------------------------
# Create, list, and bulk routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    tags=["Task"],
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def create_task(
    request: Request,
    project_id: str,
    body: CreateTaskRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = await service.create(
            project_id, body, user_id, organization_id, role
        )
        return success_response(
            "Successfully Created Task",
            {"task_id": task_id},
            http_status=201,
        )
    except Exception as exc:
        return failure(exc)


@router.get(
    "",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def get_tasks(
    request: Request,
    project_id: str,
    page: int = Query(default=1),
    page_size: int = Query(default=10),
    sort_by: str = Query(
        default="created_at",
        json_schema_extra={
            "enum": ["title", "created_at", "updated_at", "priority", "status"]
        },
    ),
    sort_order: str = Query(
        default="DESC", json_schema_extra={"enum": ["ASC", "DESC"]}
    ),
    status_id: str | None = Query(default=None),
    assignee_id: str | None = Query(default=None),
    reporter_id: str | None = Query(default=None),
    sprint_id: str | None = Query(default=None),
    user_story_id: str | None = Query(default=None),
    type: str | None = Query(default=None, json_schema_extra={"enum": TASK_TYPES}),
    priority: str | None = Query(
        default=None, json_schema_extra={"enum": TASK_PRIORITIES}
    ),
    search: str = Query(default=""),
    labels: str | None = Query(default=None),
    is_deleted: bool = Query(default=False),
    unassigned_task: bool = Query(default=False),
    match: str = Query(default="", json_schema_extra={"enum": ["any", "all"]}),
    service: TaskService = Depends(get_task_service),
):
    # The named filter arguments are retained for Go-Swagger parity. Reading
    # the raw query additionally supports repeated and comma-separated values.
    del status_id, assignee_id, reporter_id, sprint_id, user_story_id, type, priority, labels
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        stories = multi_query(request, "user_story_id")
        if not stories:
            stories = multi_query(request, "story_id")
        tasks, pagination = await service.list(
            project_id,
            user_id,
            organization_id,
            role,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            status_id=multi_query(request, "status_id"),
            assignee_id=multi_query(request, "assignee_id"),
            reporter_id=multi_query(request, "reporter_id"),
            sprint_id=multi_query(request, "sprint_id"),
            user_story_id=stories,
            type=multi_query(request, "type"),
            priority=multi_query(request, "priority"),
            search=search,
            labels=multi_query(request, "labels"),
            is_deleted=is_deleted,
            unassigned_task=unassigned_task,
            match=match,
            sequence_number=hidden_int_query(request, "sequence_number"),
            serial_number=hidden_int_query(request, "serial_number"),
        )
        return success_response(
            "Tasks retrieved successfully",
            dump(tasks),
            meta=dump(pagination),
        )
    except Exception as exc:
        return failure(exc)


@router.patch(
    "/bulk",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}, 207: {"model": SuccessResponse}},
)
async def bulk_update_tasks(
    request: Request,
    project_id: str,
    body: BulkUpdateTasksRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        result = await service.bulk_update(
            project_id, body.tasks, user_id, organization_id, role
        )
        failed_count = len(result.failed_task_ids)
        if failed_count == len(body.tasks):
            return success_response(
                "Failed to update all tasks",
                dump(result),
                http_status=400,
                success=False,
            )
        if failed_count:
            return success_response(
                "Bulk update completed with some failures",
                dump(result),
                http_status=207,
            )
        return success_response("Successfully updated tasks", dump(result))
    except Exception as exc:
        return failure(exc)


@router.delete(
    "",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}, 207: {"model": SuccessResponse}},
)
async def delete_tasks(
    request: Request,
    project_id: str,
    body: BulkDeleteTasksRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        ids = [str(task_id) for task_id in body.task_ids]
        result = await service.bulk_delete(
            project_id, ids, user_id, organization_id, role
        )
        failed_count = len(result.failed_task_ids)
        if failed_count == len(ids):
            return success_response(
                "Failed to delete all tasks",
                dump(result),
                http_status=400,
                success=False,
            )
        if failed_count:
            return success_response(
                "Bulk deletion completed with some failures",
                dump(result),
                http_status=207,
            )
        return success_response("Successfully deleted tasks", dump(result))
    except Exception as exc:
        return failure(exc)


# ---------------------------------------------------------------------------
# Single-task routes
# ---------------------------------------------------------------------------


@router.get(
    "/{task_id}",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def get_task_by_id(
    request: Request,
    project_id: str,
    task_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = task_id.strip()
        if not task_id:
            raise TaskServiceError(
                400, "BAD_REQUEST", "Task ID or Task Key is required"
            )
        task = await service.get(
            project_id, task_id, user_id, organization_id, role
        )
        return success_response("Task retrieved successfully", dump(task))
    except Exception as exc:
        return failure(exc)


@router.patch(
    "/{task_id}",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def update_task(
    request: Request,
    project_id: str,
    task_id: str,
    body: UpdateTaskRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        updated_id = await service.update(
            project_id, task_id, body, user_id, organization_id, role
        )
        return success_response(
            "Successfully Updated Task", {"task_id": updated_id}
        )
    except Exception as exc:
        return failure(exc)


@router.post(
    "/{task_id}/restore",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def restore_task(
    request: Request,
    project_id: str,
    task_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        await service.restore(project_id, task_id, user_id, organization_id, role)
        return success_response("Successfully Restored Task")
    except Exception as exc:
        return failure(exc)


@router.post(
    "/{task_id}/clone",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def clone_task(
    request: Request,
    project_id: str,
    task_id: str,
    body: CloneTaskRequest,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        task = await service.clone(
            project_id, task_id, body, user_id, organization_id, role
        )
        return success_response("Successfully Cloned Task", dump(task))
    except Exception as exc:
        return failure(exc)


@router.patch(
    "/{task_id}/assign-to-me",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def assign_to_me(
    request: Request,
    project_id: str,
    task_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        task = await service.assign_to_me(
            project_id, task_id, user_id, organization_id, role
        )
        return success_response("Task assigned to you successfully", dump(task))
    except Exception as exc:
        return failure(exc)


# ---------------------------------------------------------------------------
# Labels and favorites
# ---------------------------------------------------------------------------


@router.put(
    "/{task_id}/labels/{label_id}",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def attach_label_to_task(
    request: Request,
    project_id: str,
    task_id: str,
    label_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        label_id = parse_uuid(label_id)
        await service.attach_label(
            project_id, task_id, label_id, user_id, organization_id, role
        )
        return success_response("Label attached to task successfully")
    except Exception as exc:
        return failure(exc)


@router.delete(
    "/{task_id}/labels/{label_id}",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def remove_label_from_task(
    request: Request,
    project_id: str,
    task_id: str,
    label_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        label_id = parse_uuid(label_id)
        removed_id = await service.remove_label(
            project_id, task_id, label_id, user_id, organization_id, role
        )
        return success_response(
            "Label removed from task successfully", {"Label_id": removed_id}
        )
    except Exception as exc:
        return failure(exc)


@router.post(
    "/{task_id}/favorite",
    tags=["Task"],
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def add_task_favorite(
    request: Request,
    project_id: str,
    task_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        favorite = await service.favorite(
            project_id, task_id, user_id, organization_id, role
        )
        return success_response(
            "",
            dump(favorite),
            http_status=201,
            body_status=0,
        )
    except Exception as exc:
        return failure(exc)


@router.delete(
    "/{task_id}/favorite",
    tags=["Task"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def remove_task_favorite(
    request: Request,
    project_id: str,
    task_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, organization_id, role = auth_context(request)
        project_id = parse_uuid(project_id)
        task_id = parse_uuid(task_id)
        result = await service.unfavorite(
            project_id, task_id, user_id, organization_id, role
        )
        return success_response(
            "Task removed from favorites",
            dump(result),
            body_status=0,
        )
    except Exception as exc:
        return failure(exc)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "maxItems": 5,
                        }
                    },
                }
            }
        },
    }
}


@router.post(
    "/{task_id}/attachments",
    tags=["Attachment"],
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    openapi_extra=_UPLOAD_OPENAPI,
)
async def upload_attachment(
    request: Request,
    project_id: str,
    task_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, _, _ = auth_context(request)
        project_id = parse_uuid(project_id)
        try:
            form = await request.form()
        except Exception as exc:
            raise TaskServiceError(
                400, "BAD_REQUEST", "Failed to parse multipart form"
            ) from exc
        uploads = [
            value
            for key, value in form.multi_items()
            if key in {"file", "files"} and isinstance(value, UploadFile)
        ]
        if not uploads:
            raise TaskServiceError(
                400,
                "BAD_REQUEST",
                "Missing file(s) in request payload (use form-data keys 'file' or 'files')",
            )
        max_size_mb, max_files = service.attachment_limits()
        if len(uploads) > max_files:
            raise TaskServiceError(
                400,
                "BAD_REQUEST",
                f"Maximum of {max_files} files can be uploaded per request.",
            )
        files: list[tuple[str, bytes]] = []
        max_size_bytes = max_size_mb * 1024 * 1024
        for upload in uploads:
            if upload.size is not None and upload.size > max_size_bytes:
                raise TaskServiceError(
                    413,
                    "PAYLOAD_TOO_LARGE",
                    f"File {upload.filename} exceeds the maximum allowed size of {max_size_mb} MB.",
                )
            try:
                content = await upload.read(max_size_bytes + 1)
            finally:
                await upload.close()
            if len(content) > max_size_bytes:
                raise TaskServiceError(
                    413,
                    "PAYLOAD_TOO_LARGE",
                    f"File {upload.filename} exceeds the maximum allowed size of {max_size_mb} MB.",
                )
            files.append((upload.filename or "attachment", content))
        attachments = await service.upload_attachments(
            project_id, task_id, user_id, files
        )
        return success_response(
            "Attachments uploaded successfully",
            dump(attachments),
            http_status=201,
        )
    except Exception as exc:
        return failure(exc)


@router.get(
    "/{task_id}/attachments",
    tags=["Attachment"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}},
)
async def get_attachments(
    request: Request,
    project_id: str,
    task_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, _, _ = auth_context(request)
        project_id = parse_uuid(project_id)
        attachments = await service.get_attachments(project_id, task_id, user_id)
        return success_response(
            "Attachments retrieved successfully", dump(attachments)
        )
    except Exception as exc:
        return failure(exc)


@router.get(
    "/{task_id}/attachments/{attachment_id}/download",
    tags=["Attachment"],
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def download_attachment(
    request: Request,
    project_id: str,
    task_id: str,
    attachment_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, _, _ = auth_context(request)
        project_id = parse_uuid(project_id)
        attachment_id = parse_uuid(attachment_id)
        content, filename, mime_type, size = await service.download_attachment(
            project_id, task_id, attachment_id, user_id
        )
        filename = filename.replace("\r", "").replace("\n", "")
        fallback = filename.encode("ascii", "ignore").decode() or "attachment"
        disposition = f'attachment; filename="{fallback.replace(chr(34), "")}"'
        if fallback != filename:
            disposition += f"; filename*=UTF-8''{quote(filename)}"
        return Response(
            status_code=200,
            content=content,
            headers={
                "Content-Disposition": disposition,
                "Content-Type": mime_type,
                "Content-Length": str(size),
            },
        )
    except Exception as exc:
        return failure(exc)


@router.delete(
    "/{task_id}/attachments/{attachment_id}",
    tags=["Attachment"],
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def delete_attachment(
    request: Request,
    project_id: str,
    task_id: str,
    attachment_id: str,
    service: TaskService = Depends(get_task_service),
):
    try:
        user_id, _, _ = auth_context(request)
        project_id = parse_uuid(project_id)
        attachment_id = parse_uuid(attachment_id)
        await service.delete_attachment(project_id, task_id, attachment_id, user_id)
        return success_response("Attachment deleted successfully")
    except Exception as exc:
        return failure(exc)
