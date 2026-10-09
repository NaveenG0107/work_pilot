import asyncio
import time
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event, select, func, and_
from sqlalchemy.orm import joinedload

import src.main
from src.main import app
from src.database import AsyncSessionLocal, engine
from src.project.models import Project, ProjectMember
from src.user_story.models import UserStory
from src.task.models import Task
from src.custom_status.models import CustomStatus
from src.sprint.models import Sprint
from src.auth.models import User
from src.organization.models import Organization, Role
from src.utils.core import create_jwt


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


class QueryCounter:
    def __init__(self, db_engine):
        self.engine = db_engine.sync_engine
        self.count = 0
        self.queries = []

    def __enter__(self):
        self.count = 0
        self.queries = []
        event.listen(self.engine, "before_cursor_execute", self._before_cursor_execute)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        event.remove(self.engine, "before_cursor_execute", self._before_cursor_execute)

    def _before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.queries.append(statement)


async def get_test_context():
    """Retrieve existing database entities suitable for testing."""
    async with AsyncSessionLocal() as db:
        # Find a project and authorized user with stories, tasks, statuses, and sprint
        stmt = (
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
            .options(joinedload(User.role))
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if not row:
            pytest.skip("No suitable project and user found in database")

        project, user = row[0], row[1]
        project_id = str(project.id)
        org_id = str(project.organization_id)

        # Find a story in this project with tasks
        story_stmt = (
            select(UserStory)
            .join(Task, Task.user_story_id == UserStory.id)
            .where(
                UserStory.project_id == project.id,
                UserStory.deleted_at.is_(None),
                Task.deleted_at.is_(None),
            )
            .limit(1)
        )
        story = (await db.execute(story_stmt)).scalars().first()
        story_id = str(story.id) if story else None

        # Find status of a task belonging to this story
        task_stmt = select(Task).where(
            Task.user_story_id == story.id,
            Task.deleted_at.is_(None),
        ).limit(1)
        sample_task = (await db.execute(task_stmt)).scalars().first()
        status_id = str(sample_task.status_id) if sample_task else None

        # Find status in this project
        status_stmt = select(CustomStatus).where(
            CustomStatus.project_id == project.id,
            CustomStatus.deleted_at.is_(None)
        ).limit(1)
        custom_status = (await db.execute(status_stmt)).scalars().first()
        if not status_id and custom_status:
            status_id = str(custom_status.id)

        # Find a sprint
        sprint_stmt = select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.deleted_at.is_(None)
        ).limit(1)
        sprint = (await db.execute(sprint_stmt)).scalars().first()
        sprint_id = str(sprint.id) if sprint else None

        # Ensure we have at least one storyless task in sprint for testing
        storyless_stmt = select(Task).where(
            Task.project_id == project.id,
            Task.user_story_id.is_(None),
            Task.sprint_id.isnot(None),
            Task.deleted_at.is_(None),
        ).limit(1)
        storyless_task = (await db.execute(storyless_stmt)).scalars().first()
        if not storyless_task and sprint_id:
            # Create a storyless task in sprint
            storyless_task = Task(
                project_id=project.id,
                sprint_id=sprint.id,
                user_story_id=None,
                key=f"TEST-{int(time.time()) % 10000}",
                sequence_number=9999,
                title="Test Storyless Task",
                status_id=custom_status.id,
                status="todo",
                reporter_id=user.id,
            )
            db.add(storyless_task)
            await db.commit()

        # Find a user in another organization
        foreign_user_stmt = select(User).where(
            User.organization_id != project.organization_id,
            User.deleted_at.is_(None),
        ).limit(1)
        foreign_user = (await db.execute(foreign_user_stmt)).scalars().first()

        # Generate tokens
        token, _ = create_jwt(
            role="org_admin",
            user_id=str(user.id),
            organization_id=org_id,
            platform="mobile",
        )
        foreign_token, _ = create_jwt(
            role="org_admin",
            user_id=str(foreign_user.id) if foreign_user else str(user.id),
            organization_id=str(foreign_user.organization_id) if foreign_user else "00000000-0000-0000-0000-000000000000",
            platform="mobile",
        )

        return {
            "project_id": project_id,
            "org_id": org_id,
            "user_id": str(user.id),
            "story_id": story_id,
            "status_id": status_id,
            "sprint_id": sprint_id,
            "token": token,
            "foreign_token": foreign_token,
        }


