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

    # Verify all 5 categories are present as lists in data
    data = body["data"]
    for cat in ["tasks", "user_stories", "projects", "members", "sprints"]:
        assert cat in data
        assert isinstance(data[cat], list)

    # Verify nested pagination metadata
    assert "meta" in body
    meta = body["meta"]
    assert "pagination" in meta
    pagination = meta["pagination"]
    for cat in ["tasks", "user_stories", "projects", "members", "sprints"]:
        assert cat in pagination
        cat_p = pagination[cat]
        assert "page" in cat_p
        assert "page_size" in cat_p
        assert "has_next" in cat_p
        assert "total" in cat_p


async def test_search_empty_query(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/search", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert body.get("meta", {}).get("total") == 0
    data = body.get("data", {})
    pagination = body.get("meta", {}).get("pagination", {})
    for cat in ["tasks", "user_stories", "projects", "members", "sprints"]:
        assert cat in data
        assert data[cat] == []
        assert pagination[cat]["total"] == 0
        assert pagination[cat]["has_next"] is False


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

    # Check overall meta fields
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

    # Check independent pagination map
    assert "pagination" in meta
    for cat in ["tasks", "user_stories", "projects", "members", "sprints"]:
        assert cat in meta["pagination"]
        assert meta["pagination"][cat]["page"] == 1
        assert meta["pagination"][cat]["page_size"] == 10
        assert isinstance(meta["pagination"][cat]["has_next"], bool)
        assert isinstance(meta["pagination"][cat]["total"], int)


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


# =========================================================================
# INDEPENDENT CATEGORY PAGINATION & ISOLATION TESTS
# =========================================================================

async def test_search_category_specific_tasks(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=test&category=tasks&page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body["data"]

    # ONLY tasks should be present in data
    assert "tasks" in data
    assert "user_stories" not in data
    assert "projects" not in data
    assert "members" not in data
    assert "sprints" not in data
    assert isinstance(data["tasks"], list)

    # Check top-level meta and category pagination meta
    meta = body["meta"]
    assert meta["page"] == 1
    assert meta["page_size"] == 5
    assert meta["total"] == meta.get("total_tasks")
    assert "pagination" in meta
    assert "tasks" in meta["pagination"]
    assert meta["pagination"]["tasks"]["page"] == 1
    assert meta["pagination"]["tasks"]["page_size"] == 5
    assert meta["pagination"]["tasks"]["total"] == meta["total"]


async def test_search_category_specific_projects(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=Project&category=projects&page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body["data"]

    assert "projects" in data
    assert "tasks" not in data
    assert "user_stories" not in data
    assert "members" not in data
    assert "sprints" not in data
    assert isinstance(data["projects"], list)

    meta = body["meta"]
    assert meta.get("total_projects") is not None
    assert "total_tasks" not in meta
    assert "projects" in meta["pagination"]


async def test_search_category_specific_user_stories(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=test&category=user_stories&page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body["data"]

    assert "user_stories" in data
    assert "tasks" not in data
    assert "projects" not in data
    assert "members" not in data
    assert "sprints" not in data
    assert "user_stories" in body["meta"]["pagination"]


async def test_search_category_specific_members(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=admin&category=members&page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body["data"]

    assert "members" in data
    assert "tasks" not in data
    assert "user_stories" not in data
    assert "projects" not in data
    assert "sprints" not in data
    assert "members" in body["meta"]["pagination"]


async def test_search_category_specific_sprints(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=Sprint&category=sprints&page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body["data"]

    assert "sprints" in data
    assert "tasks" not in data
    assert "user_stories" not in data
    assert "projects" not in data
    assert "members" not in data
    assert "sprints" in body["meta"]["pagination"]


async def test_search_category_out_of_range_page(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=test&category=tasks&page=9999&page_size=10", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    data = body["data"]
    assert data["tasks"] == []
    assert body["meta"]["page"] == 9999
    assert body["meta"]["has_next"] is False
    assert body["meta"]["pagination"]["tasks"]["has_next"] is False


async def test_search_invalid_category(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=test&category=invalid_category", headers=headers)
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("success") is False
    assert body.get("error", {}).get("code") == "BAD_REQUEST"


async def test_search_invalid_pagination_parameters(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    # Page must be >= 1
    resp_page = await client.get("/api/v1/search?q=test&page=0", headers=headers)
    assert resp_page.status_code in [400, 422]

    # Page size > 100 rejected
    resp_size = await client.get("/api/v1/search?q=test&page_size=101", headers=headers)
    assert resp_size.status_code in [400, 422]


async def test_search_overall_pagination(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/search?q=test&category=all&page=2&page_size=2", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    meta = body["meta"]
    assert meta["page"] == 2
    assert meta["page_size"] == 2

    data = body["data"]
    for cat in ["tasks", "user_stories", "projects", "members", "sprints"]:
        assert cat in data
        assert isinstance(data[cat], list)
        assert meta["pagination"][cat]["page"] == 2
        assert meta["pagination"][cat]["page_size"] == 2
