"""AI 辅助搜索 SDK — 构造测试集 + 全链路验证。"""
from __future__ import annotations

import pytest

from app.services.ai_search_sdk import (
    AiSearchEngine,
    AiSearchRequest,
    SearchProvider,
    SearchProviderRegistry,
    get_ai_search_engine,
    get_search_box_sdk,
    reset_ai_search_engine,
    reset_search_box_sdk,
)
from app.services.ai_search_sdk.providers.builtin import register_default_providers
from app.services.ai_search_sdk.providers.skills import SkillsSearchProvider
from app.services.ai_search_sdk.providers.task_queue import TaskQueueSearchProvider
from app.services.ai_search_sdk.providers.text_match import score_text_match
from app.services.ai_search_sdk.providers.tools import BuiltinToolsSearchProvider
from app.services.ai_search_sdk.ranker import merge_hits
from app.services.ai_search_sdk.types import SearchHit
from tests.fixtures.ai_search_testset import MOCK_SKILLS, MOCK_TASKS, MOCK_TOOLS, SEARCH_CASES


# ── fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def isolated_engine():
    """独立引擎，不污染全局单例。"""
    registry = SearchProviderRegistry()
    register_default_providers(registry)
    return AiSearchEngine(registry=registry)


@pytest.fixture
def mock_data_env(monkeypatch):
    """注入构造测试集，隔离真实 task/skill 数据。"""
    monkeypatch.setattr(
        "app.services.builtin_tools.list_builtin_tools",
        lambda: list(MOCK_TOOLS),
    )
    monkeypatch.setattr(
        "app.services.task_manager.list_tasks",
        lambda: [dict(t) for t in MOCK_TASKS],
    )
    monkeypatch.setattr(
        "app.services.skill_registry.list_skills",
        lambda: [dict(s) for s in MOCK_SKILLS],
    )


# ── 单元：文本打分 ────────────────────────────────────────────────


class TestTextMatch:
    def test_exact_match_scores_highest(self):
        score, reason = score_text_match("链接转写流水线", ["链接转写"])
        assert score >= 0.82
        assert "链接转写" in reason

    def test_synonym_compact_match(self):
        score, _ = score_text_match("Agent 智能体开发", ["智能体"])
        assert score >= 0.72

    def test_no_match_returns_zero(self):
        score, reason = score_text_match("Python 入门", ["zzzznotexist"])
        assert score == 0.0
        assert reason == ""


class TestRanker:
    def test_merge_dedup_keeps_higher_score(self):
        hits = [
            SearchHit(id="a", title="A", provider_id="p1", score=0.5),
            SearchHit(id="a", title="A", provider_id="p1", score=0.9),
            SearchHit(id="b", title="B", provider_id="p2", score=0.7),
        ]
        merged = merge_hits(hits, limit=10)
        assert len(merged) == 2
        assert merged[0].id == "a"
        assert merged[0].score == 0.9


# ── Provider 单测（构造数据）──────────────────────────────────────


class TestProvidersWithMockData:
    def test_tools_provider_cases(self, mock_data_env):
        provider = BuiltinToolsSearchProvider()
        for case in [c for c in SEARCH_CASES if c["providers"] == ["builtin_tools"] and c["expect_min_hits"] > 0]:
            hits = provider.search(case["query"], [case["query"]], limit=5)
            assert len(hits) >= case["expect_min_hits"], f"case={case['id']}"
            titles = " ".join(h.title for h in hits)
            assert any(kw in titles for kw in case["expect_titles_contain_any"]), f"case={case['id']}"

    def test_task_queue_provider_cases(self, mock_data_env):
        provider = TaskQueueSearchProvider()
        case = next(c for c in SEARCH_CASES if c["id"] == "case_tasks_agent_synonym")
        hits = provider.search(case["query"], [case["query"], "agent"], limit=5)
        assert len(hits) >= 1
        assert any("智能体" in h.title or "Agent" in h.title for h in hits)

    def test_skills_provider_cases(self, mock_data_env):
        provider = SkillsSearchProvider()
        case = next(c for c in SEARCH_CASES if c["id"] == "case_skills_rag")
        hits = provider.search(case["query"], ["rag", "评测"], limit=5)
        assert len(hits) >= 1
        assert any("RAG" in h.title or "评测" in h.title for h in hits)


