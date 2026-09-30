import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_get_organization_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/organization/get", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    assert body["data"]["id"] == app_context["org_a_id"]


@pytest.mark.anyio
async def test_get_organization_unauthorized(client: AsyncClient):
    resp = await client.get("/api/v1/organization/get")
    assert resp.status_code in [401, 403]


@pytest.mark.anyio
async def test_get_organization_users(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/organization/get-users?page=1&page_size=10", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    assert isinstance(body["data"], list)
    if "meta" in body:
        assert body["meta"]["page"] == 1


@pytest.mark.anyio
async def test_organization_tenant_isolation(client: AsyncClient, app_context: dict):
    # Org B admin queries their own org
    headers_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    resp_b = await client.get("/api/v1/organization/get", headers=headers_b)
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["data"]["id"] == app_context["org_b_id"]
    assert body_b["data"]["id"] != app_context["org_a_id"]


@pytest.mark.anyio
async def test_invite_member_validation(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    # Invalid email
    payload = {"email": "invalid-email-format", "role": "member"}
    resp = await client.post("/api/v1/organization/invite", json=payload, headers=headers)
    assert resp.status_code in [400, 422]
