"""agent_pipeline / chat_feedback / pipeline_llm 回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def feedback_tmp(monkeypatch, request):
    """使用项目内临时目录，避免 Windows 系统 Temp 权限问题。"""
    base = Path(__file__).resolve().parents[1] / ".pytest_tmp" / "feedback" / request.node.name
    if base.exists():
        for p in base.glob("*.json"):
            p.unlink()
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.chat_feedback._FEEDBACK_DIR", base)
    return base


def test_run_agent_pipeline_rule_path():
    from app.services.agent_pipeline import run_agent_pipeline

    r = run_agent_pipeline("Milvus 向量库怎么连接？")
    assert r.intent_label == "知识库"
    assert r.pipeline_source in ("rule", "llm", "llm_gateway")
    assert r.rewritten_query


def test_merge_pipeline_into_snapshot():
    from app.services.agent_pipeline import PipelineResult, merge_pipeline_into_snapshot

    pr = PipelineResult(
        original_query="q",
        intent="kb",
        intent_label="知识库",
        rewritten_query="如何连接 Milvus",
        query_keywords=["Milvus"],
        pipeline_source="rule",
    )
    snap = merge_pipeline_into_snapshot({}, pr)
    assert snap["domain"] == "知识库"
    assert snap["domain_code"] == "kb"


def test_chat_feedback_save_and_session_list(feedback_tmp):
    from app.services import chat_feedback as cf

    row = cf.save_feedback(
        "sess_a",
        0,
        rating=4,
        intent_liked=True,
        detected_intent={"domain": "知识库", "domain_code": "kb"},
        user_id="u1",
    )
    assert row["rating"] == 4
    assert cf.get_feedback("sess_a", 0)["intent_liked"] is True
    items = cf.list_feedback_for_session("sess_a")
    assert len(items) == 1


def test_feedback_dashboard_empty(feedback_tmp):
    from app.services import chat_feedback as cf
    from app.services.feedback_analytics import compute_feedback_dashboard

    dash = compute_feedback_dashboard(days=7)
    assert dash["total_feedback"] == 0
    assert "rating_histogram" in dash


def test_build_intent_alternatives_builtin():
    from app.services.intent_suggest import build_intent_alternatives

    alt = build_intent_alternatives(
        question="知识库怎么用",
        answer="您可以在 RAG 页面导入文档并进行检索。",
        detected_intent="kb",
        detected_label="知识库",
        include_llm=False,
    )
    assert alt["detected_intent_label"] == "知识库"
    assert len(alt["builtin"]) >= 1


def test_structured_json_preprocess():
    from app.services.structured_json import parse_preprocess_output

    raw = '{"intent":"kb","rewritten_query":"如何连接 Milvus","query_keywords":["Milvus"],"retrieval_terms":[]}'
    parsed = parse_preprocess_output(raw)
    assert parsed and parsed["intent"] == "kb"


def test_pipeline_llm_node_and_concurrency_guard(monkeypatch):
    from app.services.pipeline_llm import (
        PipelineConcurrencyGuard,
        get_pipeline_llm,
        pipeline_settings,
        probe_ollama_health,
    )

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2:0.5b")
    monkeypatch.setenv("OLLAMA_PIPELINE_CONCURRENCY", "2")

    node = get_pipeline_llm()
    assert node is not None
    assert node.model == "qwen2:0.5b"
    assert pipeline_settings()["pipeline_concurrency"] == 2

    health = probe_ollama_health()
    assert health["id"] == "ollama"
    assert health["status"] in ("ok", "warn")

    with PipelineConcurrencyGuard():
        with PipelineConcurrencyGuard():
            pass


def test_resolve_chat_api_credentials_prefers_qa_route():
    from app.services.ai_chat import resolve_chat_api_credentials

    cfg = {
        "volcengine_api_key": "k",
        "volcengine_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "ai_chat_model": "ep-old",
        "gateway_task_type_route": {"qa": "ep-20260616011833-tqqpk", "chat": "ep-20260616011833-tqqpk"},
        "api_gateway_nodes": [],
    }
    creds = resolve_chat_api_credentials(cfg)
    assert creds["model"] == "ep-20260616011833-tqqpk"
