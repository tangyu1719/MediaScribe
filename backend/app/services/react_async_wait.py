"""ReAct 段异步工具等待：先挂起轮询，再回灌 Observation（对齐 Cursor 长任务工具行为）。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

_log = logging.getLogger("sba.react_async_wait")

_PIPELINE_ACTIVE_STATUSES = frozenset({
    "pending", "started", "running", "downloading", "transcribing", "generating",
    "extracting", "ocr", "comments", "assembling", "consolidating", "feishu_upload",
    "generating_html", "in_progress", "async_pending",
})

_TERMINAL_OK = frozenset({"completed", "ok", "done", "success"})
_TERMINAL_FAIL = frozenset({"failed", "cancelled", "error"})


def _coerce_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def extract_async_pipeline_ids(tool_name: str, raw_out: Any) -> List[str]:
    """从 link_pipeline_start 等返回中解析后台流水线 task_id。"""
    name = (tool_name or "").strip()
    if name != "link_pipeline_start":
        return []
    tr = _coerce_dict(raw_out)
    if tr.get("ok") is not True and not tr.get("async"):
        return []
    tid = str(tr.get("task_id") or "").strip()
    return [tid] if tid else []


def pipeline_snapshot_row(pid: str) -> Dict[str, Any]:
    """流水线任务当前快照（供 ReAct 等待后回灌）。"""
    return _pipeline_snapshot_row(pid)


def _pipeline_snapshot_row(pid: str) -> Dict[str, Any]:
    from .task_manager import get_task

    row = get_task(pid) or {}
    st = str(row.get("status") or "unknown").lower()
    return {
        "task_id": pid,
        "status": st,
        "progress": row.get("progress"),
        "stage": row.get("stage"),
        "doc_filename": row.get("doc_filename"),
        "html_status": row.get("html_status"),
        "error": row.get("error"),
        "link": row.get("link") or row.get("url"),
    }


def build_pipeline_wait_result(
    pipeline_ids: List[str],
    *,
    timeout: bool = False,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """轮询结束后构造回灌 LLM 的 tool_result 对象。"""
    ids = [str(x).strip() for x in pipeline_ids if str(x).strip()]
    results = list(rows or [])
    if not results:
        results = [_pipeline_snapshot_row(pid) for pid in ids]
    any_failed = any(str(r.get("status") or "").lower() in _TERMINAL_FAIL for r in results)
    any_active = any(str(r.get("status") or "").lower() in _PIPELINE_ACTIVE_STATUSES for r in results)
    all_ok = bool(results) and not any_failed and not any_active
    doc_names = [str(r.get("doc_filename") or "") for r in results if r.get("doc_filename")]
    return {
        "ok": all_ok and not timeout,
        "async": True,
        "waited_in_react": True,
        "timeout": bool(timeout),
        "task_id": ids[0] if ids else "",
        "pipeline_task_ids": ids,
        "pipelines": results,
        "doc_filename": doc_names[0] if doc_names else "",
        "hint": (
            "流水线仍在执行，请稍后 cache_query 或继续追问"
            if timeout or any_active
            else ("流水线失败，请检查错误字段" if any_failed else "流水线已完成，可基于 MD/HTML 产出作答")
        ),
    }


async def poll_pipelines_until_settled(
    pipeline_ids: List[str],
    *,
    timeout_sec: float,
    poll_sec: float = 4.0,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
    """
  同步轮询至流水线结束或超时。
  返回 (merged_result, rows, timed_out)。
  """
    ids = [str(x).strip() for x in (pipeline_ids or []) if str(x).strip()]
    if not ids:
        return {}, [], False
    deadline = time.perf_counter() + max(30.0, float(timeout_sec or 600))
    poll = max(1.0, float(poll_sec or 4.0))
    last_rows: List[Dict[str, Any]] = []
    while time.perf_counter() < deadline:
        last_rows = [_pipeline_snapshot_row(pid) for pid in ids]
        any_active = any(str(r.get("status") or "").lower() in _PIPELINE_ACTIVE_STATUSES for r in last_rows)
        if not any_active:
            return build_pipeline_wait_result(ids, timeout=False, rows=last_rows), last_rows, False
        await asyncio.sleep(min(poll, max(0.5, deadline - time.perf_counter())))
    last_rows = [_pipeline_snapshot_row(pid) for pid in ids]
    return (
        build_pipeline_wait_result(ids, timeout=True, rows=last_rows),
        last_rows,
        True,
    )
