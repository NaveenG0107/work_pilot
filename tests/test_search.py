import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_search_query_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/search?q=test", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body


async def test_search_empty_query(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/search", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True


async def test_search_tenant_isolation(client: AsyncClient, app_context: dict):
    headers_org_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}

    resp = await client.get("/api/v1/search?q=Project", headers=headers_org_b)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    projects = body.get("data", {}).get("projects", [])
    for proj in projects:
        assert proj.get("organization_id") != app_context["org_a_id"]


async def test_search_unauthorized(client: AsyncClient, app_context: dict):
    resp = await client.get("/api/v1/search?q=test")
    assert resp.status_code == 401
