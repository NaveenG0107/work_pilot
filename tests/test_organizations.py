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


@pytest.mark.anyio
async def test_get_organization_roles_excludes_member_and_admin(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.get("/api/v1/organization/roles", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    role_names = [r["name"].lower() for r in body["data"]]
    # member is user default, not a project role; org_admin & super_admin are also excluded
    assert "member" not in role_names
    assert "org_admin" not in role_names
    assert "super_admin" not in role_names
    assert "project_manager" in role_names
    assert "developer" in role_names


@pytest.mark.anyio
async def test_delete_role_with_soft_deleted_member(client: AsyncClient, app_context: dict):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    # 1. Create a custom role
    create_resp = await client.post(
        "/api/v1/organization/roles",
        json={"name": "Test Analyst", "description": "Analyst role for testing", "permissions": {"projects": {"view": True}}},
        headers=headers,
    )
    assert create_resp.status_code == 201
    role_id = create_resp.json()["data"]["id"]

    # 2. Add a member to project with this role
    project_id = app_context["project_a_id"]
    add_resp = await client.post(
        "/api/v1/project/add-members",
        json={"project_id": project_id, "members": [{"user_id": app_context["user_a_member_id"], "role_id": role_id}]},
        headers=headers,
    )
    assert add_resp.status_code == 201

    # 3. Soft-delete the member
    del_mem_resp = await client.delete(
        f"/api/v1/project/{project_id}/member/{app_context['user_a_member_id']}",
        headers=headers,
    )
    assert del_mem_resp.status_code == 200

    # 4. Delete the role - should succeed because the member is soft-deleted
    del_role_resp = await client.delete(f"/api/v1/organization/roles/{role_id}", headers=headers)
    assert del_role_resp.status_code == 200
    assert del_role_resp.json()["success"] is True


