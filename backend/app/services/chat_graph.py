"""LangGraph 编排图：硬编码边 + HITL interrupt + 执行段 handoff。"""
from __future__ import annotations

from typing import Any, Dict, Literal

from langgraph.graph import END, START, StateGraph

from .chat_graph_nodes import (
    node_abnormal_finalize,
    node_intent_decompose,
    node_intent_enhance,
    node_intent_recognition,
    node_paused,
    node_plan_detect,
    node_rag_decision,
    node_react_entry,
    node_rewrite_confirm_ui,
    node_rewrite_summary,
    node_simple_answer,
    node_rag_filter_confirm_ui,
    node_slot_confirm_ui,
    node_slot_fill,
)
from .chat_graph_state import ChatGraphState

GraphRoute = Literal[
    "simple",
    "rewrite",
    "intent_decompose",
    "intent_enhance",
    "plan",
    "rewrite_confirm",
    "slot_fill",
    "slot_confirm",
    "rag_filter_confirm",
    "rag_decision",
    "execute",
    "handoff_execute",
    "paused",
    "intent",
    "done",
    "abnormal",
]


def _route(state: Dict[str, Any]) -> str:
    return str(state.get("graph_route") or "").strip()


def route_after_intent(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "continue_execute":
        return "react_entry"
    if r == "simple":
        return "simple_answer"
    if r == "rewrite":
        return "rewrite_summary"
    if r == "slot_fill":
        return "slot_fill"
    if r == "plan":
        return "plan_detect"
    return "abnormal_finalize"


def route_after_rewrite_confirm(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "paused":
        return "paused"
    if r == "intent":
        return "intent_recognition"
    return "slot_fill"


def route_after_slot_confirm(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "paused":
        return "paused"
    return "rag_filter_confirm"


def route_after_rag_filter_confirm(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "paused":
        return "paused"
    return "rag_decision"


def route_after_slot_fill(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "intent_decompose":
        return "intent_decompose"
    if r == "intent_enhance":
        return "intent_enhance"
    if r == "rag_filter_confirm":
        return "rag_filter_confirm"
    if r == "rag_decision":
        return "rag_decision"
    return "intent_decompose"


def route_after_intent_decompose(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "intent_enhance":
        return "intent_enhance"
    if r == "rag_filter_confirm":
        return "rag_filter_confirm"
    if r == "rag_decision":
        return "rag_decision"
    return "intent_enhance"


def route_after_intent_enhance(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "paused":
        return "paused"
    if r == "plan":
        return "plan_detect"
    if r == "rag_filter_confirm":
        return "rag_filter_confirm"
    if r == "rag_decision":
        return "rag_decision"
    return "abnormal_finalize"


def route_after_rag_decision(state: Dict[str, Any]) -> str:
    r = _route(state)
    if r == "paused":
        return "paused"
    if r == "plan":
        return "plan_detect"
    if r == "execute":
        return "react_entry"
    return "abnormal_finalize"


def route_after_plan(state: Dict[str, Any]) -> str:
    return "react_entry"


def route_entry(state: Dict[str, Any]) -> str:
    """恢复执行：若已暂停/异常则不再自动推进。"""
    if state.get("abnormal"):
        return "abnormal_finalize"
    if state.get("paused") and _route(state) == "done":
        return "paused"
    return "intent_recognition"


def build_chat_orchestration_graph():
    """构建主编排图（不含执行段工具循环，执行由 react_entry handoff）。"""
    g = StateGraph(ChatGraphState)

    g.add_node("intent_recognition", node_intent_recognition)
    g.add_node("simple_answer", node_simple_answer)
    g.add_node("rewrite_summary", node_rewrite_summary)
    g.add_node("rewrite_confirm", node_rewrite_confirm_ui)
    g.add_node("slot_fill", node_slot_fill)
    g.add_node("intent_decompose", node_intent_decompose)
    g.add_node("intent_enhance", node_intent_enhance)
    g.add_node("slot_confirm", node_slot_confirm_ui)
    g.add_node("rag_filter_confirm", node_rag_filter_confirm_ui)
    g.add_node("rag_decision", node_rag_decision)
    g.add_node("plan_detect", node_plan_detect)
    g.add_node("react_entry", node_react_entry)
    g.add_node("paused", node_paused)
    g.add_node("abnormal_finalize", node_abnormal_finalize)

    g.add_conditional_edges(START, route_entry, {
        "intent_recognition": "intent_recognition",
        "abnormal_finalize": "abnormal_finalize",
        "paused": "paused",
    })

    g.add_conditional_edges(
        "intent_recognition",
        route_after_intent,
        {
            "simple_answer": "simple_answer",
            "rewrite_summary": "rewrite_summary",
            "slot_fill": "slot_fill",
            "plan_detect": "plan_detect",
            "react_entry": "react_entry",
            "abnormal_finalize": "abnormal_finalize",
        },
    )

    g.add_edge("rewrite_summary", "slot_fill")
    g.add_conditional_edges(
        "slot_fill",
        route_after_slot_fill,
        {
            "intent_decompose": "intent_decompose",
            "intent_enhance": "intent_enhance",
            "rag_filter_confirm": "rag_filter_confirm",
            "rag_decision": "rag_decision",
        },
    )
    g.add_conditional_edges(
        "intent_decompose",
        route_after_intent_decompose,
        {
            "intent_enhance": "intent_enhance",
            "rag_filter_confirm": "rag_filter_confirm",
            "rag_decision": "rag_decision",
        },
    )
    g.add_conditional_edges(
        "intent_enhance",
        route_after_intent_enhance,
        {
            "paused": "paused",
            "plan_detect": "plan_detect",
            "rag_filter_confirm": "rag_filter_confirm",
            "rag_decision": "rag_decision",
            "abnormal_finalize": "abnormal_finalize",
        },
    )

    g.add_edge("rewrite_confirm", "slot_fill")
    g.add_conditional_edges(
        "slot_confirm",
        route_after_slot_confirm,
        {"paused": "paused", "rag_filter_confirm": "rag_filter_confirm"},
    )
    g.add_conditional_edges(
        "rag_filter_confirm",
        route_after_rag_filter_confirm,
        {"paused": "paused", "rag_decision": "rag_decision"},
    )

    g.add_conditional_edges(
        "rag_decision",
        route_after_rag_decision,
        {
            "paused": "paused",
            "plan_detect": "plan_detect",
            "react_entry": "react_entry",
            "abnormal_finalize": "abnormal_finalize",
        },
    )

    g.add_edge("plan_detect", "react_entry")

    g.add_edge("simple_answer", END)
    g.add_edge("react_entry", END)
    g.add_edge("paused", END)
    g.add_edge("abnormal_finalize", END)

    return g


_compiled_graph = None


def get_compiled_chat_graph(*, checkpointer=None):
    global _compiled_graph
    if checkpointer is not None:
        g = build_chat_orchestration_graph()
        return g.compile(checkpointer=checkpointer)
    if _compiled_graph is None:
        g = build_chat_orchestration_graph()
        _compiled_graph = g.compile()
    return _compiled_graph
