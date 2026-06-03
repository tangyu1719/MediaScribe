"""延续主任务：须继承 needs_rag 且 react_entry 能预取知识库。"""
from __future__ import annotations

import asyncio

import pytest

from app.services.chat_context_memory import (
    enrich_snapshot_for_continue_main,
    infer_needs_rag_from_task_text,
    resolve_intent_mode,
)
from app.services.chat_graph_nodes import node_react_entry


def test_infer_needs_rag_from_mcp_task_query():
    q = "搜素知识库中关于MCP技术相关的文档进行总结反馈。"
    assert infer_needs_rag_from_task_text(q) is True


def test_enrich_snapshot_for_continue_inherits_rag():
    cur = {
        "task_id": "task_554272e197cc",
        "user_query": "搜素知识库中关于MCP技术相关的文档进行总结反馈。",
        "query_summary": "MCP技术知识库总结",
    }
    snap = enrich_snapshot_for_continue_main(
        {"needs_rag": False, "rewritten_query": "继续"},
        cur_task=cur,
        task_id="task_554272e197cc",
        rag_prefetch=True,
    )
    assert snap["needs_rag"] is True
    assert "MCP" in str(snap.get("rewritten_query") or "")


def test_short_reply_with_history_not_simple():
    hist = [
        {
            "task_id": "task_554272e197cc",
            "user_query": "检索MCP技术知识库并总结",
            "status": "resolved",
            "task_kind": "main",
        }
    ]
    dec = resolve_intent_mode(
        "嗯",
        cur_task=None,
        is_simple_heuristic=True,
        main_task_history=hist,
    )
    assert dec["mode"] == "continue_main"
    assert dec["task_id"] == "task_554272e197cc"


@pytest.mark.asyncio
async def test_continue_execute_react_entry_prefetches_rag():
    """续接主任务走 continue_execute 时，react_entry 必须真实 kb_search。"""
    from app.services.chat_graph_runtime import ChatGraphRuntime

    runtime = ChatGraphRuntime(
        message="继续",
        session_id="trace_test",
        trace_id="trace_test_001",
        rag_prefetch=True,
    )
    state = {
        "graph_route": "continue_execute",
        "task_id": "task_554272e197cc",
        "session_id": "trace_test",
        "trace_id": "trace_test_001",
        "needs_rag": True,
        "intent_rewrite_snapshot": {
            "rewritten_query": "搜索知识库中关于MCP技术相关的文档并进行总结反馈",
            "needs_rag": True,
            "query_keywords": ["MCP技术"],
        },
        "message": "继续",
        "slot_snapshot": {},
        "enhancement_snapshot": {},
        "group_seq": 1,
        "orch_chain": [],
        "runtime_config": runtime.snapshot_config(),
    }
    config = {
        "configurable": {
            "runtime": runtime,
            "session_id": "trace_test",
        }
    }
    out = await node_react_entry(state, config)
    assert out.get("graph_route") == "handoff_execute"
    slices = out.get("rag_slices") or []
    assert len(slices) > 0, "续接 MCP 主任务应预取到知识库切片"
    assert str(out.get("rag_context_block") or "").strip()
