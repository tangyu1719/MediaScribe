"""编排节点 SSE：蓝色思考（分析上一节点）+ SPAN 输入输出 + result_brief。"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .chat_graph_runtime import ChatGraphRuntime
from .task_states import SUB_ACTING, SUB_DONE
from .tool_output_schema import (
    build_orchestration_step_output,
    clamp_result_brief_cn,
    dumps_step_output,
    summarize_orchestration_payload_cn,
)


_PHASE_LABELS: Dict[str, str] = {
    "intent": "意图识别",
    "rewrite": "问题改写",
    "slot": "业务对齐",
    "decompose": "任务分解",
    "enhance": "意图增强",
    "rag_decision": "RAG 决策",
    "execute_prep": "执行准备",
}


def _new_id(prefix: str) -> str:
    import uuid

    return prefix + uuid.uuid4().hex[:12]


def _next_step_group(state: Dict[str, Any]) -> tuple[str, int]:
    group_seq = int(state.get("group_seq") or 0) + 1
    return _new_id("subplan_"), group_seq


def prior_from_state(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    chain = state.get("orch_chain") or []
    return chain[-1] if chain else None


def append_orch_chain(
    state: Dict[str, Any],
    *,
    phase: str,
    result_brief: str,
    output_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    chain = list(state.get("orch_chain") or [])
    chain.append(
        {
            "phase": phase,
            "result_brief": result_brief,
            "output": output_payload,
        }
    )
    return chain


def build_node_think_analysis(
    phase: str,
    *,
    prior: Optional[Dict[str, Any]],
    user_message: str,
    input_payload: Dict[str, Any],
    output_payload: Dict[str, Any],
) -> str:
    """规则生成蓝色思考框文案：分析上一节点产物 + 本节点结论（非 LLM 伪造）。"""
    label = _PHASE_LABELS.get(phase, phase)
    prior_brief = ""
    if prior:
        prior_brief = str(prior.get("result_brief") or "")[:80]
    lines: List[str] = []
    if prior_brief:
        lines.append(f"基于上一步「{prior_brief}」，进入{label}。")
    else:
        lines.append(f"开始{label}，分析用户任务。")

    if phase == "intent":
        ts = str(output_payload.get("task_summary") or output_payload.get("result_brief_cn") or "")[:80]
        qk = output_payload.get("query_keywords") or []
        if ts:
            lines.append(f"任务摘要：{ts}")
        if isinstance(qk, list) and qk:
            lines.append(f"原问关键词 {len(qk)} 个：{', '.join(str(x) for x in qk[:5])}")
        if output_payload.get("needs_rag"):
            lines.append("判定需查知识库（Milvus）。")
        elif output_payload.get("needs_web_search"):
            lines.append("判定需联网检索（用户已开启联网）。")
        else:
            lines.append("判定无需联网；检索/工具由后续节点决定。")
    elif phase == "rewrite":
        rq = str(output_payload.get("rewritten_query") or "")[:100]
        if rq:
            lines.append(f"已规范问题表述：{rq}")
    elif phase == "slot":
        dom = output_payload.get("domain") or ""
        op = output_payload.get("operation_type") or ""
        terms = output_payload.get("retrieval_terms") or []
        if dom:
            lines.append(f"业务域 {dom}，操作类型 {op}。")
        if terms:
            lines.append(f"检索词 {len(terms)} 个，供后续检索使用。")
    elif phase == "decompose":
        subs = output_payload.get("sub_tasks") or []
        lines.append(f"拆解为 {len(subs)} 个子任务，形成执行计划。")
    elif phase == "enhance":
        hints = output_payload.get("retrieval_hints") or []
        skw = output_payload.get("search_keyword_queries") or []
        wsq = output_payload.get("web_search_queries") or []
        vps = output_payload.get("verification_points") or []
        web_on = bool(input_payload.get("web_search"))
        if hints:
            lines.append(f"检索提示 {len(hints)} 条。")
        if skw:
            lines.append(
                f"已生成知识库检索词 {len(skw)} 条，供 Milvus 预取使用。"
            )
        if wsq and web_on:
            lines.append(
                f"已生成联网检索词 {len(wsq)} 条，将在 ReAct 推理前按词预取。"
            )
        if vps:
            lines.append(f"核验要点 {len(vps)} 条，防止幻觉进入最终回答。")
    elif phase == "rag_decision":
        if output_payload.get("needs_rag"):
            lines.append("需要知识库预检索，结果写入执行上下文。")
    elif phase == "execute_prep":
        steps = output_payload.get("plan_steps") or []
        web_on = bool(input_payload.get("web_search"))
        rag_on = bool(input_payload.get("rag_prefetch"))
        lines.append("意图识别与固定编排已完成。")
        if web_on:
            lines.append(
                "用户已开启联网搜索：将在进入 ReAct 推理链路之前，"
                "基于改写后的检索词完成联网预取（固定流程后的检索阶段，非 ReAct 内工具轮次）。"
            )
        if rag_on:
            lines.append(
                "用户已开启知识库预取：将在 ReAct 之前完成 RAG 检索，结果写入上下文。"
            )
        if steps:
            lines.append(
                f"预取结束后进入 ReAct，可参考 {len(steps)} 步计划调度注册表工具。"
            )
        elif not web_on and not rag_on:
            lines.append("随后进入 ReAct 推理与工具调用。")

    return "\n".join(lines)[:480]


def emit_orchestration_step(
    runtime: ChatGraphRuntime,
    state: Dict[str, Any],
    *,
    trace_id: str,
    task_id: str,
    step_name: str,
    phase: str,
    result_brief: str,
    input_payload: Dict[str, Any],
    output_payload: Dict[str, Any],
    prior: Optional[Dict[str, Any]] = None,
    executed: bool = True,
    think_text_override: Optional[str] = None,
    llm_powered: bool = False,
) -> Dict[str, Any]:
    """编排节点标准 SSE：思考 → 步骤完成（含输入/输出 JSON，供 SPAN/前端按钮）。"""
    prior = prior if prior is not None else prior_from_state(state)
    if think_text_override and str(think_text_override).strip():
        llm_powered = True
        think_text = build_node_think_analysis(
            phase,
            prior=prior,
            user_message=runtime.message or "",
            input_payload=input_payload,
            output_payload=output_payload,
        )
        cn_extra = summarize_orchestration_payload_cn(phase, output_payload)
        if cn_extra:
            think_text = think_text + "\n" + cn_extra
    else:
        think_text = build_node_think_analysis(
            phase,
            prior=prior,
            user_message=runtime.message or "",
            input_payload=input_payload,
            output_payload=output_payload,
        )
    sub_plan_id, group_seq = _next_step_group(state)
    if task_id:
        try:
            from .chat_context_memory import touch_task_group_seq

            touch_task_group_seq(str(task_id), group_seq)
        except Exception:
            pass
    step_id = _new_id("step_")
    brief = clamp_result_brief_cn(result_brief)

    runtime.emit(
        "orchestration_node_start",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "step_id": step_id,
            "step_name": step_name,
            "phase": phase,
            "sub_plan_id": sub_plan_id,
            "sub_index": group_seq,
            "stage": step_name,
            "progress_hint": f"正在执行：{step_name}",
        },
    )
    inp_body = build_orchestration_step_output(
        phase=phase,
        cost_ms=0,
        payload={
            **dict(input_payload or {}),
            "phase": phase,
            "step_name": step_name,
            "summary_cn": summarize_orchestration_payload_cn(
                phase, input_payload, role="input"
            ),
        },
    )
    out_body = build_orchestration_step_output(
        phase=phase,
        cost_ms=0,
        payload={
            **dict(output_payload or {}),
            "phase": phase,
            "step_name": step_name,
            "result_brief_cn": brief,
            "summary_cn": summarize_orchestration_payload_cn(phase, output_payload),
        },
    )
    inp_txt = dumps_step_output(inp_body)
    out_txt = dumps_step_output(out_body)

    runtime.emit(
        "step_think_start",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "step_id": step_id,
            "step_name": step_name,
            "sub_plan_id": sub_plan_id,
            "sub_index": group_seq,
            "think_kind": "node_analysis",
            "llm_powered": llm_powered,
        },
    )
    runtime.emit(
        "step_think_delta",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "step_id": step_id,
            "content": think_text,
            "think_kind": "node_analysis",
            "llm_powered": llm_powered,
        },
    )

    t0 = time.perf_counter()
    runtime.emit(
        "thought_step_start",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "step_id": step_id,
            "step_name": step_name,
            "step_type": "llm_call" if llm_powered else "reasoning",
            "status": SUB_ACTING,
            "status_text": "LLM 推理中…" if llm_powered else "执行中…",
            "sub_plan_id": sub_plan_id,
            "sub_index": group_seq,
            "node_kind": "sub_task",
            "operation": step_name,
            "target": (runtime.message or "")[:40],
            "phase": phase,
            "step_lane": "orchestration",
        },
    )
    cost_ms = int((time.perf_counter() - t0) * 1000)
    tid = (task_id or state.get("task_id") or "").strip()
    sid = (state.get("session_id") or runtime.session_id or "").strip()
    if tid and sid:
        from .span_orchestration import persist_reasoning_step

        persist_reasoning_step(
            tid,
            sid,
            step_name=step_name,
            phase=phase,
            trace_id=trace_id or state.get("trace_id") or runtime.trace_id or "",
            input_payload=input_payload,
            output_payload=output_payload,
            result_brief=brief,
        )

    step_status = SUB_DONE if executed else "skipped"
    runtime.emit(
        "thought_step_end",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "step_id": step_id,
            "step_name": step_name,
            "status": step_status,
            "elapsed_ms": cost_ms,
            "status_text": "完成" if executed else "跳过",
            "description": brief,
            "sub_plan_id": sub_plan_id,
            "sub_index": group_seq,
            "node_kind": "sub_task",
            "result_brief": brief,
            "input_text": inp_txt,
            "output_text": out_txt,
            "think_text": think_text,
            "think_kind": "node_analysis",
            "phase": phase,
            "step_lane": "orchestration",
            "executed": executed,
            "success": True if executed else False,
            "confidence": 0.92 if executed else 0.0,
            "token_count": max(12, len(think_text) // 4),
            "llm_powered": llm_powered,
            "io_links": [],
        },
    )

    return {
        "group_seq": group_seq,
        "sub_plan_id": sub_plan_id,
        "step_id": step_id,
        "orch_chain": append_orch_chain(
            state,
            phase=phase,
            result_brief=brief,
            output_payload=output_payload,
        ),
    }
