"""RSS 订阅定时同步 — APScheduler。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

_log = logging.getLogger("sba.rss_scheduler")
_scheduler = None
_scheduler_running = False
_config: Dict[str, Any] = {}


def _enabled() -> bool:
    return os.environ.get("RSS_SCHEDULER_ENABLED", "1").strip() not in ("0", "false", "False")


def _cron() -> str:
    return os.environ.get("RSS_DEFAULT_CRON", "*/30 * * * *").strip()


def _run_sync_job() -> None:
    from .rss_reader import sync_all_users_feeds

    try:
        result = sync_all_users_feeds(trigger="scheduled")
        _log.info(
            "[RSS订阅阅读-定时同步|rss_scheduler._run_sync_job|all-users|工具执行|完成] "
            "users=%s; ok=%s; fail=%s",
            result.get("user_count"),
            result.get("ok_count"),
            result.get("fail_count"),
        )
    except Exception as ex:
        _log.exception(
            "[RSS订阅阅读-定时同步|rss_scheduler._run_sync_job|all-users|工具执行|失败] error_message=%s",
            ex,
        )


def start_scheduler() -> Dict[str, Any]:
    global _scheduler, _scheduler_running, _config
    if not _enabled():
        _scheduler_running = False
        return {"scheduler_running": False, "reason": "RSS_SCHEDULER_ENABLED=0"}

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as ex:
        _log.error(
            "[RSS订阅阅读-定时同步|rss_scheduler.start|APScheduler|硬编执行|失败] error_message=%s",
            ex,
        )
        return {"scheduler_running": False, "error": str(ex)}

    if _scheduler is not None:
        return get_scheduler_status()

    cron = _cron()
    parts = cron.split()
    if len(parts) != 5:
        parts = "*/30 * * * *".split()

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        _run_sync_job,
        CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone="Asia/Shanghai",
        ),
        id="rss_sync_all_users",
        replace_existing=True,
    )
    _scheduler.start()
    _scheduler_running = True
    _config = {"cron": cron, "timezone": "Asia/Shanghai"}
    _log.info(
        "[RSS订阅阅读-定时同步|rss_scheduler.start|scheduler|硬编执行|启动] ok=true; cron=%s",
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
