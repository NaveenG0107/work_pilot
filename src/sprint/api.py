from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from datetime import date

from src.auth.deps import get_current_user
from src.database import get_db
from src.sprint.schema import (CreateSprintRequest, StartSprintRequest, UpdateSprintRequest,)
from src.sprint.service import SprintService
from src.utils.core import (ErrorCode, error_response,)


router = APIRouter(prefix="/projects/{project_id}/sprint", tags=["Sprint"],)


def get_sprint_service(db=Depends(get_db),) -> SprintService:
    return SprintService(
        db=db
    )

# CREATE SPRINT

@router.post("", status_code=201,)
async def create_sprint(project_id: str, req: CreateSprintRequest, current_user: dict = Depends(get_current_user), service: SprintService = Depends(get_sprint_service),):
    user_id = current_user.get(
        "user_id"
    )

    organization_id = current_user.get(
        "organization_id"
    )

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    try:
        project_uuid = uuid.UUID(
            project_id
        )

    except (
        ValueError,
        TypeError,
    ):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    try:
        user_uuid = uuid.UUID(
            str(user_id)
        )

    except (
        ValueError,
        TypeError,
    ):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    organization_uuid = None

    if organization_id:
        try:
            organization_uuid = uuid.UUID(
                str(organization_id)
            )

        except (
            ValueError,
            TypeError,
        ):
            return error_response(
                ErrorCode.ErrForbidden,
                (
                    "You do not have permission "
                    "to perform this action"
                ),
                status_code=403,
            )

    sprint_id, err = (
        await service.create_sprint(
            req=req,
            project_id=str(
                project_uuid
            ),
            user_id=str(
                user_uuid
            ),
            organization_id=(
                str(organization_uuid)
                if organization_uuid
                else None
            ),
        )
    )

    if err:
        return err

    return {
        "message": (
            "Successfully Created Sprint"
        ),
        "status_code": 201,
        "success": True,
        "data": {
            "sprint_id": sprint_id,
        },
    }

# START SPRINT

@router.post(
    "/start",
    status_code=200,
)
async def start_sprint(
    project_id: str,
    req: StartSprintRequest,
    sprint_id: str = Query(...),
    current_user: dict = Depends(
        get_current_user
    ),
    service: SprintService = Depends(
        get_sprint_service
    ),
):

    user_id = current_user.get(
        "user_id"
    )

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    try:
        project_uuid = uuid.UUID(
            project_id
        )

    except (
        ValueError,
        TypeError,
    ):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )


    if not sprint_id:
        return error_response(
            ErrorCode.ErrBadRequest,
            (
                "sprint_id query parameter "
                "is required"
            ),
            status_code=400,
        )

    try:
        sprint_uuid = uuid.UUID(
            sprint_id
        )

    except (
        ValueError,
        TypeError,
    ):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )


    try:
        user_uuid = uuid.UUID(
            str(user_id)
        )

    except (
        ValueError,
        TypeError,
    ):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    sprint, err = (
        await service.start_sprint(
            req=req,
            project_id=str(
                project_uuid
            ),
            sprint_id=str(
                sprint_uuid
            ),
            user_id=str(
                user_uuid
            ),
        )
    )

    if err:
        return err

    return {
        "success": True,
        "status_code": 200,
        "message": (
            "Sprint started successfully."
        ),
        "data": sprint,
    }


@router.post(
    "/complete",
    status_code=200,
)
async def complete_sprint(
    project_id: str,
    sprint_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
    service: SprintService = Depends(get_sprint_service),
):
    user_id = current_user.get("user_id")
    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    try:
        project_uuid = uuid.UUID(project_id)
    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    if not sprint_id:
        return error_response(
            ErrorCode.ErrBadRequest,
            "sprint_id query parameter is required",
            status_code=400,
        )

    try:
        sprint_uuid = uuid.UUID(sprint_id)
    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    sprint, error = await service.complete_sprint(
        project_id=str(project_uuid),
        sprint_id=str(sprint_uuid),
        user_id=str(user_uuid),
    )
    if error:
        return error

    return {
        "success": True,
        "status_code": 200,
        "message": "Sprint completed successfully.",
        "data": sprint,
    }