@pytest.mark.anyio
async def test_mode_a_story_list():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    token = ctx["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Initial 5 stories
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?page=1&page_size=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["status_code"] == 200
        assert isinstance(body["message"], str)
        assert "mode" not in body
        assert "data" in body
        assert "meta" in body
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 5

        data = body["data"]
        assert isinstance(data, list)
        assert len(data) <= 5
        if data:
            first = data[0]
            assert "id" in first
            assert "project_id" in first
            assert first["project_id"] == project_id
            assert "title" in first
            assert "total_tasks" in first
            assert isinstance(first["total_tasks"], int)
            assert "completed_tasks" in first
            assert "progress" in first
            assert "priority" in first
            assert "is_favourite" in first
            assert isinstance(first["is_favourite"], bool)
            assert "status_color" in first
            assert "story_points" in first
            assert isinstance(first["story_points"], int)
            assert "assignee" in first
            assert "reporter" in first
            assert "due_date" in first
            # Verify top-level tasks array is NOT returned in BoardStorySummary (it is inside statuses)
            assert "tasks" not in first
            assert "statuses" in first
            assert isinstance(first["statuses"], list)
            if first["statuses"]:
                st0 = first["statuses"][0]
                assert "status_id" in st0
                assert "status_name" in st0
                assert "task_count" in st0
                assert "tasks" in st0
                assert "meta" in st0
                assert len(st0["tasks"]) <= 5
                assert st0["meta"]["page"] == 1
                assert st0["meta"]["page_size"] == 5
                assert "has_next" in st0["meta"]

        # 2. Next page
        resp_p2 = await client.get(
            f"/api/v1/projects/{project_id}/board?page=2&page_size=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_p2.status_code == 200
        body_p2 = resp_p2.json()
        assert body_p2["success"] is True
        assert body_p2["status_code"] == 200
        assert isinstance(body_p2["data"], list)
        assert body_p2["meta"]["page"] == 2

        # 3. Test tasks_per_status query parameter
        resp_tps = await client.get(
            f"/api/v1/projects/{project_id}/board?page=1&page_size=2&tasks_per_status=2",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_tps.status_code == 200
        body_tps = resp_tps.json()
        assert body_tps["success"] is True
        if body_tps["data"]:
            story0 = body_tps["data"][0]
            if story0["statuses"]:
                for st in story0["statuses"]:
                    assert len(st["tasks"]) <= 2
                    assert st["meta"]["page_size"] == 2


@pytest.mark.anyio
async def test_mode_b_story_expansion():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    story_id = ctx["story_id"]
    token = ctx["token"]
    if not story_id:
        pytest.skip("No story available for expansion test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test primary user_story_id query parameter
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?user_story_id={story_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text

        # Verify backward-compatible story_id alias also succeeds
        resp_alias = await client.get(
            f"/api/v1/projects/{project_id}/board?story_id={story_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_alias.status_code == 200, resp_alias.text
        body = resp.json()
        assert body["success"] is True
        assert body["status_code"] == 200
        assert isinstance(body["message"], str)
        assert "mode" not in body
        assert "data" in body
        assert "meta" in body
        assert body["meta"] is not None
        assert body["meta"]["page"] == 1
        assert body["meta"]["total"] == 1
        assert body["meta"]["has_next"] is False

        data = body["data"]
        assert "mode" not in data
        assert "story" not in data

        # 1. Story details directly on data
        assert data["id"] == story_id
        assert "project_id" in data
        assert data["project_id"] == project_id
        assert "priority" in data
        assert "is_favourite" in data
        assert isinstance(data["is_favourite"], bool)
        assert "status_color" in data
        assert "story_points" in data
        assert isinstance(data["story_points"], int)
        assert "title" in data
        assert "total_tasks" in data
        assert isinstance(data["total_tasks"], int)
        assert "assignee" in data
        assert "reporter" in data
        assert "due_date" in data

        # 2. Statuses list directly on data
        assert "statuses" in data
        statuses = data["statuses"]
        assert isinstance(statuses, list)
        assert len(statuses) > 0

        for status_group in statuses:
            assert "status_id" in status_group
            assert "status_name" in status_group
            assert "task_count" in status_group
            assert "meta" in status_group
            assert status_group["meta"]["page"] == 1
            assert status_group["meta"]["page_size"] == 5
            assert "has_next" in status_group["meta"]
            assert "total" in status_group["meta"]
            assert "page" not in status_group
            assert "page_size" not in status_group
            assert "has_next" not in status_group
            assert "tasks" in status_group
            # Maximum 5 tasks per status on initial story expansion
            assert len(status_group["tasks"]) <= 5
            for task in status_group["tasks"]:
                assert "project_id" in task
                assert "priority" in task
                assert "is_favourite" in task
                assert isinstance(task["is_favourite"], bool)
                assert "status_color" in task
                assert "story_points" in task

        # Test tasks_per_status query parameter in story expansion
        resp_tps1 = await client.get(
            f"/api/v1/projects/{project_id}/board?user_story_id={story_id}&tasks_per_status=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_tps1.status_code == 200
        data_tps1 = resp_tps1.json()["data"]
        for st in data_tps1["statuses"]:
            assert len(st["tasks"]) <= 1
            assert st["meta"]["page_size"] == 1


@pytest.mark.anyio
async def test_mode_c_status_tasks_pagination():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    story_id = ctx["story_id"]
    status_id = ctx["status_id"]
    token = ctx["token"]
    if not story_id or not status_id:
        pytest.skip("Missing story or status for Mode C test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?user_story_id={story_id}&status_id={status_id}&page=1&page_size=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["status_code"] == 200
        assert isinstance(body["message"], str)
        assert "mode" not in body
        assert "data" in body
        assert "meta" in body
        assert body["meta"] is not None
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 5
        assert "total" in body["meta"]
        assert "has_next" in body["meta"]

        data = body["data"]
        assert isinstance(data, dict)
        assert data["id"] == story_id
        assert data["project_id"] == project_id
        assert "priority" in data
        assert "is_favourite" in data
        assert isinstance(data["is_favourite"], bool)
        assert "status_color" in data
        assert "story_points" in data
        assert "title" in data
        assert "total_tasks" in data
        assert isinstance(data["total_tasks"], int)
        assert "assignee" in data
        assert "reporter" in data

        assert "statuses" in data
        statuses = data["statuses"]
        assert isinstance(statuses, list)
        assert len(statuses) == 1

        status_group = statuses[0]
        assert status_group["status_id"] == status_id
        assert "status_name" in status_group
        assert "color" in status_group
        assert "meta" in status_group
        assert status_group["meta"]["page"] == 1
        assert status_group["meta"]["page_size"] == 5
        assert "has_next" in status_group["meta"]
        assert "total" in status_group["meta"]

        # All returned tasks must belong to requested story and status
        tasks = status_group["tasks"]
        assert isinstance(tasks, list)
        for task in tasks:
            assert task["user_story_id"] == story_id
            assert task["status_id"] == status_id
            assert "project_id" in task
            assert "priority" in task
            assert "is_favourite" in task
            assert isinstance(task["is_favourite"], bool)
            assert "status_color" in task
            assert "story_points" in task


@pytest.mark.anyio
async def test_mode_d_storyless_tasks():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    token = ctx["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Default storyless returns grouped by status columns (BoardStorySummary)
        resp_grouped = await client.get(
            f"/api/v1/projects/{project_id}/board?storyless=true&tasks_per_status=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_grouped.status_code == 200, resp_grouped.text
        body_grouped = resp_grouped.json()
        assert body_grouped["success"] is True
        assert body_grouped["data"]["id"] == "storyless"
        assert "statuses" in body_grouped["data"]
        for st in body_grouped["data"]["statuses"]:
            assert len(st["tasks"]) <= 1
            assert st["meta"]["page_size"] == 1

        # 2. group_by_status=false returns flat list of storyless tasks
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?storyless=true&group_by_status=false&page=1&page_size=5",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["status_code"] == 200
        assert isinstance(body["message"], str)
        assert "mode" not in body
        assert "data" in body
        assert "meta" in body
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 5

        data = body["data"]
        assert isinstance(data, list)
        for task in data:
            assert task.get("user_story_id") is None
            assert task["sprint_id"] is not None
            assert "project_id" in task
            assert "priority" in task
            assert "is_favourite" in task
            assert isinstance(task["is_favourite"], bool)
            assert "status_color" in task
            assert "story_points" in task


@pytest.mark.anyio
async def test_invalid_parameter_combinations():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    story_id = ctx["story_id"]
    status_id = ctx["status_id"]
    token = ctx["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. storyless=true + user_story_id
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?storyless=true&user_story_id={story_id}",
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BAD_REQUEST"

        # 2. page < 1
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?page=0",
            headers=headers,
        )
        assert resp.status_code == 400

        # 5. page_size <= 0
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?page_size=0",
            headers=headers,
        )
        assert resp.status_code == 400

        # 6. page_size > 50 (excessive page size)
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?page_size=100",
            headers=headers,
        )
        assert resp.status_code == 400

        # 7. Invalid project UUID
        resp = await client.get(
            "/api/v1/projects/not-a-uuid/board",
            headers=headers,
        )
        assert resp.status_code == 400

        # 8. Non-existent user_story_id
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?user_story_id=00000000-0000-0000-0000-000000000000",
            headers=headers,
        )
        assert resp.status_code == 404

        # 9. Non-existent status_id in Mode C
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?user_story_id={story_id}&status_id=00000000-0000-0000-0000-000000000000",
            headers=headers,
        )
        assert resp.status_code == 404


@pytest.mark.anyio
async def test_authorization_and_isolation():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    foreign_token = ctx["foreign_token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing authentication -> 401
        resp = await client.get(f"/api/v1/projects/{project_id}/board")
        assert resp.status_code == 401

        # 2. Invalid token -> 401
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board",
            headers={"Authorization": "Bearer invalid.token.value"},
        )
        assert resp.status_code == 401

        # 3. User from another organization -> 403
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board",
            headers={"Authorization": f"Bearer {foreign_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.anyio
async def test_n_plus_one_prevention():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    story_id = ctx["story_id"]
    status_id = ctx["status_id"]
    token = ctx["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Test Mode A with page_size=1 vs page_size=5 vs page_size=10
        with QueryCounter(engine) as q1:
            resp1 = await client.get(f"/api/v1/projects/{project_id}/board?page=1&page_size=1", headers=headers)
            assert resp1.status_code == 200

        with QueryCounter(engine) as q5:
            resp5 = await client.get(f"/api/v1/projects/{project_id}/board?page=1&page_size=5", headers=headers)
            assert resp5.status_code == 200

        with QueryCounter(engine) as q10:
            resp10 = await client.get(f"/api/v1/projects/{project_id}/board?page=1&page_size=10", headers=headers)
            assert resp10.status_code == 200

        print(f"\n[N+1 Check] Mode A Query Counts: 1 story -> {q1.count} queries, 5 stories -> {q5.count} queries, 10 stories -> {q10.count} queries")
        # Query count MUST be constant and small (typically 3-5 queries total: auth + count + stories + task_counts)
        assert q5.count == q1.count or abs(q5.count - q1.count) <= 1, f"Query count grew with stories! q1={q1.count}, q5={q5.count}"
        assert q10.count == q5.count or abs(q10.count - q5.count) <= 1, f"Query count grew with stories! q5={q5.count}, q10={q10.count}"

        # 2. Test Mode B (Story Expansion)
        with QueryCounter(engine) as q_exp:
            resp_exp = await client.get(f"/api/v1/projects/{project_id}/board?user_story_id={story_id}", headers=headers)
            assert resp_exp.status_code == 200
        print(f"[N+1 Check] Mode B (Story Expansion) Query Count: {q_exp.count} queries")
        assert q_exp.count <= 12, f"Mode B executed too many queries ({q_exp.count})!"

        # 3. Test Mode C (Status Pagination)
        with QueryCounter(engine) as q_c:
            resp_c = await client.get(f"/api/v1/projects/{project_id}/board?user_story_id={story_id}&status_id={status_id}&page=1&page_size=5", headers=headers)
            assert resp_c.status_code == 200
        print(f"[N+1 Check] Mode C (Status Pagination) Query Count: {q_c.count} queries")
        assert q_c.count <= 12, f"Mode C executed too many queries ({q_c.count})!"

        # 4. Test Mode D (Story-less Tasks)
        with QueryCounter(engine) as q_d:
            resp_d = await client.get(f"/api/v1/projects/{project_id}/board?storyless=true&page=1&page_size=5", headers=headers)
            assert resp_d.status_code == 200
        print(f"[N+1 Check] Mode D (Storyless Tasks) Query Count: {q_d.count} queries")
        assert q_d.count <= 12, f"Mode D executed too many queries ({q_d.count})!"


@pytest.mark.anyio
async def test_board_status_id_filter():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    status_id = ctx["status_id"]
    token = ctx["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?status_id={status_id}",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        for story in body["data"]:
            for st in story.get("statuses", []):
                if st["status_id"] != status_id:
                    assert st["task_count"] == 0
                    assert len(st["tasks"]) == 0


@pytest.mark.anyio
async def test_board_storyless_grouped_by_status():
    ctx = await get_test_context()
    project_id = ctx["project_id"]
    status_id = ctx["status_id"]
    token = ctx["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. storyless=true&group_by_status=true returns story-like summary with statuses
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?storyless=true&group_by_status=true",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["id"] == "storyless"
        assert "statuses" in data
        assert isinstance(data["statuses"], list)
        for st in data["statuses"]:
            assert "status_id" in st
            assert "status_name" in st
            assert "task_count" in st
            assert "tasks" in st
            for task in st["tasks"]:
                assert task.get("user_story_id") is None

        # 2. user_story_id=storyless
        resp_sl = await client.get(
            f"/api/v1/projects/{project_id}/board?user_story_id=storyless",
            headers=headers,
        )
        assert resp_sl.status_code == 200, resp_sl.text
        data_sl = resp_sl.json()["data"]
        assert data_sl["id"] == "storyless"
        assert "statuses" in data_sl

        # 3. user_story_id=storyless&status_id={status_id} (column pagination)
        resp_c = await client.get(
            f"/api/v1/projects/{project_id}/board?user_story_id=storyless&status_id={status_id}",
            headers=headers,
        )
        assert resp_c.status_code == 200, resp_c.text
        data_c = resp_c.json()["data"]
        assert data_c["id"] == "storyless"
        assert len(data_c["statuses"]) == 1
        assert data_c["statuses"][0]["status_id"] == status_id

        # 4. storyless_tasks=true&tasks_per_status=1 query parameter
        resp_st = await client.get(
            f"/api/v1/projects/{project_id}/board?storyless_tasks=true&tasks_per_status=1",
            headers=headers,
        )
        assert resp_st.status_code == 200, resp_st.text
        data_st = resp_st.json()["data"]
        assert data_st["id"] == "storyless"
        assert data_st["total_tasks"] > 0
        assert "statuses" in data_st
        for st in data_st["statuses"]:
            assert len(st["tasks"]) <= 1
            assert st["meta"]["page_size"] == 1

        # 5. include_storyless=true on board
        resp_inc = await client.get(
            f"/api/v1/projects/{project_id}/board?include_storyless=true",
            headers=headers,
        )
        assert resp_inc.status_code == 200, resp_inc.text
        body_inc = resp_inc.json()
        assert isinstance(body_inc["data"], list)
        ids = [row["id"] for row in body_inc["data"]]
        if any(row["id"] == "storyless" for row in body_inc["data"]):
            storyless_row = next(r for r in body_inc["data"] if r["id"] == "storyless")
            assert "statuses" in storyless_row