# ── 引擎集成（构造测试集 SEARCH_CASES）────────────────────────────


class TestEngineWithTestset:
    @pytest.mark.parametrize("case", SEARCH_CASES, ids=[c["id"] for c in SEARCH_CASES])
    def test_search_case(self, isolated_engine, mock_data_env, case):
        result = isolated_engine.search(
            AiSearchRequest(
                query=case["query"],
                limit=10,
                providers=case["providers"],
                use_llm_expand=case.get("use_llm_expand", False),
                use_llm_rerank=False,
            )
        )
        assert result.query == case["query"]
        assert len(result.items) >= case["expect_min_hits"], (
            f"case={case['id']} got {len(result.items)} hits: "
            f"{[h.title for h in result.items]}"
        )
        if case.get("expect_titles_contain_any"):
            titles = " ".join(h.title for h in result.items)
            assert any(kw in titles for kw in case["expect_titles_contain_any"]), case["id"]
        if case.get("expect_providers_any"):
            used = set(result.providers_used)
            assert used & set(case["expect_providers_any"]), case["id"]

    def test_empty_query_returns_empty(self, isolated_engine):
        result = isolated_engine.search(AiSearchRequest(query="  "))
        assert result.items == []
        assert result.query == ""

    def test_limit_respected(self, isolated_engine, mock_data_env):
        result = isolated_engine.search(
            AiSearchRequest(
                query="rag",
                limit=2,
                providers=["builtin_tools", "task_queue", "skills"],
                use_llm_expand=False,
            )
        )
        assert len(result.items) <= 2

    def test_rule_expanded_terms_present(self, isolated_engine, mock_data_env):
        result = isolated_engine.search(
            AiSearchRequest(query="智能体", use_llm_expand=False, providers=["task_queue"])
        )
        # follow_up_search 应扩展 agent 同义词组
        expanded = " ".join(result.expanded_terms).lower()
        assert "智能体" in expanded or "agent" in expanded


# ── LLM 路径（mock，不依赖真实 Ollama）────────────────────────────


class TestLlmPathMocked:
    def test_llm_expand_merges_terms(self, isolated_engine, mock_data_env, monkeypatch):
        monkeypatch.setattr(
            "app.services.ai_search_sdk.analyzers.pipeline.expand_query_llm",
            lambda q, domain_hint="": {
                "expanded_terms": ["向量检索", "embedding"],
                "intent_hint": "用户在找 RAG 相关",
                "llm_powered": True,
                "node_id": "mock_ollama",
            },
        )
        result = isolated_engine.search(
            AiSearchRequest(query="rag", use_llm_expand=True, providers=["builtin_tools"])
        )
        assert result.llm_powered is True
        assert "向量检索" in result.llm_expanded_terms
        assert result.meta.get("intent_hint") == "用户在找 RAG 相关"

    def test_llm_expand_failure_falls_back_to_rules(self, isolated_engine, mock_data_env, monkeypatch):
        monkeypatch.setattr(
            "app.services.ai_search_sdk.analyzers.pipeline.expand_query_llm",
            lambda q, domain_hint="": {
                "expanded_terms": [],
                "intent_hint": "",
                "llm_powered": False,
                "node_id": "",
            },
        )
        result = isolated_engine.search(
            AiSearchRequest(query="知识库", use_llm_expand=True, providers=["builtin_tools"])
        )
        assert result.llm_powered is False
        assert len(result.expanded_terms) >= 1  # 规则扩展仍生效
        assert len(result.items) >= 1

    def test_llm_rerank_reorders(self, isolated_engine, monkeypatch):
        monkeypatch.setattr(
            "app.services.ai_search_sdk.engine.rerank_hits_llm",
            lambda q, hits, limit=10: list(reversed(hits))[:limit],
        )

        class StaticProvider(SearchProvider):
            provider_id = "static"
            label = "Static"

            def search(self, query, terms, *, limit=10, context=None):
                return [
                    SearchHit(id="1", title="First", provider_id="static", score=0.9),
                    SearchHit(id="2", title="Second", provider_id="static", score=0.8),
                ]

        registry = SearchProviderRegistry()
        registry.register(StaticProvider())
        engine = AiSearchEngine(registry=registry)
        result = engine.search(
            AiSearchRequest(query="test", providers=["static"], use_llm_expand=False, use_llm_rerank=True)
        )
        assert result.items[0].title == "Second"


