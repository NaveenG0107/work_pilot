import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_health_check_basic(client: AsyncClient):
    resp = await client.get("/health_check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Work Pilot backend is running"


@pytest.mark.anyio
async def test_public_health_simple(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["version"] == "v1"
    assert "timestamp" in body


@pytest.mark.anyio
async def test_public_health_full(client: AsyncClient):
    resp = await client.get("/api/v1/health?full=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "dependencies" in body
    assert body["dependencies"]["database"] == "healthy"
    assert body["dependencies"]["redis"] == "healthy"


@pytest.mark.anyio
async def test_public_countries(client: AsyncClient):
    resp = await client.get("/api/v1/countries")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body or isinstance(body, list) or "countries" in body
