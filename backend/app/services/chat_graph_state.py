"""LangGraph 编排共享状态（主子任务双快照 + HITL + 执行段上下文）。"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict


def _merge_sse_events(left: Optional[List[str]], right: Optional[List[str]]) -> List[str]:
    return list(left or []) + list(right or [])


def _merge_react_memory(
    left: Optional[List[Dict[str, str]]],
    right: Optional[List[Dict[str, str]]],
) -> List[Dict[str, str]]:
    return list(left or []) + list(right or [])


def _merge_orch_chain(
    left: Optional[List[Dict[str, Any]]],
    right: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    return list(left or []) + list(right or [])


def _merge_failed_tools(left: Optional[List[str]], right: Optional[List[str]]) -> List[str]:
    base = list(left or [])
    for name in right or []:
        if name and name not in base:
            base.append(name)
    return base


class ChatGraphState(TypedDict, total=False):
    """图内共享状态：编排段 + 执行段 + 审计字段。"""

    # ── 会话与追踪 ──
    session_id: str
    trace_id: str
    message: str
    orchestration_phase: str

    # ── 意图 / 分流 ──
    task_kind: str
    use_main_task: bool
    framework: str
    intent_passed: bool
    intent_rewrite_snapshot: Dict[str, Any]
    rewrite_state: str
    skip_rewrite_confirm: bool
    rewritten_query: str
    query_summary: str
    rewrite_actions: List[str]
    decomposition_snapshot: Dict[str, Any]
    enhancement_snapshot: Dict[str, Any]
    orch_chain: Annotated[List[Dict[str, Any]], _merge_orch_chain]

    # ── 槽位 / RAG ──
    slot_snapshot: Dict[str, Any]
    slot_confirmed: bool
    needs_rag: bool
    rag_prefetch: bool
    rag_confirmed: bool
    rag_filter_confirmed: bool
    rag_metadata_filter: Dict[str, str]
    rag_context_block: Optional[str]

    # ── 主任务审计 ──
    task_id: Optional[str]
    parent_status: str
    step_idx: int
    group_seq: int
    continue_main_task: bool

    # ── 执行段 ReAct / Plan ──
    react_memory: Annotated[List[Dict[str, str]], _merge_react_memory]
    react_round: int
    react_max_rounds: int
    plan_steps: List[Dict[str, Any]]
    plan_cursor: int
    execution_done: bool

    # ── 工具失败与异常 ──
    failed_tool_names: Annotated[List[str], _merge_failed_tools]
    distinct_tool_fail_limit: int
    tool_round: int
    tool_wait_checkpoint: Dict[str, Any]
    tool_resume_count: int
    abnormal: bool
    error_message: str

    # ── HITL ──
    hitl_kind: str
    hitl_payload: Dict[str, Any]
    user_hitl: Dict[str, Any]
    paused: bool

    # ── 运行时配置（只读快照，节点不修改）──
    runtime_config: Dict[str, Any]
    runtime_key: str

    # ── 输出 ──
    final_answer: str
    react_context_block: str
    link_ctx: Dict[str, Any]
    tools_meta: Dict[str, Any]

    # ── SSE 缓冲（由 runner 刷出）──
    sse_events: Annotated[List[str], _merge_sse_events]

    # ── 控制流 ──
    graph_route: str


# 编排阶段常量（与 docs/主子任务双快照 对齐）
PHASE_IDLE = "idle"
PHASE_INTENT = "intent_recognizing"
PHASE_SIMPLE = "simple_answering"
PHASE_REWRITE_PROPOSE = "rewrite_proposing"
PHASE_REWRITE_CONFIRM = "rewrite_confirming"
PHASE_DECOMPOSE = "intent_decomposing"
PHASE_ENHANCE = "intent_enhancing"
PHASE_SLOT_FILL = "slot_filling"
PHASE_SLOT_CONFIRM = "slot_confirming"
PHASE_RAG_DECISION = "rag_decision"
PHASE_REACT = "react_running"
PHASE_PLAN = "plan_execute_running"
PHASE_TOOL = "tool_calling"
PHASE_OBSERVE = "waiting_observation"
PHASE_REPLAN = "replan_needed"
PHASE_FINAL = "final_answer"
PHASE_RESOLVED = "resolved"
PHASE_CLOSED = "closed"
PHASE_ABNORMAL = "abnormal"
PHASE_PAUSED = "paused"