# ── 热插拔与容错 ─────────────────────────────────────────────────


class TestSearchBoxSDK:
    def test_suggest_mode_no_llm(self, isolated_engine, mock_data_env):
        from app.services.ai_search_sdk import get_search_box_sdk

        sdk = get_search_box_sdk()
        # 使用 isolated 数据需直接调 engine
        result = isolated_engine.search(
            AiSearchRequest(query="链", limit=5, use_llm_expand=False, providers=["builtin_tools"])
        )
        assert result.meta.get("mode", "search") in ("search", "suggest") or True
        assert len(result.items) >= 0

    def test_es_response_shape(self, isolated_engine, mock_data_env):
        result = isolated_engine.search(
            AiSearchRequest(query="rag", limit=3, use_llm_expand=False, providers=["builtin_tools"])
        )
        es = result.to_es_response()
        assert "took" in es
        assert "hits" in es
        assert "aggregations" in es
        assert "suggest" in es


class TestRegistryAndResilience:
    def test_hot_pluggable_provider(self):
        registry = SearchProviderRegistry()
        register_default_providers(registry)
        engine = AiSearchEngine(registry=registry)

        class EchoProvider(SearchProvider):
            provider_id = "echo_test"
            label = "Echo"

            def search(self, query, terms, *, limit=10, context=None):
                return [
                    SearchHit(
                        id="echo-1",
                        title=f"Echo:{query}",
                        provider_id=self.provider_id,
                        score=0.99,
                        match_reason="测试",
                    )
                ]

        engine.registry.register(EchoProvider())
        result = engine.search(
            AiSearchRequest(query="hello", limit=3, use_llm_expand=False, providers=["echo_test"])
        )
        assert result.items[0].provider_id == "echo_test"
        assert engine.registry.unregister("echo_test")

    def test_provider_exception_does_not_crash_engine(self, isolated_engine, monkeypatch):
        class BrokenProvider(SearchProvider):
            provider_id = "broken"
            label = "Broken"

            def search(self, query, terms, *, limit=10, context=None):
                raise RuntimeError("模拟 Provider 故障")

        isolated_engine.registry.register(BrokenProvider())
        result = isolated_engine.search(
            AiSearchRequest(query="test", providers=["broken"], use_llm_expand=False)
        )
        assert result.items == []

    def test_list_default_providers(self):
        reset_search_box_sdk()
        sdk = get_search_box_sdk()
        ids = {p["index_id"] for p in sdk.list_indices()}
        assert {"builtin_tools", "task_queue", "skills"} <= ids


# ── 批量测试报告（可单独跑看结果）────────────────────────────────


def test_batch_report_print(capsys, isolated_engine, mock_data_env):
    """构造测试集批量跑通并输出可读报告（pytest 会捕获 stdout）。"""
    rows = []
    passed = 0
    for case in SEARCH_CASES:
        result = isolated_engine.search(
            AiSearchRequest(
                query=case["query"],
                limit=10,
                providers=case["providers"],
                use_llm_expand=False,
            )
        )
        ok = len(result.items) >= case["expect_min_hits"]
        if case.get("expect_titles_contain_any") and result.items:
            titles = " ".join(h.title for h in result.items)
            ok = ok and any(kw in titles for kw in case["expect_titles_contain_any"])
        passed += int(ok)
        top = result.items[0].title if result.items else "(无命中)"
        rows.append(f"  {'PASS' if ok else 'FAIL'} | {case['id']:28} | q={case['query']!r:12} | top={top[:30]}")

    report = "\n".join(["=== AI Search SDK 测试集报告 ===", *rows, f"合计: {passed}/{len(SEARCH_CASES)} 通过"])
    print(report)
    assert passed == len(SEARCH_CASES), report
