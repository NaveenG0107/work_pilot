"""Automated tests for WorkPilot AI Assistant:
- Atomic action claiming (Redis Lua / in-memory lock)
- Safe lock release and error rollback
- HTTP 409 rejection for competing confirmations
- Durable idempotency protection around PostgreSQL task creation
- Concurrent confirmations and cancellation race conditions
- Lock expiry and re-claiming
- Assignee and reporter resolution by username/name ("demi")
"""

import asyncio
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.auth.models import User
from src.integrations.orchestration.provider import (
    MockLLMProvider,
    ProviderResponse,
    ToolCall,
    set_llm_provider,
)
from src.integrations.orchestration.service import (
    ActionStore,
    AIOrchestrationService,
    CONFIRMATION_TTL_SECONDS,
)
from src.organization.models import Organization, Role
from src.project.models import ProjectMember
from src.task.models import Task


@pytest.fixture(autouse=True)
def reset_provider():
    """Ensure a clean MockLLMProvider for each test."""
    mock = MockLLMProvider()
    set_llm_provider(mock)
    yield mock
    set_llm_provider(None)


@pytest.fixture
async def demi_user(db_session: AsyncSession, app_context: dict):
    """Ensure an active user named 'demi' exists in Org A and is a member of Project A."""
    org_id = app_context["org_a_id"]
    proj_id = app_context["project_a_id"]
    admin_id = app_context["user_a_admin_id"]

    stmt = select(User).where(User.username == "demi")
    user = (await db_session.execute(stmt)).scalars().first()
    if user:
        user.organization_id = org_id
        user.is_active = True
        user.deleted_at = None
        user.full_name = "Demi Lovato"
    else:
        role_stmt = select(Role).where(Role.organization_id == org_id, Role.name == "developer")
        role = (await db_session.execute(role_stmt)).scalars().first()
        role_id = role.id if role else None

        user = User(
            id=str(uuid7()),
            organization_id=org_id,
            full_name="Demi Lovato",
            username="demi",
            email="demi_user_test@workpilot.io",
            role_id=role_id,
            is_active=True,
            status="active",
        )
        db_session.add(user)
        await db_session.flush()

    # Ensure project membership
    pm_stmt = select(ProjectMember).where(
        ProjectMember.project_id == proj_id,
        ProjectMember.user_id == str(user.id),
        ProjectMember.deleted_at.is_(None),
    )
    pm = (await db_session.execute(pm_stmt)).scalars().first()
    if not pm:
        pm = ProjectMember(
            id=str(uuid7()),
            project_id=proj_id,
            user_id=str(user.id),
            added_by_id=admin_id,
        )
        db_session.add(pm)
        await db_session.flush()

    await db_session.commit()
    return user


# ============================================================================
# Concurrency & Idempotency Tests
# ============================================================================

@pytest.mark.anyio
async def test_simultaneous_confirmations_race_condition(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify simultaneous confirmation requests atomically allow exactly one execution (200)

    and reject the competitor with HTTP 409 Conflict, creating only ONE task in PostgreSQL.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Verify task count before
    list_before = await client.get(f"/api/v1/projects/{project_id}/tasks", headers=headers)
    assert list_before.status_code == 200
    initial_total = list_before.json().get("meta", {}).get("total_items", 0)

    # 1. Propose task
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_c1",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Concurrent race condition test task",
                        "type": "bug",
                        "priority": "high",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create a bug task"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # 2. Fire two confirmation requests simultaneously
    resp1, resp2 = await asyncio.gather(
        client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers),
        client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers),
        return_exceptions=True,
    )

    status_codes = sorted([resp1.status_code, resp2.status_code])
    assert status_codes == [200, 409], f"Expected exactly [200, 409], got {status_codes}"

    # Verify the 409 response has conflict error
    err_resp = resp1 if resp1.status_code == 409 else resp2
    err_body = err_resp.json()
    assert err_body["success"] is False
    assert err_resp.status_code == 409

    # 3. Verify exactly ONE task was added in PostgreSQL
    list_after = await client.get(f"/api/v1/projects/{project_id}/tasks", headers=headers)
    assert list_after.status_code == 200
    final_total = list_after.json().get("meta", {}).get("total_items", 0)
    assert final_total == initial_total + 1


