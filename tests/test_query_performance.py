import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_task_list_query_scaling(client: AsyncClient, app_context: dict, query_counter):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    # 1 item
    with query_counter:
        resp_1 = await client.get(
            f"/api/v1/projects/{project_id}/tasks?page=1&page_size=1",
            headers=headers,
        )
        assert resp_1.status_code == 200
        queries_1 = query_counter.count

    # 5 items
    with query_counter:
        resp_5 = await client.get(
            f"/api/v1/projects/{project_id}/tasks?page=1&page_size=5",
            headers=headers,
        )
        assert resp_5.status_code == 200
        queries_5 = query_counter.count

    # The query count difference should not scale linearly with 5x items (no N+1 per task)
    assert queries_5 - queries_1 <= 3, f"Query count grew excessively: 1 item took {queries_1}, 5 items took {queries_5}"


async def test_user_story_list_query_scaling(client: AsyncClient, app_context: dict, query_counter):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    with query_counter:
        resp_1 = await client.get(
            f"/api/v1/projects/{project_id}/user-stories?page=1&page_size=1",
            headers=headers,
        )
        assert resp_1.status_code == 200
        queries_1 = query_counter.count

    with query_counter:
        resp_5 = await client.get(
            f"/api/v1/projects/{project_id}/user-stories?page=1&page_size=5",
            headers=headers,
        )
        assert resp_5.status_code == 200
        queries_5 = query_counter.count

    assert queries_5 - queries_1 <= 3, f"Query count grew from {queries_1} to {queries_5}"


async def test_board_api_query_budget(client: AsyncClient, app_context: dict, query_counter):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    with query_counter:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/board?page=1&page_size=5",
            headers=headers,
        )
        assert resp.status_code == 200
        count = query_counter.count

    assert count <= 15, f"Board query count exceeded budget: executed {count} queries"


async def test_dashboard_overview_query_budget(client: AsyncClient, app_context: dict, query_counter):
    headers = {"Authorization": f"Bearer {app_context['token_org_a_admin']}"}
    project_id = app_context["project_a_id"]

    with query_counter:
        resp = await client.get(
            f"/api/v1/{project_id}/overview",
            headers=headers,
        )
        assert resp.status_code == 200
        count = query_counter.count

    assert count <= 12, f"Dashboard overview query count exceeded budget: executed {count} queries"
