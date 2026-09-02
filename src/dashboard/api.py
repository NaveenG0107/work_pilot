# src/dashboard/api.py
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.deps import get_current_user
from src.dashboard.schemas import (
    DashboardOverview,
    DashboardResponse,
    TeamWorkload,
    WeeklyProgress,
)
from src.dashboard.service import DashboardService
from src.database import get_db
from src.response import error, success

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Dashboard"])


def get_dashboard_service(db: AsyncSession = Depends(get_db)) -> DashboardService:
    return DashboardService(db)


def validate_uuid(value: str, param_name: str = "ID") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {param_name}",
        )


@router.get("/{project_id}/overview", status_code=status.HTTP_200_OK)
async def get_overview(
    project_id: str = Path(..., description="Project ID (UUID)"),
    sprint_id: Optional[str] = Query(None, description="Optional Sprint ID (UUID)"),
    sprintid: Optional[str] = Query(None, description="Optional Sprint ID alias (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: DashboardService = Depends(get_dashboard_service),
):
    """
    Retrieve the task overview for a project.
    """
    logger.info("Received request to get dashboard overview for project %s", project_id)

    # Validate project ID
    valid_project_id = validate_uuid(project_id, "project ID")

    # Validate optional sprint ID
    chosen_sprint_id = sprint_id or sprintid
    valid_sprint_id: Optional[str] = None
    if chosen_sprint_id:
        valid_sprint_id = validate_uuid(chosen_sprint_id, "sprint ID")

    user_id = current_user.get("user_id")
    if not user_id:
        logger.error("Authentication required for fetching dashboard overview")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    try:
        overview_data = await service.get_overview(
            project_id=valid_project_id,
            user_id=user_id,
            sprint_id=valid_sprint_id,
        )
        return success(
            message="Task Overview fetched successfully",
            data=overview_data,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_overview: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_overview: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{project_id}/task-status", status_code=status.HTTP_200_OK)
async def get_task_status(
    project_id: str = Path(..., description="Project ID (UUID)"),
    sprint_id: Optional[str] = Query(None, description="Optional Sprint ID (UUID)"),
    sprintid: Optional[str] = Query(None, description="Optional Sprint ID alias (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: DashboardService = Depends(get_dashboard_service),
):
    """
    Retrieve the task status summary for a project.
    """
    logger.info("Received request to get task status for project %s", project_id)

    # Validate project ID
    valid_project_id = validate_uuid(project_id, "project ID")

    # Validate optional sprint ID
    chosen_sprint_id = sprint_id or sprintid
    valid_sprint_id: Optional[str] = None
    if chosen_sprint_id:
        valid_sprint_id = validate_uuid(chosen_sprint_id, "sprint ID")

    user_id = current_user.get("user_id")
    if not user_id:
        logger.error("Authentication required for fetching task status")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    try:
        task_status_data = await service.get_task_status(
            project_id=valid_project_id,
            user_id=user_id,
            sprint_id=valid_sprint_id,
        )
        return success(
            message="Task status fetched successfully",
            data=task_status_data,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_task_status: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_task_status: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{project_id}/team-workload", status_code=status.HTTP_200_OK)
async def get_team_workload(
    project_id: str = Path(..., description="Project ID (UUID)"),
    sprint_id: Optional[str] = Query(None, description="Optional Sprint ID (UUID)"),
    sprintid: Optional[str] = Query(None, description="Optional Sprint ID alias (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: DashboardService = Depends(get_dashboard_service),
):
    """
    Retrieve the workload of team members for a project.
    """
    logger.info("Received request to get team workload for project %s", project_id)

    # Validate project ID
    valid_project_id = validate_uuid(project_id, "project ID")

    # Validate optional sprint ID
    chosen_sprint_id = sprint_id or sprintid
    valid_sprint_id: Optional[str] = None
    if chosen_sprint_id:
        valid_sprint_id = validate_uuid(chosen_sprint_id, "sprint ID")

    user_id = current_user.get("user_id")
    if not user_id:
        logger.error("Authentication required for fetching team workload")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    try:
        team_workload_data = await service.get_team_workload(
            project_id=valid_project_id,
            user_id=user_id,
            sprint_id=valid_sprint_id,
        )
        return success(
            message="TeamWorkLoad fetched successfully",
            data=team_workload_data,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_team_workload: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_team_workload: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{project_id}/sprint-burndown", status_code=status.HTTP_200_OK)
async def get_sprint_burndown(
    project_id: str = Path(..., description="Project ID (UUID)"),
    sprint_id: Optional[str] = Query(None, description="Optional Sprint ID (UUID)"),
    sprintid: Optional[str] = Query(None, description="Optional Sprint ID alias (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: DashboardService = Depends(get_dashboard_service),
):
    """
    Retrieve sprint burndown chart data for a project.
    """
    logger.info("Received request to get sprint burndown for project %s", project_id)

    # Validate project ID
    valid_project_id = validate_uuid(project_id, "project ID")

    # Validate optional sprint ID
    chosen_sprint_id = sprint_id or sprintid
    valid_sprint_id: Optional[str] = None
    if chosen_sprint_id:
        valid_sprint_id = validate_uuid(chosen_sprint_id, "sprint ID")

    user_id = current_user.get("user_id")
    if not user_id:
        logger.error("Authentication required for fetching sprint burndown")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    try:
        burndown_data = await service.get_sprint_burndown(
            project_id=valid_project_id,
            user_id=user_id,
            sprint_id=valid_sprint_id,
        )
        return success(
            message="SprintBurndown fetched successfully",
            data=burndown_data,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_sprint_burndown: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_sprint_burndown: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{project_id}/weekly-progress", status_code=status.HTTP_200_OK)
async def get_weekly_progress(
    project_id: str = Path(..., description="Project ID (UUID)"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    current_user: dict = Depends(get_current_user),
    service: DashboardService = Depends(get_dashboard_service),
):
    """
    Retrieve weekly task progress for a project within the specified date range.
    """
    logger.info(
        "Received request to get weekly progress for project %s (start_date: %s, end_date: %s)",
        project_id,
        start_date,
        end_date,
    )

    # 1. Validate project ID
    valid_project_id = validate_uuid(project_id, "project ID")

    # 2. Validate authentication context
    user_id = current_user.get("user_id")
    if not user_id:
        logger.error("Authentication required for fetching weekly progress")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    # 3. Validate presence of start_date and end_date
    if not start_date or not end_date:
        logger.warning("Missing start_date or end_date in weekly progress request")
        return error("start_date and end_date are required", status_code=status.HTTP_400_BAD_REQUEST, code="BAD_REQUEST")

    # 4. Parse start_date
    try:
        parsed_start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("Invalid start_date format: %s", start_date)
        return error("Invalid start_date format. Use YYYY-MM-DD", status_code=status.HTTP_400_BAD_REQUEST, code="BAD_REQUEST")

    # 5. Parse end_date
    try:
        parsed_end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("Invalid end_date format: %s", end_date)
        return error("Invalid end_date format. Use YYYY-MM-DD", status_code=status.HTTP_400_BAD_REQUEST, code="BAD_REQUEST")

    # 6. Fetch weekly progress from service
    try:
        weekly_progress_data = await service.get_weekly_progress(
            project_id=valid_project_id,
            user_id=user_id,
            start_date=parsed_start_date,
            end_date=parsed_end_date,
        )
        return success(
            message="Task status fetched successfully",
            data=weekly_progress_data,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_weekly_progress: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_weekly_progress: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{project_id}/dashboard", status_code=status.HTTP_200_OK)
async def get_dashboard(
    project_id: str = Path(..., description="Project ID (UUID)"),
    sprint_id: Optional[str] = Query(None, description="Optional Sprint ID (UUID)"),
    sprintid: Optional[str] = Query(None, description="Optional Sprint ID alias (UUID)"),
    current_user: dict = Depends(get_current_user),
    service: DashboardService = Depends(get_dashboard_service),
):
    """
    Retrieve comprehensive dashboard data (overview, task status, team workload, sprint burndown) for a project and sprint.
    """
    logger.info("Received request to get full dashboard data for project %s", project_id)

    # 1. Validate project ID
    valid_project_id = validate_uuid(project_id, "project ID")

    # 2. Validate optional sprint ID
    chosen_sprint_id = sprint_id or sprintid
    valid_sprint_id: Optional[str] = None
    if chosen_sprint_id:
        valid_sprint_id = validate_uuid(chosen_sprint_id, "sprint ID")

    # 3. Validate user authentication
    user_id = current_user.get("user_id")
    if not user_id:
        logger.error("Authentication required for fetching dashboard data")
        return error("Authentication required", status_code=status.HTTP_401_UNAUTHORIZED, code="UNAUTHORIZED")

    # 4. Fetch composite dashboard data
    try:
        dashboard_data = await service.get_dashboard_data(
            project_id=valid_project_id,
            user_id=user_id,
            sprint_id=valid_sprint_id,
        )
        return success(
            message="Successfully Got the Dashboard",
            data=dashboard_data,
            status_code=status.HTTP_200_OK,
        )
    except HTTPException as exc:
        code = getattr(exc, "code", None)
        logger.error("HTTPException in get_dashboard: %s (status: %d)", exc.detail, exc.status_code)
        return error(
            message=exc.detail,
            status_code=exc.status_code,
            code=code,
        )
    except Exception as exc:
        logger.exception("Unexpected error in get_dashboard: %s", exc)
        return error(
            message="Something went wrong. Please try again later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