@pytest.mark.anyio
async def test_competing_confirmation_after_completion(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that once an action is completed, subsequent confirmation returns HTTP 409."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_c2",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Idempotent duplicate confirmation test",
                        "type": "task",
                        "priority": "medium",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # First confirm succeeds
    res1 = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert res1.status_code == 200

    # Second confirm rejected with 409
    res2 = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert res2.status_code == 409
    body2 = res2.json()
    assert body2["success"] is False
    assert "already been executed" in body2["error"]["message"].lower()


@pytest.mark.anyio
async def test_concurrent_cancellation_and_confirmation_race(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify simultaneous confirmation and cancellation requests cleanly resolve:

    one 200 and one 409.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_c3",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Cancel vs confirm race condition test",
                        "type": "story",
                        "priority": "low",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create story"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    res_conf, res_canc = await asyncio.gather(
        client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers),
        client.post(f"/api/v1/ai-assistant/actions/{action_id}/cancel", headers=headers),
        return_exceptions=True,
    )

    status_codes = sorted([res_conf.status_code, res_canc.status_code])
    assert status_codes == [200, 409], f"Expected [200, 409], got {status_codes}"


@pytest.mark.anyio
async def test_cancellation_of_already_completed_action_rejected(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify attempting to cancel an action that has already completed returns HTTP 409."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_c4",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Completed action cancel test",
                        "type": "task",
                        "priority": "medium",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # Confirm
    conf = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf.status_code == 200

    # Cancel should be rejected
    canc = await client.post(f"/api/v1/ai-assistant/actions/{action_id}/cancel", headers=headers)
    assert canc.status_code == 409
    assert "already been executed" in canc.json()["error"]["message"].lower()


@pytest.mark.anyio
async def test_confirmation_of_cancelled_action_rejected(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify confirming an action that has been cancelled returns HTTP 409."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_c5",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Cancelled action confirm test",
                        "type": "task",
                        "priority": "medium",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # Cancel first
    canc = await client.post(f"/api/v1/ai-assistant/actions/{action_id}/cancel", headers=headers)
    assert canc.status_code == 200

    # Confirm should be rejected
    conf = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf.status_code == 409
    assert "cancelled" in conf.json()["error"]["message"].lower()


@pytest.mark.anyio
async def test_lock_expiry_allows_reclaim(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that if an action lock expires, a subsequent confirmation request can re-claim and succeed."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_c6",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Lock expiry reclaim test",
                        "type": "task",
                        "priority": "low",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # Retrieve action store and simulate a lock that has expired (claim_ttl_seconds=1)
    from src.database import get_redis
    redis_client = get_redis()
    store = ActionStore(redis_client)

    # Claim with a 1-second TTL
    claim1 = await store.claim_action(action_id, token="token_worker_1", claim_ttl_seconds=1)
    assert claim1["result"] == "claimed"

    # Immediately trying to claim again should return already_executing
    claim2 = await store.claim_action(action_id, token="token_worker_2", claim_ttl_seconds=1)
    assert claim2["result"] == "already_executing"

    # Wait 1.2 seconds for lock to expire
    await asyncio.sleep(1.2)

    # Now the action can be reclaimed!
    conf = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf.status_code == 200
    assert conf.json()["success"] is True


@pytest.mark.anyio
async def test_database_failure_safely_releases_lock(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, monkeypatch
):
    """Verify that if database execution fails, the action claim is safely released back to 'pending',

    allowing a subsequent retry to succeed.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_c7",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Database failure rollback test",
                        "type": "task",
                        "priority": "medium",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # Patch TaskService.create to simulate a database failure on first attempt
    from src.task.service import TaskService
    original_create = TaskService.create
    first_call = True

    async def mock_failing_create(*args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            raise RuntimeError("Simulated transient PostgreSQL failure")
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(TaskService, "create", mock_failing_create)

    # Attempt 1 fails due to database failure
    res1 = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert res1.status_code == 500

    # Verify claim was released back to pending by checking ActionStore
    from src.database import get_redis
    store = ActionStore(get_redis())
    action_data = await store.get_action(action_id)
    assert action_data["status"] == "pending", f"Expected 'pending' status after failure, got {action_data['status']}"

    # Attempt 2 (retry) succeeds!
    res2 = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert res2.status_code == 200
    assert res2.json()["success"] is True


# ============================================================================
# Assignee & Reporter Resolution by Name ("demi")
# ============================================================================

@pytest.mark.anyio
async def test_create_task_with_assignee_demi_by_name(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, demi_user: User
):
    """Verify specifying assignee as username 'demi' resolves to demi's user UUID."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_u1",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Optimize Redis connection pool",
                        "type": "task",
                        "priority": "high",
                        "assignee": "demi",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task assigned to demi"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    p_task = chat_resp.json()["data"]["pending_action"]["proposed_task"]
    assert p_task["assignee_id"] == str(demi_user.id)
    assert p_task["assignee_name"] in ["demi", "Demi Lovato"]

    # Confirm and verify in database
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]
    conf = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf.status_code == 200
    task_data = conf.json()["data"]["task"]
    assert task_data["assignee_id"] == str(demi_user.id)


@pytest.mark.anyio
async def test_create_task_with_assignee_id_as_demi_string(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, demi_user: User
):
    """Verify model passing 'assignee_id': 'demi' is normalized and resolved cleanly."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_u2",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Investigate JWT expiry edge case",
                        "type": "bug",
                        "priority": "medium",
                        "assignee_id": "demi",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create bug for demi"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    p_task = chat_resp.json()["data"]["pending_action"]["proposed_task"]
    assert p_task["assignee_id"] == str(demi_user.id)

    # Confirm
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]
    conf = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf.status_code == 200
    task_data = conf.json()["data"]["task"]
    assert task_data["assignee_id"] == str(demi_user.id)


@pytest.mark.anyio
async def test_create_task_with_reporter_demi_by_name(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, demi_user: User
):
    """Verify specifying reporter as 'demi' resolves to demi's user UUID."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_u3",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Security audit for file uploads",
                        "type": "bug",
                        "priority": "critical",
                        "reporter": "demi",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create security task reported by demi"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    p_task = chat_resp.json()["data"]["pending_action"]["proposed_task"]
    assert p_task["reporter_id"] == str(demi_user.id)

    # Confirm
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]
    conf = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf.status_code == 200
    task_data = conf.json()["data"]["task"]
    assert task_data["reporter_id"] == str(demi_user.id)


@pytest.mark.anyio
async def test_create_task_with_both_assignee_and_reporter_demi(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, demi_user: User
):
    """Verify both assignee and reporter can be set to 'demi' simultaneously."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_u4",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Implement Webhook signature verification",
                        "type": "task",
                        "priority": "high",
                        "assignee": "demi",
                        "reporter": "demi",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task with assignee demi and reporter demi"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    p_task = chat_resp.json()["data"]["pending_action"]["proposed_task"]
    assert p_task["assignee_id"] == str(demi_user.id)
    assert p_task["reporter_id"] == str(demi_user.id)

    # Confirm
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]
    conf = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf.status_code == 200
    task_data = conf.json()["data"]["task"]
    assert task_data["assignee_id"] == str(demi_user.id)
    assert task_data["reporter_id"] == str(demi_user.id)


@pytest.mark.anyio
async def test_unknown_assignee_name_rejected_with_validation_error(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that specifying an unknown username for assignee returns HTTP 400 validation error."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_u5",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Unknown assignee test task",
                        "type": "task",
                        "priority": "medium",
                        "assignee": "ghost_user_9999",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task assigned to ghost_user_9999"},
        headers=headers,
    )
    assert chat_resp.status_code == 400
    body = chat_resp.json()
    assert body["success"] is False
    assert "not found" in body["error"]["message"].lower()


@pytest.mark.anyio
async def test_multi_worker_concurrency_ten_simultaneous_confirmations(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify 10 simultaneous worker confirmations result in exactly ONE 200 OK and 9 HTTP 409 Conflicts."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    list_before = await client.get(f"/api/v1/projects/{project_id}/tasks", headers=headers)
    initial_total = list_before.json().get("meta", {}).get("total_items", 0)

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_mw1",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Multi-worker heavy concurrency stress test",
                        "type": "chore",
                        "priority": "low",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create chore task"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # 10 simultaneous workers
    tasks = [
        client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
        for _ in range(10)
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1, f"Expected exactly one 200, got: {status_codes}"
    assert status_codes.count(409) == 9, f"Expected 9 409s, got: {status_codes}"

    # Verify database has exactly 1 task added
    list_after = await client.get(f"/api/v1/projects/{project_id}/tasks", headers=headers)
    final_total = list_after.json().get("meta", {}).get("total_items", 0)
    assert final_total == initial_total + 1


@pytest.mark.anyio
async def test_ambiguous_username_resolution_rejected(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, db_session: AsyncSession
):
    """Verify that when multiple users match the same identifier in the org, a clear validation error is returned."""
    org_id = app_context["org_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    # Create two users with overlapping name 'Alex Taylor' and 'Alex Morgan'
    role_stmt = select(Role).where(Role.organization_id == org_id, Role.name == "developer")
    role = (await db_session.execute(role_stmt)).scalars().first()
    role_id = role.id if role else None

    user1_stmt = select(User).where(User.username == "alex_taylor_test")
    u1 = (await db_session.execute(user1_stmt)).scalars().first()
    if not u1:
        u1 = User(
            id=str(uuid7()),
            organization_id=org_id,
            full_name="Alex Taylor",
            username="alex_taylor_test",
            email="alex.taylor@workpilot.io",
            role_id=role_id,
            is_active=True,
            status="active",
        )
        db_session.add(u1)

    user2_stmt = select(User).where(User.username == "alex_morgan_test")
    u2 = (await db_session.execute(user2_stmt)).scalars().first()
    if not u2:
        u2 = User(
            id=str(uuid7()),
            organization_id=org_id,
            full_name="Alex Morgan",
            username="alex_morgan_test",
            email="alex.morgan@workpilot.io",
            role_id=role_id,
            is_active=True,
            status="active",
        )
        db_session.add(u2)

    await db_session.commit()

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_ambig",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Resolve ambiguous user matching test",
                        "type": "task",
                        "priority": "low",
                        "assignee": "Alex",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task assigned to Alex"},
        headers=headers,
    )
    assert chat_resp.status_code == 400
    body = chat_resp.json()
    assert body["success"] is False
    assert "multiple users match 'alex'" in body["error"]["message"].lower()


@pytest.mark.anyio
async def test_unauthorized_reporter_cross_tenant_rejected(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that specifying a reporter from another organization is rejected."""
    project_id = app_context["project_a_id"]
    user_b_admin_id = app_context["user_b_admin_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_rep_cross",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Cross tenant reporter isolation test",
                        "type": "task",
                        "priority": "low",
                        "reporter_id": user_b_admin_id,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task with cross tenant reporter"},
        headers=headers,
    )
    assert chat_resp.status_code == 400
    body = chat_resp.json()
    assert body["success"] is False
    assert "not found" in body["error"]["message"].lower()


@pytest.mark.anyio
async def test_unauthorized_reporter_non_project_member_rejected(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, db_session: AsyncSession
):
    """Verify that specifying a reporter in the organization who is NOT a project member is rejected."""
    org_id = app_context["org_a_id"]
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Find or create a user in Org A who is explicitly NOT a member of Project A
    stmt = (
        select(User)
        .where(
            User.organization_id == org_id,
            User.deleted_at.is_(None),
            User.is_active == True,
            ~User.id.in_(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.deleted_at.is_(None),
                )
            ),
        )
        .limit(1)
    )
    non_member = (await db_session.execute(stmt)).scalars().first()
    if not non_member:
        # Create a non-member developer user
        role_stmt = select(Role).where(Role.organization_id == org_id, Role.name == "developer")
        role = (await db_session.execute(role_stmt)).scalars().first()
        non_member = User(
            id=str(uuid7()),
            organization_id=org_id,
            full_name="Non Member User",
            username="non_member_dev_test",
            email="non_member_dev@workpilot.io",
            role_id=role.id if role else None,
            is_active=True,
            status="active",
        )
        db_session.add(non_member)
        await db_session.commit()

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_rep_non_mem",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Non member reporter rejection test",
                        "type": "task",
                        "priority": "low",
                        "reporter": non_member.username,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task reported by non member"},
        headers=headers,
    )
    assert chat_resp.status_code == 400
    body = chat_resp.json()
    assert body["success"] is False
    assert "must be a member of project" in body["error"]["message"].lower()


