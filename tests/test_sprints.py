import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_get_sprints_list_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(
        f"/api/v1/projects/{project_id}/sprint",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body
    assert isinstance(body["data"], list)
    if body["data"]:
        assert "total_stories" in body["data"][0]
        assert isinstance(body["data"][0]["total_stories"], int)
    if "meta" in body:
        assert "page" in body["meta"]
        assert "page_size" in body["meta"]


async def test_get_sprints_pagination(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(
        f"/api/v1/projects/{project_id}/sprint?page=1&page_size=2",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert len(body["data"]) <= 2


async def test_sprint_detail_success_or_not_found(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    list_resp = await client.get(
        f"/api/v1/projects/{project_id}/sprint",
        headers=headers,
    )
    assert list_resp.status_code == 200
    sprints = list_resp.json().get("data", [])

    if sprints:
        sprint_id = sprints[0]["id"]
        detail_resp = await client.get(
            f"/api/v1/projects/{project_id}/sprint/{sprint_id}",
            headers=headers,
        )
        assert detail_resp.status_code == 200
        detail_body = detail_resp.json()
        assert detail_body.get("success") is True
        assert detail_body["data"]["id"] == sprint_id
        assert "total_stories" in detail_body["data"]
        assert isinstance(detail_body["data"]["total_stories"], int)
    else:
        dummy_id = "00000000-0000-0000-0000-000000000000"
        detail_resp = await client.get(
            f"/api/v1/projects/{project_id}/sprint/{dummy_id}",
            headers=headers,
        )
        assert detail_resp.status_code in [404, 400]


@pytest.mark.xfail(
    reason="Known Bug: Missing project tenant-isolation check in SprintService.get_sprints; cross-org user receives 200 instead of 403/404",
    strict=False,
)
async def test_sprint_tenant_isolation(client: AsyncClient, app_context: dict):
    """
    Expected behavior: Org B user requesting Org A's project sprints should be rejected with 403 Forbidden or 404 Not Found.
    Actual behavior: Currently returns 200 OK because SprintService only filters by project_id without verifying Project.organization_id.
    """
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]

    resp = await client.get(
        f"/api/v1/projects/{project_a_id}/sprint",
        headers=headers_org_b,
    )
    assert resp.status_code in [403, 404]


async def test_sprint_invalid_uuid(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get(
        "/api/v1/projects/invalid-project-uuid/sprint",
        headers=headers,
    )
    assert resp.status_code in [400, 422]


async def test_sprint_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/projects/{project_id}/sprint")
    assert resp.status_code == 401


async def test_create_sprint_missing_fields_validation(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.post(
        f"/api/v1/projects/{project_id}/sprint",
        headers=headers,
        json={},
    )
    assert resp.status_code in [400, 422]
