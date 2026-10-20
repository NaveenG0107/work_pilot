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


@pytest.mark.anyio
async def test_get_user_stories_filter_by_status_id(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    story_id = app_context["story_a_id"]

    # 1. Get the story detail to inspect its status_id
    detail_resp = await client.get(
        f"/api/v1/projects/{project_id}/user-stories/{story_id}",
        headers=headers,
    )
    assert detail_resp.status_code == 200
    story_data = detail_resp.json()["data"]
    status_id = story_data["status_id"]
    assert status_id is not None

    # 2. Filter user-stories by status_id alone
    resp = await client.get(
        f"/api/v1/projects/{project_id}/user-stories?status_id={status_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) > 0
    for story in body["data"]:
        assert story["status_id"] == status_id

    # 3. Filter using the exact query pattern requested
    query_url = (
        f"/api/v1/projects/{project_id}/user-stories"
        f"?page=1&page_size=10&sort_by=created_at&sort_order=DESC&status=todo&is_unassigned_story=True&status_id={status_id}"
    )
    resp_query = await client.get(query_url, headers=headers)
    assert resp_query.status_code == 200
    body_query = resp_query.json()
    assert body_query["success"] is True
    for story in body_query["data"]:
        assert story["status_id"] == status_id

    # 4. Filter with non-existent status_id UUID returns empty list
    resp_empty = await client.get(
        f"/api/v1/projects/{project_id}/user-stories?status_id=00000000-0000-0000-0000-000000000000",
        headers=headers,
    )
    assert resp_empty.status_code == 200
    assert resp_empty.json()["data"] == []

    # 5. Invalid status_id returns 400 Bad Request
    resp_invalid = await client.get(
        f"/api/v1/projects/{project_id}/user-stories?status_id=invalid-uuid",
        headers=headers,
    )
    assert resp_invalid.status_code == 400
    assert resp_invalid.json()["success"] is False

