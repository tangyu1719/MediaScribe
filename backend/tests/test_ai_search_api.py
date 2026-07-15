"""AI 辅助搜索 SDK — HTTP API 冒烟（构造数据 + TestClient）。"""
from __future__ import annotations

from unittest.mock import patch

from tests.conftest import client
from tests.fixtures.ai_search_testset import MOCK_SKILLS, MOCK_TASKS, MOCK_TOOLS


def test_api_list_providers(admin_headers):
    resp = client.get("/api/ai-search/providers", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    ids = {p["provider_id"] for p in body.get("providers") or []}
    assert "builtin_tools" in ids
    assert "task_queue" in ids
    assert "skills" in ids


@patch("app.services.builtin_tools.list_builtin_tools", return_value=list(MOCK_TOOLS))
@patch("app.services.task_manager.list_tasks", return_value=[dict(t) for t in MOCK_TASKS])
@patch("app.services.skill_registry.list_skills", return_value=[dict(s) for s in MOCK_SKILLS])
def test_api_query_multi_provider(_skills, _tasks, _tools, admin_headers):
    resp = client.post(
        "/api/ai-search/query",
        headers=admin_headers,
        json={
            "query": "rag",
            "limit": 5,
            "providers": ["builtin_tools", "task_queue", "skills"],
            "use_llm_expand": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("count", 0) >= 2
    assert set(body.get("providers_used") or []) & {"builtin_tools", "task_queue", "skills"}
    items = body.get("items") or []
    assert all("title" in it and "score" in it and "provider_id" in it for it in items)


def test_api_query_missing_query(admin_headers):
    resp = client.post("/api/ai-search/query", headers=admin_headers, json={"query": ""})
    assert resp.status_code == 400
