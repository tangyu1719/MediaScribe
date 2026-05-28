"""Eval 评测运维层 —— OPS 聚合、轨迹评测、Tracing 状态（对齐 Langfuse/agentevals 规范）。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .config import (
    eval_enabled,
    eval_sdk_root,
    langfuse_enabled,
    langsmith_tracing_enabled,
    rag_eval_dataset_path,
    ragas_eval_enabled,
)
from .packages import packages_installed
from .references import list_references, load_reference
from .run_store import get_last_run, get_last_runs, save_run
from .tracing import eval_tracing_status
from .trajectory_eval import evaluate_trajectory, messages_from_span_steps

_log = logging.getLogger("sba.eval.ops")


def eval_get_overview() -> Dict[str, Any]:
    """OPS Eval 子页主数据：Tracing + 包 + 最近跑批 + 能力开关。"""
    tracing = eval_tracing_status()
    pkgs = packages_installed()
    runs = get_last_runs()
    langfuse_host = (
        (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or "").strip()
        or "https://cloud.langfuse.com"
    )
    return {
        "ok": True,
        "data": {
            "eval_enabled": eval_enabled(),
            "ragas_enabled": ragas_eval_enabled(),
            "rag_dataset_configured": rag_eval_dataset_path() is not None,
            "packages": pkgs,
            "tracing": tracing,
            "langfuse_ui_url": langfuse_host if langfuse_enabled() else "",
            "references_count": len(list_references()),
            "last_runs": runs.get("last") or {},
            "capabilities": {
                "langfuse_tracing": langfuse_enabled() and pkgs.get("langfuse", False),
                "langsmith_tracing": langsmith_tracing_enabled() and pkgs.get("langsmith", False),
                "trajectory_strict": pkgs.get("agentevals", False),
                "trajectory_unordered": pkgs.get("agentevals", False),
                "ragas_offline": ragas_eval_enabled() and pkgs.get("ragas", False),
            },
        },
    }


def eval_list_traces(*, limit: int = 50, scope: str = "all") -> Dict[str, Any]:
    """最近 SPAN 主任务（pipeline + chat），供 Eval 轨迹选取。"""
    limit = max(1, min(int(limit or 50), 200))
    scope = (scope or "all").strip().lower()
    items: List[Dict[str, Any]] = []

    if scope in ("all", "pipeline"):
        try:
            from app.services.span_audit import list_pipeline_span_tasks
            from app.services.history_manager import _public_span_task

            for t in list_pipeline_span_tasks(limit=limit):
                pub = _public_span_task(t)
                pub["scope"] = "pipeline"
                items.append(pub)
        except Exception as ex:
            _log.debug("eval_list_traces pipeline: %s", ex)

    if scope in ("all", "chat"):
        try:
            from app.services.span_audit import _db_available, _row_to_task, _load_steps_from_db
            from app.services.history_manager import _public_span_task

            if _db_available:
                import db as _db

                rows = _db.execute_query(
                    "SELECT * FROM span_tasks WHERE session_id NOT LIKE 'pipeline:%' "
                    "ORDER BY updated_at DESC LIMIT %s",
                    (limit,),
                )
                seen = {x.get("task_id") for x in items}
                for r in rows:
                    tid = r.get("task_id")
                    if tid in seen:
                        continue
                    task = _row_to_task(r)
                    task["steps"] = _load_steps_from_db(tid)
                    pub = _public_span_task(task)
                    pub["scope"] = "chat"
                    items.append(pub)
        except Exception as ex:
            _log.debug("eval_list_traces chat: %s", ex)

    items.sort(key=lambda x: x.get("ended_at") or x.get("started_at") or "", reverse=True)
    return {"ok": True, "data": {"traces": items[:limit], "total": len(items[:limit])}}


def eval_trajectory_from_span(
    task_id: str,
    *,
    reference_id: str = "",
    reference_outputs: Optional[List[Dict[str, Any]]] = None,
    mode: str = "strict",
) -> Dict[str, Any]:
    """从 span_audit 步骤生成 outputs 并做轨迹评测。"""
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id 不能为空"}

    steps: List[Dict[str, Any]] = []
    span_task = None
    try:
        from app.services.span_audit import get_task

        span_task = get_task(tid)
        if span_task:
            steps = list(span_task.get("steps") or [])
    except Exception:
        pass

    if not steps:
        try:
            from app.services.history_manager import build_task_log_bundle

            bundle = build_task_log_bundle(tid)
            steps = bundle.get("spans") or []
            span_task = bundle.get("span_task")
        except Exception:
            pass

    outputs = messages_from_span_steps(steps)
    if not outputs:
        return {
            "ok": False,
            "error": "该任务无可用 SPAN 步骤，无法生成轨迹",
            "task_id": tid,
            "step_count": len(steps),
        }

    ref: List[Dict[str, Any]] = list(reference_outputs or [])
    ref_name = ""
    if not ref and reference_id:
        loaded = load_reference(reference_id)
        if not loaded.get("ok"):
            return loaded
        ref = loaded.get("reference_outputs") or []
        ref_name = loaded.get("name") or reference_id

    if not ref:
        result = {
            "ok": True,
            "task_id": tid,
            "outputs": outputs,
            "output_message_count": len(outputs),
            "eval_skipped": True,
            "reason": "未提供 reference_outputs 或 reference_id，仅返回轨迹序列",
            "span_task": span_task,
        }
        save_run("trajectory_preview", {"task_id": tid, "output_count": len(outputs), "ok": True})
        return result

    eval_result = evaluate_trajectory(outputs, ref, mode=mode)
    payload = {
        "task_id": tid,
        "mode": mode,
        "reference_id": reference_id or None,
        "reference_name": ref_name or None,
        "output_message_count": len(outputs),
        "reference_message_count": len(ref),
        **eval_result,
    }
    save_run(
        "trajectory",
        {
            "task_id": tid,
            "mode": mode,
            "score": eval_result.get("score"),
            "ok": eval_result.get("ok"),
            "skipped": eval_result.get("skipped"),
        },
    )
    return {
        "ok": True,
        "task_id": tid,
        "outputs": outputs,
        "reference_outputs": ref,
        **eval_result,
        "meta": payload,
    }


def eval_run_trajectory(
    outputs: List[Dict[str, Any]],
    reference_outputs: List[Dict[str, Any]],
    *,
    mode: str = "strict",
) -> Dict[str, Any]:
    result = evaluate_trajectory(outputs, reference_outputs, mode=mode)
    save_run(
        "trajectory_manual",
        {"mode": mode, "score": result.get("score"), "ok": result.get("ok"), "skipped": result.get("skipped")},
    )
    return result


def eval_rag_status() -> Dict[str, Any]:
    ds = rag_eval_dataset_path()
    last = get_last_run("ragas")
    return {
        "ok": True,
        "data": {
            "enabled": ragas_eval_enabled(),
            "dataset_path": str(ds) if ds else "",
            "dataset_exists": bool(ds and ds.is_file()),
            "packages": packages_installed(),
            "last_run": last,
        },
    }


def eval_get_references() -> Dict[str, Any]:
    return {"ok": True, "data": {"references": list_references()}}


def eval_extended_status() -> Dict[str, Any]:
    """扩展 /api/eval/status。"""
    base = eval_tracing_status()
    base["eval_enabled"] = eval_enabled()
    base["ragas_enabled"] = ragas_eval_enabled()
    base["packages"] = packages_installed()
    base["sdk_root"] = str(eval_sdk_root())
    base["last_trajectory_run"] = get_last_run("trajectory") or get_last_run("trajectory_manual")
    base["last_rag_run"] = get_last_run("ragas")
    return base
