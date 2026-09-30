import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_auth_me_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    assert body["data"]["id"] == app_context["user_a_admin_id"]


@pytest.mark.anyio
async def test_auth_me_mobile_platform(client: AsyncClient, app_context: dict):
    headers = {
        "Authorization": f"Bearer {app_context['token_org_a_admin']}",
        "x-client-platform": "mobile",
    }
    resp = await client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "cover_img_url" in body["data"]


@pytest.mark.anyio
async def test_auth_me_unauthorized(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["success"] is False


@pytest.mark.anyio
async def test_auth_me_invalid_token(client: AsyncClient):
    headers = {"Authorization": "Bearer invalid.jwt.token"}
    resp = await client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_auth_user_insights(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/auth/me/insights", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body


@pytest.mark.anyio
async def test_auth_validate_email_available(client: AsyncClient):
    rand_email = f"test_{uuid.uuid4().hex[:8]}@workpilot-test.com"
    resp = await client.get(f"/api/v1/auth/validate?type=email&value={rand_email}")
    assert resp.status_code in [200, 409]
    body = resp.json()
    assert "data" in body
    assert body["data"]["type"] == "email"


@pytest.mark.anyio
async def test_auth_validate_invalid_type(client: AsyncClient):
    resp = await client.get("/api/v1/auth/validate?type=phone&value=1234567890")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_auth_signin_invalid_credentials(client: AsyncClient):
    payload = {
        "email": "nonexistent_user_12345@workpilot.com",
        "password": "WrongPassword123!",
    }
    resp = await client.post("/api/v1/auth/signin", json=payload)
    assert resp.status_code in [400, 401]
    body = resp.json()
    assert body["success"] is False


@pytest.mark.anyio
async def test_auth_signin_missing_payload(client: AsyncClient):
    resp = await client.post("/api/v1/auth/signin", json={})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_auth_get_user_by_id(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    user_id = app_context["user_a_admin_id"]
    resp = await client.get(f"/api/v1/auth/{user_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == user_id


@pytest.mark.anyio
async def test_auth_get_user_by_invalid_uuid(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/auth/not-a-valid-uuid", headers=headers)
    assert resp.status_code in [400, 404]
