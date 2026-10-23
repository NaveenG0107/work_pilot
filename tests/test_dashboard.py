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


def test_dashboard_sprint_burndown_calculation_unit():
    from datetime import date, datetime, timezone
    from src.dashboard.service import DashboardService

    start = date(2026, 10, 20)
    end = date(2026, 10, 24)

    class DummyTask:
        def __init__(self, estimated_hours, is_final, updated_at):
            self.estimated_hours = estimated_hours
            self.story_points = None
            self.actual_hours = None
            self.is_final = is_final
            self.updated_at = updated_at

    tasks = [
        DummyTask(10.0, True, datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)),
        DummyTask(20.0, False, None),
        DummyTask(30.0, False, None),
    ]

    points = DashboardService._generate_burndown_points(start, end, tasks=tasks)
    assert len(points) == 5
    # Total weight is 10 + 20 + 30 = 60
    assert points[0].ideal_hours == 60.0
    assert points[-1].ideal_hours == 0.0
    # Day 1 (Oct 20): task not yet completed
    assert points[0].actual_hours == 60.0
    # Day 2 (Oct 21): task completed on Oct 21 -> 60 - 10 = 50 remaining
    assert points[1].actual_hours == 50.0

    # Also verify that passing datetime.datetime objects directly works without TypeError
    start_dt = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
    points_dt = DashboardService._generate_burndown_points(start_dt, end_dt, tasks=tasks)
    assert len(points_dt) == 5
    assert points_dt[0].ideal_hours == 60.0
    assert points_dt[1].actual_hours == 50.0



async def test_dashboard_sprint_burndown_api(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    resp = await client.get(f"/api/v1/{project_id}/sprint-burndown", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "sprint_burndown" in body["data"]


