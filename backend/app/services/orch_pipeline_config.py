"""LangGraph 前置编排链路：单节点启用/禁用配置。"""
from __future__ import annotations

from typing import Any, Dict, Optional

# 与 chat_graph 节点一一对应，供前端勾选
ORCH_PIPELINE_NODE_META: Dict[str, str] = {
    "simple_intent_gate": "简单/复杂任务分流",
    "intent_recognition": "意图识别（LLM）",
    "query_rewrite": "Query 改写（LLM）",
    "rewrite_confirm": "改写确认（HITL）",
    "slot_fill": "业务对齐 / 槽位填充",
    "task_decompose": "任务分解",
    "intent_enhance": "意图增强（检索提示/核验）",
    "rag_filter_confirm": "RAG 过滤确认",
    "rag_decision": "RAG 决策与预取",
}

DEFAULT_ORCH_PIPELINE_NODES: Dict[str, bool] = {
    "simple_intent_gate": True,
    "intent_recognition": True,
    "query_rewrite": True,
    "rewrite_confirm": False,
    "slot_fill": True,
    "task_decompose": True,
    # 默认关闭：易与知识库检索混淆并生成多余联网向词
    "intent_enhance": False,
    # 默认关闭：HITL 会阻塞 SSE，知识库检索由 rag_decision / 执行段处理
    "rag_filter_confirm": False,
    "rag_decision": True,
}


def merge_orch_pipeline_nodes(
    overrides: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """合并默认、config.json 与请求/会话覆盖。"""
    merged = dict(DEFAULT_ORCH_PIPELINE_NODES)
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
    return merged


def orch_node_enabled(nodes: Optional[Dict[str, bool]], node_id: str) -> bool:
    if not isinstance(nodes, dict):
        nodes = DEFAULT_ORCH_PIPELINE_NODES
    return bool(nodes.get(node_id, DEFAULT_ORCH_PIPELINE_NODES.get(node_id, True)))


def orch_node_meta_list() -> list[dict[str, str]]:
    """供前端渲染勾选列表。"""
    return [
        {"id": k, "label": v, "default": bool(DEFAULT_ORCH_PIPELINE_NODES.get(k, True))}
        for k, v in ORCH_PIPELINE_NODE_META.items()
    ]
