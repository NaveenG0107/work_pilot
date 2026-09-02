# src/dashboard/service.py
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.models import User
from src.custom_status.models import CustomStatus
from src.dashboard.schemas import (
    DashboardOverview,
    DashboardResponse,
    DashboardSprintBurndownResponse,
    SprintBurndownData,
    SprintBurndownPoint,
    TeamWorkload,
    WeeklyProgress,
)
from src.middleware.rbac import has_default_permission
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.sprint.models import Sprint
from src.task.models import Task

logger = logging.getLogger(__name__)


class DashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _check_permission(
        self, user: User, project_id: str, resource: str, action: str
    ) -> bool:
        """
        Evaluates project permissions considering project-level member role overrides
        and falling back to the organization-level role permissions.
        """
        if user.role and user.role.name == "super_admin":
            return False

        # 1. Check project-level member role first
        stmt = (
            select(ProjectMember)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
            )
            .options(selectinload(ProjectMember.role).selectinload(Role.permissions))
        )
        res = await self.db.execute(stmt)
        pm = res.scalar_one_or_none()

        if pm and pm.role:
            for p in pm.role.permissions:
                if p.resource == resource and p.action == action:
                    return True
            return False

        # 2. Fall back to organization role permissions
        if user.role and user.role.permissions:
            for p in user.role.permissions:
                if p.resource == resource and p.action == action:
                    return True
            if has_default_permission(user.role.name, resource, action):
                return True

        return False

    async def get_overview(
        self,
        project_id: str,
        user_id: str,
        sprint_id: Optional[str] = None,
    ) -> DashboardOverview:
        """
        Fetches task counts overview (total, completed, pending, overdue, due_soon) for a project/sprint.
        """
        logger.info(
            "Fetching dashboard overview for project %s (user: %s, sprint: %s)",
            project_id,
            user_id,
            sprint_id or "all",
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:view)
        can_view = await self._check_permission(user, project_id, "projects", "view")
        if not can_view:
            logger.warning(
                "User %s not authorized to view dashboard in project %s",
                user_id,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view dashboard in this project",
            )

        now = datetime.now(timezone.utc)
        forty_eight_hours_later = now + timedelta(hours=48)

        # 4. Total tasks
        total_stmt = (
            select(func.count(Task.id))
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
            )
        )
        if sprint_id:
            total_stmt = total_stmt.where(Task.sprint_id == sprint_id)
        total_res = await self.db.execute(total_stmt)
        total_tasks = total_res.scalar() or 0

        # 5. Completed tasks (custom_statuses.is_final = True)
        completed_stmt = (
            select(func.count(Task.id))
            .join(CustomStatus, CustomStatus.id == Task.status_id)
            .where(
                Task.project_id == project_id,
                CustomStatus.is_final.is_(True),
                Task.deleted_at.is_(None),
                CustomStatus.deleted_at.is_(None),
            )
        )
        if sprint_id:
            completed_stmt = completed_stmt.where(Task.sprint_id == sprint_id)
        completed_res = await self.db.execute(completed_stmt)
        completed_tasks = completed_res.scalar() or 0

        # 6. Pending tasks (custom_statuses.is_final = False)
        pending_stmt = (
            select(func.count(Task.id))
            .join(CustomStatus, CustomStatus.id == Task.status_id)
            .where(
                Task.project_id == project_id,
                CustomStatus.is_final.is_(False),
                Task.deleted_at.is_(None),
                CustomStatus.deleted_at.is_(None),
            )
        )
        if sprint_id:
            pending_stmt = pending_stmt.where(Task.sprint_id == sprint_id)
        pending_res = await self.db.execute(pending_stmt)
        pending_tasks = pending_res.scalar() or 0

        # 7. Overdue tasks (due_date < now and is_final = False)
        overdue_stmt = (
            select(func.count(Task.id))
            .join(CustomStatus, CustomStatus.id == Task.status_id)
            .where(
                Task.project_id == project_id,
                Task.due_date < now,
                CustomStatus.is_final.is_(False),
                Task.deleted_at.is_(None),
                CustomStatus.deleted_at.is_(None),
            )
        )
        if sprint_id:
            overdue_stmt = overdue_stmt.where(Task.sprint_id == sprint_id)
        overdue_res = await self.db.execute(overdue_stmt)
        overdue_tasks = overdue_res.scalar() or 0

        # 8. Due soon tasks (due_date between now and now + 48h and is_final = False)
        due_soon_stmt = (
            select(func.count(Task.id))
            .join(CustomStatus, CustomStatus.id == Task.status_id)
            .where(
                Task.project_id == project_id,
                Task.due_date >= now,
                Task.due_date <= forty_eight_hours_later,
                CustomStatus.is_final.is_(False),
                Task.deleted_at.is_(None),
                CustomStatus.deleted_at.is_(None),
            )
        )
        if sprint_id:
            due_soon_stmt = due_soon_stmt.where(Task.sprint_id == sprint_id)
        due_soon_res = await self.db.execute(due_soon_stmt)
        due_soon_tasks = due_soon_res.scalar() or 0

        logger.info(
            "Dashboard overview fetched successfully for project %s: total=%d, completed=%d, pending=%d, overdue=%d, due_soon=%d",
            project_id,
            total_tasks,
            completed_tasks,
            pending_tasks,
            overdue_tasks,
            due_soon_tasks,
        )

        return DashboardOverview(
            total_tasks=total_tasks,
            completed=completed_tasks,
            pending=pending_tasks,
            overdue=overdue_tasks,
            due_soon=due_soon_tasks,
        )

    async def get_task_status(
        self,
        project_id: str,
        user_id: str,
        sprint_id: Optional[str] = None,
    ) -> dict:
        """
        Fetches task counts grouped by custom status for a project/sprint.
        """
        logger.info(
            "Fetching task status summary for project %s (user: %s, sprint: %s)",
            project_id,
            user_id,
            sprint_id or "all",
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:view)
        can_view = await self._check_permission(user, project_id, "projects", "view")
        if not can_view:
            logger.warning(
                "User %s not authorized to view task status in project %s",
                user_id,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view task status in this project",
            )

        # 4. Fetch all custom statuses for the project to include zero values for unused statuses
        cs_stmt = (
            select(CustomStatus)
            .where(
                CustomStatus.project_id == project_id,
                CustomStatus.deleted_at.is_(None),
            )
            .order_by(CustomStatus.display_order.asc())
        )
        cs_res = await self.db.execute(cs_stmt)
        custom_statuses = cs_res.scalars().all()

        task_status = {}
        for cs in custom_statuses:
            status_name = "completed" if cs.is_final else cs.name
            task_status[status_name] = {
                "count": 0,
                "color": cs.color,
            }

        # 5. Fetch task counts grouped by status (using is_final for completed)
        status_expr = case(
            (CustomStatus.is_final.is_(True), "completed"),
            else_=Task.status,
        ).label("status")

        query = (
            select(
                status_expr,
                func.count(Task.id).label("count"),
            )
            .join(CustomStatus, CustomStatus.id == Task.status_id)
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                CustomStatus.deleted_at.is_(None),
            )
        )
        if sprint_id:
            query = query.where(Task.sprint_id == sprint_id)

        query = query.group_by(status_expr).order_by(status_expr)
        grouped_res = await self.db.execute(query)
        rows = grouped_res.all()

        # 6. Update task_status map with actual counts
        for row in rows:
            stat_name = row[0]
            count_val = row[1]
            if stat_name in task_status:
                task_status[stat_name]["count"] = count_val
            else:
                task_status[stat_name] = {
                    "count": count_val,
                    "color": "",
                }

        logger.info(
            "Task status fetched successfully for project %s (%d statuses)",
            project_id,
            len(task_status),
        )

        return task_status

    async def _calculate_sprint_burndown(
        self, project_id: str, sprint: Sprint
    ) -> List[SprintBurndownPoint]:
        if not sprint.start_date or not sprint.end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sprint start date and end date must be set",
            )

        start_d = sprint.start_date if isinstance(sprint.start_date, date) else sprint.start_date.date()
        end_d = sprint.end_date if isinstance(sprint.end_date, date) else sprint.end_date.date()

        if end_d < start_d:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sprint end date cannot be before start date",
            )

        # Fetch tasks in this sprint
        tasks_stmt = (
            select(Task)
            .where(
                Task.project_id == project_id,
                Task.sprint_id == sprint.id,
                Task.deleted_at.is_(None),
            )
        )
        tasks_res = await self.db.execute(tasks_stmt)
        tasks = tasks_res.scalars().all()

        total_estimated_hours = sum(t.estimated_hours or 0.0 for t in tasks)
        total_actual_hours = sum(t.actual_hours or 0.0 for t in tasks)

        total_estimated_hours = round(total_estimated_hours, 2)
        total_actual_hours = round(total_actual_hours, 2)

        total_days = (end_d - start_d).days + 1
        if total_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid sprint duration",
            )

        result: List[SprintBurndownPoint] = []
        for day in range(total_days):
            current_date = start_d + timedelta(days=day)
            if total_days == 1:
                ideal_hours = 0.0
            else:
                ideal_hours = total_estimated_hours - (total_estimated_hours / float(total_days - 1)) * float(day)

            if ideal_hours < 0:
                ideal_hours = 0.0

            ideal_hours = round(ideal_hours, 2)
            actual_hours = round(total_actual_hours, 2)

            result.append(
                SprintBurndownPoint(
                    day=day + 1,
                    date=current_date.strftime("%Y-%m-%d"),
                    ideal_hours=ideal_hours,
                    actual_hours=actual_hours,
                )
            )

        return result

    async def get_sprint_burndown(
        self,
        project_id: str,
        user_id: str,
        sprint_id: Optional[str] = None,
    ) -> DashboardSprintBurndownResponse:
        """
        Fetches sprint burndown chart data for a project dashboard.
        If sprint_id is specified, returns data for that single sprint.
        If omitted, returns burndown for all active sprints of the project.
        """
        logger.info(
            "Fetching sprint burndown for project %s (user: %s, sprint: %s)",
            project_id,
            user_id,
            sprint_id or "all active",
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:view)
        can_view = await self._check_permission(user, project_id, "projects", "view")
        if not can_view:
            logger.warning(
                "User %s not authorized to view sprint burndown in project %s",
                user_id,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view sprint burndown in this project",
            )

        response_sprints: List[SprintBurndownData] = []

        if sprint_id:
            sprint_stmt = (
                select(Sprint)
                .where(
                    Sprint.id == sprint_id,
                    Sprint.project_id == project_id,
                    Sprint.deleted_at.is_(None),
                )
            )
            sprint_res = await self.db.execute(sprint_stmt)
            sprint = sprint_res.scalar_one_or_none()
            if not sprint:
                logger.warning("Sprint %s not found in project %s", sprint_id, project_id)
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Sprint not found in this project",
                )

            burndown_points = await self._calculate_sprint_burndown(project_id, sprint)
            response_sprints.append(
                SprintBurndownData(
                    sprint_id=UUID(sprint.id),
                    sprint_name=sprint.name,
                    data=burndown_points,
                )
            )
            return DashboardSprintBurndownResponse(
                sprint_burndown=response_sprints[0] if response_sprints else None
            )
        else:
            active_sprints_stmt = (
                select(Sprint)
                .where(
                    Sprint.project_id == project_id,
                    Sprint.status == "active",
                    Sprint.deleted_at.is_(None),
                )
            )
            active_res = await self.db.execute(active_sprints_stmt)
            active_sprints = active_res.scalars().all()

            for sp in active_sprints:
                if not sp.start_date or not sp.end_date:
                    logger.warning("Skipping active sprint %s due to missing start or end date", sp.id)
                    continue
                start_d = sp.start_date if isinstance(sp.start_date, date) else sp.start_date.date()
                end_d = sp.end_date if isinstance(sp.end_date, date) else sp.end_date.date()
                if end_d < start_d:
                    logger.warning("Skipping active sprint %s because end date is before start date", sp.id)
                    continue

                try:
                    burndown_points = await self._calculate_sprint_burndown(project_id, sp)
                    response_sprints.append(
                        SprintBurndownData(
                            sprint_id=UUID(sp.id),
                            sprint_name=sp.name,
                            data=burndown_points,
                        )
                    )
                except Exception as err:
                    logger.error("Failed to calculate sprint burndown for active sprint %s: %s", sp.id, err)
                    continue

            return DashboardSprintBurndownResponse(
                sprint_burndown=response_sprints
            )

    async def get_weekly_progress(
        self,
        project_id: str,
        user_id: str,
        start_date: date,
        end_date: date,
    ) -> List[WeeklyProgress]:
        """
        Fetches daily weekly progress statistics (planned vs completed tasks)
        within the specified date range for a project.
        """
        logger.info(
            "Fetching weekly progress for project %s (user: %s, start_date: %s, end_date: %s)",
            project_id,
            user_id,
            start_date,
            end_date,
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:view)
        can_view = await self._check_permission(user, project_id, "projects", "view")
        if not can_view:
            logger.warning(
                "User %s not authorized to view weekly progress in project %s",
                user_id,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view weekly progress in this project",
            )

        # 4. Validate date range
        if start_date > end_date:
            logger.warning(
                "Invalid date range for project %s: start_date (%s) is after end_date (%s)",
                project_id,
                start_date,
                end_date,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Start date must be before end date",
            )

        # 5. Build datetime boundaries in UTC
        start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=timezone.utc)

        # 6. Fetch tasks in date range
        tasks_stmt = (
            select(Task)
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                (
                    ((Task.due_date >= start_dt) & (Task.due_date < end_dt))
                    | ((Task.created_at >= start_dt) & (Task.created_at < end_dt))
                ),
            )
        )
        tasks_res = await self.db.execute(tasks_stmt)
        tasks = tasks_res.scalars().all()

        # 7. Aggregate planned and completed counts by date
        planned: Dict[str, int] = {}
        completed: Dict[str, int] = {}

        for task in tasks:
            if task.due_date is not None:
                d_str = task.due_date.strftime("%Y-%m-%d")
                planned[d_str] = planned.get(d_str, 0) + 1
                if task.status == "completed":
                    completed[d_str] = completed.get(d_str, 0) + 1

        # 8. Construct day-by-day response list
        result: List[WeeklyProgress] = []
        curr_date = start_date
        while curr_date < end_date:
            d_str = curr_date.strftime("%Y-%m-%d")
            result.append(
                WeeklyProgress(
                    day=curr_date.strftime("%a"),
                    planned=planned.get(d_str, 0),
                    completed=completed.get(d_str, 0),
                )
            )
            curr_date += timedelta(days=1)

        logger.info(
            "Weekly progress fetched successfully for project %s (%d days)",
            project_id,
            len(result),
        )

        return result

    async def get_team_workload(
        self,
        project_id: str,
        user_id: str,
        sprint_id: Optional[str] = None,
    ) -> List[TeamWorkload]:
        """
        Fetches workload statistics (assigned tasks count and total story points)
        for all team members assigned to tasks in a project/sprint.
        """
        logger.info(
            "Fetching team workload for project %s (user: %s, sprint: %s)",
            project_id,
            user_id,
            sprint_id or "all",
        )

        # 1. Fetch user
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        user_res = await self.db.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if not user:
            logger.error("User not found: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.role and user.role.name == "super_admin":
            logger.error("Super admins are not allowed to perform organization-level activities")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        # 2. Fetch project
        proj_stmt = (
            select(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
        )
        proj_res = await self.db.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            logger.error("Project %s not found or is deleted", project_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 3. Check permission (requires projects:view)
        can_view = await self._check_permission(user, project_id, "projects", "view")
        if not can_view:
            logger.warning(
                "User %s not authorized to view team workload in project %s",
                user_id,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view team workload in this project",
            )

        # 4. Build query joining tasks with users on assignee_id
        stmt = (
            select(
                User.id.label("user_id"),
                User.username.label("user_name"),
                func.coalesce(User.full_name, "").label("full_name"),
                func.coalesce(User.avatar_url, "").label("avatar_url"),
                func.coalesce(User.color, "").label("color"),
                func.count(Task.id).label("task_count"),
                func.coalesce(func.sum(Task.story_points), 0).label("points"),
            )
            .select_from(Task)
            .join(User, User.id == Task.assignee_id)
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                User.deleted_at.is_(None),
            )
        )
        if sprint_id:
            stmt = stmt.where(Task.sprint_id == sprint_id)

        stmt = (
            stmt.group_by(
                User.id,
                User.username,
                User.full_name,
                User.avatar_url,
                User.color,
            )
            .order_by(User.username.asc())
        )

        res = await self.db.execute(stmt)
        rows = res.all()

        result: List[TeamWorkload] = []
        for row in rows:
            result.append(
                TeamWorkload(
                    user_id=UUID(row.user_id),
                    user_name=row.user_name,
                    full_name=row.full_name,
                    avatar_url=row.avatar_url,
                    color=row.color,
                    task_count=row.task_count,
                    points=float(row.points),
                )
            )

        logger.info(
            "Team workload fetched successfully for project %s (%d members)",
            project_id,
            len(result),
        )

        return result

    async def get_dashboard_data(
        self,
        project_id: str,
        user_id: str,
        sprint_id: Optional[str] = None,
    ) -> DashboardResponse:
        """
        Fetches comprehensive aggregated dashboard data (overview, task status, team workload, sprint burndown)
        for a project and optional sprint.
        """
        logger.info(
            "Fetching composite dashboard data for project %s (user: %s, sprint: %s)",
            project_id,
            user_id,
            sprint_id or "all",
        )

        # 1. Fetch overview (handles user auth, project validation, and permissions check)
        overview = await self.get_overview(project_id, user_id, sprint_id)

        # 2. Fetch task status counts
        task_status = await self.get_task_status(project_id, user_id, sprint_id)

        # 3. Fetch team workload
        team_workload = await self.get_team_workload(project_id, user_id, sprint_id)

        # 4. Fetch sprint burndown
        burndown_response = await self.get_sprint_burndown(project_id, user_id, sprint_id)

        logger.info(
            "Composite dashboard data fetched successfully for project %s",
            project_id,
        )

        return DashboardResponse(
            overview=overview,
            task_status=task_status,
            team_workload=team_workload,
            sprint_burndown=burndown_response.sprint_burndown,
        )


