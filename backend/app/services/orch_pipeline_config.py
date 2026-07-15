"""LangGraph 前置编排链路：方案预设 + 单节点启用/禁用配置。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 与 chat_graph 节点一一对应，供前端勾选
ORCH_PIPELINE_NODE_META: Dict[str, str] = {
    "simple_intent_gate": "简单/复杂任务分流",
    "intent_recognition": "意图识别（LLM）",
    "query_rewrite": "Query 改写（LLM）",
    "rewrite_confirm": "改写确认（HITL）",
    "slot_fill": "业务对齐 / 槽位填充",
    "task_decompose": "任务分解",
    "intent_enhance": "意图增强（检索提示/核验）",
    "rag_filter_confirm": "RAG 元数据筛选",
    "rag_filter_confirm_hitl": "RAG 过滤需用户确认（HITL）",
    "rag_decision": "RAG 决策与预取",
}

# 前置必备：不参与方案矩阵，合并时强制 True
ORCH_PIPELINE_MANDATORY: frozenset[str] = frozenset(
    {"simple_intent_gate", "intent_recognition"}
)

# 方案内可配置节点（不含必备项）
ORCH_PIPELINE_SCHEME_NODE_IDS: frozenset[str] = frozenset(
    k for k in ORCH_PIPELINE_NODE_META if k not in ORCH_PIPELINE_MANDATORY
)

DEFAULT_ORCH_PIPELINE_SCHEME = "standard_rag"

ORCH_PIPELINE_PRESETS: Dict[str, Dict[str, Any]] = {
    "basic_rag": {
        "label": "常规 RAG",
        "description": "Query 改写（关键词提取，不做业务映射）+ RAG 决策",
        "nodes": {
            "query_rewrite": True,
            "rewrite_confirm": False,
            "slot_fill": False,
            "task_decompose": False,
            "intent_enhance": False,
            "rag_filter_confirm": False,
            "rag_filter_confirm_hitl": False,
            "rag_decision": True,
        },
    },
    "standard_rag": {
        "label": "标准 RAG",
        "description": "除意图增强与任务分解外全开；RAG 元数据筛选由 Query 自动推导（无需用户确认）",
        "nodes": {
            "query_rewrite": True,
            "rewrite_confirm": False,
            "slot_fill": True,
            "task_decompose": False,
            "intent_enhance": False,
            "rag_filter_confirm": True,
            "rag_filter_confirm_hitl": False,
            "rag_decision": True,
        },
    },
    "complex": {
        "label": "复杂问题",
        "description": "全流程：含改写确认、任务分解、意图增强、RAG 过滤 HITL",
        "nodes": {
            "query_rewrite": True,
            "rewrite_confirm": True,
            "slot_fill": True,
            "task_decompose": True,
            "intent_enhance": True,
            "rag_filter_confirm": True,
            "rag_filter_confirm_hitl": True,
            "rag_decision": True,
        },
    },
}

DEFAULT_ORCH_PIPELINE_NODES: Dict[str, bool] = {
    **{k: True for k in ORCH_PIPELINE_MANDATORY},
    **dict(ORCH_PIPELINE_PRESETS[DEFAULT_ORCH_PIPELINE_SCHEME]["nodes"]),
}


def _normalize_scheme(scheme: Optional[str]) -> str:
    sid = str(scheme or "").strip()
    if sid in ORCH_PIPELINE_PRESETS:
        return sid
    return DEFAULT_ORCH_PIPELINE_SCHEME


def preset_nodes(scheme: Optional[str] = None) -> Dict[str, bool]:
    """返回某方案的完整节点开关（含必备项）。"""
    sid = _normalize_scheme(scheme)
    preset = ORCH_PIPELINE_PRESETS[sid]
    merged = dict(DEFAULT_ORCH_PIPELINE_NODES)
    merged.update(preset.get("nodes") or {})
    for k in ORCH_PIPELINE_MANDATORY:
        merged[k] = True
    return merged


def merge_orch_pipeline_nodes(
    overrides: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    scheme: Optional[str] = None,
) -> Dict[str, bool]:
    """合并方案预设、config.json 与请求/会话覆盖；必备节点始终开启。"""
    cfg_scheme = None
    if isinstance(cfg, dict):
        cfg_scheme = cfg.get("orch_pipeline_scheme") or cfg.get("default_orch_pipeline_scheme")
    sid = _normalize_scheme(scheme or cfg_scheme)
    merged = preset_nodes(sid)
    for src in (
        (cfg or {}).get("orch_pipeline_nodes"),
        overrides,
    ):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            key = str(k or "").strip()
            if key in ORCH_PIPELINE_NODE_META:
                merged[key] = bool(v)
    for k in ORCH_PIPELINE_MANDATORY:
        merged[k] = True
    # 未启用 RAG 筛选步骤时，HITL 开关无意义
    if not merged.get("rag_filter_confirm"):
        merged["rag_filter_confirm_hitl"] = False
    return merged


def resolve_orch_pipeline_scheme(
    overrides: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    scheme: Optional[str] = None,
) -> str:
    if scheme and str(scheme).strip() in ORCH_PIPELINE_PRESETS:
        return str(scheme).strip()
    if isinstance(cfg, dict):
        cs = cfg.get("orch_pipeline_scheme") or cfg.get("default_orch_pipeline_scheme")
        if cs and str(cs).strip() in ORCH_PIPELINE_PRESETS:
            return str(cs).strip()
    return DEFAULT_ORCH_PIPELINE_SCHEME


def orch_node_enabled(nodes: Optional[Dict[str, bool]], node_id: str) -> bool:
    if not isinstance(nodes, dict):
        nodes = DEFAULT_ORCH_PIPELINE_NODES
    return bool(nodes.get(node_id, DEFAULT_ORCH_PIPELINE_NODES.get(node_id, True)))


def orch_node_meta_list() -> List[dict[str, Any]]:
    """供前端渲染勾选列表。"""
    return [
        {
            "id": k,
            "label": v,
            "default": bool(DEFAULT_ORCH_PIPELINE_NODES.get(k, True)),
            "mandatory": k in ORCH_PIPELINE_MANDATORY,
            "scheme_configurable": k in ORCH_PIPELINE_SCHEME_NODE_IDS,
        }
        for k, v in ORCH_PIPELINE_NODE_META.items()
    ]


def orch_preset_list() -> List[dict[str, str]]:
    return [
        {
            "id": sid,
            "label": str(meta.get("label") or sid),
            "description": str(meta.get("description") or ""),
        }
        for sid, meta in ORCH_PIPELINE_PRESETS.items()
    ]
