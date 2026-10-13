import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_get_task_comments_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    tasks_resp = await client.get(
        f"/api/v1/projects/{project_id}/tasks?page=1&page_size=1",
        headers=headers,
    )
    assert tasks_resp.status_code == 200
    tasks = tasks_resp.json().get("data", [])
    if tasks:
        task_id = tasks[0]["id"]
        resp = await client.get(
            f"/api/v1/task/{task_id}/comments",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("success") is True
        assert "data" in body
        assert isinstance(body["data"], list)


async def test_get_task_comments_pagination(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    tasks_resp = await client.get(
        f"/api/v1/projects/{project_id}/tasks?page=1&page_size=1",
        headers=headers,
    )
    tasks = tasks_resp.json().get("data", [])
    if tasks:
        task_id = tasks[0]["id"]
        resp = await client.get(
            f"/api/v1/task/{task_id}/comments?page=1&page_size=2",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("success") is True
        assert len(body["data"]) <= 2


async def test_create_task_comment_validation_error(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    tasks_resp = await client.get(
        f"/api/v1/projects/{project_id}/tasks?page=1&page_size=1",
        headers=headers,
    )
    tasks = tasks_resp.json().get("data", [])
    if tasks:
        task_id = tasks[0]["id"]
        resp = await client.post(
            f"/api/v1/task/{task_id}/comments",
            headers=headers,
            json={},
        )
        assert resp.status_code in [400, 422]


async def test_task_comments_tenant_isolation(client: AsyncClient, app_context: dict):
    headers_a = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    headers_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_id = app_context["project_a_id"]

    tasks_resp = await client.get(
        f"/api/v1/projects/{project_id}/tasks?page=1&page_size=1",
        headers=headers_a,
    )
    tasks = tasks_resp.json().get("data", [])
    if tasks:
        task_id = tasks[0]["id"]
        resp = await client.get(
            f"/api/v1/task/{task_id}/comments",
            headers=headers_b,
        )
        assert resp.status_code in [403, 404]


async def test_task_comments_unauthorized(client: AsyncClient, app_context: dict):
    resp = await client.get("/api/v1/task/00000000-0000-0000-0000-000000000000/comments")
    assert resp.status_code == 401
