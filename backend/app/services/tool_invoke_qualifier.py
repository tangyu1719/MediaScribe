"""工具/检索调用定语：固定节点 / ReAct / 重试 + 简要目的（约 10 字）。"""
from __future__ import annotations

from typing import Any, Dict, Set

INVOKE_FIXED = "fixed_node"
INVOKE_REACT = "react"
INVOKE_RETRY = "retry"

_MODE_LABEL: Dict[str, str] = {
    INVOKE_FIXED: "固定节点",
    INVOKE_REACT: "ReAct",
    INVOKE_RETRY: "重试",
}

_RAG_TOOL_NAMES = frozenset({
    "rag_search", "rag_retrieve", "kb_search", "vector_search", "knowledge_base_search",
})


def _clip(text: str, limit: int = 10) -> str:
    t = str(text or "").strip()
    return t[:limit] if t else ""


def is_rag_tool_name(name: str) -> bool:
    n = str(name or "").lower()
    if n in _RAG_TOOL_NAMES:
        return True
    return any(m in n for m in ("rag", "kb", "knowledge", "vector"))


def is_web_tool_name(name: str) -> bool:
    n = str(name or "").lower()
    return n == "web_search" or ("search" in n and not is_rag_tool_name(n))


def default_purpose(*, mode: str, tool_name: str = "", phase: str = "") -> str:
    ph = str(phase or "").lower()
    if mode == INVOKE_FIXED:
        if ph == "rag_decision" or is_rag_tool_name(tool_name):
            return "编排节点预取"
        if ph in ("rag", "web") or is_web_tool_name(tool_name):
            return "执行段预取"
        return "固定流程调用"
    if mode == INVOKE_RETRY:
        if is_rag_tool_name(tool_name):
            return "换词补检索"
        if is_web_tool_name(tool_name):
            return "换词补联网"
        return "再次调用"
    if is_rag_tool_name(tool_name):
        return "模型按需检索"
    if is_web_tool_name(tool_name):
        return "模型按需联网"
    return "模型工具调用"


def build_invoke_labels(
    *,
    mode: str,
    tool_name: str = "",
    action_label: str = "",
    purpose: str = "",
    query: str = "",
    phase: str = "",
) -> Dict[str, str]:
    """返回 invoke_mode / invoke_purpose / what（用户可见主文案）。"""
    resolved_mode = mode if mode in _MODE_LABEL else INVOKE_REACT
    mode_cn = _MODE_LABEL[resolved_mode]
    purpose_cn = _clip(purpose or default_purpose(mode=resolved_mode, tool_name=tool_name, phase=phase), 10)

    if not action_label:
        if is_rag_tool_name(tool_name):
            action_label = "知识库检索"
        elif is_web_tool_name(tool_name):
            action_label = "联网搜索"
        elif tool_name:
            action_label = f"调用 {tool_name}"
        else:
            action_label = "工具调用"

    query_hint = _clip(query, 10)
    if query_hint and action_label in ("知识库检索", "联网搜索"):
        what = f"{mode_cn} · {action_label} · {query_hint}"
    else:
        what = f"{mode_cn} · {action_label} · {purpose_cn}"

    return {
        "invoke_mode": resolved_mode,
        "invoke_purpose": purpose_cn,
        "what": what,
    }


def resolve_react_invoke_mode(
    *,
    tool_name: str,
    rag_prefetch_done: bool,
    rag_slice_count: int,
    react_round: int,
    seen_tools: Set[str],
) -> str:
    """ReAct 执行段：预取空跑后再调 RAG、或同轮重复工具 → 重试。"""
    if rag_prefetch_done and is_rag_tool_name(tool_name) and rag_slice_count <= 0:
        return INVOKE_RETRY
    if tool_name in seen_tools:
        return INVOKE_RETRY
    if react_round > 1 and (is_rag_tool_name(tool_name) or is_web_tool_name(tool_name)):
        return INVOKE_RETRY
    return INVOKE_REACT


def attach_invoke_to_payload(payload: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    """将定语字段合并进 SSE payload。"""
    out = dict(payload or {})
    out.update(build_invoke_labels(**kwargs))
    return out
