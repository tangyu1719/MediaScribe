"""工具/检索调用定语：固定节点 / ReAct / 重试 + 简要目的（约 10 字）。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set

TOOL_SOURCE_BUILTIN = "builtin"
TOOL_SOURCE_MCP = "mcp"
TOOL_SOURCE_SKILL = "skill"

_TOOL_CHANNEL_LABEL: Dict[str, str] = {
    TOOL_SOURCE_BUILTIN: "Tool Call",
    TOOL_SOURCE_MCP: "MCP",
    TOOL_SOURCE_SKILL: "SKILL",
}

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
    if n == "web_search":
        return True
    if n in ("bing_search", "internet_search", "websearch"):
        return True
    return False


def resolve_tool_source(tool_name: str, tools_meta: Optional[Dict[str, Any]] = None) -> str:
    """按注册表 source 区分 builtin / mcp / skill，禁止一律标成 MCP。"""
    name = str(tool_name or "").strip()
    if not name:
        return TOOL_SOURCE_BUILTIN
    catalog = (tools_meta or {}).get("tools") or []
    if isinstance(catalog, list):
        for row in catalog:
            if not isinstance(row, dict):
                continue
            if str(row.get("name") or "").strip() == name:
                src = str(row.get("source") or TOOL_SOURCE_BUILTIN).strip().lower()
                if src in _TOOL_CHANNEL_LABEL:
                    return src
    if name.startswith("skill_"):
        return TOOL_SOURCE_SKILL
    return TOOL_SOURCE_BUILTIN


def format_tool_channel_label(tool_source: str = "") -> str:
    src = str(tool_source or TOOL_SOURCE_BUILTIN).strip().lower()
    return _TOOL_CHANNEL_LABEL.get(src, "Tool Call")


def format_tool_action_label(tool_name: str = "", tool_source: str = "") -> str:
    """用户可见动作：渠道与工具名分离（Tool Call · web_search / MCP · xhs_user_search）。"""
    name = str(tool_name or "").strip()
    if not name:
        return "工具调用"
    src = str(tool_source or TOOL_SOURCE_BUILTIN).strip().lower()
    channel = format_tool_channel_label(src)
    if src in (TOOL_SOURCE_MCP, TOOL_SOURCE_SKILL):
        return f"{channel} · {name}"
    if is_rag_tool_name(name):
        return "知识库检索"
    if is_web_tool_name(name):
        return "联网搜索"
    return f"{channel} · {name}"


def normalize_legacy_tool_step_name(step_name: str, tool_name: str = "") -> str:
    """兼容旧 SPAN step_name「MCP 工具: xxx」/「MCP:xxx」。"""
    sn = str(step_name or "").strip()
    if sn.startswith("MCP 工具:"):
        fn = sn.split(":", 1)[-1].strip() or str(tool_name or "").strip()
        return format_tool_action_label(fn, TOOL_SOURCE_MCP) if fn else "MCP"
    if sn.startswith("MCP:"):
        fn = sn.split(":", 1)[-1].strip() or str(tool_name or "").strip()
        return format_tool_action_label(fn, TOOL_SOURCE_MCP) if fn else "MCP"
    if sn.startswith("调用 "):
        fn = sn[3:].strip() or str(tool_name or "").strip()
        return format_tool_action_label(fn, TOOL_SOURCE_BUILTIN) if fn else "Tool Call"
    return sn


def default_purpose(*, mode: str, tool_name: str = "", phase: str = "", tool_source: str = "") -> str:
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
    src = str(tool_source or TOOL_SOURCE_BUILTIN).strip().lower()
    if is_rag_tool_name(tool_name):
        return "按需检索"
    if is_web_tool_name(tool_name):
        return "按需联网"
    if src == TOOL_SOURCE_MCP:
        return "MCP 调用"
    if src == TOOL_SOURCE_SKILL:
        return "SKILL 调用"
    return "按需调用"


def build_invoke_labels(
    *,
    mode: str,
    tool_name: str = "",
    action_label: str = "",
    purpose: str = "",
    query: str = "",
    phase: str = "",
    tool_source: str = "",
    tools_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """返回 invoke_mode / invoke_purpose / what / tool_source（用户可见主文案）。"""
    resolved_mode = mode if mode in _MODE_LABEL else INVOKE_REACT
    mode_cn = _MODE_LABEL[resolved_mode]
    resolved_source = str(tool_source or "").strip().lower()
    if not resolved_source and tool_name:
        resolved_source = resolve_tool_source(tool_name, tools_meta)
    if resolved_source not in _TOOL_CHANNEL_LABEL:
        resolved_source = TOOL_SOURCE_BUILTIN
    purpose_cn = _clip(
        purpose
        or default_purpose(
            mode=resolved_mode,
            tool_name=tool_name,
            phase=phase,
            tool_source=resolved_source,
        ),
        10,
    )

    if not action_label:
        action_label = format_tool_action_label(tool_name, resolved_source)

    # 检索词仅写入 IO，不进入用户可见 what（避免误显示文档标题片段）
    what = f"{mode_cn} · {action_label} · {purpose_cn}"

    return {
        "invoke_mode": resolved_mode,
        "invoke_purpose": purpose_cn,
        "what": what,
        "tool_source": resolved_source,
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
