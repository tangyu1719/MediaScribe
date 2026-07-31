"""工具调用 SPAN 拦截器 — 统一固定层 + 开放层双快照。

对齐 video_gui._execute_tool_calls_with_interceptor / TaskStateStore：
- 固定层：input_payload / output_payload（工具名、参数、真实返回）
- 开放层：objective、tool_io_brief、decision 等（upsert_open_layer）
- 热数据：span_audit → Redis（redis_cache_enabled 时）
- 冷数据：span_audit → MariaDB 异步 flush；TaskStateStore → SQLite（可选）
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

from .span_audit import (
    create_step as _span_create_step,
    start_step as _span_start,
    finish_step as _span_finish,
    patch_task_snapshot as _span_patch_snapshot,
)
from .tool_output_schema import build_tool_step_output, brief_from_payload

_LOG = logging.getLogger(__name__)
_T = TypeVar("_T")

_task_store: Any = None
_task_store_err: str = ""


def _load_cfg() -> Dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    for cp in (root / "config.json", root.parent / "src" / "agent" / "config.json"):
        if cp.exists():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _get_task_state_store() -> Any:
    """SQLite 双快照（与 GUI TaskStateStore 同库表）。"""
    global _task_store, _task_store_err
    if _task_store is not None:
        return _task_store
    try:
        from task_state_store import TaskStateStore  # src/agent
    except ImportError as ex:
        _task_store_err = str(ex)
        return None
    cfg = _load_cfg()
    db_path = str(cfg.get("task_state_db_path") or "").strip()
    if not db_path:
        agent_dir = Path(__file__).resolve().parents[3] / "src" / "agent"
        db_path = str(agent_dir / "task_state.db")
    try:
        _task_store = TaskStateStore(db_path)
        _task_store_err = ""
    except Exception as ex:
        _task_store = None
        _task_store_err = f"{type(ex).__name__}: {ex}"
    return _task_store


@dataclass
class ToolSpanHandle:
    step_id: str
    state_step_id: str
    step_name: str
    began_at: float
    task_id: str
    session_id: str
    tool_name: str
    react_round: int
    sub_plan_id: str


def begin_tool_span(
    *,
    task_id: str,
    session_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    step_name: Optional[str] = None,
    react_round: int = 0,
    sub_plan_id: str = "",
    phase: str = "tool",
) -> ToolSpanHandle:
    """拦截器入口：工具执行前写固定层 input + 开放层初始态。"""
    display = step_name or f"工具: {tool_name}"
    span_step = _span_create_step(task_id, session_id, "tool_call", display)
    step_id = span_step["step_id"]
    _span_start(
        step_id,
        input_payload={
            "tool": tool_name,
            "args": tool_args,
            "phase": phase,
            "react_round": react_round,
            "sub_plan_id": sub_plan_id,
        },
    )
    open_init = {
        "objective": f"执行 {tool_name}",
        "current_assessment": "工具执行中",
        "progress_percent": min(90, 30 + react_round * 8),
        "decision": "continue",
        "tool_io_brief": {"tool_name": tool_name, "input": tool_args},
    }
    store = _get_task_state_store()
    state_step_id = ""
    if store and task_id:
        try:
            ids = store.create_step(
                session_id=session_id,
                task_id=task_id,
                step_type="tool_call",
                status="running",
                input_payload={
                    "tool": tool_name,
                    "arguments": tool_args,
                    "phase": phase,
                    "span_step_id": step_id,
                },
            )
            state_step_id = str(ids.step_id or "")
            store.upsert_open_layer(step_id=ids.step_id, open_layer=open_init)
            store.append_event(
                session_id=session_id,
                task_id=task_id,
                step_id=ids.step_id,
                event_type="step_started",
                payload={
                    "tool": tool_name,
                    "arguments": tool_args,
                    "span_step_id": step_id,
                },
            )
        except Exception as ex:
            _LOG.warning(
                "[AI问答-SPAN拦截|span_tool_interceptor.begin_tool_span|step:%s|硬编执行|TaskStateStore] "
                "record_step_started 失败; error_type=%s; error_message=%s",
                step_id,
                type(ex).__name__,
                str(ex)[:200],
            )
    _LOG.info(
        "[AI问答-SPAN拦截|span_tool_interceptor.begin_tool_span|tool:%s|硬编执行|开始] "
        "task_id=%s; step_id=%s; react_round=%s",
        tool_name,
        task_id,
        step_id,
        react_round,
    )
    return ToolSpanHandle(
        step_id=step_id,
        state_step_id=state_step_id,
        step_name=display,
        began_at=time.perf_counter(),
        task_id=task_id,
        session_id=session_id,
        tool_name=tool_name,
        react_round=react_round,
        sub_plan_id=sub_plan_id,
    )


def end_tool_span(
    handle: ToolSpanHandle,
    *,
    tool_args: Dict[str, Any],
    raw_out: Any,
    tool_err: Optional[str] = None,
    phase: str = "tool",
) -> Dict[str, Any]:
    """拦截器出口：写 output_payload + 开放层决策 + 主任务 snapshot_json 双快照。"""
    cost_ms = int((time.perf_counter() - handle.began_at) * 1000)
    ok = not tool_err
    tool_payload = build_tool_step_output(
        tool_name=handle.tool_name,
        tool_args=tool_args,
        tool_result=raw_out,
        error=tool_err,
        cost_ms=cost_ms,
        phase=phase,
    )
    open_done = {
        "objective": f"执行 {handle.tool_name}",
        "current_assessment": (brief_from_payload(tool_payload) if ok else "工具失败"),
        "progress_percent": min(95, 40 + handle.react_round * 5),
        "decision": "continue" if ok else "replan",
        "tool_io_brief": {
            "tool_name": handle.tool_name,
            "cost_ms": cost_ms,
            "input": tool_args,
            "output_preview": str(raw_out)[:500] if raw_out is not None else "",
        },
        "tool_result_analysis": (tool_err or brief_from_payload(tool_payload))[:500],
        "confidence": 0.88 if ok else 0.2,
    }
    _span_finish(
        handle.step_id,
        status="completed" if ok else "failed",
        output_payload=tool_payload,
        error_message=tool_err or "",
        open_layer=open_done,
    )
    if handle.task_id:
        _span_patch_snapshot(
            handle.task_id,
            fixed={
                "task_id": handle.task_id,
                "session_id": handle.session_id,
                "react_round": handle.react_round,
                "last_tool": handle.tool_name,
                "sub_plan_id": handle.sub_plan_id,
                "last_step_id": handle.step_id,
            },
            open_layer={
                "current_assessment": (
                    f"第 {handle.react_round} 轮 · {handle.tool_name} 已完成"
                    if ok
                    else f"第 {handle.react_round} 轮 · {handle.tool_name} 失败"
                ),
                "tool_io_brief": open_done["tool_io_brief"],
                "decision": open_done["decision"],
            },
        )
    store = _get_task_state_store()
    if store and handle.task_id and handle.state_step_id:
        try:
            store.record_step_finished(
                session_id=handle.session_id,
                task_id=handle.task_id,
                step_id=handle.state_step_id,
                status="completed" if ok else "failed",
                output_payload=tool_payload,
                error_message=tool_err or "",
            )
            store.record_step_decision(
                session_id=handle.session_id,
                task_id=handle.task_id,
                step_id=handle.state_step_id,
                open_layer=open_done,
            )
        except Exception as ex:
            _LOG.warning(
                "[AI问答-SPAN拦截|span_tool_interceptor.end_tool_span|step:%s|硬编执行|TaskStateStore] "
                "落库失败; error_type=%s; error_message=%s",
                handle.step_id,
                type(ex).__name__,
                str(ex)[:200],
            )
    _LOG.info(
        "[AI问答-SPAN拦截|span_tool_interceptor.end_tool_span|tool:%s|硬编执行|结束] "
        "step_id=%s; ok=%s; cost_ms=%s",
        handle.tool_name,
        handle.step_id,
        ok,
        cost_ms,
    )
    return tool_payload


async def invoke_tool_with_span(
    handle: ToolSpanHandle,
    *,
    tool_args: Dict[str, Any],
    invoke: Callable[[], Awaitable[_T]],
    phase: str = "tool",
) -> tuple[_T, Dict[str, Any]]:
    """执行 callable 并自动走拦截器收尾。"""
    tool_err: Optional[str] = None
    raw_out: Any = None
    try:
        raw_out = await invoke()
        if isinstance(raw_out, dict) and raw_out.get("error"):
            tool_err = str(raw_out.get("error"))
    except Exception as ex:
        raw_out = {"ok": False, "error": str(ex)}
        tool_err = str(ex)
    payload = end_tool_span(
        handle,
        tool_args=tool_args,
        raw_out=raw_out,
        tool_err=tool_err,
        phase=phase,
    )
    return raw_out, payload
