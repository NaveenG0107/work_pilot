from fastapi import (
    APIRouter,
    Depends,
    Path,
    Query,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.audit.schema import (
    AuditFilter,
    AuditSuccessResponse,
)
from src.audit.service import AuditService
from src.config import get_logger
from src.database import get_db
from src.utils.core import GoJSONResponse as JSONResponse, authenticate_request, bearer_scheme


logger = get_logger(__name__)


_ACTIVITY_TYPES = ["view", "activity"]


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


router = APIRouter(
    prefix="/audit",
    tags=["Audit"],
    route_class=GoValidationRoute,
    dependencies=[Depends(bearer_scheme)],
    default_response_class=JSONResponse,
)


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
    if error_type == "string_too_small":
        return f"{field} must be at least {context.get('gt')}."
    if error_type == "string_too_large":
        return f"{field} must not exceed {context.get('lt')}."
    if error_type == "uuid_parsing":
        return f"{field} must be a valid UUID."
    if error_type == "date_from_datetime_parsing":
        return f"{field} must be a valid datetime."
    if error_type == "enum":
        allowed = context.get("expected") or []
        values = ", ".join(allowed) if allowed else "the allowed values"
        return f"{field} must be one of: {values}."
    if error_type == "value_error":
        message = str(context.get("error") or "")
        if message:
            return message
    return "Invalid request."


def get_audit_service(
    db: AsyncSession = Depends(get_db),
) -> AuditService:
    return AuditService(db)


def get_auth_context(
    request: Request,
) -> tuple[str, str]:

    user_id = getattr(
        request.state,
        "user_id",
        None,
    )

    organization_id = getattr(
        request.state,
        "organization_id",
        None,
    )

    if not user_id:
        raise ValueError("Authenticated user is required")

    if not organization_id:
        raise ValueError("Organization is required")

    return str(user_id), str(organization_id)


@router.get(
    "/{activity_type}",
    response_model=AuditSuccessResponse,
    response_model_exclude_none=True,
)
async def get_audit_logs(
    request: Request,
    activity_type: str = Path(
        ...,
        json_schema_extra={"enum": _ACTIVITY_TYPES},
    ),
    page: int = Query(
        default=1,
    ),
    page_size: int = Query(
        default=10,
    ),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    user_story_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    service: AuditService = Depends(get_audit_service),
):
    try:
        normalized_type = activity_type.strip().lower()

        # Match PaginationQuery.Normalize in the Go implementation.
        page = page if page > 0 else 1
        page_size = page_size if page_size > 0 else 10

        if normalized_type not in {
            "view",
            "activity",
        }:
            return JSONResponse(
                status_code=(status.HTTP_400_BAD_REQUEST),
                content={
                    "success": False,
                    "error": {
                        "code": "BAD_REQUEST",
                        "status_code": 400,
                        "message": (
                            "Invalid activity type. Allowed values: view, activity"
                        ),
                    },
                },
            )

        (
            user_id,
            organization_id,
        ) = get_auth_context(request)

        filters = AuditFilter(
            page=page,
            page_size=page_size,
            user_id=user_id,
            organization_id=(organization_id),
            resource_type=resource_type,
            resource_id=resource_id,
            task_id=task_id,
            user_story_id=(user_story_id),
            project_id=project_id,
            type=normalized_type,
        )

        audits, pagination = await service.get_audit_logs(filters)

        logger.info(
            "Audit logs retrieved successfully: type=%s count=%s user_id=%s",
            normalized_type,
            len(audits.activities),
            user_id,
        )

        return AuditSuccessResponse(
            success=True,
            status_code=(status.HTTP_200_OK),
            message=("Activity received successfully"),
            data=audits,
            meta=pagination,
        )

    except ValueError as exc:
        logger.warning(
            "Audit authorization failed: %s",
            exc,
        )

        return JSONResponse(
            status_code=(status.HTTP_401_UNAUTHORIZED),
            content={
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "status_code": 401,
                    "message": str(exc),
                },
            },
        )

    except RuntimeError as exc:
        logger.error(
            "Failed to retrieve audit logs: %s",
            exc,
        )

        return JSONResponse(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            content={
                "success": False,
                "error": {
                    "code": ("INTERNAL_SERVER_ERROR"),
                    "status_code": 500,
                    "message": str(exc),
                },
            },
        )

    except Exception:
        logger.exception("Unexpected error while retrieving audit logs")

        return JSONResponse(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            content={
                "success": False,
                "error": {
                    "code": ("INTERNAL_SERVER_ERROR"),
                    "status_code": 500,
                    "message": ("An unexpected error occurred"),
                },
            },
        )
