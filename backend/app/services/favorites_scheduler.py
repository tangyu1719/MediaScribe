"""收藏夹订阅 — 启动时与每日 0:00 调度。"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Dict, Optional

_log = logging.getLogger("sba.favorites_scheduler")
_scheduler = None
_scheduler_running = False
_config: Dict[str, Any] = {}
_startup_scheduled = False


def _enabled() -> bool:
    return os.environ.get("FAVORITES_SCHEDULER_ENABLED", "1").strip() not in ("0", "false", "False")


def _cron() -> str:
    return os.environ.get("FAVORITES_DEFAULT_CRON", "0 0 * * *").strip()


def _red_id() -> str:
    return os.environ.get("XHS_FAVORITES_RED_ID", "9545679835").strip()


def _display_name() -> str:
    return os.environ.get("XHS_FAVORITES_DISPLAY_NAME", "三点、水-收藏夹").strip()


def _startup_delay_sec() -> float:
    try:
        return float(os.environ.get("FAVORITES_STARTUP_DELAY_SEC", "45"))
    except ValueError:
        return 45.0


def ensure_default_favorites_subscription() -> Dict[str, Any]:
    from .creator_subscription_store import get_or_create_subscription, get_subscription_by_platform_creator
    from .xhs_owner_chrome import refresh_owner_xhs_cookies

    os.environ["SBA_BROWSER"] = "chrome"
    red_id = _red_id()
    ck = refresh_owner_xhs_cookies()
    if not ck.get("ok"):
        _log.warning(
            "[小红书收藏夹-调度|favorites_scheduler.ensure|Cookie|硬编执行|未就绪] %s",
            ck.get("error"),
        )

    creator_override = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "").strip()
    if creator_override:
        profile_url = f"https://www.xiaohongshu.com/user/profile/{creator_override}?tab=fav"
        existing = get_subscription_by_platform_creator("xiaohongshu_favorites", creator_override)
        if existing:
            return existing
        return get_or_create_subscription(
            platform="xiaohongshu_favorites",
            creator_id=creator_override,
            profile_url=profile_url,
            display_name=_display_name() or red_id,
            read_comments=False,
            auto_analyze=True,
            tags=["kind:favorites", f"red_id:{red_id}"],
        )

    from .xhs_favorites_adapter import resolve_favorites_owner

    resolved = resolve_favorites_owner(red_id, display_name=_display_name())
    tags = ["kind:favorites", f"red_id:{red_id}"]
    sub = get_or_create_subscription(
        platform="xiaohongshu_favorites",
        creator_id=resolved["creator_id"],
        profile_url=resolved.get("favorites_url") or resolved["profile_url"],
        display_name=_display_name() or resolved.get("display_name") or red_id,
        read_comments=False,
        auto_analyze=True,
        tags=tags,
    )
    return sub


async def _run_startup_sync() -> None:
    try:
        sub = ensure_default_favorites_subscription()
        from .favorites_sync_runner import run_favorites_sync

        result = await run_favorites_sync(sub["subscription_id"], trigger="startup")
        _log.info(
            "[小红书收藏夹-调度|favorites_scheduler._run_startup_sync|sync|Agent执行|完成] ok=%s; new=%s; status=%s",
            result.get("ok"),
            result.get("new_count"),
            result.get("status"),
        )
    except Exception as ex:
        _log.exception(
            "[小红书收藏夹-调度|favorites_scheduler._run_startup_sync|sync|Agent执行|失败] error=%s",
            ex,
        )


def schedule_favorites_on_startup() -> None:
    global _startup_scheduled
    if not _enabled():
        return
    if _startup_scheduled:
        return
    _startup_scheduled = True
    delay = _startup_delay_sec()

    def _defer():
        import time

        time.sleep(delay)
        try:
            asyncio.run(_run_startup_sync())
        except Exception as ex:
            _log.exception(
                "[小红书收藏夹-调度|favorites_scheduler.schedule_favorites_on_startup|sync|Agent执行|失败] %s",
                ex,
            )

    threading.Thread(target=_defer, daemon=True, name="favorites-startup-sync").start()
    _log.info(
        "[小红书收藏夹-调度|favorites_scheduler.schedule_favorites_on_startup|scheduler|硬编执行|启动] delay_sec=%s",
        delay,
    )


def start_scheduler() -> Dict[str, Any]:
    global _scheduler, _scheduler_running, _config
    if not _enabled():
        _scheduler_running = False
        return {"scheduler_running": False, "reason": "FAVORITES_SCHEDULER_ENABLED=0"}

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as ex:
        _log.error("[小红书收藏夹-调度|favorites_scheduler.start|APScheduler|硬编执行|失败] %s", ex)
        return {"scheduler_running": False, "error": str(ex)}

    if _scheduler is not None:
        return get_scheduler_status()

    cron = _cron()
    parts = cron.split()
    if len(parts) != 5:
        parts = "0 0 * * *".split()

    def _job():
        try:
            ensure_default_favorites_subscription()
            from .favorites_sync_runner import run_favorites_sync_all

            asyncio.run(run_favorites_sync_all(trigger="scheduled"))
        except Exception as ex:
            _log.exception(
                "[小红书收藏夹-调度|favorites_scheduler._job|sync-all|Agent执行|失败] error=%s",
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
        id="xhs_favorites_daily",
        replace_existing=True,
    )
    _scheduler.start()
    _scheduler_running = True
    _config = {"cron": cron, "timezone": "Asia/Shanghai", "red_id": _red_id()}
    _log.info(
        "[小红书收藏夹-调度|favorites_scheduler.start|scheduler|硬编执行|启动] ok=true; cron=%s",
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
        "red_id": _red_id(),
    }
