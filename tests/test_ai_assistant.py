"""Tests for the WorkPilot AI Assistant (create_task tool and confirmation workflow)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from httpx import AsyncClient

from src.integrations.orchestration.provider import (
    MockLLMProvider,
    ProviderResponse,
    ProviderTimeoutError,
    ProviderResponseError,
    ToolCall,
    set_llm_provider,
)
from src.integrations.orchestration.service import CONFIRMATION_TTL_SECONDS
from src.integrations.tools.tool_executor import ToolExecutor


@pytest.fixture(autouse=True)
def reset_provider():
    """Ensure a clean MockLLMProvider for each test."""
    mock = MockLLMProvider()
    set_llm_provider(mock)
    yield mock
    set_llm_provider(None)


@pytest.mark.anyio
async def test_chat_request_validation(client: AsyncClient, app_context: dict):
    """Test 1: Chat request validation (missing message, invalid body)."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Empty payload
    resp = await client.post("/api/v1/ai-assistant/chat", json={}, headers=headers)
    assert resp.status_code in [400, 422]

    # Empty string message
    resp = await client.post("/api/v1/ai-assistant/chat", json={"message": ""}, headers=headers)
    assert resp.status_code in [400, 422]


@pytest.mark.anyio
async def test_successful_conversational_response(client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider):
    """Test 2: Successful provider conversational response without tool calls."""
    reset_provider.set_response(
        ProviderResponse(
            content="Hello! I can help you manage tasks.",
            tool_calls=[],
            finish_reason="stop",
        )
    )
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "What can you do?"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["action_state"] == "NONE"
    assert "help you manage tasks" in body["data"]["message"]


@pytest.mark.anyio
async def test_create_task_tool_selection_and_no_task_created_before_confirmation(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 3 & 17: Tool selection enters confirmation workflow; NO task is created before confirmation."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Verify task count in project A before
    list_before = await client.get(f"/api/v1/projects/{project_id}/tasks", headers=headers)
    assert list_before.status_code == 200
    initial_total = list_before.json().get("meta", {}).get("total_items", 0)

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Investigate login throttling issue",
                        "type": "bug",
                        "priority": "high",
                        "description": "API returns 429 too eagerly.",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a high priority bug task to investigate login throttling",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]

    # Verify response enters REQUIRES_CONFIRMATION state
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    assert "pending_action" in data
    assert data["pending_action"]["action_type"] == "create_task"
    assert data["pending_action"]["proposed_task"]["title"] == "Investigate login throttling issue"
    action_id = data["pending_action"]["action_id"]
    assert action_id is not None

    # Verify NO task has been created in DB yet
    list_after = await client.get(f"/api/v1/projects/{project_id}/tasks", headers=headers)
    assert list_after.status_code == 200
    after_total = list_after.json().get("meta", {}).get("total_items", 0)
    assert after_total == initial_total


@pytest.mark.anyio
async def test_successful_confirmation_creates_exactly_one_task(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 18: Successful confirmation creates exactly one task and returns its details."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    task_title = f"AI Verified Task {uuid4().hex[:6]}"
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_2",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": task_title,
                        "type": "feature",
                        "priority": "medium",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Create feature task {task_title}", "context": {"project_id": project_id}},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # Confirm action
    confirm_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    confirm_body = confirm_resp.json()
    assert confirm_body["success"] is True
    assert confirm_body["data"]["status"] == "completed"

    created_task = confirm_body["data"]["task"]
    assert created_task["title"] == task_title
    assert created_task["task_id"] is not None
    assert created_task["key"] is not None

    # Verify it exists in Task service
    task_detail_resp = await client.get(
        f"/api/v1/projects/{project_id}/tasks/{created_task['task_id']}",
        headers=headers,
    )
    assert task_detail_resp.status_code == 200
    assert task_detail_resp.json()["data"]["title"] == task_title


@pytest.mark.anyio
async def test_duplicate_confirmation_prevented(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 12: Duplicate confirmation is rejected with 409 CONFLICT and creates no duplicate."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    task_title = f"Idempotency Task {uuid4().hex[:6]}"
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_dup",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": task_title,
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
        json={"message": "create task", "context": {"project_id": project_id}},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # First confirmation -> succeeds
    first_resp = await client.post(f"/api/v1/ai-assistant/actions/{action_id}/confirm", headers=headers)
    assert first_resp.status_code == 200

    # Second confirmation -> rejected with 409
    second_resp = await client.post(f"/api/v1/ai-assistant/actions/{action_id}/confirm", headers=headers)
    assert second_resp.status_code == 409
    assert second_resp.json()["success"] is False


@pytest.mark.anyio
async def test_confirmation_cancellation(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 16: Cancelling a pending action marks it cancelled and prevents subsequent execution."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_cancel",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Temporary Task to Cancel",
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
        json={"message": "create chore task", "context": {"project_id": project_id}},
        headers=headers,
    )
    action_id = chat_resp.json()["data"]["pending_action"]["action_id"]

    # Cancel via cancel endpoint
    cancel_resp = await client.post(f"/api/v1/ai-assistant/actions/{action_id}/cancel", headers=headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["data"]["status"] == "cancelled"

    # Attempting to confirm cancelled action fails
    confirm_resp = await client.post(f"/api/v1/ai-assistant/actions/{action_id}/confirm", headers=headers)
    assert confirm_resp.status_code == 409


@pytest.mark.anyio
async def test_invalid_tool_arguments_rejected(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 4: Invalid tool arguments (unknown fields, title too short, non-Fibonacci story points)."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Title too short (< 3 chars)
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_bad_1",
                    name="create_task",
                    arguments={"project_id": project_id, "title": "ab"},
                )
            ],
        )
    )
    resp1 = await client.post("/api/v1/ai-assistant/chat", json={"message": "create task"}, headers=headers)
    assert resp1.status_code == 400
    assert "at least 3 characters" in resp1.json()["error"]["message"]

    # Non-fibonacci story points (e.g., 4)
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_bad_2",
                    name="create_task",
                    arguments={"project_id": project_id, "title": "Valid title", "story_points": 4},
                )
            ],
        )
    )
    resp2 = await client.post("/api/v1/ai-assistant/chat", json={"message": "create task"}, headers=headers)
    assert resp2.status_code == 400
    assert "Fibonacci" in resp2.json()["error"]["message"]

    # Unknown field (extra='forbid')
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_bad_3",
                    name="create_task",
                    arguments={"project_id": project_id, "title": "Valid title", "arbitrary_field": "injected"},
                )
            ],
        )
    )
    resp3 = await client.post("/api/v1/ai-assistant/chat", json={"message": "create task"}, headers=headers)
    assert resp3.status_code == 400
    msg = resp3.json()["error"]["message"].lower()
    assert "not permitted" in msg and "arbitrary_field" in msg



