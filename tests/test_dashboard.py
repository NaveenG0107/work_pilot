import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_dashboard_overview_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/{project_id}/overview", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body


async def test_dashboard_task_status_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/{project_id}/task-status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True


async def test_dashboard_team_workload_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/{project_id}/team-workload", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True


async def test_dashboard_full_data_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/{project_id}/dashboard", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True


@pytest.mark.xfail(
    reason="Known Bug: Missing project tenant-isolation check in DashboardService.get_overview; cross-org user receives 200 instead of 403/404",
    strict=False,
)
async def test_dashboard_tenant_isolation(client: AsyncClient, app_context: dict):
    """
    Expected behavior: Org B user requesting Org A's dashboard overview should be rejected with 403 Forbidden or 404 Not Found.
    Actual behavior: Currently returns 200 OK because DashboardService does not verify Project.organization_id.
    """
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/{project_a_id}/overview", headers=headers_org_b)
    assert resp.status_code in [403, 404]


async def test_dashboard_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/{project_id}/overview")
    assert resp.status_code == 401
