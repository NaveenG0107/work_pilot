import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_get_tasks_list(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/tasks?page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    assert isinstance(body["data"], list)
    if "meta" in body:
        assert body["meta"]["page"] == 1


@pytest.mark.anyio
async def test_get_task_detail(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    task_id = app_context["task_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == task_id


@pytest.mark.anyio
async def test_get_tasks_with_priority_filter(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/tasks?priority=high&page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


@pytest.mark.anyio
async def test_task_tenant_isolation(client: AsyncClient, app_context: dict):
    # Org B user attempts to access Project A task
    headers_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]
    task_a_id = app_context["task_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_a_id}/tasks/{task_a_id}", headers=headers_b)
    assert resp.status_code in [403, 404]
    body = resp.json()
    assert body["success"] is False


@pytest.mark.anyio
async def test_task_not_found(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/tasks/00000000-0000-0000-0000-000000000000", headers=headers)
    assert resp.status_code in [400, 404]


@pytest.mark.anyio
async def test_task_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/tasks")
    assert resp.status_code == 401
