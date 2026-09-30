import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_get_user_stories_list(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/user-stories?page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    assert isinstance(body["data"], list)
    if "meta" in body:
        assert body["meta"]["page"] == 1


@pytest.mark.anyio
async def test_get_user_story_detail(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    story_id = app_context["story_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/user-stories/{story_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == story_id


@pytest.mark.anyio
async def test_user_story_tenant_isolation(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]
    story_a_id = app_context["story_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_a_id}/user-stories/{story_a_id}", headers=headers)
    assert resp.status_code in [403, 404]
    body = resp.json()
    assert body["success"] is False


@pytest.mark.anyio
async def test_user_story_invalid_id(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/user-stories/00000000-0000-0000-0000-000000000000", headers=headers)
    assert resp.status_code in [400, 404]


@pytest.mark.anyio
async def test_user_story_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/user-stories")
    assert resp.status_code == 401
