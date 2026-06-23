"""定时任务 — 频率预设、执行体、默认任务种子。"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .scheduled_job_store import (
    create_run,
    finish_run,
    get_job,
    get_run,
    get_running_run_for_job,
    is_enabled,
    is_run_cancel_requested,
    list_active_run_cards,
    list_jobs,
    mark_job_run,
    request_run_cancel,
    update_run_live,
    upsert_job,
)

_log = logging.getLogger("sba.scheduled_job_service")
_CHAIN = "定时任务-编排"

FREQUENCY_PRESETS: Dict[str, Dict[str, Any]] = {
    "30MIN": {"label": "每 30 分钟", "type": "interval", "minutes": 30},
    "1H": {"label": "每 1 小时", "type": "interval", "minutes": 60},
    "6H": {"label": "每 6 小时", "type": "interval", "minutes": 360},
    "12H": {"label": "每 12 小时", "type": "interval", "minutes": 720},
    "24H": {"label": "每 24 小时（默认 08:00）", "type": "daily", "hour": 8, "minute": 0},
    "DAILY_FIXED": {"label": "每天固定时间", "type": "daily"},
    "CUSTOM_INTERVAL": {"label": "自定义间隔（分钟）", "type": "interval"},
    "CUSTOM_CRON": {"label": "自定义 Cron", "type": "cron"},
}

DEFAULT_JOBS: List[Dict[str, Any]] = [
    {
        "job_key": "creator_sync_all",
        "name": "社媒 UP 订阅同步",
        "category": "subscription",
        "description": "拉取所有活跃小红书 UP 订阅，判重后入队链接沉淀流水线。",
        "frequency_preset": "24H",
        "daily_hour": 8,
        "daily_minute": 0,
        "enabled": True,
        "params_json": {"trigger_label": "scheduled"},
    },
    {
        "job_key": "favorites_sync_all",
        "name": "收藏夹订阅同步",
        "category": "subscription",
        "description": "同步小红书收藏夹订阅，增量拉取并分析新笔记。",
        "frequency_preset": "24H",
        "daily_hour": 0,
        "daily_minute": 0,
        "enabled": True,
        "params_json": {"trigger_label": "scheduled"},
    },
    {
        "job_key": "rss_sync_all",
        "name": "RSS 订阅同步",
        "category": "subscription",
        "description": "同步所有用户的 RSS 源并抓取正文。",
        "frequency_preset": "30MIN",
        "enabled": True,
        "params_json": {"trigger_label": "scheduled"},
    },
]


def seed_default_jobs() -> int:
    if not is_enabled():
        return 0
    seeded = 0
    env_map = {
        "creator_sync_all": ("SUBSCRIPTION_DEFAULT_CRON", "0 8 * * *"),
        "favorites_sync_all": ("FAVORITES_DEFAULT_CRON", "0 0 * * *"),
        "rss_sync_all": ("RSS_DEFAULT_CRON", "*/30 * * * *"),
    }
    for tpl in DEFAULT_JOBS:
        key = tpl["job_key"]
        if get_job(key):
            continue
        job = dict(tpl)
        env_key, default_cron = env_map.get(key, ("", ""))
        cron = (os.environ.get(env_key) or default_cron).strip()
        if cron and cron != default_cron:
            parts = cron.split()
            if len(parts) == 5:
                job["frequency_preset"] = "CUSTOM_CRON"
                job["custom_cron"] = cron
        upsert_job(job)
        seeded += 1
    if seeded:
        _log.info(
            "[%s|scheduled_job_service.seed_default_jobs|scheduled_jobs|硬编执行|种子] count=%s",
            _CHAIN,
            seeded,
        )
    return seeded


def list_frequency_presets() -> List[Dict[str, Any]]:
    return [{"key": k, **v} for k, v in FREQUENCY_PRESETS.items()]


def resolve_trigger(job: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """将 job 配置解析为 APScheduler trigger 类型与参数。"""
    preset = (job.get("frequency_preset") or "24H").upper()
    meta = FREQUENCY_PRESETS.get(preset) or FREQUENCY_PRESETS["24H"]
    ttype = meta.get("type")

    if preset == "DAILY_FIXED" or (preset == "24H" and ttype == "daily"):
        hour = int(job.get("daily_hour") if preset == "DAILY_FIXED" else meta.get("hour", 8))
        minute = int(job.get("daily_minute") if preset == "DAILY_FIXED" else meta.get("minute", 0))
        return "cron", {"hour": hour, "minute": minute}

    if preset == "CUSTOM_CRON":
        cron = (job.get("custom_cron") or "").strip()
        parts = cron.split()
        if len(parts) != 5:
            parts = "0 8 * * *".split()
        return "cron", {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4],
            "raw_cron": cron,
        }

    if preset == "CUSTOM_INTERVAL":
        minutes = max(1, int(job.get("custom_interval_minutes") or 60))
        return "interval", {"minutes": minutes}

    if ttype == "interval":
        return "interval", {"minutes": int(meta.get("minutes") or 60)}

    return "cron", {"hour": 8, "minute": 0}


def _summarize_result(job_key: str, result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:500]
    if job_key == "creator_sync_all":
        results = result.get("results") or []
        ok = sum(1 for r in results if r.get("ok"))
        return f"订阅 {len(results)} 个；成功 {ok}；失败 {len(results) - ok}"
    if job_key == "favorites_sync_all":
        return (
            f"状态={result.get('status') or '—'}；新增={result.get('new_count', 0)}；"
            f"分析={result.get('analyzed_count', 0)}；失败={result.get('failed_count', 0)}"
        )
    if job_key == "rss_sync_all":
        return f"用户={result.get('user_count', 0)}；feed={result.get('feed_count', 0)}；错误={result.get('error_count', 0)}"
    if result.get("ok"):
        return str(result.get("message") or result.get("summary") or "ok")
    return str(result.get("error") or result.get("error_message") or "failed")[:500]


async def execute_job(
    job_key: str,
    trigger: str = "scheduled",
    *,
    retry_count: int = 0,
    parent_run_id: str = "",
) -> Dict[str, Any]:
    job = get_job(job_key)
    if not job:
        return {"ok": False, "error_code": "JOB_NOT_FOUND", "error": "任务不存在"}
    if not is_enabled():
        return {"ok": False, "error_code": "DB_UNAVAILABLE", "error": "SBA_DATABASE_URL 未配置"}

    existing = get_running_run_for_job(job_key)
    if existing and trigger != "retry":
        return {
            "ok": False,
            "error_code": "JOB_ALREADY_RUNNING",
            "error": "该任务正在执行中",
            "run_id": existing.get("run_id"),
        }

    run_id = create_run(job_key, trigger, retry_count=retry_count, parent_run_id=parent_run_id)
    started = time.time()
    result: Dict[str, Any] = {}
    status = "failed"
    err_msg = ""

    def _progress(pct: int, stage: str) -> None:
        update_run_live(run_id, progress=pct, stage=stage)

    def _cancelled() -> bool:
        return is_run_cancel_requested(run_id)

    try:
        update_run_live(run_id, progress=3, stage="启动执行")
        if job_key == "creator_sync_all":
            from .creator_sync_runner import run_sync_all

            result = await run_sync_all(
                trigger=trigger if trigger not in ("manual_test", "retry") else "manual",
                progress_cb=_progress,
                cancel_check=_cancelled,
            )
            if result.get("cancelled"):
                status = "cancelled"
                err_msg = "用户取消"
            else:
                status = "completed" if result.get("ok") else "failed"
                if not result.get("ok"):
                    err_msg = str(result.get("error") or "sync_all_failed")
        elif job_key == "favorites_sync_all":
            from .favorites_sync_runner import run_favorites_sync_all

            result = await run_favorites_sync_all(
                trigger=trigger if trigger not in ("manual_test", "retry") else "manual",
                progress_cb=_progress,
                cancel_check=_cancelled,
            )
            if result.get("cancelled"):
                status = "cancelled"
                err_msg = "用户取消"
            else:
                status = "completed" if result.get("ok") else "failed"
                if not result.get("ok"):
                    err_msg = str(result.get("error") or "favorites_sync_failed")
        elif job_key == "rss_sync_all":
            from .rss_reader import sync_all_users_feeds

            _progress(8, "同步 RSS 源")
            if _cancelled():
                status = "cancelled"
                err_msg = "用户取消"
                result = {"ok": False, "cancelled": True}
            else:
                result = sync_all_users_feeds(trigger=trigger if trigger not in ("manual_test", "retry") else "manual")
                _progress(92, "汇总 RSS 结果")
                status = "completed" if result.get("ok") else "failed"
                if not result.get("ok"):
                    err_msg = str(result.get("error") or "rss_sync_failed")
        else:
            err_msg = f"未知任务类型: {job_key}"
            result = {"ok": False, "error": err_msg}
    except Exception as ex:
        err_msg = str(ex)
        result = {"ok": False, "error": err_msg}
        _log.exception(
            "[%s|scheduled_job_service.execute_job|%s|Agent执行|失败] trigger=%s; error=%s",
            _CHAIN,
            job_key,
            trigger,
            ex,
        )

    duration_ms = int((time.time() - started) * 1000)
    summary = _summarize_result(job_key, result)
    if status == "completed" and not result.get("ok"):
        status = "failed"
    if err_msg and status == "completed":
        status = "partial" if result.get("ok") else "failed"

    finish_run(
        run_id,
        status=status,
        summary=summary,
        result=result if isinstance(result, dict) else {"raw": str(result)},
        error_message=err_msg,
        duration_ms=duration_ms,
    )
    mark_job_run(job_key, status, err_msg)

    try:
        from .ops import ops_add_scheduled_job_event

        ops_add_scheduled_job_event(
            job_key=job_key,
            run_id=run_id,
            trigger=trigger,
            status=status,
            summary=summary,
            duration_ms=duration_ms,
            error_message=err_msg,
        )
    except Exception:
        pass

    _log.info(
        "[%s|scheduled_job_service.execute_job|%s|Agent执行|完成] trigger=%s; status=%s; duration_ms=%s; summary=%s",
        _CHAIN,
        job_key,
        trigger,
        status,
        duration_ms,
        summary[:120],
    )
    return {
        "ok": status in ("completed", "partial"),
        "job_key": job_key,
        "run_id": run_id,
        "status": status,
        "summary": summary,
        "duration_ms": duration_ms,
        "result": result,
        "error": err_msg,
    }


def api_list_jobs() -> Dict[str, Any]:
    seed_default_jobs()
    jobs = list_jobs()
    return {"ok": True, "jobs": jobs, "presets": list_frequency_presets()}


def api_update_job(job_key: str, body: Dict[str, Any]) -> Dict[str, Any]:
    from .scheduled_job_store import update_job
    from .scheduled_job_scheduler import refresh_job_schedule

    allowed = {
        "frequency_preset",
        "custom_cron",
        "custom_interval_minutes",
        "daily_hour",
        "daily_minute",
        "enabled",
        "name",
        "description",
    }
    patch = {k: body[k] for k in allowed if k in body}
    row = update_job(job_key, patch)
    if not row:
        return {"ok": False, "error_code": "JOB_NOT_FOUND", "error": "任务不存在"}
    refresh_job_schedule(job_key)
    return {"ok": True, "job": row}


async def api_run_job(job_key: str, trigger: str = "manual_test") -> Dict[str, Any]:
    return await execute_job(job_key, trigger=trigger)


def api_list_active_cards() -> Dict[str, Any]:
    cards = list_active_run_cards()
    running = sum(1 for c in cards if (c.get("status") or "") in ("running", "started", "in_progress"))
    return {"ok": True, "cards": cards, "running_count": running}


def api_cancel_run(run_id: str) -> Dict[str, Any]:
    row = get_run(run_id)
    if not row:
        return {"ok": False, "error_code": "RUN_NOT_FOUND", "error": "执行记录不存在"}
    if (row.get("status") or "") not in ("running", "started", "in_progress"):
        return {"ok": False, "error_code": "NOT_RUNNING", "error": "任务未在运行中"}
    ok = request_run_cancel(run_id)
    return {"ok": ok, "run_id": run_id, "message": "已请求取消，将在当前步骤结束后停止"}


async def api_retry_run(run_id: str) -> Dict[str, Any]:
    row = get_run(run_id)
    if not row:
        return {"ok": False, "error_code": "RUN_NOT_FOUND", "error": "执行记录不存在"}
    if (row.get("status") or "") not in ("failed", "cancelled"):
        return {"ok": False, "error_code": "NOT_RETRYABLE", "error": "仅失败或已取消的任务可重试"}
    job_key = row.get("job_key") or ""
    retry_count = int(row.get("retry_count") or 0) + 1
    return await execute_job(
        job_key,
        trigger="retry",
        retry_count=retry_count,
        parent_run_id=run_id,
    )
