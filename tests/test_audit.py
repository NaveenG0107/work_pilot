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
