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


def test_sprint_service_parse_datetime_unit():
    from src.sprint.service import SprintService
    from datetime import datetime, timezone, date

    # _parse_utc_datetime strictly accepts YYYY-MM-DDTHH:MM:SSZ
    dt1 = SprintService._parse_utc_datetime("2026-10-14T10:00:00Z")
    assert dt1 == datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)

    # _parse_utc_datetime rejects date-only format
    with pytest.raises(ValueError) as exc:
        SprintService._parse_utc_datetime("2026-10-14")
    assert "expected format: YYYY-MM-DDTHH:MM:SSZ" in str(exc.value)

    # _parse_utc_datetime rejects invalid strings
    with pytest.raises(ValueError):
        SprintService._parse_utc_datetime("invalid-date-format")


async def test_start_sprint_validation_and_utc_datetime(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    # 1. Create a planned sprint
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/sprint",
        headers=headers,
        json={
            "sprints": [
                {
                    "name": "Validation Test Sprint",
                    "goal": "Test date validation",
                }
            ]
        },
    )
    assert create_resp.status_code in [200, 201]
    create_data = create_resp.json()
    assert create_data.get("success") is True
    sprint_id = create_data["data"]["sprint_id"]

    # 2. Reject date-only format (YYYY-MM-DD)
    date_only_resp = await client.post(
        f"/api/v1/projects/{project_id}/sprint/start?sprint_id={sprint_id}",
        headers=headers,
        json={
            "start_date": "2026-11-01",
            "end_date": "2026-11-02",
        },
    )
    assert date_only_resp.status_code == 400
    assert "expected format: YYYY-MM-DDTHH:MM:SSZ" in date_only_resp.json().get("message", "")

    # 3. Reject start_date in the past (before current datetime)
    past_resp = await client.post(
        f"/api/v1/projects/{project_id}/sprint/start?sprint_id={sprint_id}",
        headers=headers,
        json={
            "start_date": "2020-01-01T00:00:00Z",
            "end_date": "2026-12-31T00:00:00Z",
        },
    )
    assert past_resp.status_code == 400
    assert "start_date cannot be in the past" in past_resp.json().get("message", "")

    # 4. Reject end_date equal to start_date
    equal_resp = await client.post(
        f"/api/v1/projects/{project_id}/sprint/start?sprint_id={sprint_id}",
        headers=headers,
        json={
            "start_date": "2026-11-01T10:00:00Z",
            "end_date": "2026-11-01T10:00:00Z",
        },
    )
    assert equal_resp.status_code == 400
    assert "end_date cannot be before or equal to start_date" in equal_resp.json().get("message", "")

    # 5. Reject end_date before start_date
    before_resp = await client.post(
        f"/api/v1/projects/{project_id}/sprint/start?sprint_id={sprint_id}",
        headers=headers,
        json={
            "start_date": "2026-11-10T10:00:00Z",
            "end_date": "2026-11-05T10:00:00Z",
        },
    )
    assert before_resp.status_code == 400
    assert "end_date cannot be before or equal to start_date" in before_resp.json().get("message", "")

    # 6. Accept same date with different time (end > start)
    start_resp = await client.post(
        f"/api/v1/projects/{project_id}/sprint/start?sprint_id={sprint_id}",
        headers=headers,
        json={
            "start_date": "2026-11-01T04:30:12Z",
            "end_date": "2026-11-01T07:30:12Z",
        },
    )
    assert start_resp.status_code == 200
    start_body = start_resp.json()
    assert start_body.get("success") is True
    assert start_body["data"]["status"] == "active"
    assert start_body["data"]["start_date"] == "2026-11-01T04:30:12Z"
    assert start_body["data"]["end_date"] == "2026-11-01T07:30:12Z"

    # 7. Verify GET /sprint/{sprint_id} preserves exact UTC datetime with time
    detail_resp = await client.get(
        f"/api/v1/projects/{project_id}/sprint/{sprint_id}",
        headers=headers,
    )
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data.get("success") is True
    assert detail_data["data"]["start_date"] == "2026-11-01T04:30:12Z"
    assert detail_data["data"]["end_date"] == "2026-11-01T07:30:12Z"

    # 8. Verify GET /sprint/{sprint_id}/burndown does not raise 500
    burndown_resp = await client.get(
        f"/api/v1/projects/{project_id}/sprint/{sprint_id}/burndown",
        headers=headers,
    )
    assert burndown_resp.status_code == 200
    bd_data = burndown_resp.json()
    assert bd_data.get("success") is True
    assert "burndown_data" in bd_data["data"]

    # 9. Verify PATCH /sprint/{sprint_id} update works for active sprint with timestamps
    update_resp = await client.patch(
        f"/api/v1/projects/{project_id}/sprint/{sprint_id}",
        headers=headers,
        json={
            "name": "Updated Sprint Name",
            "end_date": "2026-11-05T12:00:00Z",
        },
    )
    assert update_resp.status_code == 200
    update_data = update_resp.json()
    assert update_data.get("success") is True

