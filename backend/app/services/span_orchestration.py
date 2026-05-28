"""LangGraph 编排段 + RAG 检索 → span_audit 持久化（EVAL 事实来源）。"""

from __future__ import annotations



import logging

import time

from contextvars import ContextVar

from typing import Any, Dict, List, Optional



from .span_audit import create_step, finish_step, start_step



_log = logging.getLogger("sba.span_orchestration")



_active_ctx: ContextVar[Optional[Dict[str, str]]] = ContextVar("span_active_ctx", default=None)





def set_active_span_context(

    *,

    session_id: str,

    task_id: str,

    trace_id: str = "",

    rag_metadata_filter: Optional[Dict[str, Any]] = None,

) -> None:

    ctx: Dict[str, Any] = {

        "session_id": (session_id or "").strip(),

        "task_id": (task_id or "").strip(),

        "trace_id": (trace_id or "").strip(),

    }

    if isinstance(rag_metadata_filter, dict) and rag_metadata_filter:

        ctx["rag_metadata_filter"] = rag_metadata_filter

    _active_ctx.set(ctx)





def clear_active_span_context() -> None:

    _active_ctx.set(None)





def get_active_span_context() -> Optional[Dict[str, str]]:

    ctx = _active_ctx.get()

    if not ctx or not ctx.get("task_id") or not ctx.get("session_id"):

        return None

    return dict(ctx)





def _resolve_ctx(

    span_ctx: Optional[Dict[str, Any]] = None,

) -> Optional[Dict[str, str]]:

    if span_ctx and span_ctx.get("task_id") and span_ctx.get("session_id"):

        return {

            "session_id": str(span_ctx["session_id"]),

            "task_id": str(span_ctx["task_id"]),

            "trace_id": str(span_ctx.get("trace_id") or ""),

        }

    return get_active_span_context()





def persist_reasoning_step(

    task_id: str,

    session_id: str,

    *,

    step_name: str,

    phase: str,

    trace_id: str = "",

    input_payload: Optional[Dict[str, Any]] = None,

    output_payload: Optional[Dict[str, Any]] = None,

    result_brief: str = "",

) -> Optional[Dict[str, Any]]:

    """编排节点写入 span_audit：每步同步热写 Redis/本地，MariaDB 仅 finish 后异步 flush。"""

    tid = (task_id or "").strip()

    sid = (session_id or "").strip()

    if not tid or not sid:

        return None

    idem = f"{tid}:orch:{phase}:{step_name}"

    t0 = time.perf_counter()

    try:

        step = create_step(tid, sid, "reasoning", step_name, idempotency_key=idem)

        start_step(

            step["step_id"],

            input_payload={

                **(input_payload or {}),

                "trace_id": trace_id,

                "phase": phase,

                "layer": "langgraph_orchestration",

            },

        )

        out = dict(output_payload or {})

        out.setdefault("phase", phase)

        finish_step(

            step["step_id"],

            status="completed",

            output_payload=out,

            open_layer={

                "current_assessment": result_brief or step_name,

                "tool_io_brief": {"result": (result_brief or "")[:500]},

                "decision": "continue",

            },

        )

        hot_ms = int((time.perf_counter() - t0) * 1000)

        _log.info(

            "[AI问答-SPAN编排|span_orchestration.persist_reasoning_step|%s|硬编执行|完成] "

            "session_id=%s; phase=%s; hot_persist_ms=%s; ok=true",

            step_name,

            sid,

            phase,

            hot_ms,

        )

        return step

    except Exception as ex:

        _log.warning(

            "[AI问答-SPAN编排|span_orchestration.persist_reasoning_step|%s|硬编执行|失败] "

            "error_type=%s; error_message=%s",

            step_name,

            type(ex).__name__,

            ex,

        )

        return None





def schedule_persist_reasoning_step(

    task_id: str,

    session_id: str,

    *,

    step_name: str,

    phase: str,

    trace_id: str = "",

    input_payload: Optional[Dict[str, Any]] = None,

    output_payload: Optional[Dict[str, Any]] = None,

    result_brief: str = "",

) -> None:

    """兼容别名：同步热写，禁止线程池延迟（暂停前须已落 Redis/本地）。"""

    persist_reasoning_step(

        task_id,

        session_id,

        step_name=step_name,

        phase=phase,

        trace_id=trace_id,

        input_payload=input_payload,

        output_payload=output_payload,

        result_brief=result_brief,

    )





def persist_retrieval_step(

    query: str,

    hits: List[Dict[str, Any]],

    *,

    task_id: str,

    session_id: str,

    trace_id: str = "",

    source: str = "rag",

) -> Optional[Dict[str, Any]]:

    """检索步 SPAN（同步热写 Redis/本地）。"""

    tid = (task_id or "").strip()

    sid = (session_id or "").strip()

    if not tid or not sid:

        return None

    t0 = time.perf_counter()

    idem = f"{tid}:retrieval:{source}:{(query or '')[:80]}"

    try:

        step = create_step(tid, sid, "retrieval", source, idempotency_key=idem)

        start_step(

            step["step_id"],

            input_payload={"query": query, "trace_id": trace_id, "source": source},

        )

        cost_ms = int((time.perf_counter() - t0) * 1000)

        finish_step(

            step["step_id"],

            status="completed",

            output_payload={"hits": hits, "hit_count": len(hits), "cost_ms": cost_ms},

            open_layer={

                "current_assessment": f"召回 {len(hits)} 条",

                "decision": "continue",

            },

        )

        return step

    except Exception as ex:

        _log.warning(

            "[AI问答-SPAN编排|span_orchestration.persist_retrieval_step|retrieval|硬编执行|失败] "

            "error_type=%s; error_message=%s",

            type(ex).__name__,

            ex,

        )

        return None

