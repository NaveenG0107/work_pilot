import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_get_custom_statuses_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(
        f"/api/v1/projects/{project_id}/custom-statuses",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body
    assert isinstance(body["data"], list)


async def test_get_custom_statuses_tenant_isolation(client: AsyncClient, app_context: dict):
    """
    Expected behavior: Org B user requesting Org A's custom statuses should be rejected with 403 Forbidden or 404 Not Found.
    """
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]

    resp = await client.get(
        f"/api/v1/projects/{project_a_id}/custom-statuses",
        headers=headers_org_b,
    )
    assert resp.status_code in [403, 404]


async def test_get_custom_statuses_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/custom-statuses")
    assert resp.status_code == 401


async def test_get_user_story_statuses_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(
        f"/api/v1/projects/{project_id}/user-story-statuses",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body
    assert isinstance(body["data"], list)


async def test_get_user_story_statuses_tenant_isolation(client: AsyncClient, app_context: dict):
    """
    Expected behavior: Org B user requesting Org A's user story statuses should be rejected with 403 Forbidden or 404 Not Found.
    """
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]

    resp = await client.get(
        f"/api/v1/projects/{project_a_id}/user-story-statuses",
        headers=headers_org_b,
    )
    assert resp.status_code in [403, 404]


async def test_create_custom_status_validation_error(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.post(
        f"/api/v1/projects/{project_id}/custom-statuses",
        headers=headers,
        json={},
    )
    assert resp.status_code in [400, 422]