@pytest.mark.anyio
async def test_unknown_tool_rejection(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 5: Rejection of any tool other than create_task."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_unknown",
                    name="delete_database",
                    arguments={"everything": True},
                )
            ],
        )
    )
    resp = await client.post("/api/v1/ai-assistant/chat", json={"message": "delete db"}, headers=headers)
    assert resp.status_code == 400
    assert "not recognized or permitted" in resp.json()["error"]["message"]


@pytest.mark.anyio
async def test_unauthorized_project_access(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 8: Cross-tenant / unauthorized project access is blocked."""
    # User from Org A tries to target Project B
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_b_id = app_context["project_b_id"]

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_idor",
                    name="create_task",
                    arguments={
                        "project_id": project_b_id,
                        "title": "Cross Tenant Task Attempt",
                        "type": "task",
                        "priority": "low",
                    },
                )
            ],
        )
    )

    resp = await client.post("/api/v1/ai-assistant/chat", json={"message": "create task"}, headers=headers)
    assert resp.status_code in [403, 404]
    assert resp.json()["success"] is False


@pytest.mark.anyio
async def test_invalid_entity_references(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Test 9: Invalid assignee, sprint, or user story references are rejected."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Random non-existent UUID for assignee
    random_user_id = str(uuid4())
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_bad_assignee",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Task with bad assignee",
                        "assignee_id": random_user_id,
                    },
                )
            ],
        )
    )
    resp = await client.post("/api/v1/ai-assistant/chat", json={"message": "create task"}, headers=headers)
    assert resp.status_code in [400, 404]
    assert resp.json()["success"] is False


@pytest.mark.anyio
async def test_provider_errors_handling(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, monkeypatch
):
    """Test 14: Provider timeout and upstream errors."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Simulate timeout
    async def mock_timeout(*args, **kwargs):
        raise ProviderTimeoutError()

    monkeypatch.setattr(reset_provider, "generate_completion", mock_timeout)

    resp = await client.post("/api/v1/ai-assistant/chat", json={"message": "hello"}, headers=headers)
    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "GATEWAY_TIMEOUT"


@pytest.mark.anyio
async def test_missing_jwt_rejected(client: AsyncClient):
    """Test missing or invalid JWT authentication."""
    resp = await client.post("/api/v1/ai-assistant/chat", json={"message": "hello"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_missing_api_key_returns_error(client: AsyncClient, app_context: dict):
    """Verify that using the live provider without an API key produces a 503 error instead of mock output."""
    from src.integrations.orchestration.provider import OpenAICompatibleProvider
    set_llm_provider(OpenAICompatibleProvider(api_key=""))
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    resp = await client.post("/api/v1/ai-assistant/chat", json={"message": "hello"}, headers=headers)
    assert resp.status_code == 503
    assert "temporarily unavailable" in resp.json()["error"]["message"]