@pytest.mark.anyio
async def test_database_failure_around_commit_boundary(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, monkeypatch
):
    """Verify that a database failure at the commit boundary triggers transaction rollback and claim release."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_db_boundary",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Commit boundary failure rollback test",
                        "type": "bug",
                        "priority": "low",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create bug"},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # Patch AsyncSession.commit to simulate database failure during commit
    from sqlalchemy.ext.asyncio import AsyncSession
    original_commit = AsyncSession.commit
    first_commit = True

    async def mock_failing_commit(self_session, *args, **kwargs):
        nonlocal first_commit
        if first_commit:
            first_commit = False
            raise RuntimeError("Simulated PostgreSQL connection drop at commit boundary")
        return await original_commit(self_session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "commit", mock_failing_commit)

    # Confirm attempt 1 encounters simulated commit boundary error
    res1 = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert res1.status_code == 500

    # Verify action claim was safely released back to pending
    from src.database import get_redis
    store = ActionStore(get_redis())
    action_data = await store.get_action(action_id)
    assert action_data["status"] == "pending"

    # Confirm attempt 2 (retry) succeeds!
    res2 = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert res2.status_code == 200
    assert res2.json()["success"] is True


@pytest.mark.anyio
async def test_task_creation_rejected_without_meaningful_name(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify AI asks user to provide a meaningful name when title is missing or generic (e.g. 'task', 'create task')."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Case 1: Generic title 'task'
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_no_name_1",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "task",
                        "type": "task",
                        "priority": "low",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp1 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create task"},
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()["data"]
    assert data1["action_state"] == "NONE"
    assert "meaningful name" in data1["message"].lower()
    assert data1.get("pending_action") is None

    # Case 2: Generic title 'create a task'
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_no_name_2",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "create a task",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp2 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create a task"},
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["action_state"] == "NONE"
    assert "meaningful name" in data2["message"].lower()


@pytest.mark.anyio
async def test_default_task_type_and_priority_values(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when type and priority are omitted, default type is 'task' and priority is 'low'."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_defaults",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Check database index fragmentation",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "create a task to check database index fragmentation"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    p_task = chat_resp.json()["data"]["pending_action"]["proposed_task"]
    assert p_task["type"] == "task", f"Expected default type 'task', got {p_task['type']}"
    assert p_task["priority"] == "low", f"Expected default priority 'low', got {p_task['priority']}"

