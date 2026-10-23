import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_search_query_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/search?q=test", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body


async def test_search_empty_query(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/search", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True


async def test_search_tenant_isolation(client: AsyncClient, app_context: dict):
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}

    resp = await client.get("/api/v1/search?q=Project", headers=headers_org_b)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    projects = body.get("data", {}).get("projects", [])
    for proj in projects:
        assert proj.get("organization_id") != app_context["org_a_id"]


async def test_search_unauthorized(client: AsyncClient, app_context: dict):
    resp = await client.get("/api/v1/search?q=test")
    assert resp.status_code == 401


async def test_search_members_response_fields(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=admin", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    members = body.get("data", {}).get("members", [])
    if members:
        member = members[0]
        assert "id" in member
        assert "name" in member
        assert "user_name" in member
        assert "email" in member
        assert "role" in member
        assert "color" in member
        assert isinstance(member.get("role"), str)


async def test_search_metadata_totals(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=test&page=1&page_size=10", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body.get("data", {})

    # Check meta fields
    assert "meta" in body
    meta = body["meta"]
    assert meta["page"] == 1
    assert meta["page_size"] == 10
    assert "has_next" in meta
    assert isinstance(meta["has_next"], bool)
    assert "total" in meta
    assert "total_tasks" in meta
    assert "total_projects" in meta
    assert "total_members" in meta
    assert "total_sprints" in meta
    assert "total_user_stories" in meta

    expected_sum = (
        meta["total_tasks"]
        + meta["total_user_stories"]
        + meta["total_projects"]
        + meta["total_members"]
        + meta["total_sprints"]
    )
    assert meta["total"] == expected_sum

    # Ensure data only contains lists
    assert "tasks" in data
    assert "user_stories" in data
    assert "projects" in data
    assert "members" in data
    assert "sprints" in data
    assert "tasks_total" not in data
    assert "meta" not in data


async def test_search_rich_text_html_tags_and_urls_not_matched(client: AsyncClient, app_context: dict):
    from src.database import AsyncSessionLocal
    from src.task.models import Task

    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    task_id = app_context["task_a_id"]

    rich_html = (
        '<p><img src="https://ptsrhvcviwjmeyghxcua.storage.supabase.co/secretimageurl.jpg" '
        'alt="diagram.png"></p><ul><li><u><i><b><strong>uniqueuniquetextsecret</strong></b></i></u></li></ul>'
    )
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        task.description = rich_html
        await session.commit()

    # 1. Searching for the plain text inside the tags should match
    resp = await client.get("/api/v1/search?q=uniqueuniquetextsecret", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    tasks = body.get("data", {}).get("tasks", [])
    task_ids = [t["id"] for t in tasks]
    assert task_id in task_ids

    # 2. Searching for URL, img tag, or attribute inside HTML should NOT match
    resp_tag = await client.get("/api/v1/search?q=secretimageurl", headers=headers)
    assert resp_tag.status_code == 200
    body_tag = resp_tag.json()
    tasks_tag = body_tag.get("data", {}).get("tasks", [])
    task_ids_tag = [t["id"] for t in tasks_tag]
    assert task_id not in task_ids_tag


async def test_search_unassigned_member_and_teammate_scoping(client: AsyncClient, app_context: dict):
    from sqlalchemy import select
    from src.database import AsyncSessionLocal
    from src.auth.models import User
    from src.organization.models import Role
    from src.utils.core import create_jwt
    import uuid

    org_id = app_context["org_a_id"]
    unassigned_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as session:
        role_stmt = select(Role).where(Role.organization_id == org_id, Role.name != "org_admin").limit(1)
        role = (await session.execute(role_stmt)).scalars().first()
        role_id = role.id if role else None

        unassigned_user = User(
            id=unassigned_id,
            organization_id=org_id,
            username=f"unassigned_{uuid.uuid4().hex[:6]}",
            email=f"unassigned_{uuid.uuid4().hex[:6]}@example.com",
            full_name="Unassigned User Test",
            role_id=role_id,
            is_active=True,
        )
        session.add(unassigned_user)
        await session.commit()

    token_unassigned, _ = create_jwt(
        role="member",
        user_id=unassigned_id,
        organization_id=org_id,
        platform="web",
    )
    headers = {"Authorization": f"Bearer {token_unassigned}"}

    # 1. Unassigned member searching tasks/projects/stories should see 0
    resp = await client.get("/api/v1/search?q=task", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body.get("data", {})
    assert len(data.get("tasks", [])) == 0
    assert len(data.get("projects", [])) == 0
    assert len(data.get("user_stories", [])) == 0
    assert len(data.get("sprints", [])) == 0

    # 2. Unassigned member searching members should only find self and org_admin, not other project members
    resp_members = await client.get("/api/v1/search?q=User", headers=headers)
    assert resp_members.status_code == 200
    members_data = resp_members.json().get("data", {}).get("members", [])
    for m in members_data:
        assert m["id"] == unassigned_id or m["role"] == "org_admin"





