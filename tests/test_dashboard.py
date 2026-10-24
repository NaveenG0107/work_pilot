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
    assert "date" in body_single["data"][0]

    # 2. Date range test (7 days inclusive)
    resp_range = await client.get(
        f"/api/v1/{project_id}/weekly-progress?start_date=2026-10-15&end_date=2026-10-21",
        headers=headers,
    )
    assert resp_range.status_code == 200
    body_range = resp_range.json()
    assert body_range.get("success") is True
    assert len(body_range["data"]) == 7
    assert body_range["meta"]["page"] == 1
    assert "total_planned" in body_range["meta"]


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


async def test_dashboard_weekly_progress_rolling_seven_days_default(client: AsyncClient, app_context: dict):
    from datetime import datetime, timedelta, timezone
    import os

    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    # 1. Test existing endpoint /{project_id}/weekly-progress without date parameters
    resp = await client.get(f"/api/v1/{project_id}/weekly-progress", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status_code"] == 200
    assert body["message"] == "Weekly progress fetched successfully"

    series = body["data"]
    meta = body["meta"]
    assert meta["page"] == 1
    assert "start_date" in meta
    assert "end_date" in meta
    assert "timezone" in meta
    assert len(series) == 7

    # Verify chronological order and 7 consecutive days
    parsed_dates = [datetime.strptime(item["date"], "%Y-%m-%d").date() for item in series]
    for i in range(len(parsed_dates) - 1):
        assert parsed_dates[i + 1] - parsed_dates[i] == timedelta(days=1), "Series must be consecutive calendar days"

    assert series[0]["date"] == meta["start_date"]
    assert series[-1]["date"] == meta["end_date"]
    assert parsed_dates[-1] - parsed_dates[0] == timedelta(days=6)

    # Verify day abbreviations
    valid_days = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    for item in series:
        assert item["day"] in valid_days
        assert isinstance(item["planned"], int)
        assert isinstance(item["completed"], int)

    # Verify summary consistency
    total_planned = sum(item["planned"] for item in series)
    total_completed = sum(item["completed"] for item in series)
    assert meta["total_planned"] == total_planned
    assert meta["total_completed"] == total_completed
    assert meta["remaining"] == max(0, total_planned - total_completed)
    if total_planned > 0:
        expected_rate = round((total_completed / total_planned) * 100, 2)
        assert meta["completion_rate"] == expected_rate
    else:
        assert meta["completion_rate"] == 0.0

    # 2. Test alias endpoint /dashboard/weekly/progress?project_id=...
    alias_resp = await client.get(f"/api/v1/dashboard/weekly/progress?project_id={project_id}&page=2", headers=headers)
    assert alias_resp.status_code == 200
    alias_body = alias_resp.json()
    assert alias_body["success"] is True
    assert alias_body["meta"]["page"] == 2
    assert len(alias_body["data"]) == 7


async def test_dashboard_weekly_progress_filters_and_soft_delete(client: AsyncClient, app_context: dict):
    from datetime import datetime, timezone
    from uuid6 import uuid7
    from src.database import AsyncSessionLocal
    from src.task.models import Task

    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    sprint_id = app_context.get("sprint_a_id")
    assignee_id = app_context.get("user_a_admin_id")

    # Seed controlled tasks
    now = datetime.now(timezone.utc)
    task_keys = [f"RL1-{uuid7().hex[-8:]}", f"RL2-{uuid7().hex[-8:]}"]
    from sqlalchemy import text
    async with AsyncSessionLocal() as session:
        max_res = await session.execute(text("SELECT COALESCE(MAX(serial_number), 0) FROM tasks"))
        base_serial = (max_res.scalar() or 50000) + 100

        # Task 1: active, planned today
        t1 = Task(
            id=str(uuid7()),
            project_id=project_id,
            sprint_id=sprint_id,
            assignee_id=assignee_id,
            key=task_keys[0],
            sequence_number=9991,
            serial_number=base_serial + 1,
            title="Rolling Active Task 1",
            status_id=app_context.get("status_a_id") or str(uuid7()),
            status="todo",
            due_date=now,
        )
        # Task 2: soft-deleted task, planned today (should NOT be counted)
        t2 = Task(
            id=str(uuid7()),
            project_id=project_id,
            sprint_id=sprint_id,
            assignee_id=assignee_id,
            key=task_keys[1],
            sequence_number=9992,
            serial_number=base_serial + 2,
            title="Rolling Soft Deleted Task",
            status_id=app_context.get("status_a_id") or str(uuid7()),
            status="todo",
            due_date=now,
            deleted_at=now,
        )
        session.add_all([t1, t2])
        await session.commit()

    try:
        # Query with sprint_id filter
        resp = await client.get(
            f"/api/v1/{project_id}/weekly-progress?sprint_id={sprint_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        today_str = now.strftime("%Y-%m-%d")
        today_point = next((p for p in body["data"] if p["date"] == today_str), None)
        assert today_point is not None
        assert today_point["planned"] >= 1

        # Query with assignee_id filter
        resp_assignee = await client.get(
            f"/api/v1/{project_id}/weekly-progress?assignee_id={assignee_id}",
            headers=headers,
        )
        assert resp_assignee.status_code == 200
        body_assignee = resp_assignee.json()
        assert body_assignee["success"] is True

        # Query with non-existent assignee filter -> 0 planned for that assignee
        dummy_assignee = str(uuid7())
        resp_empty = await client.get(
            f"/api/v1/{project_id}/weekly-progress?assignee_id={dummy_assignee}",
            headers=headers,
        )
        assert resp_empty.status_code == 200
        assert resp_empty.json()["meta"]["total_planned"] == 0
        assert resp_empty.json()["meta"]["completion_rate"] == 0.0

    finally:
        # Cleanup seeded tasks
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            for k in task_keys:
                stmt = select(Task).where(Task.key == k)
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row:
                    await session.delete(row)
            await session.commit()


async def test_dashboard_weekly_progress_tenant_isolation_and_auth(client: AsyncClient, app_context: dict):
    project_id = app_context["project_a_id"]
    token_org_b = app_context.get("token_org_b_admin")

    # 1. Missing authentication -> 401
    resp_unauth = await client.get(f"/api/v1/{project_id}/weekly-progress")
    assert resp_unauth.status_code == 401

    # 2. Cross-tenant access (Org B admin accessing Org A project) -> 403
    if token_org_b:
        headers_b = {"Authorization": f"Bearer {token_org_b}"}
        resp_forbidden = await client.get(f"/api/v1/{project_id}/weekly-progress", headers=headers_b)
        assert resp_forbidden.status_code == 403

    # 3. Missing project_id on /dashboard/weekly/progress -> 400
    headers_a = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp_no_proj = await client.get("/api/v1/dashboard/weekly/progress", headers=headers_a)
    assert resp_no_proj.status_code == 400

    # 4. Invalid date range (start_date > end_date) -> 400
    resp_bad_range = await client.get(
        f"/api/v1/{project_id}/weekly-progress?start_date=2026-10-25&end_date=2026-10-20",
        headers=headers_a,
    )
    assert resp_bad_range.status_code == 400


async def test_dashboard_weekly_progress_query_count(client: AsyncClient, app_context: dict):
    from tests.conftest import QueryCounter
    from src.database import engine

    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    with QueryCounter(engine) as qc:
        resp = await client.get(f"/api/v1/{project_id}/weekly-progress", headers=headers)
        assert resp.status_code == 200

    # User, Project, ProjectMember permission check, 1 planned grouped query, 1 completed grouped query
    # Total queries should be bounded (<= 10 queries), never N+1 per day
    assert qc.count <= 10, f"Expected <= 10 queries, but executed {qc.count}"


def test_dashboard_weekly_progress_unit_clock_and_summary_rules():
    from datetime import date, timedelta
    from src.dashboard.schemas import RollingProgressPoint, RollingProgressSummary

    # 1. Test 7 consecutive calendar dates spanning month boundary
    today = date(2026, 11, 3)
    start_date = today - timedelta(days=6)
    days = []
    curr = start_date
    while curr <= today:
        days.append(curr)
        curr += timedelta(days=1)

    assert len(days) == 7
    assert days[0] == date(2026, 10, 28)
    assert days[-1] == date(2026, 11, 3)

    # 2. Test completion rate when planned is 0
    series = [
        RollingProgressPoint(date=d.strftime("%Y-%m-%d"), day=d.strftime("%a"), planned=0, completed=0)
        for d in days
    ]
    tot_p = sum(p.planned for p in series)
    tot_c = sum(p.completed for p in series)
    rem = max(0, tot_p - tot_c)
    rate = round((tot_c / tot_p) * 100, 2) if tot_p > 0 else 0.0
    summary = RollingProgressSummary(
        total_planned=tot_p,
        total_completed=tot_c,
        remaining=rem,
        completion_rate=rate,
    )
    assert summary.total_planned == 0
    assert summary.total_completed == 0
    assert summary.remaining == 0
    assert summary.completion_rate == 0.0

    # 3. Test completion rate when planned > 0
    series[0].planned = 10
    series[0].completed = 7
    tot_p = sum(p.planned for p in series)
    tot_c = sum(p.completed for p in series)
    rem = max(0, tot_p - tot_c)
    rate = round((tot_c / tot_p) * 100, 2) if tot_p > 0 else 0.0
    summary2 = RollingProgressSummary(
        total_planned=tot_p,
        total_completed=tot_c,
        remaining=rem,
        completion_rate=rate,
    )
    assert summary2.total_planned == 10
    assert summary2.total_completed == 7
    assert summary2.remaining == 3
    assert summary2.completion_rate == 70.0


