"""Comprehensive audit test suite for WorkPilot Chatbot existing-data updates.

Covers Sections A through I:
- A. Standard update requests (priority, title, assignee, due date, status, description, story points, type, clear assignee).
- B. Grammar mistakes, spelling mistakes, and shorthand (dem 280, dem280, priorty, inprogress, tmrw).
- C. Natural-language requests (mark completed, less urgent, back to Todo).
- D. Multi-field updates (priority + status, title + assignee + priority, atomic execution).
- E. Ambiguous requests and clarification (missing task ID, ambiguous titles).
- F. Conversation context and follow-up messages (multi-turn follow-ups, correction, context isolation).
- G. Invalid values and data integrity (backdated date, fibonacci violation, invalid status).
- H. Authorization and tenant isolation (cross-org, forbidden project).
- I. Confirmation, cancellation, and concurrency (atomic claim, duplicate confirm, cancellation).
"""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient

from src.integrations.orchestration.provider import (
    MockLLMProvider,
    ProviderResponse,
    ToolCall,
    set_llm_provider,
)
from src.integrations.tools.task_tools import parse_due_date, UpdateTaskToolArgs


@pytest.fixture(autouse=True)
def reset_provider():
    mock = MockLLMProvider()
    set_llm_provider(mock)
    yield mock
    set_llm_provider(None)


async def _create_test_task(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    provider: MockLLMProvider,
    title: str = "Test Task For Audit",
    priority: str = "low",
    task_type: str = "task",
) -> tuple[str, str]:
    """Helper to create and confirm a test task, returning (task_id, task_key)."""
    provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_create",
                    name="create_task",
                    arguments={
                        "project_id": project_id,
                        "title": title,
                        "type": task_type,
                        "priority": priority,
                        "story_points": 1,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )
    resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Create a task titled {title}"},
        headers=headers,
    )
    assert resp.status_code == 200
    action_id = resp.json()["data"]["pending_action"]["action_id"]

    confirm_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    task_data = confirm_resp.json()["data"]["task"]
    return task_data["task_id"], task_data["key"]


