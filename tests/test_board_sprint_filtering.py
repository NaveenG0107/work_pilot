import asyncio
import uuid
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

import src.main
from src.main import app
from src.database import engine, get_db
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


@pytest.fixture
async def board_tx_env():
    """
    Sets up an isolated, transactional test environment.
    All created test data (projects, sprints, stories, tasks) exists ONLY
    within this transaction and is unconditionally rolled back at teardown.
    The underlying database is NEVER affected.
    """
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)

        async def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db

        try:
            # 1. Fetch existing project, user (org_admin), and active sprint
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
            row = (await session.execute(stmt)).first()
            if not row:
                pytest.skip("No suitable project and user found in database")

            project, user = row[0], row[1]
            project_id = str(project.id)
            org_id = str(project.organization_id)
            user_id = str(user.id)

            # Find statuses for tasks in this project
            status_stmt = select(CustomStatus).where(
                CustomStatus.project_id == project.id,
                CustomStatus.deleted_at.is_(None),
            )
            statuses = (await session.execute(status_stmt)).scalars().all()
            done_status = next((s for s in statuses if s.is_final), statuses[0])
            todo_status = next((s for s in statuses if not s.is_final), statuses[0])
            status_id = str(todo_status.id)
            done_status_id = str(done_status.id)

            # Sprints in main project
            # Active sprint
            active_sprint_stmt = select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status == "active",
                Sprint.deleted_at.is_(None),
            ).order_by(Sprint.created_at.desc()).limit(1)
            active_sprint = (await session.execute(active_sprint_stmt)).scalars().first()
            if not active_sprint:
                active_sprint = Sprint(
                    project_id=project.id,
                    name=f"Active Sprint Tx {uuid.uuid4().hex[:6]}",
                    status="active",
                    created_by_id=user.id,
                )
                session.add(active_sprint)
                await session.flush()

            active_sprint_id = str(active_sprint.id)

            # Query existing active stories or add a dedicated active story
            story_active = UserStory(
                project_id=project.id,
                sprint_id=active_sprint.id,
                key=f"US-ACT-{uuid.uuid4().hex[:8]}",
                title="Active Sprint Story Alpha",
                status_id=status_id,
                reporter_id=user.id,
            )
            session.add(story_active)
            await session.flush()

            # Add tasks for active story (1 todo, 1 done)
            task_active_todo = Task(
                project_id=project.id,
                sprint_id=active_sprint.id,
                user_story_id=story_active.id,
                key=f"TSK-AT1-{uuid.uuid4().hex[:8]}",
                title="Active Task Todo",
                status_id=todo_status.id,
                status="todo",
                reporter_id=user.id,
            )
            task_active_done = Task(
                project_id=project.id,
                sprint_id=active_sprint.id,
                user_story_id=story_active.id,
                key=f"TSK-AT2-{uuid.uuid4().hex[:8]}",
                title="Active Task Done",
                status_id=done_status.id,
                status="done",
                reporter_id=user.id,
            )
            session.add_all([task_active_todo, task_active_done])

            # Unassigned story (sprint_id IS NULL)
            story_unassigned = UserStory(
                project_id=project.id,
                sprint_id=None,
                key=f"US-UN-{uuid.uuid4().hex[:8]}",
                title="Backlog Unassigned Story",
                status_id=status_id,
                reporter_id=user.id,
            )
            session.add(story_unassigned)

            # Completed sprint and historical story
            sprint_completed = Sprint(
                project_id=project.id,
                name=f"Completed Sprint Tx {uuid.uuid4().hex[:6]}",
                status="completed",
                created_by_id=user.id,
            )
            session.add(sprint_completed)
            await session.flush()

            story_completed = UserStory(
                project_id=project.id,
                sprint_id=sprint_completed.id,
                key=f"US-CMP-{uuid.uuid4().hex[:8]}",
                title="Completed Sprint Story Beta",
                status_id=status_id,
                reporter_id=user.id,
            )
            session.add(story_completed)
            await session.flush()

            task_comp_done = Task(
                project_id=project.id,
                sprint_id=sprint_completed.id,
                user_story_id=story_completed.id,
                key=f"TSK-CD1-{uuid.uuid4().hex[:8]}",
                title="Completed Sprint Task Done",
                status_id=done_status.id,
                status="done",
                reporter_id=user.id,
            )
            session.add(task_comp_done)

            # Future/planned sprint and story
            sprint_future = Sprint(
                project_id=project.id,
                name=f"Future Sprint Tx {uuid.uuid4().hex[:6]}",
                status="planned",
                created_by_id=user.id,
            )
            session.add(sprint_future)
            await session.flush()

            story_future = UserStory(
                project_id=project.id,
                sprint_id=sprint_future.id,
                key=f"US-FUT-{uuid.uuid4().hex[:8]}",
                title="Future Sprint Story Gamma",
                status_id=status_id,
                reporter_id=user.id,
            )
            session.add(story_future)

            # Cross-project sprint in another project
            other_proj_stmt = select(Project).where(
                Project.organization_id == project.organization_id,
                Project.id != project.id,
                Project.deleted_at.is_(None),
            ).limit(1)
            other_proj = (await session.execute(other_proj_stmt)).scalars().first()
            if not other_proj:
                other_proj = Project(
                    organization_id=project.organization_id,
                    name=f"Other Proj Tx {uuid.uuid4().hex[:6]}",
                    key=f"OP{uuid.uuid4().hex[:4]}",
                    slug=f"op-{uuid.uuid4().hex[:6]}",
                    status="active",
                    created_by=user.id,
                )
                session.add(other_proj)
                await session.flush()

            sprint_cross_proj = Sprint(
                project_id=other_proj.id,
                name=f"Cross Proj Sprint {uuid.uuid4().hex[:6]}",
                status="active",
                created_by_id=user.id,
            )
            session.add(sprint_cross_proj)
            await session.flush()

            # Cross-organization reference
            foreign_user_stmt = select(User).where(
                User.organization_id != project.organization_id,
                User.deleted_at.is_(None),
            ).limit(1)
            foreign_user = (await session.execute(foreign_user_stmt)).scalars().first()

            foreign_proj_stmt = select(Project).where(
                Project.organization_id != project.organization_id,
                Project.deleted_at.is_(None),
            ).limit(1)
            foreign_proj = (await session.execute(foreign_proj_stmt)).scalars().first()

            sprint_cross_org = None
            if foreign_proj:
                foreign_sprint_stmt = select(Sprint).where(
                    Sprint.project_id == foreign_proj.id,
                    Sprint.deleted_at.is_(None),
                ).limit(1)
                sprint_cross_org = (await session.execute(foreign_sprint_stmt)).scalars().first()
                if not sprint_cross_org and foreign_user:
                    sprint_cross_org = Sprint(
                        project_id=foreign_proj.id,
                        name=f"Foreign Sprint Tx {uuid.uuid4().hex[:6]}",
                        status="active",
                        created_by_id=foreign_user.id,
                    )
                    session.add(sprint_cross_org)
                    await session.flush()

            # Project with NO active sprint
            no_active_proj = Project(
                organization_id=project.organization_id,
                name=f"No Active Proj Tx {uuid.uuid4().hex[:6]}",
                key=f"NA{uuid.uuid4().hex[:4]}",
                slug=f"na-{uuid.uuid4().hex[:6]}",
                status="active",
                created_by=user.id,
            )
            session.add(no_active_proj)
            await session.flush()

            no_active_comp_sprint = Sprint(
                project_id=no_active_proj.id,
                name=f"Planned Sprint Tx {uuid.uuid4().hex[:6]}",
                status="planned",
                created_by_id=user.id,
            )
            session.add(no_active_comp_sprint)
            await session.flush()

            no_active_story = UserStory(
                project_id=no_active_proj.id,
                sprint_id=no_active_comp_sprint.id,
                key=f"US-NA1-{uuid.uuid4().hex[:8]}",
                title="Story in Inactive Sprint",
                status_id=status_id,
                reporter_id=user.id,
            )
            no_active_unassigned = UserStory(
                project_id=no_active_proj.id,
                sprint_id=None,
                key=f"US-NA2-{uuid.uuid4().hex[:8]}",
                title="Unassigned Story in No-Active Proj",
                status_id=status_id,
                reporter_id=user.id,
            )
            session.add_all([no_active_story, no_active_unassigned])

            # Storyless task in active sprint
            storyless_task = Task(
                project_id=project.id,
                sprint_id=active_sprint.id,
                user_story_id=None,
                key=f"TSK-SL-{uuid.uuid4().hex[:8]}",
                title="Storyless Task in Active Sprint",
                status_id=todo_status.id,
                status="todo",
                reporter_id=user.id,
            )
            session.add(storyless_task)

            await session.flush()

            # Tokens
            token, _ = create_jwt(
                role="org_admin",
                user_id=user_id,
                organization_id=org_id,
                platform="web",
            )
            foreign_token, _ = create_jwt(
                role="org_admin",
                user_id=str(foreign_user.id) if foreign_user else user_id,
                organization_id=str(foreign_user.organization_id) if foreign_user else "00000000-0000-0000-0000-000000000000",
                platform="web",
            )

            data = {
                "project_id": project_id,
                "org_id": org_id,
                "user_id": user_id,
                "token": token,
                "foreign_token": foreign_token,
                "active_sprint_id": active_sprint_id,
                "completed_sprint_id": str(sprint_completed.id),
                "future_sprint_id": str(sprint_future.id),
                "story_active_id": str(story_active.id),
                "completed_story_id": str(story_completed.id),
                "future_story_id": str(story_future.id),
                "unassigned_story_id": str(story_unassigned.id),
                "cross_project_sprint_id": str(sprint_cross_proj.id),
                "cross_org_sprint_id": str(sprint_cross_org.id) if sprint_cross_org else None,
                "no_active_project_id": str(no_active_proj.id),
                "status_id": status_id,
                "done_status_id": done_status_id,
            }

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, data

        finally:
            app.dependency_overrides.pop(get_db, None)
            await trans.rollback()


