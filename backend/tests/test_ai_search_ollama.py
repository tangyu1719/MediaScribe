"""AI 辅助搜索 — Ollama 配置与健康检查测试。"""
from __future__ import annotations

import pytest

from app.services.ai_search_sdk.ollama_config import (
    ai_search_ollama_settings,
    apply_ai_search_ollama_config,
    get_ai_search_ollama_node,
    probe_ai_search_ollama_health,
    resolve_ai_search_llm_nodes,
)
from tests.conftest import client


def test_ollama_settings_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2:0.5b")
    monkeypatch.setenv("OLLAMA_AI_SEARCH_TIMEOUT_SEC", "15")
    monkeypatch.setenv("OLLAMA_AI_SEARCH_ENABLED", "1")
    cfg = ai_search_ollama_settings()
    assert cfg["ollama_base_url"] == "http://127.0.0.1:11434/v1"
    assert cfg["ollama_model"] == "qwen2:0.5b"
    assert cfg["timeout_sec"] == 15.0
    assert cfg["enabled"] is True
    assert cfg["openai_chat_url"].endswith("/chat/completions")


def test_ollama_node_id():
    node = get_ai_search_ollama_node()
    assert node is not None
    assert node.id == "ollama_ai_search"
    assert node.provider == "openai_compatible"


def test_resolve_nodes_ollama_first(monkeypatch):
    monkeypatch.setenv("OLLAMA_AI_SEARCH_GATEWAY_FALLBACK", "0")
    nodes = resolve_ai_search_llm_nodes()
    assert nodes
    assert nodes[0].id == "ollama_ai_search"


def test_apply_runtime_config(monkeypatch):
    monkeypatch.delenv("OLLAMA_AI_SEARCH_TIMEOUT_SEC", raising=False)
    cfg = apply_ai_search_ollama_config({"timeout_sec": 18, "enabled": True})
    assert cfg["timeout_sec"] == 18.0


def test_api_ollama_config_get(admin_headers):
    resp = client.get("/api/ai-search/ollama/config", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("configured") is True
    assert "ollama_base_url" in body
    assert "timeout_sec" in body


def test_api_ollama_health(admin_headers):
    resp = client.get("/api/ai-search/ollama/health", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert "tags_health" in body
    assert "chat_probe" in body


@pytest.mark.integration
def test_live_ollama_expand_or_skip(monkeypatch):
    """真实 Ollama 探测：服务不可用或 CUDA 崩溃时跳过，不阻塞 CI。"""
    import httpx

    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3)
        if tags.status_code != 200:
            pytest.skip("Ollama 未运行")
    except Exception:
        pytest.skip("Ollama 未运行")

    monkeypatch.setenv("OLLAMA_AI_SEARCH_GATEWAY_FALLBACK", "0")
    monkeypatch.setenv("OLLAMA_AI_SEARCH_TIMEOUT_SEC", "20")
    from app.services.ai_search_sdk.llm import expand_query_llm

    result = expand_query_llm("rag 知识库", domain_hint="工具检索")
    if not result.get("llm_powered"):
        pytest.skip(f"Ollama 推理不可用: node={result.get('node_id')}")
    assert result.get("node_id") == "ollama_ai_search"
    assert result.get("expanded_terms")
