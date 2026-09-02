import math
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from src.audit.models import AuditLog, AuditLogType
from src.audit.service import AuditService
from src.auth.models import User

# Register relationship targets used by the project graph before the first ORM
# statement. The model files themselves remain unchanged.
from src.comments import models as comments_models  # noqa: F401
from src.custom_status import models as custom_status_models  # noqa: F401
from src.custom_status.models import CustomStatus
from src.favorite import models as favorite_models  # noqa: F401
from src.label import models as label_models  # noqa: F401
from src.organization.models import Organization, Role
from src.project.models import Project, ProjectMember
from src.project.schema import (
    CreateProjectMemberRequest,
    CreateProjectRequest,
    PaginationResponse,
    ProjectActivityResponse,
    ProjectActivityUser,
    ProjectDetail,
    ProjectMemberResponse,
    ProjectMetrics,
    ProjectSummary,
    SprintResponse,
    UpdateProjectMemberRequest,
    UpdateProjectRequest,
    UserProject,
    UserProjectRoleResponse,
    UserProjectsResponse,
)
from src.serial import models as serial_models  # noqa: F401
from src.sprint.models import Sprint
from src.task.models import Task
from src.user_story import models as user_story_models  # noqa: F401
from src.user_story_status import models as user_story_status_models  # noqa: F401
from src.user_story_status.models import UserStoryStatus


DEFAULT_TASK_STATUSES = [
    ("Todo", "#808080", 0, True, False),
    ("In Progress", "#1E90FF", 1, True, False),
    ("In Review", "#FF8C00", 2, True, False),
    ("Testing", "#8A2BE2", 3, True, False),
    ("Completed", "#228B22", 4, True, True),
    ("Blocked", "#DC143C", 5, True, False),
]

DEFAULT_USER_STORY_STATUSES = [
    ("Todo", "#808080", 0, True, False, False),
    ("In Progress", "#1E90FF", 1, True, False, False),
    ("In Review", "#FF8C00", 2, True, False, False),
    ("Testing", "#8A2BE2", 3, True, False, False),
    ("Completed", "#228B22", 4, True, True, True),
    ("Blocked", "#DC143C", 5, True, False, False),
]


class ProjectServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


class ProjectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def pagination(page: int, size: int, total: int) -> PaginationResponse:
        pages = max(1, math.ceil(total / size))
        return PaginationResponse(
            page=page,
            page_size=size,
            total_items=total,
            total_pages=pages,
            has_next=page < pages,
            has_previous=page > 1,
        )

    async def _project(self, project_id: str, organization_id: str | None = None) -> Project:
        conditions = [Project.id == project_id, Project.deleted_at.is_(None)]
        if organization_id:
            conditions.append(Project.organization_id == organization_id)
        project = (
            await self.db.execute(
                select(Project).where(*conditions)
            )
        ).scalar_one_or_none()
        if not project:
            raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "Project not found")
        return project

    async def _project_by_reference(
        self, project_id_or_slug: str, organization_id: str
    ) -> Project:
        try:
            project_id = str(uuid.UUID(project_id_or_slug))
            reference = Project.id == project_id
        except (ValueError, TypeError, AttributeError):
            reference = Project.slug == project_id_or_slug

        project = (
            await self.db.execute(
                select(Project).where(
                    reference,
                    Project.organization_id == organization_id,
                    Project.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not project:
            raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "Project not found")
        return project

    async def _role(self, role_id: str | None, project_role: str | None,
                    organization_id: str) -> Role:
        if role_id:
            role = (
                await self.db.execute(
                    select(Role).where(
                        Role.id == role_id,
                        or_(
                            Role.organization_id == organization_id,
                            Role.organization_id.is_(None),
                        ),
                        Role.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
        else:
            aliases = {"tester": "qa", "viewer": "stakeholder"}
            name = aliases.get(project_role or "", project_role)
            role = (
                await self.db.execute(
                    select(Role).where(Role.name == name,
                        or_(Role.organization_id == organization_id,
                            Role.organization_id.is_(None)), Role.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
        if not role:
            raise ProjectServiceError(400, "VALIDATION_ERROR", "Invalid project role")
        return role

    async def _member_role(self, user_id: str, organization_id: str) -> Role:
        user = (
            await self.db.execute(
                select(User).where(User.id == user_id, User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if not user or not user.role_id:
            raise ProjectServiceError(400, "VALIDATION_ERROR", "Invalid project role")
        return await self._role(str(user.role_id), None, organization_id)

    async def create(self, body: CreateProjectRequest, user_id: str,
                     organization_id: str) -> str:
        slug = slugify(body.name) or "project"
        base_slug = slug
        suffix = 1
        while True:
            exists = (
                await self.db.execute(
                    select(Project.id).where(
                        Project.slug == slug,
                        Project.organization_id == organization_id,
                        Project.deleted_at.is_(None),
                    )
                )
            ).first()
            if not exists:
                break
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        role = await self._member_role(user_id, organization_id)
        project = Project(organization_id=organization_id, name=body.name,
            slug=slug, description=body.description, status="planning", created_by=user_id)
        try:
            self.db.add(project)
            await self.db.flush()

            # 1. Add project creator as member
            self.db.add(ProjectMember(project_id=project.id, user_id=user_id,
                role_id=role.id, added_by_id=user_id, joined_at=datetime.now(timezone.utc)))

            # 2. Create default custom statuses for tasks
            for name, color, order, is_default, is_final in DEFAULT_TASK_STATUSES:
                self.db.add(CustomStatus(000000000000000000
                    project_id=project.id,
                    name=name,
                    color=color,
                    display_order=order,
                    is_default=is_default,
                    is_final=is_final,
                ))

            # 3. Create default statuses for user stories
            for name, color, order, is_default, is_closed, is_final in DEFAULT_USER_STORY_STATUSES:
                self.db.add(UserStoryStatus(
                    project_id=project.id,
                    name=name,
                    color=color,
                    display_order=order,
                    is_default=is_default,
                    is_closed=is_closed,
                    is_final=is_final,
                ))

            # 4. Log project creation audit event
            user = (
                await self.db.execute(
                    select(User).where(User.id == user_id, User.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
            creator_name = getattr(user, "username", "user") if user else "user"

            self.db.add(AuditLog(
                user_id=user_id,
                organization_id=organization_id,
                project_id=project.id,
                action="created",
                resource_type="project",
                resource_id=str(project.id),
                details=f"The project '{body.name}' was created by {creator_name}",
                type=AuditLogType.ACTIVITY,
                created_at=datetime.now(timezone.utc),
            ))

            await self.db.commit()
            return str(project.id)
        except IntegrityError as exc:
            await self.db.rollback()
            raise ProjectServiceError(409, "CONFLICT", "Project already exists") from exc

    async def update(self, project_id: str, body: UpdateProjectRequest,
                     user_id: str, organization_id: str) -> str:
        project = await self._project(project_id, organization_id)
        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        if not updates:
            raise ProjectServiceError(400, "BAD_REQUEST", "No changes to update")
        if "status" in updates and updates["status"] not in {
            "planning", "active", "on_hold", "completed", "cancelled", "archived"
        }:
            raise ProjectServiceError(
                400,
                "BAD_REQUEST",
                "Invalid status. Allowed values: active, archived, on_hold, completed, cancelled, planning",
            )
        if updates.get("slug"):
            updates["slug"] = slugify(updates["slug"])
            if not updates["slug"]:
                raise ProjectServiceError(400, "BAD_REQUEST", "Slug cannot be empty")
            duplicate = (
                await self.db.execute(
                    select(Project.id).where(
                        Project.organization_id == organization_id,
                        Project.slug == updates["slug"],
                        Project.id != project_id,
                        Project.deleted_at.is_(None),
                    )
                )
            ).first()
            if duplicate:
                raise ProjectServiceError(
                    400, "BAD_REQUEST", "Project slug is already in use"
                )
        for key, value in updates.items():
            setattr(project, key, value)
        project.updated_at = datetime.now(timezone.utc)
        self._audit(user_id, organization_id, project_id, "updated", "project",
                    project_id, f"Updated project {project.name}")
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ProjectServiceError(
                409, "CONFLICT", "Project already exists"
            ) from exc
        return project_id

    async def list_projects(self, *, organization_id: str | None, user_id: str | None,
                            user_role: str = "", page: int = 1, page_size: int = 10,
                            name: str = "", status: str = "", search: str = "",
                            created_by: str | None = None, include_sprints: bool = False,
                            sort_by: str = "created_at", sort_order: str = "DESC"):
        sort_by = sort_by.strip() or "created_at"
        sort_order = sort_order.strip().upper()
        if sort_order not in {"ASC", "DESC"}:
            sort_order = "DESC"
        conditions = [Project.deleted_at.is_(None)]
        if organization_id:
            conditions.append(Project.organization_id == organization_id)
        if user_id and user_role not in {"org_admin", "super_admin"}:
            conditions.append(Project.id.in_(select(ProjectMember.project_id).where(
                ProjectMember.user_id == user_id, ProjectMember.deleted_at.is_(None))))
        if name:
            conditions.append(Project.name.ilike(f"%{name.strip()}%"))
        if status:
            conditions.append(func.lower(Project.status) == status.lower().strip())
        if search:
            conditions.append(or_(Project.name.ilike(f"%{search.strip()}%"),
                                  Project.description.ilike(f"%{search.strip()}%")))
        if created_by:
            conditions.append(Project.created_by == created_by)
        total = (
            await self.db.execute(
                select(func.count(Project.id)).where(*conditions)
            )
        ).scalar_one()
        columns = {
            "name": Project.name,
            "created_at": Project.created_at,
            "updated_at": Project.updated_at,
            "status": Project.status,
        }
        order = columns.get(sort_by, Project.created_at)
        order = order.asc() if sort_order.upper() == "ASC" else order.desc()
        projects = list(
            (
                await self.db.execute(
                    select(Project)
                    .where(*conditions)
                    .order_by(order)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars()
        )
        return (
            await self._summaries(projects, include_sprints, user_id),
            self.pagination(page, page_size, total),
        )

    async def _summaries(
        self,
        projects: list[Project],
        include_sprints: bool,
        user_id: str | None = None,
    ) -> list[ProjectSummary]:
        if not projects:
            return []

        project_ids = [project.id for project in projects]
        organization_ids = {project.organization_id for project in projects}

        organization_names = dict(
            (
                await self.db.execute(
                    select(Organization.id, Organization.name).where(
                        Organization.id.in_(organization_ids)
                    )
                )
            ).all()
        )

        task_counts = dict(
            (
                await self.db.execute(
                    select(Task.project_id, func.count(Task.id))
                    .where(
                        Task.project_id.in_(project_ids),
                        Task.deleted_at.is_(None),
                    )
                    .group_by(Task.project_id)
                )
            ).all()
        )
        member_counts = dict(
            (
                await self.db.execute(
                    select(ProjectMember.project_id, func.count(ProjectMember.id))
                    .where(
                        ProjectMember.project_id.in_(project_ids),
                        ProjectMember.deleted_at.is_(None),
                    )
                    .group_by(ProjectMember.project_id)
                )
            ).all()
        )
        sprint_rows = list(
            (
                await self.db.execute(
                    select(Sprint).where(
                        Sprint.project_id.in_(project_ids),
                        Sprint.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        sprints_by_project: dict[str, list[SprintResponse]] = {
            str(project_id): [] for project_id in project_ids
        }
        for sprint in sprint_rows:
            sprints_by_project[str(sprint.project_id)].append(
                SprintResponse(
                    id=str(sprint.id),
                    name=sprint.name,
                    goal=sprint.goal,
                    status=sprint.status,
                    start_date=sprint.start_date,
                    end_date=sprint.end_date,
                )
            )

        summaries: list[ProjectSummary] = []
        for project in projects:
            project_id = str(project.id)
            key = self._project_key(project.name)
            project_sprints = sprints_by_project[project_id]
            summaries.append(
                ProjectSummary(
                    id=project_id,
                    organization_id=str(project.organization_id),
                    organization_name=organization_names.get(project.organization_id),
                    name=project.name,
                    key=key,
                    project_key=key,
                    description=project.description,
                    status=project.status,
                    created_by=str(project.created_by),
                    created_at=project.created_at,
                    sprint_count=len(project_sprints),
                    total_tasks=task_counts.get(project.id, 0),
                    total_members=member_counts.get(project.id, 0),
                    slug=project.slug,
                    sprints=project_sprints,
                )
            )
        return summaries

    @staticmethod
    def _project_key(name: str) -> str:
        name = name.strip()
        if not name:
            return "TASK"

        parts = [part for part in re.split(r"[ _-]", name) if part]
        if len(parts) > 1:
            prefix = "".join(part[0].upper() for part in parts)
        else:
            prefix = name.upper()[:3]

        cleaned = "".join(
            character
            for character in prefix
            if "A" <= character <= "Z" or "0" <= character <= "9"
        )
        if len(cleaned) < 2:
            cleaned = "WP"
        return cleaned[:10]

    async def members(self, project_id: str, organization_id: str, page: int,
                      page_size: int, name: str = ""):
        project = await self._project(project_id, organization_id)
        stmt = self._member_statement(project_id, name)
        total = (
            await self.db.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self.db.execute(
                stmt.order_by(User.full_name.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        data = await self._member_responses(project, rows)
        return data, self.pagination(page, page_size, total)

    @staticmethod
    def _member_statement(project_id: str, name: str = ""):
        stmt = (
            select(ProjectMember, User, Role)
            .join(User, User.id == ProjectMember.user_id)
            .join(Role, Role.id == ProjectMember.role_id)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.deleted_at.is_(None),
                User.deleted_at.is_(None),
                Role.deleted_at.is_(None),
            )
        )
        search = name.strip()
        if search:
            stmt = stmt.where(
                or_(
                    User.full_name.ilike(f"%{search}%"),
                    User.username.ilike(f"%{search}%"),
                )
            )
        return stmt

    async def _member_responses(
        self,
        project: Project,
        rows,
        include_context: bool = True,
    ) -> list[ProjectMemberResponse]:
        org_name = None
        project_key = None
        if include_context:
            org_name = (
                await self.db.execute(
                    select(Organization.name).where(
                        Organization.id == project.organization_id
                    )
                )
            ).scalar_one_or_none()
            project_key = self._project_key(project.name)
        return [
            ProjectMemberResponse(
                user_id=str(user.id),
                username=user.username,
                full_name=user.full_name,
                role=role.name,
                avatar_url=user.avatar_url or None,
                color=user.color if include_context else "",
                organization_name=org_name,
                project_key=project_key,
            )
            for _, user, role in rows
        ]

    async def add_members(self, body: CreateProjectMemberRequest, actor: str,
                          organization_id: str):
        project = await self._project(body.project_id, organization_id)
        try:
            user_ids = [item.user_id for item in body.members]
            users = {
                str(user.id): user
                for user in (
                    await self.db.execute(
                        select(User).where(
                            User.id.in_(user_ids),
                            User.organization_id == organization_id,
                            User.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            }
            existing_user_ids = set(
                str(user_id)
                for user_id in (
                    await self.db.execute(
                        select(ProjectMember.user_id).where(
                            ProjectMember.project_id == project.id,
                            ProjectMember.user_id.in_(user_ids),
                            ProjectMember.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )
            role_cache: dict[tuple[str | None, str | None], Role] = {}

            existing_users: list[str] = []
            for item in body.members:
                user = users.get(item.user_id)
                if not user:
                    raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "User not found")
                if item.user_id in existing_user_ids:
                    existing_users.append(user.username)
                    continue
                project_role = item.project_role or "developer"
                if project_role not in {
                    "org_admin",
                    "project_manager",
                    "developer",
                    "tester",
                    "qa",
                    "viewer",
                    "stakeholder",
                }:
                    project_role = "developer"
                role_key = (item.role_id, project_role)
                role = role_cache.get(role_key)
                if role is None:
                    role = await self._role(
                        item.role_id, project_role, organization_id
                    )
                    role_cache[role_key] = role
                self.db.add(ProjectMember(project_id=project.id, user_id=user.id,
                    role_id=role.id, added_by_id=actor, joined_at=datetime.now(timezone.utc)))
            await self.db.commit()
            if existing_users:
                raise ProjectServiceError(
                    400,
                    "BAD_REQUEST",
                    "The following users are already members of the project: "
                    + ", ".join(existing_users),
                )
        except Exception:
            await self.db.rollback()
            raise

    async def remove_member(self, project_id: str, target_id: str, actor: str,
                            organization_id: str):
        await self._project(project_id, organization_id)
        if target_id == actor:
            raise ProjectServiceError(403, "FORBIDDEN", "You cannot remove yourself from the project")
        member = (
            await self.db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id, ProjectMember.user_id == target_id,
                    ProjectMember.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if not member:
            raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "Project member not found")
        member.deleted_at = datetime.now(timezone.utc)
        await self._commit()

    async def update_member(self, project_id: str, target_id: str,
                            body: UpdateProjectMemberRequest, actor: str,
                            organization_id: str):
        if target_id == actor:
            raise ProjectServiceError(403, "FORBIDDEN", "You cannot update your own project role")
        project = await self._project(project_id, organization_id)
        member = (
            await self.db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project.id, ProjectMember.user_id == target_id,
                    ProjectMember.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if not member:
            raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "Project member not found")
        role = await self._role(body.role_id, body.project_role, organization_id)
        member.role_id = role.id
        member.updated_at = datetime.now(timezone.utc)
        await self._commit()

    async def detail(self, project_id: str, user_id: str, organization_id: str) -> ProjectDetail:
        project = await self._project_by_reference(project_id, organization_id)
        resolved_project_id = str(project.id)
        member_rows = (
            await self.db.execute(
                self._member_statement(resolved_project_id)
                .order_by(User.full_name.asc())
                .limit(1000)
            )
        ).all()
        members = await self._member_responses(project, member_rows, include_context=False)
        sprints = list(
            (
                await self.db.execute(
                    select(Sprint).where(
                        Sprint.project_id == resolved_project_id,
                        Sprint.deleted_at.is_(None),
                    ).limit(1000)
                )
            ).scalars()
        )
        task_rows = (
            await self.db.execute(
                select(Task.status, Task.due_date).where(
                    Task.project_id == resolved_project_id,
                    Task.deleted_at.is_(None),
                ).limit(10000)
            )
        ).all()
        total = len(task_rows)
        complete = sum(1 for status_value, _ in task_rows if status_value == "completed")
        now = datetime.now(timezone.utc)
        overdue = sum(
            1
            for status_value, due_date in task_rows
            if status_value != "completed" and due_date is not None and due_date < now
        )
        creator = (
            await self.db.execute(
                select(User.username).where(
                    User.id == project.created_by,
                )
            )
        ).scalar_one_or_none() or ""
        active = sum(1 for s in sprints if s.status == "active")
        completed_sprints = sum(1 for s in sprints if s.status == "completed")
        metrics = ProjectMetrics(total_tasks=total, completed_tasks=complete,
            pending_tasks=total-complete, overdue_tasks=overdue,
            completed_tasks_percentage=int(complete * 100 / total) if total else 0,
            total_sprints=len(sprints), active_sprints=active,
            completed_sprints=completed_sprints, total_members=len(members))
        sprint_responses = [
            SprintResponse(
                id=str(sprint.id),
                name=sprint.name,
                goal=sprint.goal,
                status=sprint.status,
                start_date=sprint.start_date,
                end_date=sprint.end_date,
            )
            for sprint in sprints
        ]
        active_sprint = next(
            (sr for sr in sprint_responses if sr.status == "active"), None
        )
        return ProjectDetail(id=str(project.id), organization_id=str(project.organization_id),
            organization_name=None, name=project.name, key=None, project_key=None,
            description=project.description, status=project.status,
            created_by=str(project.created_by), creator=creator,
            created_at=project.created_at, members=members,
            sprints=sprint_responses,
            active_sprint=active_sprint,
            metrics=metrics, slug=project.slug)

    async def delete(self, project_id: str, user_id: str, organization_id: str):
        project = await self._project(project_id, organization_id)
        project.deleted_at = datetime.now(timezone.utc)
        await self._commit()

    async def user_projects(
        self,
        target_id: str,
        organization_id: str,
        recent: bool = False,
        caller_id: str | None = None,
        caller_role: str = "",
    ) -> UserProjectsResponse:
        user = (
            await self.db.execute(
                select(User)
                .options(selectinload(User.role))
                .where(
                    User.id == target_id,
                    User.organization_id == organization_id,
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not user:
            raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "User not found")
        if caller_id and caller_id != target_id and caller_role != "org_admin":
            raise ProjectServiceError(
                403,
                "FORBIDDEN",
                "You do not have permission to view this user's projects",
            )
        stmt = select(ProjectMember, Project, Role).join(Project,
            Project.id == ProjectMember.project_id).join(Role,
            Role.id == ProjectMember.role_id).where(ProjectMember.user_id == target_id,
            ProjectMember.deleted_at.is_(None), Project.deleted_at.is_(None),
            Project.organization_id == organization_id)
        if recent:
            # A project is still recent/available even before its first task is
            # created. Requiring a task made valid assigned projects disappear.
            stmt = stmt.order_by(Project.updated_at.desc(), Project.created_at.desc())
        rows = (await self.db.execute(stmt)).all()
        task_counts: dict[str, int] = {}
        if recent and rows:
            project_ids = [project.id for _, project, _ in rows]
            task_counts = {
                str(project_id): count
                for project_id, count in (
                    await self.db.execute(
                        select(Task.project_id, func.count(Task.id))
                        .where(
                            Task.project_id.in_(project_ids),
                            Task.deleted_at.is_(None),
                        )
                        .group_by(Task.project_id)
                    )
                ).all()
            }
        projects = [
            UserProject(
                project_id=str(project.id),
                role=role.name,
                project_name=project.name,
                status=project.status,
                project_slug=project.slug if recent else None,
            )
            for _, project, role in rows
            if not recent or task_counts.get(str(project.id), 0) > 0
        ]
        return UserProjectsResponse(user_id=str(user.id), user_name=user.username,
            full_name=user.full_name, email=user.email, avatar_url=user.avatar_url or None,
            color=user.color, role=getattr(user.role, "name", None), project=projects)

    async def user_role(self, project_id: str, user_id: str,
                        organization_id: str) -> UserProjectRoleResponse:
        user = (
            await self.db.execute(
                select(User).where(
                    User.id == user_id,
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "User not found")
        if not user.organization_id or str(user.organization_id) != organization_id:
            raise ProjectServiceError(
                403,
                "FORBIDDEN",
                "You do not have permission to perform this action",
            )

        row = (
            await self.db.execute(
                select(ProjectMember, Project, Role)
                .join(Project, Project.id == ProjectMember.project_id)
                .join(Role, Role.id == ProjectMember.role_id)
                .where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                    ProjectMember.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                    Project.organization_id == organization_id,
                    Role.deleted_at.is_(None),
                )
            )
        ).first()
        if not row:
            project_exists = (
                await self.db.execute(
                    select(Project.id).where(
                        Project.id == project_id,
                        Project.organization_id == organization_id,
                        Project.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if project_exists is None:
                raise ProjectServiceError(
                    404, "RESOURCE_NOT_FOUND", "Project not found"
                )
            raise ProjectServiceError(404, "RESOURCE_NOT_FOUND", "Project member not found")
        member, project, role = row
        return UserProjectRoleResponse(project_id=project_id, project_name=project.name,
            project_key=project.slug, role_id=str(member.role_id), role=role.name)

    async def activities(self, project_id: str, organization_id: str, page: int,
                         page_size: int, activity_type: str, action: str = "",
                         user_id: str = "", resource_type: str = "",
                         resource_id: str = "", task_id: str = "",
                         user_story_id: str = "", sprint_id: str = "",
                         start_date: str = "", end_date: str = ""):
        await self._project(project_id, organization_id)
        conditions = [AuditLog.project_id == project_id]
        normalized_type = activity_type.strip().lower()
        if normalized_type == "view":
            conditions.append(
                or_(
                    func.lower(AuditLog.type) == "view",
                    func.lower(AuditLog.action).like("%view%"),
                )
            )
        elif normalized_type == "audit":
            conditions.append(func.lower(AuditLog.type) == "audit")
        elif normalized_type:
            conditions.append(
                and_(
                    or_(
                        func.lower(AuditLog.type) == "activity",
                        AuditLog.type.is_(None),
                        AuditLog.type == "",
                    ),
                    ~func.lower(AuditLog.action).like("%view%"),
                )
            )
        if action:
            conditions.append(func.lower(AuditLog.action) == action.strip().lower())
        if user_id:
            conditions.append(AuditLog.user_id == user_id)
        normalized_resource_type = resource_type.strip().lower()
        if normalized_resource_type == "task":
            conditions.append(
                or_(
                    AuditLog.task_id.is_not(None),
                    func.lower(AuditLog.resource_type).in_(["task", "task_attachment"]),
                )
            )
        elif normalized_resource_type in {"userstory", "user_story"}:
            conditions.append(
                or_(
                    AuditLog.user_story_id.is_not(None),
                    func.lower(AuditLog.resource_type).in_(
                        ["user_story", "userstory", "user_story_attachment"]
                    ),
                )
            )
        elif normalized_resource_type == "sprint":
            conditions.append(
                or_(
                    AuditLog.sprint_id.is_not(None),
                    func.lower(AuditLog.resource_type).in_(["sprint", "sprints"]),
                )
            )
        elif normalized_resource_type == "project":
            conditions.append(
                func.lower(AuditLog.resource_type).in_(["project", "project_member"])
            )
        elif normalized_resource_type in {"comment", "comments"}:
            conditions.append(
                or_(
                    func.lower(AuditLog.resource_type).in_(
                        ["comment", "comments", "comment_attachment"]
                    ),
                    func.lower(AuditLog.action).like("%comment%"),
                    func.lower(AuditLog.action).like("%reply%"),
                )
            )
        elif normalized_resource_type not in {"", "all"}:
            conditions.append(
                func.lower(AuditLog.resource_type) == normalized_resource_type
            )
        if resource_id:
            resource_id = resource_id.strip()
            conditions.append(
                or_(
                    AuditLog.resource_id == resource_id,
                    AuditLog.task_id == resource_id,
                    AuditLog.user_story_id == resource_id,
                    AuditLog.sprint_id == resource_id,
                )
            )
        if task_id:
            conditions.append(
                or_(
                    AuditLog.task_id == task_id,
                    and_(
                        func.lower(AuditLog.resource_type).in_(
                            ["task", "task_attachment", "comment"]
                        ),
                        AuditLog.resource_id == task_id,
                    ),
                )
            )
        if user_story_id:
            conditions.append(
                or_(
                    AuditLog.user_story_id == user_story_id,
                    and_(
                        func.lower(AuditLog.resource_type).in_(
                            [
                                "user_story",
                                "userstory",
                                "user_story_attachment",
                                "comment",
                            ]
                        ),
                        AuditLog.resource_id == user_story_id,
                    ),
                )
            )
        if sprint_id:
            conditions.append(
                or_(
                    AuditLog.sprint_id == sprint_id,
                    and_(
                        func.lower(AuditLog.resource_type).in_(["sprint", "sprints"]),
                        AuditLog.resource_id == sprint_id,
                    ),
                )
            )
        if start_date:
            conditions.append(AuditLog.created_at >= start_date)
        if end_date:
            conditions.append(AuditLog.created_at <= end_date)
        total = (
            await self.db.execute(
                select(func.count(AuditLog.id)).where(*conditions)
            )
        ).scalar_one()
        logs = (
            await self.db.execute(
                select(AuditLog).where(*conditions).order_by(
                    AuditLog.created_at.desc()).offset((page-1)*page_size).limit(page_size))
        ).scalars().all()
        audit_responses = await AuditService(self.db)._build_audit_responses(list(logs))
        data = [
            ProjectActivityResponse(
                id=item.id,
                project_id=item.project_id,
                project_name=item.project_name,
                organization_id=item.organization_id,
                user=self._activity_user(item.user),
                action=item.action,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                details=item.details,
                timestamp=(
                    item.created_at.astimezone(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                ),
                task_key=item.task_key,
                title=item.title,
                task_name=item.task_name,
                user_story_name=item.user_story_name,
                sprint_name=item.sprint_name,
            )
            for item in audit_responses
        ]
        return data, self.pagination(page, page_size, total)

    @staticmethod
    def _activity_user(user) -> ProjectActivityUser | None:
        if user is None:
            return None

        return ProjectActivityUser(
            id=user.id,
            name=user.full_name,
            email=user.email,
            avatar_url=user.avatar_url or None,
            color=user.color or "",
            role=user.role or None,
        )

    def _audit(self, user_id: str, org_id: str, project_id: str, action: str,
               resource_type: str, resource_id: str, details: str):
        self.db.add(AuditLog(user_id=user_id, organization_id=org_id,
            project_id=project_id, action=action, resource_type=resource_type,
            resource_id=resource_id, details=details, type="activity",
            created_at=datetime.now(timezone.utc)))

    async def _commit(self) -> None:
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
