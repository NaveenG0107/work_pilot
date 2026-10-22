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


async def test_dashboard_tenant_isolation(client: AsyncClient, app_context: dict):
    """
    Expected behavior: Org B user requesting Org A's dashboard overview should be rejected with 403 Forbidden or 404 Not Found.
    """
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_a_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/{project_a_id}/overview", headers=headers_org_b)
    assert resp.status_code in [403, 404]


async def test_dashboard_unauthorized(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    resp = await client.get(f"/api/v1/{project_id}/overview")
    assert resp.status_code == 401


async def test_dashboard_weekly_progress_inclusive_date_range(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    # 1. Single day test (start_date == end_date should yield 1 day)
    resp_single = await client.get(
        f"/api/v1/{project_id}/weekly-progress?start_date=2026-10-21&end_date=2026-10-21",
        headers=headers,
    )
    assert resp_single.status_code == 200
    body_single = resp_single.json()
    assert body_single.get("success") is True
    assert len(body_single["data"]) == 1

    # 2. Date range test (7 days inclusive)
    resp_range = await client.get(
        f"/api/v1/{project_id}/weekly-progress?start_date=2026-10-15&end_date=2026-10-21",
        headers=headers,
    )
    assert resp_range.status_code == 200
    body_range = resp_range.json()
    assert body_range.get("success") is True
    assert len(body_range["data"]) == 7