# ==============================================================================
# Scenario 1: Default board displays active AND completed sprint stories simultaneously
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_1_default_board_shows_active_and_completed_sprints(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    returned_story_ids = {s["id"] for s in data}

    # Must contain stories from BOTH active and completed sprints simultaneously
    assert env["story_active_id"] in returned_story_ids
    assert env["completed_story_id"] in returned_story_ids

    # Must NOT contain future (planned) or unassigned stories
    assert env["future_story_id"] not in returned_story_ids
    assert env["unassigned_story_id"] not in returned_story_ids

    # Active sprint stories must appear first before completed sprint stories
    active_idx = next(i for i, s in enumerate(data) if s["id"] == env["story_active_id"])
    comp_idx = next(i for i, s in enumerate(data) if s["id"] == env["completed_story_id"])
    assert active_idx < comp_idx, f"Active story (idx {active_idx}) should appear before completed story (idx {comp_idx})"

    # meta.sprint is populated
    assert "sprint" in body["meta"]
    assert body["meta"]["sprint"] is not None

    # meta.sprints has active sprint first
    assert "sprints" in body["meta"]
    assert len(body["meta"]["sprints"]) >= 1
    assert body["meta"]["sprints"][0]["status"] == "active"


# ==============================================================================
# Scenario 2: Unassigned stories (sprint_id IS NULL) are excluded from main board
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_2_unassigned_stories_excluded(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    for story in data:
        assert story["id"] != env["unassigned_story_id"]
        assert story["sprint_id"] is not None


# ==============================================================================
# Scenario 3: Future (planned) sprint stories excluded from default board
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_3_future_sprint_stories_excluded(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200
    story_ids = [s["id"] for s in resp.json()["data"]]
    assert env["future_story_id"] not in story_ids


# ==============================================================================
# Scenario 4: No active or completed sprints: return empty sprint-based story list
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_4_no_active_or_completed_sprints_empty_board(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['no_active_project_id']}/board",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["has_next"] is False


# ==============================================================================
# Scenario 6: Explicit active sprint selection: return only that sprint's stories
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_6_explicit_active_sprint(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['active_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    story_ids = {s["id"] for s in resp.json()["data"]}
    assert env["story_active_id"] in story_ids
    assert env["completed_story_id"] not in story_ids
    assert env["future_story_id"] not in story_ids
    assert env["unassigned_story_id"] not in story_ids
    for story in resp.json()["data"]:
        assert story["sprint_id"] == env["active_sprint_id"]


# ==============================================================================
# Scenario 7: Explicit completed sprint selection: return historical stories & tasks
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_7_explicit_completed_sprint(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['completed_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = body["data"]
    assert len(data) >= 1
    comp_story = next((s for s in data if s["id"] == env["completed_story_id"]), None)
    assert comp_story is not None
    assert comp_story["sprint_id"] == env["completed_sprint_id"]
    story_ids = {s["id"] for s in data}
    assert env["story_active_id"] not in story_ids
    assert env["future_story_id"] not in story_ids
    # Preserves actual task counts
    assert comp_story["total_tasks"] == 1
    assert comp_story["completed_tasks"] == 1
    assert comp_story["progress"] == 100.0


# ==============================================================================
# Scenario 7b: sprint_status=completed filter: return only completed sprint stories
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_7b_sprint_status_completed_filter(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_status=completed",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    story_ids = {s["id"] for s in data}
    assert env["completed_story_id"] in story_ids
    assert env["story_active_id"] not in story_ids
    assert env["future_story_id"] not in story_ids


# ==============================================================================
# Scenario 7c: sprint_status=active filter: return only active sprint stories
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_7c_sprint_status_active_filter(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_status=active",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    story_ids = {s["id"] for s in data}
    assert env["story_active_id"] in story_ids
    assert env["completed_story_id"] not in story_ids
    assert env["future_story_id"] not in story_ids


# ==============================================================================
# Scenario 8: Explicit sprint selection: stories from other sprints excluded
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_8_explicit_sprint_excludes_other_sprints(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['future_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    for story in data:
        assert story["sprint_id"] == env["future_sprint_id"]
        assert story["id"] != env["completed_story_id"]
        assert story["id"] != env["unassigned_story_id"]


# ==============================================================================
# Scenario 9: Invalid sprint ID: return expected API error (400 or 404)
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_9_invalid_sprint_id(board_tx_env):
    client, env = board_tx_env
    # Non-existent UUID
    non_existent_id = str(uuid.uuid4())
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={non_existent_id}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"

    # Malformed UUID
    resp_malformed = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id=invalid-uuid-123",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp_malformed.status_code in [400, 422]
    assert resp_malformed.json()["error"]["code"] in ["BAD_REQUEST", "VALIDATION_ERROR"]


# ==============================================================================
# Scenario 10: Cross-project sprint ID: access is rejected (404)
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_10_cross_project_sprint_rejected(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['cross_project_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"


# ==============================================================================
# Scenario 11: Cross-organization sprint ID: access is rejected (404)
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_11_cross_org_sprint_rejected(board_tx_env):
    client, env = board_tx_env
    if not env["cross_org_sprint_id"]:
        pytest.skip("No cross-org sprint available")

    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['cross_org_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"


# ==============================================================================
# Scenario 12: Pagination: total count and returned records match same sprint filter
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_12_pagination_consistency(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['active_sprint_id']}&page=1&page_size=2",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["page"] == 1
    assert body["meta"]["page_size"] == 2
    assert len(body["data"]) <= 2
    assert body["meta"]["total"] >= len(body["data"])


# ==============================================================================
# Scenario 13: Task aggregation: counts and progress match selected sprint's stories
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_13_task_aggregation(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['active_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    story_map = {s["id"]: s for s in data}
    assert env["story_active_id"] in story_map
    s_act = story_map[env["story_active_id"]]
    assert s_act["total_tasks"] == 2
    assert s_act["completed_tasks"] == 1
    assert s_act["progress"] == 50.0


# ==============================================================================
# Scenario 14: Status filtering: task status counts remain correct
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_14_status_filtering(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?user_story_id={env['story_active_id']}&status_id={env['status_id']}&sprint_id={env['active_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "statuses" in data
    assert len(data["statuses"]) == 1
    status_group = data["statuses"][0]
    assert status_group["status_id"] == env["status_id"]
    assert status_group["task_count"] == 1
    assert len(status_group["tasks"]) == 1
    assert status_group["tasks"][0]["status_id"] == env["status_id"]


# ==============================================================================
# Scenario 15: Search and other existing filters work together with sprint filter
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_15_search_and_filters_combined(board_tx_env):
    client, env = board_tx_env
    # Search for "Alpha" (matches story_active)
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['active_sprint_id']}&search=Alpha",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    story_ids = [s["id"] for s in resp.json()["data"]]
    assert env["story_active_id"] in story_ids

    # Search for non-existent text
    resp_empty = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['active_sprint_id']}&search=NonExistentUniqueTerm123XYZ",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp_empty.status_code == 200
    assert resp_empty.json()["data"] == []


# ==============================================================================
# Scenario 16: Story-less tasks: existing behavior remains unchanged
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_16_storyless_tasks(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?sprint_id={env['active_sprint_id']}&include_storyless=true",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    storyless_card = next((s for s in data if s["id"] == "storyless"), None)
    assert storyless_card is not None
    assert storyless_card["total_tasks"] >= 1

    # Request storyless tasks grouped by status (Mode C)
    resp_tasks = await client.get(
        f"/api/v1/projects/{env['project_id']}/board?storyless_tasks=true&sprint_id={env['active_sprint_id']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp_tasks.status_code == 200
    body = resp_tasks.json()
    assert body["data"]["type"] == "storyless"
    assert body["data"]["total_tasks"] >= 1


# ==============================================================================
# Scenario 17: Regression: existing board endpoint response structure remains unchanged
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_17_response_structure_contract(board_tx_env):
    client, env = board_tx_env
    resp = await client.get(
        f"/api/v1/projects/{env['project_id']}/board",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()

    # Contract assertions
    assert "success" in body and isinstance(body["success"], bool)
    assert "status_code" in body and body["status_code"] == 200
    assert "message" in body and isinstance(body["message"], str)
    assert "data" in body and isinstance(body["data"], list)
    assert "meta" in body and isinstance(body["meta"], dict)

    # Meta fields contract
    meta = body["meta"]
    for key in ["page", "page_size", "total", "has_next"]:
        assert key in meta, f"Missing key '{key}' in meta"

    # Story summary item contract
    if body["data"]:
        item = body["data"][0]
        expected_fields = [
            "id", "project_id", "sprint_id", "title", "description",
            "story_points", "total_tasks", "completed_tasks",
            "progress", "statuses"
        ]
        for field in expected_fields:
            assert field in item, f"Missing field '{field}' in story data"
