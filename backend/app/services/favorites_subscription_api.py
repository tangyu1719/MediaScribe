"""收藏夹订阅 API。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .creator_subscription_store import (
    SubscriptionDbError,
    get_latest_digest,
    get_subscription,
    init_db,
)
from .favorites_habit import get_habit
from .favorites_scheduler import ensure_default_favorites_subscription, get_scheduler_status
from .xhs_owner_chrome import get_owner_session_status
from .favorites_sync_runner import run_favorites_sync

_log = logging.getLogger("sba.favorites_subscription_api")


def _ensure_db() -> None:
    init_db()


def api_ensure_favorites_subscription() -> Dict[str, Any]:
    _ensure_db()
    session = get_owner_session_status()
    sub = ensure_default_favorites_subscription()
    habit = get_habit(sub["subscription_id"])
    digest = get_latest_digest(sub["subscription_id"])
    return {
        "subscription": sub,
        "habit": habit,
        "latest_digest": digest,
        "scheduler": get_scheduler_status(),
        "owner_session": session,
    }


async def api_trigger_favorites_sync(
    *,
    subscription_id: Optional[str] = None,
    force_analyze_latest: int = 0,
) -> Dict[str, Any]:
    _ensure_db()
    if subscription_id:
        sub = get_subscription(subscription_id)
        if not sub:
            return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}
        sid = subscription_id
    else:
        sub = ensure_default_favorites_subscription()
        sid = sub["subscription_id"]
    return await run_favorites_sync(
        sid,
        trigger="manual",
        force_analyze_latest=force_analyze_latest,
    )


def api_get_favorites_habit(subscription_id: Optional[str] = None) -> Dict[str, Any]:
    _ensure_db()
    if not subscription_id:
        sub = ensure_default_favorites_subscription()
        subscription_id = sub["subscription_id"]
    return get_habit(subscription_id)


def api_get_favorites_digest(subscription_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    _ensure_db()
    if not subscription_id:
        sub = ensure_default_favorites_subscription()
        subscription_id = sub["subscription_id"]
    return get_latest_digest(subscription_id)


def health() -> Dict[str, Any]:
    try:
        _ensure_db()
        from .creator_subscription_store import db_health

        return db_health()
    except SubscriptionDbError as ex:
        return {"ok": False, "error": str(ex)}
