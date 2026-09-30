import asyncio
import time
from typing import AsyncGenerator, Dict, Any
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event, select, and_
from sqlalchemy.orm import joinedload

import src.main
from src.main import app
from src.database import engine, AsyncSessionLocal
from src.organization.models import Organization, Role
from src.project.models import Project, ProjectMember
from src.auth.models import User
from src.task.models import Task
from src.user_story.models import UserStory
from src.sprint.models import Sprint
from src.custom_status.models import CustomStatus
from src.label.models import Label
from src.comments.models import Comments
from src.utils.core import create_jwt


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


class QueryCounter:
    def __init__(self, target_engine):
        self.engine = target_engine.sync_engine
        self.count = 0
        self.queries = []
        self._listener = None

    def __enter__(self):
        self.count = 0
        self.queries = []
        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            self.count += 1
            self.queries.append({"statement": statement, "parameters": parameters})
        self._listener = _before_cursor_execute
        event.listen(self.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._listener:
            event.remove(self.engine, "before_cursor_execute", self._listener)

    @property
    def duplicates(self):
        counts = {}
        for q in self.queries:
            s = q["statement"]
            counts[s] = counts.get(s, 0) + 1
        return {s: c for s, c in counts.items() if c > 1}


@pytest.fixture
def query_counter():
    return QueryCounter(engine)


@pytest.fixture
async def db_session() -> AsyncGenerator:
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
async def app_context() -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:
        # 1. Organization A: Org Admin, Project, Story, Task, Sprint
        stmt_a = (
            select(Project, User)
            .join(User, User.organization_id == Project.organization_id)
            .join(Role, and_(Role.id == User.role_id, Role.name == "org_admin"))
            .join(UserStory, UserStory.project_id == Project.id)
            .join(Task, Task.user_story_id == UserStory.id)
            .where(
                Project.deleted_at.is_(None),
                User.deleted_at.is_(None),
                UserStory.deleted_at.is_(None),
                Task.deleted_at.is_(None),
            )
            .limit(1)
        )
        row_a = (await db.execute(stmt_a)).first()
        if not row_a:
            raise RuntimeError("Database missing valid Organization A test entities.")
        project_a, user_a_admin = row_a[0], row_a[1]
        org_a_id = str(project_a.organization_id)
        project_a_id = str(project_a.id)
        user_a_admin_id = str(user_a_admin.id)

        # Developer / Member in Org A
        member_stmt = (
            select(User)
            .where(
                User.organization_id == project_a.organization_id,
                User.id != user_a_admin.id,
                User.deleted_at.is_(None),
            )
            .limit(1)
        )
        user_a_member = (await db.execute(member_stmt)).scalars().first()
        user_a_member_id = str(user_a_member.id) if user_a_member else user_a_admin_id

        # Story in Project A
        story_stmt = select(UserStory).where(UserStory.project_id == project_a.id, UserStory.deleted_at.is_(None)).limit(1)
        story_a = (await db.execute(story_stmt)).scalars().first()
        story_a_id = str(story_a.id) if story_a else None

        # Task in Project A
        task_stmt = select(Task).where(Task.project_id == project_a.id, Task.deleted_at.is_(None)).limit(1)
        task_a = (await db.execute(task_stmt)).scalars().first()
        task_a_id = str(task_a.id) if task_a else None
        status_a_id = str(task_a.status_id) if task_a else None

        # Sprint in Project A
        sprint_stmt = select(Sprint).where(Sprint.project_id == project_a.id, Sprint.deleted_at.is_(None)).limit(1)
        sprint_a = (await db.execute(sprint_stmt)).scalars().first()
        sprint_a_id = str(sprint_a.id) if sprint_a else None

        # Label in Project A
        label_stmt = select(Label).where(Label.project_id == project_a.id, Label.deleted_at.is_(None)).limit(1)
        label_a = (await db.execute(label_stmt)).scalars().first()
        label_a_id = str(label_a.id) if label_a else None

        # Comment in Task
        comment_stmt = select(Comments).where(Comments.deleted_at.is_(None)).limit(1)
        comment_a = (await db.execute(comment_stmt)).scalars().first()
        comment_a_id = str(comment_a.id) if comment_a else None

        # 2. Organization B (for Tenant Isolation & IDOR tests)
        stmt_b = (
            select(Project, User)
            .join(User, User.organization_id == Project.organization_id)
            .join(Role, and_(Role.id == User.role_id, Role.name == "org_admin"))
            .where(
                Project.organization_id != project_a.organization_id,
                Project.deleted_at.is_(None),
                User.deleted_at.is_(None),
            )
            .limit(1)
        )
        row_b = (await db.execute(stmt_b)).first()
        project_b, user_b_admin = row_b[0], row_b[1] if row_b else (None, None)
        org_b_id = str(project_b.organization_id) if project_b else "01a014cc-cb87-7384-8467-741cacb46387"
        project_b_id = str(project_b.id) if project_b else "01a014cd-343d-7384-b114-be840bf31a49"
        user_b_admin_id = str(user_b_admin.id) if user_b_admin else "01a014cb-e970-7384-b48f-12bc9ec92ded"

        # Generate standard JWT tokens
        token_org_a_admin, _ = create_jwt(
            role="org_admin",
            user_id=user_a_admin_id,
            organization_id=org_a_id,
            platform="web",
        )
        token_org_a_member, _ = create_jwt(
            role="developer",
            user_id=user_a_member_id,
            organization_id=org_a_id,
            platform="web",
        )
        token_org_b_admin, _ = create_jwt(
            role="org_admin",
            user_id=user_b_admin_id,
            organization_id=org_b_id,
            platform="web",
        )

        return {
            "org_a_id": org_a_id,
            "project_a_id": project_a_id,
            "user_a_admin_id": user_a_admin_id,
            "user_a_member_id": user_a_member_id,
            "token_org_a_admin": token_org_a_admin,
            "token_org_a_member": token_org_a_member,
            "story_a_id": story_a_id,
            "task_a_id": task_a_id,
            "status_a_id": status_a_id,
            "sprint_a_id": sprint_a_id,
            "label_a_id": label_a_id,
            "comment_a_id": comment_a_id,
            "org_b_id": org_b_id,
            "project_b_id": project_b_id,
            "user_b_admin_id": user_b_admin_id,
            "token_org_b_admin": token_org_b_admin,
        }
