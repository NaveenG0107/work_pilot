import re
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.database import AsyncSessionLocal
from src.project.models import Project, ProjectKeyHistory
from src.user_story.models import UserStory
from src.task.models import Task


@pytest.mark.anyio
async def test_project_creation_generates_key_from_slug(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    uid = uuid.uuid4().hex[:6]
    payload = {
        "name": f"Alpha Platform {uid}",
        "slug": f"alpha-platform-{uid}",
        "description": "Test description",
    }
    resp = await client.post("/api/v1/project/create", json=payload, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    project_id = body["data"]["project_id"]

    # Verify detail returns key generated from slug ("alpha..." -> starts with "ALP")
    detail_resp = await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert detail["key"].startswith("ALP")
    assert detail["project_key"].startswith("ALP")


@pytest.mark.anyio
async def test_project_name_and_slug_rename_preserves_key(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    uid = uuid.uuid4().hex[:6]
    # 1. Create project
    create_payload = {
        "name": f"Gamma Project {uid}",
        "slug": f"gamma-project-{uid}",
        "description": "Testing rename immutability",
    }
    create_resp = await client.post("/api/v1/project/create", json=create_payload, headers=headers)
    assert create_resp.status_code == 201
    project_id = create_resp.json()["data"]["project_id"]

    # Verify initial key
    detail_resp = await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)
    initial_key = detail_resp.json()["data"]["key"]
    assert initial_key.startswith("GAM")

    # 2. Update name and slug
    update_payload = {
        "name": f"Delta Project {uid}",
        "slug": f"delta-project-{uid}",
    }
    update_resp = await client.patch(f"/api/v1/project/update/{project_id}", json=update_payload, headers=headers)
    assert update_resp.status_code == 200

    # Key must NOT change
    detail_resp2 = await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)
    assert detail_resp2.json()["data"]["name"] == f"Delta Project {uid}"
    assert detail_resp2.json()["data"]["slug"] == f"delta-project-{uid}"
    assert detail_resp2.json()["data"]["key"] == initial_key


@pytest.mark.anyio
async def test_explicit_key_update_and_history(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    uid = uuid.uuid4().hex[:6]
    # 1. Create project
    payload = {
        "name": f"History Test {uid}",
        "slug": f"history-test-{uid}",
        "description": "Test history",
    }
    create_resp = await client.post("/api/v1/project/create", json=payload, headers=headers)
    assert create_resp.status_code == 201
    project_id = create_resp.json()["data"]["project_id"]

    detail1 = (await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)).json()["data"]
    initial_key = detail1["key"]
    assert initial_key.startswith("HIS")

    # 2. First key change: initial_key -> WP{uid[:2].upper()}
    key1 = f"WP{uid[:2].upper()}"
    up1 = await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": key1},
        headers=headers,
    )
    assert up1.status_code == 200

    detail2 = (await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)).json()["data"]
    assert detail2["key"] == key1

    # Check history in DB: initial_key should be in history
    async with AsyncSessionLocal() as session:
        hist = (
            await session.execute(
                select(ProjectKeyHistory.key).where(ProjectKeyHistory.project_id == project_id)
            )
        ).scalars().all()
        assert initial_key in hist

    # 3. No-op key change: update with key1 again
    up_noop = await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": key1},
        headers=headers,
    )
    assert up_noop.status_code == 200
    async with AsyncSessionLocal() as session:
        hist_count = len(
            (
                await session.execute(
                    select(ProjectKeyHistory.id).where(ProjectKeyHistory.project_id == project_id)
                )
            ).scalars().all()
        )
        assert hist_count == 1  # No duplicate history created

    # 4. Second key change: key1 -> EN{uid[:2].upper()}
    key2 = f"EN{uid[:2].upper()}"
    up2 = await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": key2},
        headers=headers,
    )
    assert up2.status_code == 200

    detail3 = (await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)).json()["data"]
    assert detail3["key"] == key2

    # Check history: both initial_key and key1 should be preserved
    async with AsyncSessionLocal() as session:
        hist_keys = (
            await session.execute(
                select(ProjectKeyHistory.key).where(ProjectKeyHistory.project_id == project_id)
            )
        ).scalars().all()
        assert initial_key in hist_keys
        assert key1 in hist_keys


