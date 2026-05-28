"""流水线 MD 完成后收尾：指标汇总、任务完成态、HTML/飞书后台任务。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

from .pipeline_checkpoint import clear_pipeline_cache
from .pipeline_stages import PipelineStageTracker
from .task_manager import add_log, get_task, update_task


def _parse_iso_dt(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return None


def _sum_tokens_from_logs(task: Dict[str, Any]) -> int:
    total = 0
    for ln in task.get("logs") or []:
        msg = str(ln.get("message") or "")
        for m in re.finditer(r"token_count=(\d+)", msg):
            total += int(m.group(1))
    return total


def sync_pipeline_task_metrics(task_id: str) -> Dict[str, int]:
    """汇总流水线总耗时(ms)与 Token（SPAN 优先，日志 token_count= 兜底）。"""
    task = get_task(task_id) or {}
    started = _parse_iso_dt(task.get("pipeline_started_at") or task.get("created_at") or "")
    elapsed_ms = 0
    if started:
        elapsed_ms = max(0, int((datetime.now() - started.replace(tzinfo=None)).total_seconds() * 1000))

    total_tokens = 0
    try:
        from .span_audit import get_task as span_get_task

        span = span_get_task(task_id) or {}
        total_tokens = int(span.get("total_token_count") or 0)
        span_ms = int(span.get("total_duration_ms") or 0)
        if span_ms > 0:
            elapsed_ms = span_ms
    except Exception:
        pass

    if total_tokens <= 0:
        total_tokens = _sum_tokens_from_logs(task)

    return {"total_duration_ms": elapsed_ms, "total_token_count": total_tokens}


def apply_task_card_metrics(task_id: str, *, persist: bool = False) -> Dict[str, int]:
    """汇总卡片展示用耗时/Token；persist=True 时写回 task。"""
    metrics = sync_pipeline_task_metrics(task_id)
    if persist:
        update_task(task_id, **metrics)
    return metrics


def mark_pipeline_running(task_id: str) -> None:
    """记录流水线实际开始时间（用于卡片总耗时）。"""
    task = get_task(task_id) or {}
    if task.get("pipeline_started_at"):
        return
    update_task(task_id, pipeline_started_at=datetime.now().isoformat(timespec="seconds"))


def complete_task_after_md(
    task_id: str,
    *,
    doc_path: str,
    link: str,
    platform: str,
    tracker: Optional[PipelineStageTracker] = None,
    title: str = "",
    html_step_id: str = "",
    url_hash: str = "",
) -> Dict[str, int]:
    """
    MD 落盘后即视为任务完成（飞书/HTML 走后台，不再阻塞 status=completed）。
    """
    from .file_naming import output_basename
    from .history_manager import add_or_update_task_in_history
    from .video_pipeline import start_html_generation

    metrics = sync_pipeline_task_metrics(task_id)
    update_task(
        task_id,
        status="completed",
        stage="完成",
        progress=100,
        doc_filename=output_basename(doc_path),
        doc_path=doc_path,
        failed_stage="",
        failed_stage_label="",
        resume_from="",
        error=None,
        feishu_status=(get_task(task_id) or {}).get("feishu_status") or "",
        **metrics,
    )
    add_log(
        task_id,
        f"MD 已生成，任务完成；耗时 {metrics['total_duration_ms']}ms，"
        f"Token {metrics['total_token_count']}（飞书/HTML 后台继续）",
        "INFO",
    )

    start_html_generation(
        doc_path,
        task_id,
        title=title,
        platform=platform,
        link=link,
        html_step_id=html_step_id or "",
    )
    if tracker:
        tracker.complete("html", {"html_status": "async_pending"})
        tracker.finish_success()
    if url_hash:
        clear_pipeline_cache(url_hash)

    task = get_task(task_id)
    if task:
        add_or_update_task_in_history(task)
    return metrics
