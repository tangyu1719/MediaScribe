"""社媒订阅定时调度 — APScheduler。"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

_log = logging.getLogger("sba.creator_scheduler")
_scheduler = None
_scheduler_running = False
_config: Dict[str, Any] = {}


def _enabled() -> bool:
    return os.environ.get("SUBSCRIPTION_SCHEDULER_ENABLED", "1").strip() not in ("0", "false", "False")


def _cron() -> str:
    return os.environ.get("SUBSCRIPTION_DEFAULT_CRON", "0 8 * * *").strip()


def start_scheduler() -> Dict[str, Any]:
    global _scheduler, _scheduler_running, _config
    if not _enabled():
        _scheduler_running = False
        return {"scheduler_running": False, "reason": "SUBSCRIPTION_SCHEDULER_ENABLED=0"}

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as ex:
        _log.error("[社媒订阅-调度|creator_scheduler.start|APScheduler|硬编执行|失败] %s", ex)
        return {"scheduler_running": False, "error": str(ex)}

    if _scheduler is not None:
        return get_scheduler_status()

    cron = _cron()
    parts = cron.split()
    if len(parts) != 5:
        parts = "0 8 * * *".split()

    def _job():
        try:
            from .creator_sync_runner import run_sync_all

            asyncio.run(run_sync_all(trigger="scheduled"))
        except Exception as ex:
            _log.exception(
                "[社媒订阅-调度|creator_scheduler._job|sync-all|Agent执行|失败] error=%s",
                ex,
            )

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        _job,
        CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone="Asia/Shanghai",
        ),
        id="creator_subscription_daily",
        replace_existing=True,
    )
    _scheduler.start()
    _scheduler_running = True
    _config = {"cron": cron, "timezone": "Asia/Shanghai"}
    _log.info(
        "[社媒订阅-调度|creator_scheduler.start|scheduler|硬编执行|启动] ok=true; cron=%s",
        cron,
    )
    return get_scheduler_status()


def stop_scheduler() -> Dict[str, Any]:
    global _scheduler, _scheduler_running
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
    _scheduler_running = False
    return {"scheduler_running": False}


def get_scheduler_status() -> Dict[str, Any]:
    return {
        "scheduler_running": _scheduler_running and _enabled(),
        "config": dict(_config),
        "enabled_env": _enabled(),
        "default_cron": _cron(),
    }