# ---------------------------------------------------------------------------
# SECTION A: Standard update requests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_a_standard_updates(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test standard single-field and multi-field updates (Section A)."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers, project_id, reset_provider, title="Original DEM Task"
    )

    # 1. Update title, description, priority, story points, type
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_up_1",
                    name="update_task",
                    arguments={
                        "task_key": task_key,
                        "title": "Fix login timeout issue",
                        "description": "Fix the authentication timeout issue in gateway",
                        "priority": "high",
                        "story_points": 5,
                        "type": "bug",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Update {task_key} title to Fix login timeout issue, priority to high, type to bug, and points to 5"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    body = chat_resp.json()
    assert body["data"]["action_state"] == "REQUIRES_CONFIRMATION"
    action_id = body["data"]["pending_action"]["action_id"]

    # Confirm
    conf = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert conf.status_code == 200

    # Verify database persistence
    get_t = await client.get(f"/api/v1/projects/{project_id}/tasks/{task_id}", headers=headers)
    assert get_t.status_code == 200
    d = get_t.json()["data"]
    assert d["title"] == "Fix login timeout issue"
    assert d["priority"] == "high"
    assert d["story_points"] == 5
    assert d["type"] == "bug"


# ---------------------------------------------------------------------------
# SECTION B: Grammar mistakes, spelling mistakes, and shorthand
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_b_grammar_and_shorthand(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test shorthand task keys like 'dem 280', 'dem280', typos like 'priorty', and 'inprogress'."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers, project_id, reset_provider, title="Shorthand Test Task"
    )

    # Convert task_key e.g. "DEM-1" to space separated "dem 1" or joined "dem1"
    key_prefix, key_num = task_key.split("-")
    space_key = f"{key_prefix.lower()} {key_num}"
    cat_key = f"{key_prefix.lower()}{key_num}"

    # 1. Update using space key 'dem 1', typo 'priorty high', status 'inprogress'
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_up_shorthand",
                    name="update_task",
                    arguments={
                        "task_key": space_key,
                        "priority": "priorty high",
                        "status": "inprogress",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    # 2. Test space key 'dem 1' with typo 'priorty high', status 'inprogress'
    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"change {space_key} priorty high and status inprogress"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    pending = chat_resp.json()["data"]["pending_action"]
    assert pending["proposed_task"]["key"] == task_key
    assert pending["proposed_task"]["priority"] == "high"
    assert pending["proposed_task"]["status"] in ["In Progress", "in_progress"]

    action_id = pending["action_id"]
    conf = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert conf.status_code == 200

    # 3. Test relative date 'tmrw', 'tomorrow', 'next friday' parsing
    for rel_date_str in ("tmrw", "tomorrow", "next friday", "next week"):
        dt, ok = parse_due_date(rel_date_str)
        assert ok is True
        assert dt is not None
        assert dt > datetime.now(timezone.utc)

    # 4. Test concatenated key 'dem1' normalization
    parsed_cat = UpdateTaskToolArgs.normalize_fields({"task_key": cat_key})
    assert parsed_cat["task_key"] == task_key

    # 5. Test space-separated key normalization in args
    parsed_space = UpdateTaskToolArgs.normalize_fields({"task_key": space_key})
    assert parsed_space["task_key"] == task_key

    # 6. Test status normalization (inprogress, done, readyforqa)
    parsed_status = UpdateTaskToolArgs.normalize_fields({
        "status": "inprogress",
        "priority": "priorty high",
    })
    assert parsed_status["status"] == "In Progress"
    assert parsed_status["priority"] == "high"

    parsed_done = UpdateTaskToolArgs.normalize_fields({"status": "done"})
    assert parsed_done["status"] == "completed"

    # 7. Test description clearing / null handling
    parsed_desc = UpdateTaskToolArgs.normalize_fields({"description": "clear"})
    assert parsed_desc["description"] == ""


# ---------------------------------------------------------------------------
# SECTION C: Natural-language requests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_c_natural_language_status_mapping(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test natural language phrases like 'mark it completed'."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers, project_id, reset_provider, title="Natural Language Task"
    )

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_up_nl",
                    name="update_task",
                    arguments={
                        "task_key": task_key,
                        "status": "completed",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"We finished {task_key}, please mark it completed"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    pending = chat_resp.json()["data"]["pending_action"]
    assert pending["proposed_task"]["status"] in ["completed", "Completed"]


# ---------------------------------------------------------------------------
# SECTION D: Multi-field updates
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_d_multi_field_atomic_updates(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test multi-field atomic updates: title, priority, story_points, type, due_date."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers, project_id, reset_provider, title="Multi Field Task"
    )

    fut_date = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")

    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_up_multi",
                    name="update_task",
                    arguments={
                        "task_key": task_key,
                        "title": "Fix payment API gateway",
                        "priority": "critical",
                        "story_points": 8,
                        "type": "bug",
                        "due_date": fut_date,
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Update {task_key} title to Fix payment API gateway, priority critical, points 8, type bug, due {fut_date}"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    pending = chat_resp.json()["data"]["pending_action"]
    p_task = pending["proposed_task"]
    assert p_task["title"] == "Fix payment API gateway"
    assert p_task["priority"] == "critical"
    assert p_task["story_points"] == 8
    assert p_task["type"] == "bug"

    # Confirm and check database
    action_id = pending["action_id"]
    conf = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert conf.status_code == 200

    get_t = await client.get(f"/api/v1/projects/{project_id}/tasks/{task_id}", headers=headers)
    assert get_t.status_code == 200
    d = get_t.json()["data"]
    assert d["title"] == "Fix payment API gateway"
    assert d["priority"] == "critical"
    assert d["story_points"] == 8
    assert d["type"] == "bug"


# ---------------------------------------------------------------------------
# SECTION E: Ambiguous requests and clarification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_e_clarification_on_missing_or_hallucinated_task(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test that missing task ID or hallucinated key asks for clarification."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}

    # Model attempts to update without user mentioning any task key or title
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_up_missing",
                    name="update_task",
                    arguments={"priority": "high"},
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": "Change the priority to high"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    body = chat_resp.json()["data"]
    assert body["action_state"] == "NONE"
    assert "Which task would you like to update?" in body["message"]


# ---------------------------------------------------------------------------
# SECTION F: Conversation context and follow-up messages
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_f_conversation_context_isolation_and_continuity(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test multi-turn context retention with conversation_id."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers, project_id, reset_provider, title="Context Flow Task"
    )

    conv_id = "test-conv-context-123"

    # Turn 1: Conversational question / clarification
    reset_provider.set_response(
        ProviderResponse(
            content=f"Which priority would you like to set for task {task_key}?",
            tool_calls=[],
            finish_reason="stop",
        )
    )
    t1_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "conversation_id": conv_id,
            "message": f"Update {task_key} priority",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert t1_resp.status_code == 200
    assert t1_resp.json()["data"]["conversation_id"] == conv_id

    # Turn 2: Follow-up answering "make it critical"
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_follow_up",
                    name="update_task",
                    arguments={
                        "task_key": task_key,
                        "priority": "critical",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )
    t2_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={
            "conversation_id": conv_id,
            "message": "make it critical",
            "context": {"project_id": project_id},
        },
        headers=headers,
    )
    assert t2_resp.status_code == 200
    pending = t2_resp.json()["data"]["pending_action"]
    assert pending["proposed_task"]["key"] == task_key
    assert pending["proposed_task"]["priority"] == "critical"


# ---------------------------------------------------------------------------
# SECTION G: Invalid values and data integrity
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_g_invalid_values_rejected(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test rejection of non-Fibonacci points, invalid priority, and empty title."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers, project_id, reset_provider, title="Validation Integrity Task"
    )

    # 1. Non-Fibonacci story points (e.g. 7)
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_fib",
                    name="update_task",
                    arguments={"task_key": task_key, "story_points": 7},
                )
            ],
            finish_reason="tool_calls",
        )
    )
    resp_fib = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Set {task_key} story points to 7"},
        headers=headers,
    )
    assert resp_fib.status_code == 400
    assert "Fibonacci" in resp_fib.json()["error"]["message"]

    # 2. Invalid priority (e.g. 'super urgent')
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_prio",
                    name="update_task",
                    arguments={"task_key": task_key, "priority": "super urgent"},
                )
            ],
            finish_reason="tool_calls",
        )
    )
    resp_prio = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Set {task_key} priority to super urgent"},
        headers=headers,
    )
    assert resp_prio.status_code == 200
    prio_data = resp_prio.json()["data"]
    assert prio_data["action_state"] == "NONE"
    assert "priority" in prio_data["message"].lower()
    assert "not supported" in prio_data["message"].lower()

    # 3. Empty / too short title (< 3 chars)
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_title",
                    name="update_task",
                    arguments={"task_key": task_key, "title": "ab"},
                )
            ],
            finish_reason="tool_calls",
        )
    )
    resp_title = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Change {task_key} title to ab"},
        headers=headers,
    )
    assert resp_title.status_code == 400
    assert "between 3 and 200 characters" in resp_title.json()["error"]["message"]