@pytest.mark.anyio
async def test_project_key_validation_and_conflicts(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    uid = uuid.uuid4().hex[:6]

    # 1. Invalid key (spaces / symbols)
    r_invalid = await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": "IN VALID!"},
        headers=headers,
    )
    assert r_invalid.status_code in (400, 422)

    # 2. Empty key
    r_empty = await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": "  "},
        headers=headers,
    )
    assert r_empty.status_code in (400, 422)

    # 3. Duplicate key conflict within same organization
    uniq_key = f"U{uid[:4].upper()}"
    p1 = await client.post(
        "/api/v1/project/create",
        json={"name": f"Unique One {uid}", "slug": f"unique-one-{uid}", "key": uniq_key},
        headers=headers,
    )
    assert p1.status_code == 201

    # Try creating project 2 with same key in same org
    p2 = await client.post(
        "/api/v1/project/create",
        json={"name": f"Unique Two {uid}", "slug": f"unique-two-{uid}", "key": uniq_key},
        headers=headers,
    )
    assert p2.status_code == 409


@pytest.mark.anyio
async def test_unauthorized_user_cannot_change_key(client: AsyncClient, app_context: dict):
    # Member without project modify permission
    member_headers = {"Authorization": f"Bearer {app_context['token_org_a_member']}"}
    project_id = app_context["project_a_id"]

    resp = await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": "HACK"},
        headers=member_headers,
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_task_key_generation_and_continuous_sequence(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    uid = uuid.uuid4().hex[:6]
    key_prefix1 = f"S1{uid[:3].upper()}"
    key_prefix2 = f"S2{uid[:3].upper()}"

    # 1. Create project with key
    p = await client.post(
        "/api/v1/project/create",
        json={"name": f"Sequence Test {uid}", "slug": f"seq-test-{uid}", "key": key_prefix1},
        headers=headers,
    )
    assert p.status_code == 201
    project_id = p.json()["data"]["project_id"]

    # 2. Create task 1
    t1_resp = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "First Task in S1", "type": "task", "priority": "medium"},
        headers=headers,
    )
    assert t1_resp.status_code == 201
    t1_id = t1_resp.json()["data"]["task_id"]

    t1_get = await client.get(f"/api/v1/projects/{project_id}/tasks/{t1_id}", headers=headers)
    assert t1_get.status_code == 200
    t1_data = t1_get.json()["data"]
    assert t1_data["key"].startswith(f"{key_prefix1}-")
    seq1 = int(t1_data["key"].split("-")[-1])

    # 3. Create task 2
    t2_resp = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Second Task in S1", "type": "task", "priority": "medium"},
        headers=headers,
    )
    assert t2_resp.status_code == 201
    t2_id = t2_resp.json()["data"]["task_id"]

    t2_get = await client.get(f"/api/v1/projects/{project_id}/tasks/{t2_id}", headers=headers)
    assert t2_get.status_code == 200
    t2_data = t2_get.json()["data"]
    assert t2_data["key"] == f"{key_prefix1}-{seq1 + 1}"

    # 4. Change project key to key_prefix2
    up = await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": key_prefix2},
        headers=headers,
    )
    assert up.status_code == 200

    # 5. Create task 3: must use key_prefix2 prefix and continuous sequence (seq1 + 2)
    t3_resp = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Third Task after key change", "type": "task", "priority": "medium"},
        headers=headers,
    )
    assert t3_resp.status_code == 201
    t3_id = t3_resp.json()["data"]["task_id"]

    t3_get = await client.get(f"/api/v1/projects/{project_id}/tasks/{t3_id}", headers=headers)
    assert t3_get.status_code == 200
    t3_data = t3_get.json()["data"]
    assert t3_data["key"] == f"{key_prefix2}-{seq1 + 2}"

    # 6. Verify existing task keys were NOT modified
    fetch1 = await client.get(f"/api/v1/projects/{project_id}/tasks/{t1_id}", headers=headers)
    assert fetch1.status_code == 200
    assert fetch1.json()["data"]["key"] == f"{key_prefix1}-{seq1}"