@router.get(
    "",
    status_code=200,
)
async def get_sprints(
    project_id: str,
    page: int = Query(1),
    page_size: int = Query(10),
    status: str | None = Query(None),
    search: str | None = Query(None),
    fields: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    current_user: dict = Depends(get_current_user),
    service: SprintService = Depends(get_sprint_service),
):
    user_id = current_user.get("user_id")
    organization_id = current_user.get(
        "organization_id"
    )

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # Validate project UUID
    try:
        project_uuid = uuid.UUID(project_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # Validate user UUID
    try:
        user_uuid = uuid.UUID(str(user_id))

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    sprints, pagination, err = await service.get_sprints(
        project_id=str(project_uuid),
        user_id=str(user_uuid),
        organization_id=(
            str(organization_id)
            if organization_id
            else None
        ),
        page=page,
        page_size=page_size,
        status=status,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )

    if err:
        return err

    filtered_data = sprints

    if fields:
        requested_fields = {
            field.strip()
            for field in fields.split(",")
            if field.strip()
        }

        if requested_fields:
            filtered_data = [
                {
                    key: value
                    for key, value in sprint.items()
                    if key in requested_fields
                }
                for sprint in sprints
            ]

    return {
        "success": True,
        "status_code": 200,
        "message": "Sprints retrieved successfully.",
        "data": filtered_data,
        "meta": pagination,
    }


@router.post(
    "/complete",
    status_code=200,
)
async def complete_sprint(
    project_id: str,
    sprint_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
    service: SprintService = Depends(get_sprint_service),
):
    user_id = current_user.get("user_id")

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # Validate project UUID
    try:
        project_uuid = uuid.UUID(project_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # Go explicitly requires sprint_id query parameter
    if not sprint_id:
        return error_response(
            ErrorCode.ErrBadRequest,
            "sprint_id query parameter is required",
            status_code=400,
        )

    try:
        sprint_uuid = uuid.UUID(sprint_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    try:
        user_uuid = uuid.UUID(str(user_id))

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    data, error = await service.complete_sprint(
        project_id=str(project_uuid),
        sprint_id=str(sprint_uuid),
        user_id=str(user_uuid),
    )

    if error:
        return error

    return {
        "success": True,
        "status_code": 200,
        "message": "Sprint completed successfully.",
        "data": data,
    }

@router.get(
    "/{sprint_id}",
    status_code=200,
)
async def get_sprint_by_id(
    project_id: str,
    sprint_id: str,
    current_user: dict = Depends(get_current_user),
    service: SprintService = Depends(get_sprint_service),
):
    user_id = current_user.get("user_id")
    organization_id = current_user.get(
        "organization_id"
    )

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # Validate project UUID
    try:
        project_uuid = uuid.UUID(project_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # Validate sprint UUID
    try:
        sprint_uuid = uuid.UUID(sprint_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # Validate user UUID
    try:
        user_uuid = uuid.UUID(str(user_id))

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    sprint, err = await service.get_sprint_by_id(
        project_id=str(project_uuid),
        sprint_id=str(sprint_uuid),
        user_id=str(user_uuid),
        organization_id=(
            str(organization_id)
            if organization_id
            else None
        ),
    )

    if err:
        return err

    return {
        "success": True,
        "status_code": 200,
        "message": "Sprint retrieved successfully.",
        "data": sprint,
    }


@router.patch(
    "/{sprint_id}",
    status_code=200,
)
async def update_sprint(
    project_id: str,
    sprint_id: str,
    req: UpdateSprintRequest,
    current_user: dict = Depends(get_current_user),
    service: SprintService = Depends(get_sprint_service),
):
    user_id = current_user.get("user_id")
    organization_id = current_user.get(
        "organization_id"
    )

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # Project UUID
    try:
        project_uuid = uuid.UUID(project_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # Sprint UUID
    try:
        sprint_uuid = uuid.UUID(sprint_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # User UUID
    try:
        user_uuid = uuid.UUID(str(user_id))

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    error = await service.update_sprint(
        req=req,
        project_id=str(project_uuid),
        sprint_id=str(sprint_uuid),
        user_id=str(user_uuid),
        organization_id=(
            str(organization_id)
            if organization_id
            else None
        ),
    )

    if error:
        return error

    return {
        "message": "Sprint Updated successfully",
        "status_code": 200,
        "success": True,
        "data": {
            "Sprint ID": str(sprint_uuid),
        },
    }


@router.delete(
    "/{sprint_id}",
    status_code=200,
)
async def delete_sprint(
    project_id: str,
    sprint_id: str,
    current_user: dict = Depends(get_current_user),
    service: SprintService = Depends(get_sprint_service),
):
    user_id = current_user.get("user_id")
    organization_id = current_user.get(
        "organization_id"
    )

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # Validate project UUID
    try:
        project_uuid = uuid.UUID(project_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # Validate sprint UUID
    try:
        sprint_uuid = uuid.UUID(sprint_id)

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid sprint id",
            status_code=400,
        )

    # Validate user UUID
    try:
        user_uuid = uuid.UUID(str(user_id))

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    error = await service.delete_sprint(
        project_id=str(project_uuid),
        sprint_id=str(sprint_uuid),
        user_id=str(user_uuid),
        organization_id=(
            str(organization_id)
            if organization_id
            else None
        ),
    )

    if error:
        return error

    return {
        "message": "Sprint deleted successfully",
        "status_code": 200,
        "success": True,
        "data": {
            "Sprint ID": str(sprint_uuid),
        },
    }


@router.get(
    "/{sprint_id}/burndown",
    status_code=200,
)
async def get_sprint_burndown(
    project_id: str,
    sprint_id: str,
    current_user: dict = Depends(
        get_current_user
    ),
    service: SprintService = Depends(
        get_sprint_service
    ),
):
    user_id = current_user.get(
        "user_id"
    )

    organization_id = current_user.get(
        "organization_id"
    )

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # Project UUID
    try:
        project_uuid = uuid.UUID(
            project_id
        )

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # Sprint UUID
    try:
        sprint_uuid = uuid.UUID(
            sprint_id
        )

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    # User UUID
    try:
        user_uuid = uuid.UUID(
            str(user_id)
        )

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # Organization UUID
    try:
        organization_uuid = uuid.UUID(
            str(organization_id)
        )

    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrForbidden,
            "You do not have permission to perform this action",
            status_code=403,
        )

    data, error = (
        await service.get_sprint_burndown(
            sprint_id=str(
                sprint_uuid
            ),
            project_id=str(
                project_uuid
            ),
            user_id=str(
                user_uuid
            ),
            organization_id=str(
                organization_uuid
            ),
        )
    )

    if error:
        return error

    return {
        "success": True,
        "status_code": 200,
        "message": (
            "Sprint burndown data "
            "retrieved successfully."
        ),
        "data": data,
    }


@router.post(
    "/{sprint_id}/snapshot",
    status_code=200,
)
async def trigger_sprint_snapshot(
    project_id: str,
    sprint_id: str,
    current_user: dict = Depends(get_current_user),
    service: SprintService = Depends(get_sprint_service),
):
    user_id = current_user.get("user_id")
    organization_id = current_user.get("organization_id")

    if not user_id:
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    # validates project_id
    try:
        project_uuid = uuid.UUID(project_id)
    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrBadRequest,
            "Invalid ID format",
            status_code=400,
        )

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrUnauthorized,
            "Authentication required",
            status_code=401,
        )

    try:
        organization_uuid = uuid.UUID(
            str(organization_id)
        )
    except (ValueError, TypeError):
        return error_response(
            ErrorCode.ErrForbidden,
            "You do not have permission to perform this action",
            status_code=403,
        )

    err = await service.trigger_daily_snapshots(
        project_id=str(project_uuid),
        user_id=str(user_uuid),
        organization_id=str(organization_uuid),
    )

    if err:
        return err

    return {
        "success": True,
        "status_code": 200,
        "message": "Sprint snapshots triggered successfully.",
    }
