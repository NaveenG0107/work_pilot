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

    # Cancel via confirm endpoint with confirm=False
    cancel_resp = await client.post(f"/api/v1/ai-assistant/actions/{action_id}/confirm", json={"confirm": False}, headers=headers)
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
async def test_llm_swapped_priority_and_type_arguments(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when LLM swaps priority ('bug') and type ('high'), they are cleanly normalized."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_swapped",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Fix crash on checkout page",
                        "type": "high",
                        "priority": "bug",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a high priority bug titled Fix crash on checkout page",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    p_task = data["pending_action"]["proposed_task"]
    assert p_task["type"] == "bug"
    assert p_task["priority"] == "high"


@pytest.mark.anyio
async def test_llm_task_type_in_priority_with_missing_type(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when LLM puts 'bug' into priority and omits type, it maps cleanly."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_type_in_prio",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Fix memory leak in background worker",
                        "priority": "bug",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a high priority bug titled Fix memory leak in background worker",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    p_task = data["pending_action"]["proposed_task"]
    assert p_task["type"] == "bug"
    assert p_task["priority"] == "high"


@pytest.mark.anyio
async def test_invalid_type_field_identification(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that an unsupported task type error correctly identifies the 'type' field."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_bad_type",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Refactor billing system",
                        "type": "custom_type",
                        "priority": "high",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a custom_type task titled Refactor billing system",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "NONE"
    assert "task type 'custom_type' is not supported" in data["message"].lower()


@pytest.mark.anyio
async def test_invalid_priority_field_identification(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that an unsupported priority error correctly identifies the 'priority' field."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_bad_prio",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Refactor billing system",
                        "type": "bug",
                        "priority": "super_urgent",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a bug titled Refactor billing system with priority super_urgent",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "NONE"
    assert "priority 'super_urgent' is not supported" in data["message"].lower()


def test_create_task_tool_args_schema_and_validation():
    """Direct validation of CreateTaskToolArgs model: swap normalization and field error identification."""
    from src.integrations.tools.task_tools import CreateTaskToolArgs
    from pydantic import ValidationError

    pid = uuid4()

    # 1. Swapped arguments
    args_swapped = CreateTaskToolArgs.model_validate({
        "project_id": pid,
        "title": "Swapped argument test",
        "type": "high",
        "priority": "bug",
    })
    assert args_swapped.type == "bug"
    assert args_swapped.priority == "high"

    # 2. Priority contains task type, type omitted
    args_type_in_prio = CreateTaskToolArgs.model_validate({
        "project_id": pid,
        "title": "Type in priority test",
        "priority": "bug",
    })
    assert args_type_in_prio.type == "bug"
    assert args_type_in_prio.priority == "low"

    # 3. Invalid type raises ValidationError with loc ('type',)
    with pytest.raises(ValidationError) as exc_type:
        CreateTaskToolArgs.model_validate({
            "project_id": pid,
            "title": "Invalid type test",
            "type": "invalid_item",
        })
    err1 = exc_type.value.errors()[0]
    assert err1["loc"] == ("type",)
    assert "Task type 'invalid_item' is not supported" in err1["msg"]

    # 4. Invalid priority raises ValidationError with loc ('priority',)
    with pytest.raises(ValidationError) as exc_prio:
        CreateTaskToolArgs.model_validate({
            "project_id": pid,
            "title": "Invalid priority test",
            "priority": "not_a_priority",
        })
    err2 = exc_prio.value.errors()[0]
    assert err2["loc"] == ("priority",)
    assert "Priority 'not_a_priority' is not supported" in err2["msg"]

    # 5. Missing mandatory project_id raises ValidationError with loc ('project_id',)
    with pytest.raises(ValidationError) as exc_pid:
        CreateTaskToolArgs.model_validate({
            "title": "Missing project_id test",
            "type": "chore",
            "story_points": 3,
        })
    err_pid = exc_pid.value.errors()[0]
    assert err_pid["loc"] == ("project_id",)

    # 6. Valid project_id with story_points string conversion
    args_valid = CreateTaskToolArgs.model_validate({
        "project_id": pid,
        "title": "Valid project ID test",
        "story_points": "3",
    })
    assert args_valid.project_id == pid
    assert args_valid.story_points == 3

    # 7. Assignee matching project_name is cleared so omitted assignee stays null
    args_assignee_cleared = CreateTaskToolArgs.model_validate({
        "project_id": pid,
        "project_name": "demo",
        "assignee": "demo",
        "title": "Assignee matching project cleared",
    })
    assert args_assignee_cleared.assignee is None
    assert args_assignee_cleared.assignee_id is None

    # 8. due_date validation in CreateTaskToolArgs
    args_due = CreateTaskToolArgs.model_validate({
        "project_id": pid,
        "title": "Task with valid due date",
        "due_date": "2026-11-20",
    })
    assert args_due.due_date is not None
    assert args_due.due_date.year == 2026
    assert args_due.due_date.month == 11
    assert args_due.due_date.day == 20

    # Omitted / null due_date is acceptable
    args_due_null = CreateTaskToolArgs.model_validate({
        "project_id": pid,
        "title": "Task with null due date",
        "due_date": "null",
    })
    assert args_due_null.due_date is None

    # Invalid due_date raises ValidationError
    with pytest.raises(ValidationError) as exc_due:
        CreateTaskToolArgs.model_validate({
            "project_id": pid,
            "title": "Task with invalid due date",
            "due_date": "invalid-date",
        })
    err_due = exc_due.value.errors()[0]
    assert err_due["loc"] == ("due_date",)
    assert "Invalid due date 'invalid-date'" in err_due["msg"]

    # Past due_date raises ValidationError
    with pytest.raises(ValidationError) as exc_past:
        CreateTaskToolArgs.model_validate({
            "project_id": pid,
            "title": "Task with past due date",
            "due_date": "2020-01-01",
        })
    err_past = exc_past.value.errors()[0]
    assert err_past["loc"] == ("due_date",)
    assert "in the past" in err_past["msg"]


@pytest.mark.anyio
async def test_user_explicit_unsupported_type_asks_for_valid_type_without_pending_action(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when the user explicitly requests an unsupported type (e.g. 'epic'),

    the backend asks them to choose a valid type and does NOT create a pending action.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Model might attempt to default to 'task' or call with 'epic'
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_unsupported_type",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Onboarding overhaul",
                        "type": "task",
                        "priority": "high",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task in the current project titled 'Onboarding overhaul'. Set the type to Epic, priority to High.",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "NONE"
    assert data.get("pending_action") is None
    msg = data["message"].lower()
    assert "task type 'epic' is not supported" in msg
    assert "choose a valid type" in msg


@pytest.mark.anyio
async def test_confirmation_wording_no_repeated_task_word(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that confirmation message for a 'task' type does not repeat 'task task'."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_wording_test",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Upgrade Postgres version",
                        "type": "task",
                        "priority": "medium",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Upgrade Postgres version' in the current project",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    msg = data["message"]
    # Verify natural phrasing and no repeated 'task task' or 'story task'
    assert "task task" not in msg.lower()
    assert "I've prepared the medium-priority task 'Upgrade Postgres version'" in msg

    # Verify story confirmation wording is natural without 'story task'
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_story_wording",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Update notification settings",
                        "type": "story",
                        "priority": "high",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )
    resp2 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a high priority story titled 'Update notification settings'",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp2.status_code == 200
    msg2 = resp2.json()["data"]["message"]
    assert "story task" not in msg2.lower()
    assert "I've prepared the high-priority story 'Update notification settings'" in msg2


@pytest.mark.anyio
async def test_omitted_assignee_stays_null_and_does_not_resolve_project_name_as_assignee(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when the LLM incorrectly maps the project name 'demo' to assignee,

    the backend clears the assignee to None, does not attempt to resolve 'demo' as a user,
    and creates a pending action with assignee_id=None.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Simulate LLM returning assignee: "demo" when user did not specify an assignee
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_omitted_assignee",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Update Python dependencies",
                        "type": "chore",
                        "priority": "low",
                        "assignee": "demo",
                        "story_points": 3,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Update Python dependencies' in demo. Set type to Chore, priority to Low, and story points to 3.",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    proposed = data["pending_action"]["proposed_task"]
    assert proposed["title"] == "Update Python dependencies"
    assert proposed["type"] == "chore"
    assert proposed["priority"] == "low"
    assert proposed["story_points"] == 3
    # Assignee must stay null
    assert proposed["assignee_id"] is None
    assert proposed["assignee_name"] is None

    # Confirm action and ensure task is created in DB with assignee_id=None
    action_id = data["pending_action"]["action_id"]
    conf_resp = await client.post(f"/api/v1/ai-assistant/confirm/{action_id}", headers=headers)
    assert conf_resp.status_code == 200
    task_created = conf_resp.json()["data"]["task"]
    assert task_created["assignee_id"] is None


@pytest.mark.anyio
async def test_independent_project_resolution_by_project_name(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that project_name is resolved independently when project_id is not passed by LLM."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Retrieve actual project A name
    proj_resp = await client.get(f"/api/v1/project/{project_id}/detail", headers=headers)
    assert proj_resp.status_code == 200
    proj_name = proj_resp.json()["data"]["name"]

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_proj_name_test",
                    name="create_task",
                    arguments={
                        "project_name": proj_name,
                        "title": "Resolve project independently by name",
                        "type": "feature",
                        "priority": "high",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": f"Create a feature task titled 'Resolve project independently by name' in project {proj_name}.",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    proposed = data["pending_action"]["proposed_task"]
    assert proposed["project_id"] == project_id
    assert proposed["assignee_id"] is None


@pytest.mark.anyio
async def test_absent_project_id_returns_clarification_before_pending_action(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when project_id is absent from arguments and context,

    the assistant returns a clarification response and does NOT create a pending action.
    """
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_missing_project",
                    name="create_task",
                    arguments={
                        "title": "Task without any project specified",
                        "type": "task",
                        "priority": "low",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Task without any project specified'",
            # Note: No context.project_id provided
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Must NOT create a pending action
    assert data["action_state"] == "NONE"
    assert "pending_action" not in data or data["pending_action"] is None
    assert "specify the project" in data["message"].lower()


@pytest.mark.anyio
async def test_user_explicit_invalid_due_date_asks_for_valid_date_without_pending_action(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when the user explicitly provides an invalid due date (e.g. 'invalid-date'),

    the assistant validates it, asks for a valid date instead of silently dropping it,
    and does NOT create a pending action.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Case A: Model outputs invalid due_date in arguments
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_invalid_due",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Prepare quarterly report",
                        "due_date": "invalid-date",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Prepare quarterly report' in the current project due on invalid-date",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "NONE"
    assert data.get("pending_action") is None
    msg = data["message"].lower()
    assert "due date" in msg
    assert "valid date" in msg or "valid due date" in msg

    # Case B: Model silently dropped due_date from tool arguments, but user explicitly specified it
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_dropped_due",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Prepare quarterly report",
                        "due_date": None,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp2 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Prepare quarterly report' in the current project with due date: not-a-real-date",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["action_state"] == "NONE"
    assert data2.get("pending_action") is None
    msg2 = data2["message"].lower()
    assert "due date" in msg2
    assert "valid date" in msg2 or "valid due date" in msg2


@pytest.mark.anyio
async def test_omitted_due_date_sets_null_and_prepares_pending_action(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that when due date is optional and omitted by the user,

    due_date: null is acceptable and a pending action is created normally.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_omitted_due",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Refactor logging subsystem",
                        "due_date": None,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Refactor logging subsystem' in the current project",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    assert data["pending_action"] is not None
    proposed = data["pending_action"]["proposed_task"]
    assert proposed["title"] == "Refactor logging subsystem"
    assert proposed["due_date"] is None


@pytest.mark.anyio
async def test_explicit_valid_due_date_prepares_pending_action_and_creates_task_with_due_date(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, db_session
):
    """Verify that when a valid due date is explicitly provided,

    it is validated, included in the pending action, and stored on the task upon confirmation.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_valid_due",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Deploy API Gateway v2",
                        "due_date": "2026-11-25",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Deploy API Gateway v2' in the current project due on 2026-11-25",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    action = data["pending_action"]
    proposed = action["proposed_task"]
    assert proposed["title"] == "Deploy API Gateway v2"
    assert proposed["due_date"] is not None
    assert "2026-11-25" in proposed["due_date"]

    # Now confirm the action and verify the created task has due_date stored
    conf_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action['action_id']}/confirm",
        headers=headers,
    )
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()["data"]
    created_task_id = conf_data["task"]["task_id"]

    # Verify task in DB
    from uuid import UUID
    from src.task.models import Task
    task_in_db = await db_session.get(Task, UUID(created_task_id))
    assert task_in_db is not None
    assert task_in_db.due_date is not None
    assert task_in_db.due_date.year == 2026
    assert task_in_db.due_date.month == 11
    assert task_in_db.due_date.day == 25


@pytest.mark.anyio
async def test_create_task_tool_args_priority_normalization_and_type_defaults():
    """Verify that CreateTaskToolArgs normalizes natural-language priority phrases,

    keeps priority and task type as separate fields, and defaults type to 'task'.
    """
    from pydantic import ValidationError
    from src.integrations.tools.task_tools import CreateTaskToolArgs

    test_pid = uuid4()

    # 1. 'medium-priority' with omitted type -> priority="medium", type="task"
    args1 = CreateTaskToolArgs.model_validate({
        "project_id": test_pid,
        "title": "Database optimization chore",
        "priority": "medium-priority",
    })
    assert args1.priority == "medium"
    assert args1.type == "task"

    # 2. 'high-priority' with explicit type 'bug' -> priority="high", type="bug"
    args2 = CreateTaskToolArgs.model_validate({
        "project_id": test_pid,
        "title": "Fix memory leak in websocket worker",
        "priority": "high-priority",
        "type": "bug",
    })
    assert args2.priority == "high"
    assert args2.type == "bug"

    # 3. 'low priority' with empty / None type -> priority="low", type="task"
    args3 = CreateTaskToolArgs.model_validate({
        "project_id": test_pid,
        "title": "Update README docs",
        "priority": "low priority",
        "type": None,
    })
    assert args3.priority == "low"
    assert args3.type == "task"

    # 4. 'critical-level' -> priority="critical", type="task"
    args4 = CreateTaskToolArgs.model_validate({
        "project_id": test_pid,
        "title": "Patch production security vulnerability",
        "priority": "critical-level",
    })
    assert args4.priority == "critical"
    assert args4.type == "task"

    # 5. Type containing a priority phrase (e.g. 'medium-priority') when priority not provided
    args5 = CreateTaskToolArgs.model_validate({
        "project_id": test_pid,
        "title": "Review PR comments",
        "type": "medium-priority",
    })
    assert args5.priority == "medium"
    assert args5.type == "task"

    # 6. Unsupported priorities like 'urgent-priority' must be rejected
    with pytest.raises(ValidationError):
        CreateTaskToolArgs.model_validate({
            "project_id": test_pid,
            "title": "Urgent task",
            "priority": "urgent-priority",
        })


@pytest.mark.anyio
async def test_natural_language_priority_phrase_normalized_and_type_defaults_to_task(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider, db_session
):
    """Verify that chat request with a natural-language priority phrase ('medium-priority')

    and omitted type normalizes priority to 'medium', defaults type to 'task', keeps them separate,
    and upon confirmation creates the task with priority='medium' and type='task'.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    task_title = f"Refactor caching middleware {uuid4().hex[:6]}"
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_prio_norm",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": task_title,
                        "priority": "medium-priority",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": f"Create a medium-priority task titled '{task_title}' in the current project",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    assert "medium-priority task" in data["message"]

    action = data["pending_action"]
    proposed = action["proposed_task"]
    assert proposed["title"] == task_title
    assert proposed["priority"] == "medium"
    assert proposed["type"] == "task"

    # Confirm the action
    conf_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action['action_id']}/confirm",
        headers=headers,
    )
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()["data"]
    created_task = conf_data["task"]
    assert created_task["priority"] == "medium"
    assert created_task["type"] == "task"

    # Verify task directly in DB
    from uuid import UUID
    from src.task.models import Task
    task_in_db = await db_session.get(Task, UUID(created_task["task_id"]))
    assert task_in_db is not None
    assert task_in_db.priority == "medium"
    assert task_in_db.type == "task"


@pytest.mark.anyio
async def test_unsupported_priority_phrase_prompts_user_to_choose_valid_priority(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that an unsupported priority phrase (e.g. 'urgent-priority')

    prompts the user to specify an allowed priority without creating a pending action.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_unsupported_prio",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Fix urgent outage",
                        "priority": "urgent-priority",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create an urgent-priority task titled 'Fix urgent outage' in the current project",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "NONE"
    assert "not supported" in data["message"].lower()
    assert "low, medium, high, critical" in data["message"]


@pytest.mark.anyio
async def test_past_due_date_prohibited_and_explains_without_pending_action(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that past due dates are prohibited:

    The chatbot explains that the date is in the past and does not create a pending action.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Case 1: Explicit past ISO date provided in arguments and user message
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_past_iso",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Backfill event logs",
                        "due_date": "2020-01-01",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp1 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Backfill event logs' in the current project due on 2020-01-01",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()["data"]
    assert data1["action_state"] == "NONE"
    assert data1["pending_action"] is None
    assert "in the past" in data1["message"].lower()

    # Case 2: Natural-language past date ('yesterday')
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_past_yesterday",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Patch legacy auth",
                        "due_date": "yesterday",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp2 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Patch legacy auth' in the current project due yesterday",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["action_state"] == "NONE"
    assert data2["pending_action"] is None
    assert "in the past" in data2["message"].lower()

    # Case 3: Model omitted due_date from tool arguments, but user explicitly specified a past date
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_past_omitted",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Migrate database",
                        "due_date": None,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp3 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Migrate database' in the current project due on 2021-06-15",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp3.status_code == 200
    data3 = resp3.json()["data"]
    assert data3["action_state"] == "NONE"
    assert data3["pending_action"] is None
    assert "in the past" in data3["message"].lower()


@pytest.mark.anyio
async def test_priority_extraction_does_not_misidentify_connecting_words_like_and(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that connecting words like 'and' preceding 'priority' are not misidentified

    as field values, and that 'urgent' is extracted and rejected with a validation error.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Case 1: "and priority urgent" in sentence
    resp1 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Fix critical login crash' in the current project and priority urgent",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()["data"]
    assert data1["action_state"] == "NONE"
    assert data1["pending_action"] is None
    # Must identify 'urgent' as the unsupported priority, NOT 'and'
    assert "priority 'urgent' is not supported" in data1["message"].lower()
    assert "'and'" not in data1["message"]

    # Case 2: "with type bug and priority urgent"
    resp2 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Fix checkout crash' with type bug and priority urgent",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["action_state"] == "NONE"
    assert data2["pending_action"] is None
    assert "priority 'urgent' is not supported" in data2["message"].lower()
    assert "'and'" not in data2["message"]

    # Case 3: "create an urgent task titled ..."
    resp3 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create an urgent task titled 'Fix payment gateway error' in the current project",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp3.status_code == 200
    data3 = resp3.json()["data"]
    assert data3["action_state"] == "NONE"
    assert data3["pending_action"] is None
    assert "priority 'urgent' is not supported" in data3["message"].lower()


@pytest.mark.anyio
async def test_conflicting_repeated_priorities_detected_without_pending_action(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that specifying conflicting priorities (e.g. low and high) is detected

    as a conflict, prompts the user to clarify, and prevents creating a pending action.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Case 1: "Create a low-priority task titled ... with high priority"
    resp1 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a low-priority task titled 'Resolve login failure' in the current project with high priority",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()["data"]
    assert data1["action_state"] == "NONE"
    assert data1["pending_action"] is None
    msg1 = data1["message"].lower()
    assert "conflict" in msg1
    assert "low" in msg1
    assert "high" in msg1
    assert "which priority" in msg1

    # Case 2: "priority low and high"
    resp2 = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Migrate database schema' in the current project with priority low and high",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["action_state"] == "NONE"
    assert data2["pending_action"] is None
    msg2 = data2["message"].lower()
    assert "conflict" in msg2
    assert "low" in msg2
    assert "high" in msg2
    assert "which priority" in msg2


@pytest.mark.anyio
async def test_conflicting_repeated_task_types_detected_without_pending_action(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that specifying conflicting task types (e.g. bug and feature) is detected

    as a conflict, prompts the user to clarify, and prevents creating a pending action.
    """
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a bug task titled 'Improve dashboard layout' in the current project with type feature",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["action_state"] == "NONE"
    assert data["pending_action"] is None
    msg = data["message"].lower()
    assert "conflict" in msg
    assert "bug" in msg
    assert "feature" in msg
    assert "which task type" in msg


@pytest.mark.anyio
async def test_create_task_with_sprint_name(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that specifying a sprint by name resolves the sprint ID and includes sprint name."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Fetch existing active sprint in project A
    sprint_resp = await client.get(
        f"/api/v1/projects/{project_id}/sprint",
        headers=headers,
    )
    assert sprint_resp.status_code == 200
    sprints = sprint_resp.json()["data"]
    assert len(sprints) > 0, "Project A must have at least one sprint"
    target_sprint = next((s for s in sprints if s.get("status") != "completed"), sprints[0])
    sprint_name = target_sprint["name"]
    sprint_id = target_sprint["id"]

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_sprint_1",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Fix the login API throttling",
                        "type": "bug",
                        "priority": "high",
                        "sprint_name": sprint_name,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": f"Create a high-priority bug task to fix the login API throttling in sprint {sprint_name}",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]

    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    assert "pending_action" in data
    proposed = data["pending_action"]["proposed_task"]
    assert proposed["title"] == "Fix the login API throttling"
    assert proposed["priority"] == "high"
    assert proposed["type"] == "bug"
    assert proposed["sprint_id"] == sprint_id
    assert proposed["sprint_name"] == sprint_name
    assert f"in sprint '{sprint_name}'" in data["message"]

    action_id = data["pending_action"]["action_id"]

    # Confirm action
    confirm_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    created_task = confirm_resp.json()["data"]["task"]
    assert created_task["title"] == "Fix the login API throttling"

    # Verify task in Task service has sprint_id attached
    task_get = await client.get(
        f"/api/v1/projects/{project_id}/tasks/{created_task['task_id']}",
        headers=headers,
    )
    assert task_get.status_code == 200
    assert task_get.json()["data"]["sprint_id"] == sprint_id


@pytest.mark.anyio
async def test_create_task_with_user_story_name(
    client: AsyncClient, app_context: dict, reset_provider: MockLLMProvider
):
    """Verify that specifying a user story by name resolves the story ID and includes story name."""
    project_id = app_context["project_a_id"]
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Fetch existing user story in project A
    story_resp = await client.get(
        f"/api/v1/projects/{project_id}/user-stories",
        headers=headers,
    )
    assert story_resp.status_code == 200
    stories = story_resp.json()["data"]
    assert len(stories) > 0, "Project A must have at least one user story"
    target_story = stories[0]
    story_name = target_story["title"]
    story_id = target_story["id"]

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_story_1",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Fix the login API throttling",
                        "type": "bug",
                        "priority": "high",
                        "user_story_name": story_name,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": f"Create a high-priority bug task to fix the login API throttling in userstory {story_name}",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]

    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    assert "pending_action" in data
    proposed = data["pending_action"]["proposed_task"]
    assert proposed["title"] == "Fix the login API throttling"
    assert proposed["priority"] == "high"
    assert proposed["type"] == "bug"
    assert proposed["user_story_id"] == story_id
    assert proposed["user_story_name"] == story_name
    assert f"in user story '{story_name}'" in data["message"]

    action_id = data["pending_action"]["action_id"]

    # Confirm action
    confirm_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    created_task = confirm_resp.json()["data"]["task"]
    assert created_task["title"] == "Fix the login API throttling"

    # Verify task in Task service has user_story_id attached
    task_get = await client.get(
        f"/api/v1/projects/{project_id}/tasks/{created_task['task_id']}",
        headers=headers,
    )
    assert task_get.status_code == 200
    assert task_get.json()["data"]["user_story_id"] == story_id


@pytest.mark.anyio
async def test_update_task_flow_requires_confirmation(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test updating an existing task via update_task tool and confirming it."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    # 1. Create a task via AI assistant to update later
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_create_init",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Initial Task For Update",
                        "type": "task",
                        "priority": "low",
                        "story_points": 1,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )
    chat_init = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "Create task Initial Task For Update"},
        headers=headers,
    )
    assert chat_init.status_code == 200
    init_action_id = chat_init.json()["data"]["pending_action"]["action_id"]
    confirm_init = await client.post(
        f"/api/v1/ai-assistant/actions/{init_action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_init.status_code == 200
    task_data = confirm_init.json()["data"]["task"]
    task_key = task_data["key"]
    task_id = task_data["task_id"]

    # 2. Mock LLM calling update_task
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_update_1",
                    name="update_task",
                    arguments={
                        "task_key": task_key,
                        "title": "Updated Task Title",
                        "priority": "critical",
                        "story_points": 5,
                        "status": "in_progress",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Update task {task_key} title to Updated Task Title, priority to critical, and points to 5"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    body = chat_resp.json()
    assert body["success"] is True
    data = body["data"]

    assert data["action_state"] == "REQUIRES_CONFIRMATION"
    assert "pending_action" in data
    pending = data["pending_action"]
    assert pending["action_type"] == "update_task"
    proposed = pending["proposed_task"]
    assert proposed["task_id"] == task_id
    assert proposed["key"] == task_key
    assert proposed["title"] == "Updated Task Title"
    assert proposed["priority"] == "critical"
    assert proposed["story_points"] == 5
    assert proposed["status"] in ["in_progress", "In Progress"]
    assert f"updates for task '{task_key}'" in data["message"]

    action_id = pending["action_id"]

    # 3. Confirm the update
    confirm_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    c_data = confirm_resp.json()["data"]
    assert c_data["status"] == "completed"
    assert f"Task '{task_key}' updated successfully." in c_data["message"]
    assert c_data["task"]["title"] == "Updated Task Title"
    assert c_data["task"]["priority"] == "critical"

    # 4. Verify updated task in project API
    get_resp = await client.get(
        f"/api/v1/projects/{project_id}/tasks/{task_id}",
        headers=headers,
    )
    assert get_resp.status_code == 200
    updated_db = get_resp.json()["data"]
    assert updated_db["title"] == "Updated Task Title"
    assert updated_db["priority"] == "critical"
    assert updated_db["story_points"] == 5
    assert updated_db["status"] in ["in_progress", "In Progress"]


@pytest.mark.anyio
async def test_update_task_unassign_and_sprint(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test updating task to unassign assignee and clear sprint."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]
    sprint_id = app_context["sprint_a_id"]

    # 1. Create task with assignee 'me' and sprint
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_create_sprint_task",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": "Implement payment gateway webhooks",
                        "type": "task",
                        "priority": "medium",
                        "assignee": "me",
                        "sprint_id": sprint_id,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )
    chat_init = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": "Create a task titled 'Implement payment gateway webhooks' assigned to me in sprint",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert chat_init.status_code == 200
    assert chat_init.json()["data"]["pending_action"] is not None, f"Expected pending_action but got: {chat_init.json()['data']}"
    init_action_id = chat_init.json()["data"]["pending_action"]["action_id"]
    confirm_init = await client.post(
        f"/api/v1/ai-assistant/actions/{init_action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_init.status_code == 200
    task_data = confirm_init.json()["data"]["task"]
    task_key = task_data["key"]
    task_id = task_data["task_id"]
    assert task_data["assignee_id"] is not None

    # 2. Mock LLM calling update_task to unassign and clear sprint
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_update_2",
                    name="update_task",
                    arguments={
                        "task_key": task_key,
                        "clear_assignee": True,
                        "clear_sprint": True,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "message": f"Unassign {task_key} and remove it from sprint",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert chat_resp.status_code == 200
    pending = chat_resp.json()["data"]["pending_action"]
    assert pending["proposed_task"]["assignee_id"] is None
    assert pending["proposed_task"]["sprint_id"] is None

    action_id = pending["action_id"]

    # Confirm
    confirm_resp = await client.post(
        f"/api/v1/ai-assistant/confirm/{action_id}",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_resp.status_code == 200

    # Verify task in project API has null assignee and null sprint
    get_resp = await client.get(
        f"/api/v1/projects/{project_id}/tasks/{task_id}",
        headers=headers,
    )
    assert get_resp.status_code == 200
    data = get_resp.json()["data"]
    assert data.get("assignee_id") is None
    assert data.get("assignee") is None
    assert data.get("sprint_id") is None


@pytest.mark.anyio
async def test_update_task_validation_not_found_and_cross_org(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test update_task with unknown task_key and cross-org access protection."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Non-existent task key
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_update_3",
                    name="update_task",
                    arguments={
                        "task_key": "NONEXISTENT-9999",
                        "priority": "high",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "Update NONEXISTENT-9999 to high priority"},
        headers=headers,
    )
    assert chat_resp.status_code in [400, 404]













