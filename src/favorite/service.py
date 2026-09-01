from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from src.database import AsyncSession
from src.favorite.models import Favorite
from src.favorite.schema import (
    FAVORITE_SORT_FIELDS,
    FavoriteListResponse,
    FavoriteResponse,
    RemoveFavoriteResponse,
)
from src.task.models import (
    DEFAULT_STATUS_COLORS,
    DEFAULT_STATUS_IS_FINAL,
    Task,
    normalize_task_status,
)
from src.user_story.models import UserStory


class FavoriteServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class FavoriteService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _task_or_404(self, item_id: str) -> Task:
        task = (
            await self.db.execute(
                select(Task)
                .where(Task.id == item_id, Task.deleted_at.is_(None))
                .options(
                    selectinload(Task.project),
                    selectinload(Task.sprint),
                    selectinload(Task.user_story),
                    selectinload(Task.reporter),
                    selectinload(Task.assignee),
                    selectinload(Task.labels),
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise FavoriteServiceError(404, "RESOURCE_NOT_FOUND", "Task not found")
        return task

    async def _story_or_404(self, item_id: str) -> UserStory:
        story = (
            await self.db.execute(
                select(UserStory)
                .where(UserStory.id == item_id, UserStory.deleted_at.is_(None))
                .options(
                    selectinload(UserStory.project),
                    selectinload(UserStory.sprint),
                    selectinload(UserStory.status),
                    selectinload(UserStory.reporter),
                    selectinload(UserStory.assignee),
                )
            )
        ).scalar_one_or_none()
        if story is None:
            raise FavoriteServiceError(
                404, "RESOURCE_NOT_FOUND", "User story not found"
            )
        return story

    async def _favorite_or_404(
        self,
        user_id: str,
        item_type: str,
        item_id: str,
    ) -> Favorite:
        conditions = [Favorite.user_id == user_id, Favorite.deleted_at.is_(None)]
        if item_type == "task":
            conditions.append(Favorite.item_type == "task")
            conditions.append(Favorite.task_id == item_id)
        elif item_type == "user_story":
            conditions.append(Favorite.item_type == "user_story")
            conditions.append(Favorite.user_story_id == item_id)
        else:
            raise FavoriteServiceError(
                400, "VALIDATION_ERROR", "Invalid item type"
            )
        favorite = (
            await self.db.execute(
                select(Favorite)
                .where(*conditions)
                .options(
                    *self._favorite_load_options(),
                )
            )
        ).scalar_one_or_none()
        if favorite is None:
            raise FavoriteServiceError(
                404, "RESOURCE_NOT_FOUND", "Favorite record not found"
            )
        return favorite

    async def list_favorites(
        self,
        user_id: str,
        *,
        item_type: str | None = None,
        search: str = "",
        sort_by: str = "created_at",
        sort_order: str = "DESC",
        page: int = 1,
        page_size: int = 10,
    ) -> FavoriteListResponse:
        page = page if page >= 1 else 1
        page_size = page_size if page_size >= 1 else 10
        sort_by = sort_by.strip() or "created_at"
        if sort_by not in FAVORITE_SORT_FIELDS:
            sort_by = "created_at"
        order_value = sort_order.strip().upper()
        sort_order = order_value if order_value in {"ASC", "DESC"} else "DESC"

        conditions = [Favorite.user_id == user_id, Favorite.deleted_at.is_(None)]
        if item_type:
            if item_type not in {"user_story", "task"}:
                raise FavoriteServiceError(
                    400, "VALIDATION_ERROR", "Invalid item type"
                )
            conditions.append(Favorite.item_type == item_type)

        query = (
            select(Favorite)
            .where(*conditions)
            .options(*self._favorite_load_options())
        )
        favorites = list((await self.db.execute(query)).scalars())

        responses: list[FavoriteResponse] = []
        for favorite in favorites:
            response = self._build_response(favorite)
            if search:
                item = favorite.task if favorite.item_type == "task" else favorite.user_story
                haystack = " ".join(
                    part
                    for part in (
                        getattr(item, "title", "") if item else "",
                        getattr(item, "description", "") if item else "",
                    )
                    if part
                ).lower()
                if search.strip().lower() not in haystack:
                    continue
            responses.append(response)

        reverse = sort_order == "DESC"
        if sort_by == "created_at":
            responses.sort(key=lambda item: item.created_at, reverse=reverse)
        else:
            responses.sort(
                key=lambda item: (
                    item.task_title or item.user_story_title or ""
                ).lower(),
                reverse=reverse,
            )

        total = len(responses)
        total_tasks = sum(item.item_type == "task" for item in responses)
        total_user_stories = sum(
            item.item_type == "user_story" for item in responses
        )
        start = (page - 1) * page_size
        return FavoriteListResponse(
            favorites=responses[start : start + page_size],
            total=total,
            total_tasks=total_tasks,
            total_user_stories=total_user_stories,
        )

    async def add_favorite(
        self,
        user_id: str,
        item_id: str,
        item_type: str,
    ) -> FavoriteResponse:
        if item_type == "task":
            task = await self._task_or_404(item_id)
            if task.project is None:
                raise FavoriteServiceError(
                    404, "RESOURCE_NOT_FOUND", "Task not found"
                )
            project = task.project
            story_title = task.user_story.title if task.user_story else ""
            task_title = task.title
            story_id = str(task.user_story_id) if task.user_story_id else None
            existing = (
                await self.db.execute(
                    select(Favorite.id).where(
                        Favorite.user_id == user_id,
                        Favorite.item_type == "task",
                        Favorite.task_id == item_id,
                        Favorite.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
        elif item_type == "user_story":
            story = await self._story_or_404(item_id)
            project = story.project
            story_title = story.title
            task_title = ""
            story_id = item_id
            existing = (
                await self.db.execute(
                    select(Favorite.id).where(
                        Favorite.user_id == user_id,
                        Favorite.item_type == "user_story",
                        Favorite.user_story_id == item_id,
                        Favorite.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
        else:
            raise FavoriteServiceError(
                400, "VALIDATION_ERROR", "Invalid item type"
            )

        if existing is not None:
            raise FavoriteServiceError(
                409, "CONFLICT", "Item is already added to favorites"
            )

        project_id = str(project.id) if project else None
        project_name = project.name if project else ""

        favorite = Favorite(
            user_id=user_id,
            item_type=item_type,
            user_story_id=story_id if item_type == "user_story" else None,
            task_id=item_id if item_type == "task" else None,
        )
        try:
            self.db.add(favorite)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise FavoriteServiceError(
                409, "CONFLICT", "Item is already added to favorites"
            ) from exc
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise FavoriteServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to add item to favorites"
            ) from exc

        return FavoriteResponse(
            id=str(favorite.id),
            user_id=user_id,
            item_type=item_type,
            user_story_id=story_id if item_type == "user_story" else None,
            task_id=item_id if item_type == "task" else None,
            project_id=project_id,
            project_name=project_name,
            user_story_name=story_title if item_type == "user_story" else "",
            user_story_title=story_title if item_type == "user_story" else "",
            task_name=task_title if item_type == "task" else "",
            task_title=task_title if item_type == "task" else "",
            user_story=self._story_payload(story)
            if item_type == "user_story"
            else None,
            task=self._task_payload(task) if item_type == "task" else None,
            created_at=favorite.created_at,
        )

    async def remove_favorite(
        self,
        user_id: str,
        item_type: str,
        item_id: str,
    ) -> RemoveFavoriteResponse:
        favorite = await self._favorite_or_404(user_id, item_type, item_id)
        favorite.deleted_at = datetime.now(timezone.utc)
        try:
            await self.db.commit()
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise FavoriteServiceError(
                500, "INTERNAL_SERVER_ERROR", "Failed to remove item from favorites"
            ) from exc
        return RemoveFavoriteResponse(id=str(favorite.id))

    @staticmethod
    def _favorite_load_options():
        return (
            selectinload(Favorite.task).selectinload(Task.project),
            selectinload(Favorite.task).selectinload(Task.sprint),
            selectinload(Favorite.task).selectinload(Task.user_story),
            selectinload(Favorite.task).selectinload(Task.reporter),
            selectinload(Favorite.task).selectinload(Task.assignee),
            selectinload(Favorite.task).selectinload(Task.labels),
            selectinload(Favorite.user_story).selectinload(UserStory.project),
            selectinload(Favorite.user_story).selectinload(UserStory.sprint),
            selectinload(Favorite.user_story).selectinload(UserStory.status),
            selectinload(Favorite.user_story).selectinload(UserStory.reporter),
            selectinload(Favorite.user_story).selectinload(UserStory.assignee),
        )

    @staticmethod
    def _user_summary(user) -> dict | None:
        if user is None:
            return None
        return {
            "id": str(user.id),
            "full_name": user.full_name or "",
            "email": user.email or "",
            "avatar_url": user.avatar_url,
            "color": user.color or "",
        }

    def _task_payload(self, task: Task) -> dict:
        status_key = normalize_task_status(task.status or "")
        return {
            "id": str(task.id),
            "project_id": str(task.project_id),
            "project_name": task.project.name if task.project else "",
            "sprint_id": str(task.sprint_id) if task.sprint_id else None,
            "sprint_name": task.sprint.name if task.sprint else "",
            "user_story_id": str(task.user_story_id) if task.user_story_id else None,
            "user_story_title": task.user_story.title if task.user_story else "",
            "key": task.key,
            "serial_number": int(task.serial_number or 0),
            "formatted_serial_number": task.formatted_serial_number,
            "title": task.title,
            "description": task.description or "",
            "type": task.type,
            "priority": task.priority,
            "status_id": str(task.status_id),
            "status": task.status or "",
            "status_color": DEFAULT_STATUS_COLORS.get(status_key, "#808080"),
            "is_final": DEFAULT_STATUS_IS_FINAL.get(status_key, False),
            "is_favourite": True,
            "assignee_id": str(task.assignee_id) if task.assignee_id else None,
            "reporter_id": str(task.reporter_id) if task.reporter_id else None,
            "reporter_name": task.reporter.full_name if task.reporter else "",
            "assignee_name": task.assignee.full_name if task.assignee else "",
            "story_points": int(task.story_points or 0),
            "due_date": task.due_date,
            "estimated_hours": task.estimated_hours,
            "actual_hours": task.actual_hours,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "labels": [
                {"id": str(label.id), "name": label.name, "color": label.color}
                for label in task.labels
            ],
            "reporter": self._user_summary(task.reporter),
            "assignee": self._user_summary(task.assignee),
        }

    def _story_payload(self, story: UserStory) -> dict:
        status = story.status
        return {
            "id": str(story.id),
            "project_id": str(story.project_id),
            "project_name": story.project.name if story.project else "",
            "sprint_id": str(story.sprint_id) if story.sprint_id else None,
            "sprint_name": story.sprint.name if story.sprint else "",
            "sequence_number": int(story.serial_number or 0),
            "serial_number": int(story.serial_number or 0),
            "formatted_serial_number": story.formatted_serial_number,
            "title": story.title,
            "description": story.description or "",
            "priority": story.priority,
            "status_id": str(story.status_id),
            "status": status.name if status else "",
            "status_color": status.color if status else "#808080",
            "is_closed": bool(story.is_closed),
            "is_favourite": True,
            "assignee_id": str(story.assignee_id) if story.assignee_id else None,
            "reporter_id": str(story.reporter_id) if story.reporter_id else None,
            "reporter_name": story.reporter.full_name if story.reporter else "",
            "assignee_name": story.assignee.full_name if story.assignee else "",
            "story_points": int(story.story_points or 0),
            "backlog_order": int(story.backlog_order or 0),
            "created_at": story.created_at,
            "updated_at": story.updated_at,
            "total_tasks": 0,
            "completed_tasks": 0,
            "progress": 0.0,
            "tasks": [],
            "reporter": self._user_summary(story.reporter),
            "assignee": self._user_summary(story.assignee),
        }

    def _build_response(self, favorite: Favorite) -> FavoriteResponse:
        task = favorite.task
        story = favorite.user_story
        if favorite.item_type == "task" and task is not None:
            project = task.project
            story = task.user_story
            return FavoriteResponse(
                id=str(favorite.id),
                user_id=str(favorite.user_id),
                item_type="task",
                task_id=str(favorite.task_id) if favorite.task_id else None,
                user_story_id=(
                    str(favorite.user_story_id) if favorite.user_story_id else None
                ),
                project_id=str(project.id) if project else None,
                project_name=project.name if project else "",
                user_story_name=story.title if story else "",
                user_story_title=story.title if story else "",
                task_name=task.title,
                task_title=task.title,
                task=self._task_payload(task),
                created_at=favorite.created_at,
            )
        if favorite.item_type == "user_story" and story is not None:
            project = story.project
            return FavoriteResponse(
                id=str(favorite.id),
                user_id=str(favorite.user_id),
                item_type="user_story",
                user_story_id=str(favorite.user_story_id)
                if favorite.user_story_id
                else None,
                project_id=str(project.id) if project else None,
                project_name=project.name if project else "",
                user_story_name=story.title,
                user_story_title=story.title,
                user_story=self._story_payload(story),
                created_at=favorite.created_at,
            )
        return FavoriteResponse(
            id=str(favorite.id),
            user_id=str(favorite.user_id),
            item_type=favorite.item_type,
            created_at=favorite.created_at,
        )
