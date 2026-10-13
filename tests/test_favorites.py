import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_get_favorites_list_success(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/favorites", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "data" in body


async def test_get_favorites_pagination(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.get("/api/v1/favorites?page=1&page_size=2", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True


async def test_add_favorite_validation_error(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.post("/api/v1/favorites", headers=headers, json={})
    assert resp.status_code in [400, 422]


async def test_favorites_unauthorized(client: AsyncClient, app_context: dict):
    resp = await client.get("/api/v1/favorites")
    assert resp.status_code == 401
