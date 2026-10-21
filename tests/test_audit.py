import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_get_audit_logs_activity_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/audit/activity", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body
    assert "meta" in body


async def test_get_audit_logs_view_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/audit/view", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True


async def test_get_audit_logs_invalid_type(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/audit/invalid_type", headers=headers)
    assert resp.status_code == 400


async def test_get_audit_logs_pagination(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/audit/activity?page=1&page_size=2", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert body["meta"]["page"] == 1
    assert body["meta"]["page_size"] == 2


async def test_get_audit_logs_unauthorized(client: AsyncClient, app_context: dict):
    resp = await client.get("/api/v1/audit/activity")
    assert resp.status_code == 401


async def test_project_view_audit_logged(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    # 1. View the project detail
    detail_resp = await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)
    assert detail_resp.status_code == 200
    project_name = detail_resp.json()["data"]["name"]

    # 2. Query audit views filtered by resource_type=project
    audit_resp = await client.get(
        f"/api/v1/audit/view?resource_type=project&project_id={project_id}",
        headers=headers,
    )
    assert audit_resp.status_code == 200
    body = audit_resp.json()
    assert body.get("success") is True
    activities = body["data"]["activities"]
    assert len(activities) > 0

    latest_view = activities[0]
    assert latest_view["action"] == "viewed"
    assert latest_view["resource_type"] == "project"
    assert latest_view["type"] == "view"
    assert latest_view["project_id"] == project_id
    assert latest_view["title"] == project_name
    assert "viewed" in latest_view["details"].lower()


async def test_project_view_audit_deduplication(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_id = app_context["project_b_id"]

    # Check baseline view count before calls
    audit_before = await client.get(
        f"/api/v1/audit/view?resource_type=project&project_id={project_id}&page_size=20",
        headers=headers,
    )
    count_before = len([
        act for act in audit_before.json().get("data", {}).get("activities", [])
        if act.get("project_id") == project_id and act.get("resource_type") == "project" and act.get("action") == "viewed"
    ])

    # Call detail endpoint 3 times in rapid succession
    for _ in range(3):
        resp = await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)
        assert resp.status_code == 200

    # Query audit views for project_b
    audit_resp = await client.get(
        f"/api/v1/audit/view?resource_type=project&project_id={project_id}&page_size=20",
        headers=headers,
    )
    assert audit_resp.status_code == 200
    body = audit_resp.json()
    assert body.get("success") is True
    activities = [
        act for act in body["data"]["activities"]
        if act.get("project_id") == project_id and act.get("resource_type") == "project" and act.get("action") == "viewed"
    ]
    # Deduplication ensures that 3 consecutive calls resulted in at most 1 additional view log entry
    assert len(activities) - count_before in (0, 1)



