import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_get_projects_list(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/project/get?page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    assert isinstance(body["data"], list)
    if "meta" in body:
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 5


@pytest.mark.anyio
async def test_project_detail_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == project_id


@pytest.mark.anyio
async def test_project_members_list(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/project/members/{project_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


@pytest.mark.anyio
async def test_project_tenant_isolation_detail(client: AsyncClient, app_context: dict):
    # Org A user attempts to access Project B
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_b_id = app_context["project_b_id"]
    resp = await client.get(f"/api/v1/project/{project_b_id}/detail", headers=headers)
    assert resp.status_code in [403, 404]
    body = resp.json()
    assert body["success"] is False


@pytest.mark.anyio
async def test_project_tenant_isolation_members(client: AsyncClient, app_context: dict):
    # Org A user attempts to access Project B members
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_b_id = app_context["project_b_id"]
    resp = await client.get(f"/api/v1/project/members/{project_b_id}", headers=headers)
    assert resp.status_code in [403, 404]
    body = resp.json()
    assert body["success"] is False


@pytest.mark.anyio
async def test_project_invalid_uuid(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/project/invalid-uuid-1234/detail", headers=headers)
    assert resp.status_code in [400, 404]


@pytest.mark.anyio
async def test_project_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/project/{project_id}/detail")
    assert resp.status_code == 401
