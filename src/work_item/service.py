import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.models import User
from src.config import get_logger
from src.custom_status.models import CustomStatus
from src.favorite.models import Favorite
from src.organization.models import Role
from src.project.models import Project, ProjectMember
from src.task.models import (
    DEFAULT_STATUS_COLORS,
    DEFAULT_STATUS_IS_FINAL,
    Task,
    normalize_task_status,
)
from src.task.service import TaskService
from src.user_story.models import UserStory
from src.user_story.service import UserStoryService
from src.work_item.schema import WorkItemResponse

logger = get_logger(__name__)


class WorkItemServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val).strip().strip('"').strip("'"))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class WorkItemService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_project(self, project_id_or_slug: str) -> Project:
        if _is_valid_uuid(project_id_or_slug):
            query = select(Project).where(
                Project.id == project_id_or_slug,
                Project.deleted_at.is_(None)
            )
        else:
            query = select(Project).where(
                Project.slug == project_id_or_slug,
                Project.deleted_at.is_(None)
            )

        project = (await self.db.execute(query)).scalar_one_or_none()
        if not project:
            logger.warning("Project not found for project_id_or_slug=%s", project_id_or_slug)
            raise WorkItemServiceError(404, "NOT_FOUND", "Project not found")

        return project

    async def _check_authorization(self, project: Project, user_id: str) -> User:
        user_query = select(User).where(
            User.id == user_id,
            User.deleted_at.is_(None)
        ).options(
            selectinload(User.role).selectinload(Role.permissions)
        )

        user = (await self.db.execute(user_query)).scalar_one_or_none()
        if not user:
            logger.warning("User not found for user_id=%s", user_id)
            raise WorkItemServiceError(404, "NOT_FOUND", "User not found")

        role_name = getattr(user.role, "name", "") or ""
        if role_name.lower() in ("superadmin", "super_admin"):
            logger.warning("Super admin user_id=%s blocked from project activity", user_id)
            raise WorkItemServiceError(
                403,
                "FORBIDDEN",
                "Super admins are not allowed to perform organization-level activities"
            )

        member = (await self.db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == str(project.id),
                ProjectMember.user_id == str(user.id),
                ProjectMember.deleted_at.is_(None)
            )
        )).scalars().first()

        if member is not None or role_name.lower() == "org_admin":
            return user

        logger.warning("User user_id=%s forbidden from accessing project_id=%s", user_id, project.id)
        raise WorkItemServiceError(403, "FORBIDDEN", "You do not have access to this project")

    async def get_work_item_by_serial_number(
        self,
        project_id_or_slug: str,
        serial_id: int,
        user_id: str
    ) -> WorkItemResponse:
        logger.info(
            "Fetching work item for project_id_or_slug=%s, serial_id=%d, user_id=%s",
            project_id_or_slug,
            serial_id,
            user_id
        )

        project = await self._get_project(project_id_or_slug)
        await self._check_authorization(project, user_id)

        task_query = select(Task).where(
            Task.project_id == str(project.id),
            Task.serial_number == serial_id,
            Task.deleted_at.is_(None)
        ).options(
            selectinload(Task.project),
            selectinload(Task.sprint),
            selectinload(Task.user_story),
            selectinload(Task.assignee).selectinload(User.role),
            selectinload(Task.reporter).selectinload(User.role),
            selectinload(Task.labels)
        )

        task = (await self.db.execute(task_query)).scalar_one_or_none()

        if task is not None:
            if str(task.project_id) != str(project.id):
                logger.warning("Task serial_id=%d does not belong to project_id=%s", serial_id, project.id)
                raise WorkItemServiceError(404, "NOT_FOUND", "Work item not found in this project")

            statuses_query = select(CustomStatus).where(
                CustomStatus.project_id == str(project.id),
                CustomStatus.deleted_at.is_(None)
            ).order_by(CustomStatus.display_order.asc())
            statuses = list((await self.db.execute(statuses_query)).scalars())

            colors = dict(DEFAULT_STATUS_COLORS)
            finals = dict(DEFAULT_STATUS_IS_FINAL)
            for st in statuses:
                key = normalize_task_status(st.name)
                colors[key] = st.color
                finals[key] = bool(st.is_final)

            fav_query = select(Favorite).where(
                Favorite.user_id == str(user_id),
                Favorite.item_type == "task",
                Favorite.task_id == str(task.id),
                Favorite.deleted_at.is_(None)
            )
            fav = (await self.db.execute(fav_query)).scalar_one_or_none()
            is_fav = fav is not None

            task_service = TaskService(self.db)
            task_resp = task_service._build_response(task, colors, finals, is_favourite=is_fav)

            assignee_name = task.assignee.full_name if task.assignee else ""
            reporter_name = task.reporter.full_name if task.reporter else ""
            sprint_name = task.sprint.name if task.sprint else ""

            work_item_resp = WorkItemResponse(
                work_item_type="task",
                id=str(task.id),
                project_id=str(task.project_id),
                serial_number=int(task.serial_number),
                formatted_serial_number=task.formatted_serial_number,
                title=task.title,
                description=task.description or "",
                priority=task.priority,
                status_id=str(task.status_id),
                status=task.status or "",
                status_color=task_resp.status_color,
                is_favourite=is_fav,
                story_points=int(task.story_points or 0),
                sprint_id=str(task.sprint_id) if task.sprint_id else None,
                sprint_name=sprint_name,
                assignee_id=str(task.assignee_id) if task.assignee_id else None,
                assignee_name=assignee_name,
                reporter_id=str(task.reporter_id) if task.reporter_id else None,
                reporter_name=reporter_name,
                created_at=task.created_at,
                updated_at=task.updated_at,
                task_details=task_resp,
                user_story_details=None
            )

            logger.info("Successfully retrieved task work item ID=%s for serial_id=%d", task.id, serial_id)
            return work_item_resp

        story_query = select(UserStory).where(
            UserStory.project_id == str(project.id),
            UserStory.serial_number == serial_id,
            UserStory.deleted_at.is_(None)
        ).options(
            selectinload(UserStory.project),
            selectinload(UserStory.sprint),
            selectinload(UserStory.assignee).selectinload(User.role),
            selectinload(UserStory.reporter).selectinload(User.role),
            selectinload(UserStory.status)
        )

        story = (await self.db.execute(story_query)).scalar_one_or_none()

        if story is not None:
            if str(story.project_id) != str(project.id):
                logger.warning("User story serial_id=%d does not belong to project_id=%s", serial_id, project.id)
                raise WorkItemServiceError(404, "NOT_FOUND", "Work item not found in this project")

            user_story_service = UserStoryService(self.db)
            story_resp = await user_story_service._build_single_story(story, user_id, str(project.id))

            assignee_name = story.assignee.full_name if story.assignee else ""
            reporter_name = story.reporter.full_name if story.reporter else ""
            sprint_name = story.sprint.name if story.sprint else ""

            work_item_resp = WorkItemResponse(
                work_item_type="user_story",
                id=str(story.id),
                project_id=str(story.project_id),
                serial_number=int(story.serial_number),
                formatted_serial_number=story.formatted_serial_number,
                title=story.title,
                description=story.description or "",
                priority=story.priority,
                status_id=str(story_resp.status_id) if story_resp.status_id else str(story.status_id),
                status=story_resp.status if story_resp and story_resp.status else (story.status.name if (story.status and hasattr(story.status, "name")) else ""),
                status_color=story_resp.status_color,
                is_favourite=story_resp.is_favourite,
                story_points=int(story.story_points or 0),
                sprint_id=str(story.sprint_id) if story.sprint_id else None,
                sprint_name=sprint_name,
                assignee_id=str(story.assignee_id) if story.assignee_id else None,
                assignee_name=assignee_name,
                reporter_id=str(story.reporter_id) if story.reporter_id else None,
                reporter_name=reporter_name,
                created_at=story.created_at,
                updated_at=story.updated_at,
                task_details=None,
                user_story_details=story_resp
            )

            logger.info("Successfully retrieved user story work item ID=%s for serial_id=%d", story.id, serial_id)
            return work_item_resp

        logger.warning("Work item not found for project_id_or_slug=%s, serial_id=%d", project_id_or_slug, serial_id)
        raise WorkItemServiceError(404, "NOT_FOUND", "Work item not found")