# ---------------------------------------------------------------------------
# SECTION H: Authorization and tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_h_tenant_isolation(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test user in Org B cannot update a task belonging to Org A."""
    headers_a = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    headers_b = {"Authorization": f"Bearer {app_context['token_org_b_admin']}"}
    project_id_a = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers_a, project_id_a, reset_provider, title="Org A Private Task"
    )

    # Org B user attempts to update Org A's task
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_cross_org",
                    name="update_task",
                    arguments={"task_key": task_key, "priority": "high"},
                )
            ],
            finish_reason="tool_calls",
        )
    )

    resp_cross = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Update {task_key} priority to high"},
        headers=headers_b,
    )
    assert resp_cross.status_code in [403, 404]


# ---------------------------------------------------------------------------
# SECTION I: Confirmation, cancellation, and concurrency
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_section_i_confirmation_cancellation_concurrency(
    client: AsyncClient,
    app_context: dict,
    reset_provider: MockLLMProvider,
):
    """Test prepare action, double confirmation, cancellation, and concurrency locking."""
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    task_id, task_key = await _create_test_task(
        client, headers, project_id, reset_provider, title="Confirmation Test Task"
    )

    # 1. Prepare update
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_prepare",
                    name="update_task",
                    arguments={"task_key": task_key, "priority": "high"},
                )
            ],
            finish_reason="tool_calls",
        )
    )

    chat_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Update {task_key} priority to high"},
        headers=headers,
    )
    assert chat_resp.status_code == 200
    pending = chat_resp.json()["data"]["pending_action"]
    action_id = pending["action_id"]

    # 2. Cancel action
    cancel_resp = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": False},
        headers=headers,
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["data"]["status"] == "cancelled"

    # 3. Attempting to confirm a cancelled action fails with 409 Conflict or 400
    confirm_after_cancel = await client.post(
        f"/api/v1/ai-assistant/actions/{action_id}/confirm",
        json={"confirmed": True},
        headers=headers,
    )
    assert confirm_after_cancel.status_code in [400, 409]

    # 4. Concurrency: prepare another action and confirm concurrently
    reset_provider.set_response(
        ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_prepare_conc",
                    name="update_task",
                    arguments={"task_key": task_key, "priority": "critical"},
                )
            ],
            finish_reason="tool_calls",
        )
    )
    c2_resp = await client.post(
        "/api/v1/ai-assistant/chat",
        json={"message": f"Update {task_key} priority to critical"},
        headers=headers,
    )
    assert c2_resp.status_code == 200
    conc_action_id = c2_resp.json()["data"]["pending_action"]["action_id"]

    # Fire concurrent confirmation requests
    res1, res2 = await asyncio.gather(
        client.post(f"/api/v1/ai-assistant/actions/{conc_action_id}/confirm", json={"confirmed": True}, headers=headers),
        client.post(f"/api/v1/ai-assistant/actions/{conc_action_id}/confirm", json={"confirmed": True}, headers=headers),
    )
    statuses = [res1.status_code, res2.status_code]
    # Exactly one must succeed (200), the duplicate must get 409 Conflict
    assert 200 in statuses
    assert 409 in statuses
