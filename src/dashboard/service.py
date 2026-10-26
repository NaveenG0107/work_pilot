# src/dashboard/service.py
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import and_, case, cast, Date, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.auth.models import User
from src.custom_status.models import CustomStatus
from src.dashboard.schemas import (
    DashboardOverview,
    DashboardResponse,
    DashboardSprintBurndownResponse,
    RollingProgressData,
    RollingProgressPoint,
    RollingProgressSummary,
    SprintBurndownData,
    SprintBurndownPoint,
    TeamWorkload,
    UpcomingDeadline,
    WeeklyProgress,
)
from src.middleware.rbac import has_default_permission
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.sprint.models import Sprint, SprintSnapshot
from src.task.models import Task
from src.utils.performance_cache import cache_get_json, cache_set_json, project_version

logger = logging.getLogger(__name__)


class DashboardService:
    def __init__(self, db: AsyncSession, redis=None):
        self.db = db
        self.redis = redis

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
                ProjectMember.deleted_at.is_(None),
            )
            .options(joinedload(ProjectMember.role).selectinload(Role.permissions))
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

    async def get_upcoming_deadlines(
        self,
        user_id: str,
        organization_id: str,
        *,
        project_id: str,
        sprint_id: Optional[str] = None,
        limit: int = 7,
    ) -> list[UpcomingDeadline]:
        """Return the exact open tasks counted by dashboard ``due_soon``."""
        user = (
            await self.db.execute(
                select(User)
                .where(User.id == user_id, User.deleted_at.is_(None))
                .options(joinedload(User.role).selectinload(Role.permissions))
            )
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        if user.role and user.role.name == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins are not allowed to perform organization-level activities",
            )

        project = (
            await self.db.execute(
                select(Project).where(
                    Project.id == project_id,
                    Project.organization_id == organization_id,
                    Project.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        if not await self._check_permission(user, project_id, "projects", "view"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view dashboard in this project",
            )

        now = datetime.now(timezone.utc)
        forty_eight_hours_later = now + timedelta(hours=48)
        conditions = [
            Task.project_id == project_id,
            Task.deleted_at.is_(None),
            Task.due_date >= now,
            Task.due_date <= forty_eight_hours_later,
            CustomStatus.is_final.is_(False),
            CustomStatus.deleted_at.is_(None),
        ]
        if sprint_id:
            conditions.append(Task.sprint_id == sprint_id)

        rows = (
            await self.db.execute(
                select(Task, Project, Sprint)
                .join(Project, Project.id == Task.project_id)
                .outerjoin(Sprint, and_(
                    Sprint.id == Task.sprint_id,
                    Sprint.deleted_at.is_(None),
                ))
                .join(CustomStatus, CustomStatus.id == Task.status_id)
                .where(*conditions)
                .order_by(Task.due_date.asc(), Task.created_at.asc())
                .limit(limit)
            )
        ).all()

        deadlines: list[UpcomingDeadline] = []
        for task, project, sprint in rows:
            due_date = task.due_date
            if due_date.date() == now.date():
                deadline_status = "due_today"
            else:
                deadline_status = "upcoming"
            deadlines.append(
                UpcomingDeadline(
                    id=task.id,
                    task_id=task.id,
                    project_id=project.id,
                    project_name=project.name,
                    sprint_id=sprint.id if sprint else None,
                    sprint_name=sprint.name if sprint else None,
                    key=task.key,
                    task_key=task.key,
                    title=task.title,
                    priority=task.priority,
                    due_date=due_date,
                    deadline_status=deadline_status,
                )
            )
        return deadlines

    async def _validate_user_project_and_permission(
        self,
        user_id: str,
        project_id: str,
        resource: str = "projects",
        action: str = "view",
        denied_detail: str = "You do not have permission to view dashboard in this project",
    ) -> tuple[User, Project]:
        user_stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(joinedload(User.role).selectinload(Role.permissions))
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

        if str(project.organization_id) != str(user.organization_id):
            logger.warning(
                "Tenant isolation violation: user %s (org: %s) attempted to access project %s (org: %s)",
                user_id,
                user.organization_id,
                project_id,
                project.organization_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this project",
            )

        can_view = await self._check_permission(user, project_id, resource, action)
        if not can_view:
            logger.warning(
                "User %s not authorized for %s:%s in project %s",
                user_id,
                resource,
                action,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=denied_detail,
            )

        return user, project

    async def _get_overview_data(
        self,
        project_id: str,
        sprint_id: Optional[str] = None,
    ) -> DashboardOverview:
        now = datetime.now(timezone.utc)
        forty_eight_hours_later = now + timedelta(hours=48)

        metrics_stmt = (
            select(
                func.count(Task.id),
                func.count(Task.id).filter(CustomStatus.is_final.is_(True)),
                func.count(Task.id).filter(CustomStatus.is_final.is_(False)),
                func.count(Task.id).filter(
                    CustomStatus.is_final.is_(False), Task.due_date < now,
                ),
                func.count(Task.id).filter(
                    CustomStatus.is_final.is_(False),
                    Task.due_date >= now,
                    Task.due_date <= forty_eight_hours_later,
                ),
            )
            .outerjoin(CustomStatus, and_(
                CustomStatus.id == Task.status_id,
                CustomStatus.deleted_at.is_(None),
            ))
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
            )
        )
        if sprint_id:
            metrics_stmt = metrics_stmt.where(Task.sprint_id == sprint_id)
        total_tasks, completed_tasks, pending_tasks, overdue_tasks, due_soon_tasks = (
            await self.db.execute(metrics_stmt)
        ).one()

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
        await self._validate_user_project_and_permission(
            user_id, project_id, "projects", "view",
            "You do not have permission to view dashboard in this project"
        )
        return await self._get_overview_data(project_id, sprint_id)

    async def _get_task_status_data(
        self,
        project_id: str,
        sprint_id: Optional[str] = None,
    ) -> dict:
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

        total_tasks = sum(
            item["count"] for item in task_status.values() if isinstance(item, dict)
        )
        for item in task_status.values():
            if isinstance(item, dict):
                count_val = item.get("count", 0)
                item["percentage"] = (
                    round((count_val / total_tasks * 100), 2)
                    if total_tasks > 0
                    else 0.0
                )

        logger.info(
            "Task status fetched successfully for project %s (%d statuses, total=%d)",
            project_id,
            len(task_status),
            total_tasks,
        )

        return task_status

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
        await self._validate_user_project_and_permission(
            user_id, project_id, "projects", "view",
            "You do not have permission to view task status in this project"
        )
        return await self._get_task_status_data(project_id, sprint_id)

    @staticmethod
    def _generate_burndown_points(
        start_d: date,
        end_d: date,
        total_estimated_hours: float = 0.0,
        total_actual_hours: float = 0.0,
        tasks: Optional[List[Any]] = None,
        snapshots: Optional[Dict[str, float]] = None,
    ) -> List[SprintBurndownPoint]:
        if isinstance(start_d, datetime) or hasattr(start_d, "date"):
            start_d = start_d.date()
        if isinstance(end_d, datetime) or hasattr(end_d, "date"):
            end_d = end_d.date()

        total_days = (end_d - start_d).days + 1
        if total_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid sprint duration",
            )

        task_data = []
        if tasks:
            for t in tasks:
                est = getattr(t, "estimated_hours", None)
                sp = getattr(t, "story_points", None)
                act = getattr(t, "actual_hours", None)
                if est is not None and float(est) > 0:
                    weight = float(est)
                elif sp is not None and float(sp) > 0:
                    weight = float(sp)
                elif act is not None and float(act) > 0:
                    weight = float(act)
                else:
                    weight = 0.0

                is_final = bool(getattr(t, "is_final", False))
                updated_at = getattr(t, "updated_at", None)
                if updated_at:
                    comp_d = updated_at.date() if isinstance(updated_at, datetime) or hasattr(updated_at, "date") else updated_at
                else:
                    comp_d = start_d

                if isinstance(comp_d, datetime) or hasattr(comp_d, "date"):
                    comp_d = comp_d.date()

                task_data.append((weight, is_final, comp_d))

        total_weight = sum(w for w, _, _ in task_data) if task_data else float(total_estimated_hours or 0.0)
        if total_weight == 0.0 and task_data:
            # Fallback to counting tasks if estimates were not set
            task_data = [(1.0, is_f, c_d) for _, is_f, c_d in task_data]
            total_weight = float(len(task_data))

        total_weight = round(total_weight, 2)
        today = datetime.now(timezone.utc).date()

        result: List[SprintBurndownPoint] = []
        for day_idx in range(total_days):
            current_date = start_d + timedelta(days=day_idx)
            date_str = current_date.strftime("%Y-%m-%d")

            if total_days == 1:
                ideal_hours = 0.0
            else:
                ideal_hours = total_weight - (total_weight / float(total_days - 1)) * float(day_idx)
            ideal_hours = max(0.0, round(ideal_hours, 2))

            actual_hours: Optional[float] = None
            if current_date <= today:
                if snapshots and date_str in snapshots:
                    actual_hours = round(float(snapshots[date_str]), 2)
                elif task_data:
                    completed_so_far = sum(
                        w for w, is_final, c_d in task_data
                        if is_final and c_d <= current_date
                    )
                    actual_hours = max(0.0, round(total_weight - completed_so_far, 2))
                else:
                    actual_hours = round(float(total_actual_hours or 0.0), 2)

            result.append(
                SprintBurndownPoint(
                    day=day_idx + 1,
                    date=date_str,
                    ideal_hours=ideal_hours,
                    actual_hours=actual_hours,
                )
            )

        return result

    async def _calculate_sprint_burndown(
        self, project_id: str, sprint: Sprint
    ) -> List[SprintBurndownPoint]:
        if not sprint.start_date or not sprint.end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sprint start date and end date must be set",
            )

        start_d = sprint.start_date.date() if isinstance(sprint.start_date, datetime) or hasattr(sprint.start_date, "date") else sprint.start_date
        end_d = sprint.end_date.date() if isinstance(sprint.end_date, datetime) or hasattr(sprint.end_date, "date") else sprint.end_date

        if end_d < start_d:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sprint end date cannot be before start date",
            )

        tasks_stmt = (
            select(
                Task.id,
                Task.estimated_hours,
                Task.actual_hours,
                Task.story_points,
                Task.updated_at,
                CustomStatus.is_final,
            )
            .outerjoin(CustomStatus, CustomStatus.id == Task.status_id)
            .where(
                Task.project_id == project_id,
                Task.sprint_id == sprint.id,
                Task.deleted_at.is_(None),
            )
        )
        task_rows = (await self.db.execute(tasks_stmt)).all()

        snap_stmt = (
            select(SprintSnapshot.date, SprintSnapshot.remaining_story_points)
            .where(SprintSnapshot.sprint_id == sprint.id)
        )
        snap_rows = (await self.db.execute(snap_stmt)).all()
        snapshots = {
            row.date.strftime("%Y-%m-%d"): float(row.remaining_story_points)
            for row in snap_rows if row.date
        } if snap_rows else None

        return self._generate_burndown_points(
            start_d=start_d,
            end_d=end_d,
            tasks=task_rows,
            snapshots=snapshots,
        )

    async def _get_sprint_burndown_data(
        self,
        project_id: str,
        sprint_id: Optional[str] = None,
    ) -> DashboardSprintBurndownResponse:
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

            valid_sprints = []
            for sp in active_sprints:
                if not sp.start_date or not sp.end_date:
                    logger.warning("Skipping active sprint %s due to missing start or end date", sp.id)
                    continue
                start_d = sp.start_date.date() if isinstance(sp.start_date, datetime) or hasattr(sp.start_date, "date") else sp.start_date
                end_d = sp.end_date.date() if isinstance(sp.end_date, datetime) or hasattr(sp.end_date, "date") else sp.end_date
                if end_d < start_d:
                    logger.warning("Skipping active sprint %s because end date is before start date", sp.id)
                    continue
                valid_sprints.append((sp, start_d, end_d))

            if not valid_sprints:
                return DashboardSprintBurndownResponse(sprint_burndown=[])

            # Batch query tasks for all active sprints in ONE query
            sp_ids = [sp.id for sp, _, _ in valid_sprints]
            tasks_stmt = (
                select(
                    Task.sprint_id,
                    Task.id,
                    Task.estimated_hours,
                    Task.actual_hours,
                    Task.story_points,
                    Task.updated_at,
                    CustomStatus.is_final,
                )
                .outerjoin(CustomStatus, CustomStatus.id == Task.status_id)
                .where(
                    Task.project_id == project_id,
                    Task.sprint_id.in_(sp_ids),
                    Task.deleted_at.is_(None),
                )
            )
            task_rows = (await self.db.execute(tasks_stmt)).all()
            tasks_by_sprint: Dict[str, List[Any]] = {}
            for row in task_rows:
                tasks_by_sprint.setdefault(str(row.sprint_id), []).append(row)

            # Batch query snapshots for all active sprints in ONE query
            snap_stmt = (
                select(SprintSnapshot.sprint_id, SprintSnapshot.date, SprintSnapshot.remaining_story_points)
                .where(SprintSnapshot.sprint_id.in_(sp_ids))
            )
            snap_rows = (await self.db.execute(snap_stmt)).all()
            snaps_by_sprint: Dict[str, Dict[str, float]] = {}
            for s_row in snap_rows:
                if s_row.date:
                    d_str = s_row.date.strftime("%Y-%m-%d")
                    snaps_by_sprint.setdefault(str(s_row.sprint_id), {})[d_str] = float(s_row.remaining_story_points)

            for sp, start_d, end_d in valid_sprints:
                s_tasks = tasks_by_sprint.get(str(sp.id), [])
                s_snaps = snaps_by_sprint.get(str(sp.id))
                try:
                    burndown_points = self._generate_burndown_points(
                        start_d=start_d,
                        end_d=end_d,
                        tasks=s_tasks,
                        snapshots=s_snaps,
                    )
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
        await self._validate_user_project_and_permission(
            user_id, project_id, "projects", "view",
            "You do not have permission to view sprint burndown in this project"
        )
        return await self._get_sprint_burndown_data(project_id, sprint_id)

    async def get_weekly_progress(
        self,
        project_id: str,
        user_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        sprint_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
    ) -> RollingProgressData:
        """
        Fetches daily rolling progress statistics (planned vs completed tasks)
        and summary metrics for a project within a rolling seven-day window.
        """
        start_time = time.perf_counter()
        logger.info(
            "Fetching rolling progress for project %s (user: %s, sprint: %s, assignee: %s, start_date: %s, end_date: %s)",
            project_id,
            user_id,
            sprint_id,
            assignee_id,
            start_date,
            end_date,
        )

        # 1. Validate user, tenant isolation, and project view permissions
        await self._validate_user_project_and_permission(
            user_id,
            project_id,
            "projects",
            "view",
            "You do not have permission to view weekly progress in this project",
        )

        # 2. Resolve application configured timezone
        tz_str = os.getenv("TIMEZONE") or os.getenv("APP_TIMEZONE") or "UTC"
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = timezone.utc
            tz_str = "UTC"

        # 3. Determine reporting dates (defaults to rolling 7 calendar days: today - 6 to today)
        today = datetime.now(tz).date()
        if start_date is None and end_date is None:
            end_date = today
            start_date = today - timedelta(days=6)
        elif start_date is None and end_date is not None:
            start_date = end_date - timedelta(days=6)
        elif start_date is not None and end_date is None:
            end_date = start_date + timedelta(days=6)
        else:
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

        # 4. Build timezone-aware datetime boundaries
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=tz)
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=tz)

        # 5. Database dialect expression for date grouping
        dialect = self.db.bind.dialect.name if self.db.bind else "postgresql"
        if dialect == "postgresql":
            planned_date_expr = cast(func.timezone(literal(tz_str), Task.due_date), Date)
            completed_date_expr = cast(func.timezone(literal(tz_str), Task.updated_at), Date)
        else:
            planned_date_expr = func.date(Task.due_date)
            completed_date_expr = func.date(Task.updated_at)

        # 6. Grouped query for planned tasks (by due_date)
        planned_stmt = (
            select(
                planned_date_expr.label("d"),
                func.count(Task.id).label("cnt"),
            )
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                Task.due_date >= start_dt,
                Task.due_date < end_dt,
            )
        )
        if sprint_id:
            planned_stmt = planned_stmt.where(Task.sprint_id == sprint_id)
        if assignee_id:
            planned_stmt = planned_stmt.where(Task.assignee_id == assignee_id)
        planned_stmt = planned_stmt.group_by(planned_date_expr)

        # 7. Grouped query for completed tasks (terminal status by updated_at)
        is_terminal_cond = or_(
            CustomStatus.is_final.is_(True),
            func.lower(Task.status).in_(["completed", "done", "closed"]),
        )
        completed_stmt = (
            select(
                completed_date_expr.label("d"),
                func.count(Task.id).label("cnt"),
            )
            .outerjoin(
                CustomStatus,
                and_(
                    CustomStatus.id == Task.status_id,
                    CustomStatus.deleted_at.is_(None),
                ),
            )
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                is_terminal_cond,
                Task.updated_at >= start_dt,
                Task.updated_at < end_dt,
            )
        )
        if sprint_id:
            completed_stmt = completed_stmt.where(Task.sprint_id == sprint_id)
        if assignee_id:
            completed_stmt = completed_stmt.where(Task.assignee_id == assignee_id)
        completed_stmt = completed_stmt.group_by(completed_date_expr)

        # 8. Execute queries and record query duration
        q_start = time.perf_counter()
        planned_res = await self.db.execute(planned_stmt)
        completed_res = await self.db.execute(completed_stmt)
        planned_rows = planned_res.all()
        completed_rows = completed_res.all()
        q_duration_ms = (time.perf_counter() - q_start) * 1000

        # 9. Map counts to date strings (YYYY-MM-DD)
        planned_map: Dict[str, int] = {}
        for d_val, cnt in planned_rows:
            d_key = d_val.strftime("%Y-%m-%d") if isinstance(d_val, (date, datetime)) else str(d_val)
            planned_map[d_key] = cnt

        completed_map: Dict[str, int] = {}
        for d_val, cnt in completed_rows:
            d_key = d_val.strftime("%Y-%m-%d") if isinstance(d_val, (date, datetime)) else str(d_val)
            completed_map[d_key] = cnt

        # 10. Construct daily series in chronological order (including zero-count days)
        series: List[RollingProgressPoint] = []
        curr = start_date
        while curr <= end_date:
            d_str = curr.strftime("%Y-%m-%d")
            day_name = curr.strftime("%a")
            series.append(
                RollingProgressPoint(
                    date=d_str,
                    day=day_name,
                    planned=planned_map.get(d_str, 0),
                    completed=completed_map.get(d_str, 0),
                )
            )
            curr += timedelta(days=1)

        # 11. Compute summary metrics
        total_planned = sum(item.planned for item in series)
        total_completed = sum(item.completed for item in series)
        remaining = max(0, total_planned - total_completed)
        completion_rate = (
            round((total_completed / total_planned) * 100, 2)
            if total_planned > 0
            else 0.0
        )

        summary = RollingProgressSummary(
            total_planned=total_planned,
            total_completed=total_completed,
            remaining=remaining,
            completion_rate=completion_rate,
        )

        total_duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Rolling progress fetched successfully for project %s (start: %s, end: %s, planned: %d, completed: %d, query_ms: %.2f, total_ms: %.2f)",
            project_id,
            start_date,
            end_date,
            total_planned,
            total_completed,
            q_duration_ms,
            total_duration_ms,
        )

        return RollingProgressData(
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            timezone=tz_str,
            series=series,
            summary=summary,
        )

    async def _get_team_workload_data(
        self,
        project_id: str,
        sprint_id: Optional[str] = None,
    ) -> List[TeamWorkload]:
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
        await self._validate_user_project_and_permission(
            user_id, project_id, "projects", "view",
            "You do not have permission to view team workload in this project"
        )
        return await self._get_team_workload_data(project_id, sprint_id)

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

        # 1. Fetch overview (handles user auth, project validation, and permissions check once)
        await self._validate_user_project_and_permission(
            user_id, project_id, "projects", "view",
            "You do not have permission to view dashboard in this project"
        )

        overview = await self._get_overview_data(project_id, sprint_id)

        version = await project_version(self.redis, project_id)
        cache_key = (
            f"wp:dashboard:v{version}:{project_id}:{user_id}:{sprint_id or 'all'}"
            if version is not None else None
        )
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            result = DashboardResponse.model_validate(cached)
            # Overview was deliberately re-read to preserve current authorization
            # behavior and time-dependent overdue/due-soon values.
            result.overview = overview
            return result

        # 2. Fetch task status counts
        task_status = await self._get_task_status_data(project_id, sprint_id)

        # 3. Fetch team workload
        team_workload = await self._get_team_workload_data(project_id, sprint_id)

        # 4. Fetch sprint burndown
        burndown_response = await self._get_sprint_burndown_data(project_id, sprint_id)

        logger.info(
            "Composite dashboard data fetched successfully for project %s",
            project_id,
        )

        result = DashboardResponse(
            overview=overview,
            task_status=task_status,
            team_workload=team_workload,
            sprint_burndown=burndown_response.sprint_burndown,
        )
        await cache_set_json(self.redis, cache_key, result.model_dump(mode="json"))
        return result


