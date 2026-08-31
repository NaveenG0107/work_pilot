from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.audit.schemas import (
    AuditFilter,
    AuditSuccessResponse,
)
from src.audit.service import AuditService
from src.config import get_logger
from src.database import get_db


logger = get_logger(__name__)


router = APIRouter(
    prefix="/audit",
    tags=["Audit"],
)


def get_audit_service(
    db: Session = Depends(get_db),
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
def get_audit_logs(
    activity_type: str,
    request: Request,
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

        audits, pagination = service.get_audit_logs(filters)

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