@pytest.mark.anyio
async def test_task_lookup_by_current_and_historical_keys(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    uid = uuid.uuid4().hex[:6]
    key_a = f"LA{uid[:3].upper()}"
    key_b = f"LB{uid[:3].upper()}"

    # 1. Create project with key_a
    p = await client.post(
        "/api/v1/project/create",
        json={"name": f"Lookup Test {uid}", "slug": f"lookup-test-{uid}", "key": key_a},
        headers=headers,
    )
    assert p.status_code == 201
    project_id = p.json()["data"]["project_id"]

    # Create Task 1: key will be key_a-seq1
    t1 = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Original Key Task", "type": "task", "priority": "medium"},
        headers=headers,
    )
    assert t1.status_code == 201
    t1_id = t1.json()["data"]["task_id"]

    t1_detail = (await client.get(f"/api/v1/projects/{project_id}/tasks/{t1_id}", headers=headers)).json()["data"]
    t1_key = t1_detail["key"]
    seq1 = int(t1_key.split("-")[-1])

    # 2. Change project key from key_a -> key_b
    await client.patch(
        f"/api/v1/project/update/{project_id}",
        json={"key": key_b},
        headers=headers,
    )

    # Create Task 2: key will be key_b-seq2
    t2 = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "New Key Task", "type": "task", "priority": "medium"},
        headers=headers,
    )
    assert t2.status_code == 201
    t2_id = t2.json()["data"]["task_id"]

    t2_detail = (await client.get(f"/api/v1/projects/{project_id}/tasks/{t2_id}", headers=headers)).json()["data"]
    t2_key = t2_detail["key"]
    seq2 = int(t2_key.split("-")[-1])

    # 3. Test lookups via GET /projects/{project_id}/tasks/{identifier}:
    # A) Original key resolves Task 1
    r_old = await client.get(f"/api/v1/projects/{project_id}/tasks/{t1_key}", headers=headers)
    assert r_old.status_code == 200
    assert r_old.json()["data"]["title"] == "Original Key Task"

    # B) New prefix with old sequence resolves Task 1
    r_new_prefix_old_seq = await client.get(f"/api/v1/projects/{project_id}/tasks/{key_b}-{seq1}", headers=headers)
    assert r_new_prefix_old_seq.status_code == 200
    assert r_new_prefix_old_seq.json()["data"]["title"] == "Original Key Task"

    # C) New task key resolves Task 2
    r_new = await client.get(f"/api/v1/projects/{project_id}/tasks/{t2_key}", headers=headers)
    assert r_new.status_code == 200
    assert r_new.json()["data"]["title"] == "New Key Task"

    # D) Old prefix with new sequence resolves Task 2
    r_old_prefix_new_seq = await client.get(f"/api/v1/projects/{project_id}/tasks/{key_a}-{seq2}", headers=headers)
    assert r_old_prefix_new_seq.status_code == 200
    assert r_old_prefix_new_seq.json()["data"]["title"] == "New Key Task"

    # 4. Test search compatibility:
    # Searching for historical key finds Task 1
    s_old = await client.get(f"/api/v1/projects/{project_id}/tasks?search={t1_key}", headers=headers)
    assert s_old.status_code == 200
    items = s_old.json()["data"]
    assert any(item["title"] == "Original Key Task" for item in items)

    # Searching for current key alias finds Task 1
    s_new_alias = await client.get(f"/api/v1/projects/{project_id}/tasks?search={key_b}-{seq1}", headers=headers)
    assert s_new_alias.status_code == 200
    items_alias = s_new_alias.json()["data"]
    assert any(item["title"] == "Original Key Task" for item in items_alias)

    # 5. Test work item endpoint:
    wi_old = await client.get(f"/api/v1/projects/{project_id}/work-items/task/{t1_key}", headers=headers)
    assert wi_old.status_code == 200
    assert wi_old.json()["data"]["title"] == "Original Key Task"

    wi_new_alias = await client.get(f"/api/v1/projects/{project_id}/work-items/task/{key_b}-{seq1}", headers=headers)
    assert wi_new_alias.status_code == 200
    assert wi_new_alias.json()["data"]["title"] == "Original Key Task"


@pytest.mark.anyio
async def test_kanban_board_excludes_null_sprint_user_stories(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    sprint_id = app_context["sprint_a_id"]
    uid = uuid.uuid4().hex[:6]

    # 1. Create Story 1 WITH sprint
    story1_resp = await client.post(
        f"/api/v1/projects/{project_id}/user-stories",
        json={
            "title": f"Story Assigned To Sprint {uid}",
            "priority": "medium",
            "sprint_id": sprint_id,
        },
        headers=headers,
    )
    assert story1_resp.status_code == 201
    story1_id = story1_resp.json()["data"]["id"]

    # 2. Create Story 2 WITHOUT sprint (sprint_id is NULL)
    story2_resp = await client.post(
        f"/api/v1/projects/{project_id}/user-stories",
        json={
            "title": f"Story Without Sprint {uid}",
            "priority": "medium",
            "sprint_id": None,
        },
        headers=headers,
    )
    assert story2_resp.status_code == 201
    story2_id = story2_resp.json()["data"]["id"]

    # 3. Fetch Kanban board without sprint query filter (should return stories with sprint_id IS NOT NULL)
    board_resp = await client.get(f"/api/v1/projects/{project_id}/board?page_size=50", headers=headers)
    assert board_resp.status_code == 200
    returned_stories = board_resp.json()["data"]
    assert isinstance(returned_stories, list)

    returned_story_ids = [s["id"] for s in returned_stories]

    # Story 1 (with sprint) MUST be present
    assert story1_id in returned_story_ids
    # Story 2 (sprint_id = NULL) MUST be EXCLUDED
    assert story2_id not in returned_story_ids

    # All returned stories on board MUST have a sprint_id
    for s in returned_stories:
        assert s.get("sprint_id") is not None
