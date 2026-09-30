import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_get_labels_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/projects/{project_id}/labels", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body
    assert isinstance(body["data"], list)


async def test_get_labels_tenant_isolation(client: AsyncClient, app_context: dict):
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/projects/{project_a_id}/labels", headers=headers_org_b)
    assert resp.status_code in [403, 404]


async def test_create_label_validation_error(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.post(f"/api/v1/projects/{project_id}/labels", headers=headers, json={})
    assert resp.status_code in [400, 422]


async def test_work_items_invalid_key_or_not_found(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/projects/{project_id}/work-items/task/NON-EXISTENT-999", headers=headers)
    assert resp.status_code in [400, 404]


async def test_labels_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/labels")
    assert resp.status_code == 401
