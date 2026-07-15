"""SearchBox SDK — ES 风格 API 测试。"""
from __future__ import annotations

from unittest.mock import patch

from tests.conftest import client
from tests.fixtures.ai_search_testset import MOCK_SKILLS, MOCK_TASKS, MOCK_TOOLS


@patch("app.services.builtin_tools.list_builtin_tools", return_value=list(MOCK_TOOLS))
@patch("app.services.task_manager.list_tasks", return_value=[dict(t) for t in MOCK_TASKS])
@patch("app.services.skill_registry.list_skills", return_value=[dict(s) for s in MOCK_SKILLS])
def test_search_box_search(_s, _t, _tools, admin_headers):
    resp = client.post(
        "/api/search-box/_search",
        headers=admin_headers,
        json={"q": "rag", "size": 5, "indices": ["builtin_tools", "skills"], "use_llm_expand": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("count", 0) >= 1
    assert "hits" in body or "items" in body
    assert "aggregations" in body
    assert "suggest" in body


@patch("app.services.builtin_tools.list_builtin_tools", return_value=list(MOCK_TOOLS))
def test_search_box_suggest_get(_tools, admin_headers):
    resp = client.get("/api/search-box/_suggest?q=链接&size=5", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("meta", {}).get("mode") == "suggest"


@patch("app.services.builtin_tools.list_builtin_tools", return_value=list(MOCK_TOOLS))
@patch("app.services.task_manager.list_tasks", return_value=[dict(t) for t in MOCK_TASKS])
@patch("app.services.skill_registry.list_skills", return_value=[dict(s) for s in MOCK_SKILLS])
def test_search_box_es_format(_s, _t, _tools, admin_headers):
    resp = client.post(
        "/api/search-box/_search",
        headers=admin_headers,
        json={"q": "rag", "format": "es", "use_llm_expand": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert "hits" in body
    assert "hits" in body["hits"]
    assert "took" in body


def test_search_box_indices(admin_headers):
    resp = client.get("/api/search-box/indices", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    ids = {x["index_id"] for x in body.get("indices") or []}
    assert "builtin_tools" in ids
