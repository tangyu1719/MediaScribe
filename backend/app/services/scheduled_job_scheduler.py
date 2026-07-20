"""统一定时任务调度 — APScheduler 读取 DB 配置。"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

_log = logging.getLogger("sba.scheduled_job_scheduler")
_CHAIN = "定时任务-调度"

_scheduler = None
_scheduler_running = False
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_job_ids: Dict[str, str] = {}

# 收藏夹同步只能由用户通过手动执行接口触发。即使旧数据库中该任务仍为
# enabled，也不能在应用启动时恢复为 APScheduler 周期任务。
MANUAL_ONLY_JOB_KEYS = frozenset({"favorites_sync_all"})


def register_main_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def _enabled_globally() -> bool:
    return os.environ.get("SCHEDULED_JOBS_ENABLED", "1").strip() not in ("0", "false", "False")


def _submit_async(coro) -> None:
    if _main_loop and _main_loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(coro, _main_loop)
        fut.add_done_callback(
            lambda f: _log.error(
                "[%s|scheduled_job_scheduler._submit_async|event_loop|Agent执行|失败] error=%s",
                _CHAIN,
                f.exception(),
            )
            if f.exception()
            else None
        )
    else:
        try:
            asyncio.run(coro)
        except Exception as ex:
            _log.exception(
                "[%s|scheduled_job_scheduler._submit_async|asyncio.run|Agent执行|失败] error=%s",
                _CHAIN,
                ex,
            )


def _make_trigger(job: Dict[str, Any]):
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from .scheduled_job_service import resolve_trigger

    ttype, params = resolve_trigger(job)
    tz = "Asia/Shanghai"
    if ttype == "interval":
        return IntervalTrigger(minutes=int(params.get("minutes") or 60), timezone=tz)
    return CronTrigger(
        minute=params.get("minute", "*"),
        hour=params.get("hour", "*"),
        day=params.get("day", "*"),
        month=params.get("month", "*"),
        day_of_week=params.get("day_of_week", "*"),
        timezone=tz,
    )


def _run_job_sync(job_key: str) -> None:
    from .scheduled_job_service import execute_job

    _submit_async(execute_job(job_key, trigger="scheduled"))


def refresh_job_schedule(job_key: str) -> None:
    from .scheduled_job_store import get_job

    if _scheduler is None:
        return
    job = get_job(job_key)
    if not job:
        return
    ap_id = _job_ids.get(job_key) or f"sched_{job_key}"
    try:
        _scheduler.remove_job(ap_id)
    except Exception:
        pass
    if job_key in MANUAL_ONLY_JOB_KEYS:
        _job_ids.pop(job_key, None)
        _log.info(
            "[%s|scheduled_job_scheduler.refresh_job_schedule|%s|硬编执行|跳过] mode=manual_only",
            _CHAIN,
            job_key,
        )
        return
    if not job.get("enabled"):
        _job_ids.pop(job_key, None)
        return
    trigger = _make_trigger(job)
    _scheduler.add_job(
        _run_job_sync,
        trigger,
        id=ap_id,
        replace_existing=True,
        args=[job_key],
    )
    _job_ids[job_key] = ap_id
    _log.info(
        "[%s|scheduled_job_scheduler.refresh_job_schedule|%s|硬编执行|刷新] preset=%s; enabled=%s",
        _CHAIN,
        job_key,
        job.get("frequency_preset"),
        job.get("enabled"),
    )


def start_scheduled_job_scheduler() -> Dict[str, Any]:
    global _scheduler, _scheduler_running
    if not _enabled_globally():
        _scheduler_running = False
        return {"scheduler_running": False, "reason": "SCHEDULED_JOBS_ENABLED=0"}

    from .scheduled_job_service import seed_default_jobs
    from .scheduled_job_store import is_enabled, list_jobs

    if not is_enabled():
        _log.warning(
            "[%s|scheduled_job_scheduler.start|scheduled_jobs|硬编执行|跳过] reason=SBA_DATABASE_URL未配置",
            _CHAIN,
        )
        return {"scheduler_running": False, "reason": "SBA_DATABASE_URL 未配置"}

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError as ex:
        _log.error("[%s|scheduled_job_scheduler.start|APScheduler|硬编执行|失败] %s", _CHAIN, ex)
        return {"scheduler_running": False, "error": str(ex)}

    if _scheduler is not None:
        return get_scheduler_status()

    seed_default_jobs()
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    for job in list_jobs():
        if job.get("enabled"):
            refresh_job_schedule(job["job_key"])
    _scheduler.start()
    _scheduler_running = True
    _log.info(
        "[%s|scheduled_job_scheduler.start|scheduler|硬编执行|启动] ok=true; jobs=%s",
        _CHAIN,
        len(_job_ids),
    )
    return get_scheduler_status()


def stop_scheduled_job_scheduler() -> Dict[str, Any]:
    global _scheduler, _scheduler_running, _job_ids
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
    _job_ids = {}
    _scheduler_running = False
    return {"scheduler_running": False}


def get_scheduler_status() -> Dict[str, Any]:
    jobs = []
    if _scheduler is not None:
        for ap_id, job_key in list(_job_ids.items()):
            j = _scheduler.get_job(ap_id)
            jobs.append(
                {
                    "job_key": job_key,
                    "apscheduler_id": ap_id,
                    "next_run": j.next_run_time.isoformat() if j and j.next_run_time else "",
                }
            )
    return {
        "scheduler_running": _scheduler_running and _enabled_globally(),
        "enabled_env": _enabled_globally(),
        "registered_jobs": jobs,
        "job_count": len(_job_ids),
    }
