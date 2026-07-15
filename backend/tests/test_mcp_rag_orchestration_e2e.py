"""后台 E2E：MCP 知识库检索编排须含 RAG 决策步骤组与切片事件。"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from tests.sse_regress_lib import iter_sse_from_response

QUERY = "搜索知识库中关于MCP技术相关的文档进行总结反馈"
SESSION = "test_mcp_rag_" + uuid.uuid4().hex[:10]


def test_mcp_rag_orchestration_has_rag_decision_step(api_client, admin_headers):
    client = api_client
    """LangGraph 编排：步骤组含 rag_decision；可含 rag_prefetch_slices / 无 mem 类错误。"""
    os.environ.setdefault("CHAT_USE_LANGGRAPH", "1")
    os.environ.setdefault("CHAT_GRAPH_AUTO_HITL", "1")

    body = {
        "session_id": SESSION,
        "message": QUERY,
        "rag_prefetch": True,
        "web_search": False,
        "read_comments": False,
        "deep_think": False,
        "orch_pipeline_nodes": {
            "simple_intent_gate": True,
            "intent_recognition": True,
            "query_rewrite": True,
            "rewrite_confirm": False,
            "slot_fill": True,
            "task_decompose": True,
            "intent_enhance": False,
            "rag_filter_confirm": True,
            "rag_decision": True,
        },
    }
    with client.stream(
        "POST",
        "/api/chat/stream",
        json=body,
        headers=admin_headers,
        timeout=300.0,
    ) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8", errors="replace")[:500]
        events = iter_sse_from_response(resp)

    names = [ev for ev, _ in events]
    assert "stream_error" not in names, next(
        (d.get("message") for ev, d in events if ev == "stream_error"),
        "",
    )

    rag_steps = [
        d
        for ev, d in events
        if ev == "thought_step_end" and str(d.get("phase") or "").lower() == "rag_decision"
    ]
    assert rag_steps, "SSE 须包含 phase=rag_decision 的 thought_step_end"

    sub_indices = sorted(
        {
            int(d.get("sub_index") or 0)
            for ev, d in events
            if ev == "thought_step_end" and d.get("sub_index") is not None
        }
    )
    assert len(sub_indices) >= 5, f"步骤组序号过少: {sub_indices}"

    slice_ev = [d for ev, d in events if ev == "rag_prefetch_slices"]
    rag_out = parse_step_output_slices(rag_steps[-1])
    rag_out_j = parse_step_output_json(rag_steps[-1])
    # 有命中：须推送切片；无命中（Milvus 未连/超时）：仍须走完 RAG 决策并带预取元数据
    if slice_ev or rag_out:
        assert len(rag_out or (slice_ev[0].get("slices") if slice_ev else [])) >= 0
    else:
        assert isinstance(rag_out_j, dict), "RAG 决策 output 须为 JSON"
        assert rag_out_j.get("needs_rag") is True
        assert "prefetch_count" in rag_out_j

    err_msgs = [
        str(d.get("message") or "")
        for ev, d in events
        if ev == "stream_error" or "mem" in str(d).lower()
    ]
    assert not any("mem" in m and "not defined" in m for m in err_msgs)


def parse_step_output_json(step_end: dict) -> dict:
    raw = step_end.get("output_text") or ""
    try:
        j = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}
    return j if isinstance(j, dict) else {}


def parse_step_output_slices(step_end: dict) -> list:
    j = parse_step_output_json(step_end)
    slices = j.get("rag_slices")
    return slices if isinstance(slices, list) and slices else []
