"""收藏夹订阅 — 启动时与每日 0:00 调度。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional

_log = logging.getLogger("sba.favorites_scheduler")
_scheduler = None
_scheduler_running = False
_config: Dict[str, Any] = {}
_startup_scheduled = False
# 主 uvicorn 事件循环（禁止在子线程 asyncio.run，会与 LangGraph 导入死锁）
_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


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


def register_main_event_loop(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """在 FastAPI startup 注册主事件循环，供 APScheduler 线程安全投递协程。"""
    global _MAIN_LOOP
    try:
        _MAIN_LOOP = loop or asyncio.get_running_loop()
    except RuntimeError:
        _MAIN_LOOP = None


def _submit_to_main_loop(coro) -> None:
    """从 APScheduler 后台线程把协程投递到主 loop（禁止 asyncio.run）。"""
    loop = _MAIN_LOOP
    if loop is None or not loop.is_running():
        _log.warning(
            "[小红书收藏夹-调度|favorites_scheduler._submit_to_main_loop|event_loop|硬编执行|跳过] "
            "主事件循环未就绪"
        )
        return

    fut = asyncio.run_coroutine_threadsafe(coro, loop)

    def _done(f) -> None:
        try:
            f.result()
        except Exception as ex:
            _log.exception(
                "[小红书收藏夹-调度|favorites_scheduler._submit_to_main_loop|sync|Agent执行|失败] "
                "error=%s",
                ex,
            )

    fut.add_done_callback(_done)


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

    creator_override = (
        os.environ.get("XHS_FAVORITES_CREATOR_ID") or "60dc2e340000000001008a1f"
    ).strip()
    if creator_override and re.fullmatch(r"[a-f0-9]{24}", creator_override, re.I):
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

    from .xhs_owner_chrome import resolve_owner_creator_id_from_cdp

    try:
        resolved = resolve_owner_creator_id_from_cdp()
    except Exception as ex:
        _log.warning(
            "[小红书收藏夹-调度|favorites_scheduler.ensure|CDP|硬编执行|回退] %s",
            ex,
        )
        from .xhs_favorites_adapter import resolve_favorites_owner

        resolved = resolve_favorites_owner(red_id, display_name=_display_name())
    tags = ["kind:favorites", f"red_id:{red_id}"]
    cid = resolved["creator_id"]

    from .creator_subscription_store import list_subscriptions, delete_subscription

    for row in (list_subscriptions(platform="xiaohongshu_favorites", page=1, page_size=10).get("items") or []):
        old_cid = (row.get("creator_id") or "").strip()
        if not old_cid:
            continue
        stale = old_cid != cid or not re.fullmatch(r"[a-f0-9]{24}", old_cid, re.I)
        if stale:
            delete_subscription(row["subscription_id"])
            _log.warning(
                "[小红书收藏夹-调度|favorites_scheduler.ensure|subscription|硬编执行|修复] 删除过期 creator_id=%s",
                old_cid,
            )

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
        sub = await asyncio.to_thread(ensure_default_favorites_subscription)
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


async def _run_scheduled_sync_all() -> None:
    await asyncio.to_thread(ensure_default_favorites_subscription)
    from .favorites_sync_runner import run_favorites_sync_all

    await run_favorites_sync_all(trigger="scheduled")


def schedule_favorites_on_startup() -> None:
    """FastAPI startup：延迟后在主事件循环后台同步（禁止 threading + asyncio.run）。"""
    global _startup_scheduled
    if not _enabled():
        return
    if _startup_scheduled:
        return
    _startup_scheduled = True
    delay = _startup_delay_sec()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log.warning(
            "[小红书收藏夹-调度|favorites_scheduler.schedule_favorites_on_startup|startup|硬编执行|跳过] "
            "无运行中事件循环"
        )
        return

    register_main_event_loop(loop)

    async def _delayed_startup() -> None:
        await asyncio.sleep(delay)
        await _run_startup_sync()

    loop.create_task(_delayed_startup())
    _log.info(
        "[小红书收藏夹-调度|favorites_scheduler.schedule_favorites_on_startup|scheduler|硬编执行|启动] "
        "delay_sec=%s; mode=main_loop_task",
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
        _submit_to_main_loop(_run_scheduled_sync_all())

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
