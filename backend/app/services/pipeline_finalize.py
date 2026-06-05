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


def _empty_task_metrics() -> Dict[str, int]:
    return {
        "total_duration_ms": 0,
        "total_token_count": 0,
        "article_char_count": 0,
        "summary_char_count": 0,
    }


def _resolve_md_char_counts(task: Dict[str, Any], tracker: Optional[PipelineStageTracker] = None) -> tuple[int, int]:
    """从断点上下文 / 阶段结果 / 日志解析原文字数与摘要字数。"""
    ai_ctx: Dict[str, Any] = {}
    if tracker is not None:
        ai_ctx = tracker.ctx_get("ai_analysis") or {}
    if not ai_ctx:
        rc = task.get("resume_context") or {}
        ai_ctx = rc.get("ai_analysis") or {}
    article = len(str(ai_ctx.get("article") or ""))
    summary = len(str(ai_ctx.get("ai_summary") or ""))
    if article or summary:
        return article, summary

    ps = (task.get("pipeline_stages") or {}).get("ai_analysis") or {}
    res = ps.get("result") or {}
    summary = int(res.get("ai_summary_len") or 0)
    article = int(res.get("article_len") or 0)
    if article or summary:
        return article, summary

    for ln in reversed(task.get("logs") or []):
        msg = str(ln.get("message") or "")
        m = re.search(r"ai_summary_len=(\d+).*article_len=(\d+)", msg)
        if m:
            return int(m.group(2)), int(m.group(1))
    return 0, 0


def _compute_md_duration_ms(task: Dict[str, Any], *, end_dt: Optional[datetime] = None) -> int:
    """仅计算「开始分析链接 → MD 产出」耗时，不含 HTML/飞书后台。"""
    started = _parse_iso_dt(task.get("pipeline_started_at") or task.get("created_at") or "")
    if not started:
        return 0

    end = end_dt
    if end is None:
        end = _parse_iso_dt(str(task.get("md_completed_at") or ""))
    if end is None:
        ps = (task.get("pipeline_stages") or {}).get("generate_md") or {}
        end = _parse_iso_dt(str(ps.get("updated_at") or ""))
    if end is None:
        return 0

    return max(
        0,
        int((end.replace(tzinfo=None) - started.replace(tzinfo=None)).total_seconds() * 1000),
    )


def sync_pipeline_task_metrics(task_id: str) -> Dict[str, int]:
    """仅 completed 任务返回卡片指标；耗时冻结至 MD 产出，Token 不含 HTML/飞书。"""
    task = get_task(task_id) or {}
    if str(task.get("status") or "").lower() != "completed":
        return _empty_task_metrics()

    duration_ms = int(task.get("total_duration_ms") or 0)
    if duration_ms <= 0:
        duration_ms = _compute_md_duration_ms(task)

    total_tokens = int(task.get("total_token_count") or 0)
    if total_tokens <= 0:
        total_tokens = _sum_tokens_from_logs(task)

    article_chars = int(task.get("article_char_count") or 0)
    summary_chars = int(task.get("summary_char_count") or 0)
    if article_chars <= 0 and summary_chars <= 0:
        article_chars, summary_chars = _resolve_md_char_counts(task)

    return {
        "total_duration_ms": duration_ms,
        "total_token_count": total_tokens,
        "article_char_count": article_chars,
        "summary_char_count": summary_chars,
    }


def apply_task_card_metrics(task_id: str, *, persist: bool = False) -> Dict[str, int]:
    """汇总卡片展示用耗时/Token；persist=True 时写回 task。"""
    metrics = sync_pipeline_task_metrics(task_id)
    if persist:
        update_task(task_id, **metrics)
    return metrics


def enrich_completed_task_metrics(task: Dict[str, Any]) -> Dict[str, Any]:
    """为历史/队列条目回填 MD 阶段指标（completed 专用，不修改非完成态）。"""
    out = dict(task)
    if str(out.get("status") or "").lower() != "completed":
        out.update(_empty_task_metrics())
        return out

    duration_ms = int(out.get("total_duration_ms") or 0)
    if duration_ms <= 0:
        duration_ms = _compute_md_duration_ms(out)
        out["total_duration_ms"] = duration_ms

    article_chars = int(out.get("article_char_count") or 0)
    summary_chars = int(out.get("summary_char_count") or 0)
    if article_chars <= 0 and summary_chars <= 0:
        article_chars, summary_chars = _resolve_md_char_counts(out)
        out["article_char_count"] = article_chars
        out["summary_char_count"] = summary_chars

    return out


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
    from .pipeline_options_util import skip_html as _skip_html

    now = datetime.now()
    now_iso = now.isoformat(timespec="seconds")
    task = get_task(task_id) or {}
    article_chars, summary_chars = _resolve_md_char_counts(task, tracker)
    duration_ms = _compute_md_duration_ms({**task, "md_completed_at": now_iso}, end_dt=now)
    total_tokens = int(task.get("total_token_count") or 0)
    if total_tokens <= 0:
        total_tokens = _sum_tokens_from_logs(task)

    metrics = {
        "md_completed_at": now_iso,
        "total_duration_ms": duration_ms,
        "total_token_count": total_tokens,
        "article_char_count": article_chars,
        "summary_char_count": summary_chars,
    }
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
        feishu_status=task.get("feishu_status") or "",
        **metrics,
    )
    add_log(
        task_id,
        f"MD 已生成，任务完成；耗时 {metrics['total_duration_ms']}ms，"
        f"Token {metrics['total_token_count']}（{article_chars}+{summary_chars} 字；飞书/HTML 后台继续）",
        "INFO",
    )

    if not _skip_html(task):
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
    else:
        add_log(task_id, "[流水线选项] 跳过 HTML 长页生成", "INFO")
        if tracker:
            tracker.complete("html", {"html_status": "skipped"})
        tracker.finish_success()
    if url_hash:
        clear_pipeline_cache(url_hash)

    task = get_task(task_id)
    if task:
        add_or_update_task_in_history(task)
    return metrics
