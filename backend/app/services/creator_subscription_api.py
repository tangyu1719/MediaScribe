"""社媒订阅 API 路由逻辑（薄层）。"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from .creator_feed_adapter import get_feed_adapter, parse_xiaohongshu_profile_url, resolve_xhs_red_id
from .creator_subscription_store import (
    SubscriptionDbError,
    create_subscription,
    db_health,
    delete_subscription,
    get_digest,
    get_latest_digest,
    get_subscription,
    list_subscriptions,
    list_sync_runs,
    update_subscription,
)
from .creator_sync_runner import run_sync, run_sync_all

_log = logging.getLogger("sba.creator_subscription_api")


def _ensure_db() -> None:
    from .creator_subscription_store import init_db

    init_db()


def health() -> Dict[str, Any]:
    try:
        _ensure_db()
        return db_health()
    except SubscriptionDbError as ex:
        return {"ok": False, "error": str(ex)}


def api_create_subscription(body: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_db()
    platform = (body.get("platform") or "xiaohongshu").strip()
    profile_url = (body.get("profile_url") or "").strip()
    red_id = (body.get("red_id") or "").strip()
    if platform != "xiaohongshu":
        raise ValueError("SUB_UNSUPPORTED_PLATFORM")

    resolved = None
    creator_id = ""
    display_name = ""

    if red_id:
        resolved = resolve_xhs_red_id(red_id, display_name=(body.get("display_name") or "").strip())
        profile_url = resolved["profile_url"]
        creator_id = resolved["creator_id"]
        display_name = (body.get("display_name") or resolved.get("display_name") or creator_id).strip()
    elif profile_url:
        if re.fullmatch(r"\d{6,20}", profile_url):
            resolved = resolve_xhs_red_id(profile_url)
            profile_url = resolved["profile_url"]
            creator_id = resolved["creator_id"]
            display_name = (body.get("display_name") or resolved.get("display_name") or creator_id).strip()
        else:
            try:
                parse_xiaohongshu_profile_url(profile_url)
            except ValueError as ex:
                raise ValueError("SUB_INVALID_URL") from ex
            adapter = get_feed_adapter(platform)
            try:
                meta = adapter.fetch_profile_meta(profile_url)
                creator_id = meta["creator_id"]
                display_name = (body.get("display_name") or meta.get("display_name") or creator_id).strip()
            except RuntimeError as ex:
                if "404" in str(ex) or "UNREACHABLE" in str(ex):
                    # 可能误填小红书号到 profile 路径
                    rid = parse_xiaohongshu_profile_url(profile_url)
                    resolved = resolve_xhs_red_id(rid)
                    profile_url = resolved["profile_url"]
                    creator_id = resolved["creator_id"]
                    display_name = (
                        body.get("display_name") or resolved.get("display_name") or creator_id
                    ).strip()
                else:
                    raise
    else:
        raise ValueError("SUB_INVALID_URL")

    tags = list(body.get("tags") or [])
    if resolved and resolved.get("red_id") and f"red_id:{resolved['red_id']}" not in tags:
        tags.append(f"red_id:{resolved['red_id']}")

    row = create_subscription(
        platform=platform,
        creator_id=creator_id,
        profile_url=profile_url,
        display_name=display_name,
        cron_override=body.get("cron_override"),
        read_comments=bool(body.get("read_comments", False)),
        auto_analyze=bool(body.get("auto_analyze", True)),
        tags=tags,
        owner_user_id=body.get("owner_user_id"),
    )
    return row


def api_list_subscriptions(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    _ensure_db()
    return list_subscriptions(platform=platform, status=status, page=page, page_size=page_size)


def api_get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    return get_subscription(subscription_id)


def api_update_subscription(subscription_id: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    _ensure_db()
    patch = {}
    for k in ("display_name", "status", "cron_override", "read_comments", "auto_analyze", "tags"):
        if k in body:
            patch[k] = body[k]
    return update_subscription(subscription_id, patch)


def api_delete_subscription(subscription_id: str) -> bool:
    _ensure_db()
    return delete_subscription(subscription_id)


async def api_trigger_sync(subscription_id: str) -> Dict[str, Any]:
    _ensure_db()
    sub = get_subscription(subscription_id)
    if sub and sub.get("platform") == "xiaohongshu_favorites":
        from .favorites_sync_runner import run_favorites_sync

        return await run_favorites_sync(subscription_id, trigger="manual")
    return await run_sync(subscription_id, trigger="manual")


async def api_trigger_sync_all() -> Dict[str, Any]:
    _ensure_db()
    return await run_sync_all(trigger="manual")


def api_list_sync_runs(
    subscription_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    _ensure_db()
    return list_sync_runs(subscription_id=subscription_id, page=page, page_size=page_size)


def api_get_digest(digest_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    return get_digest(digest_id)


def api_get_latest_digest(subscription_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    _ensure_db()
    return get_latest_digest(subscription_id)


async def api_run_creator_profile(subscription_id: str) -> Dict[str, Any]:
    _ensure_db()
    from .creator_profile_runner import run_creator_profile

    return await run_creator_profile(subscription_id, trigger="manual")


async def api_seed_subscription_catalog(subscription_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """摘录 UP 主页链接到 seen（博客信息），可选入队链接流水线。"""
    _ensure_db()
    from .creator_catalog_seed import seed_subscription_catalog

    sub = get_subscription(subscription_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}
    return await seed_subscription_catalog(
        subscription_id=subscription_id,
        limit=int(body.get("limit") or 20),
        enqueue=bool(body.get("enqueue", False)),
        dry_run=bool(body.get("dry_run", False)),
        trigger=str(body.get("trigger") or "manual_catalog"),
    )


def api_list_subscription_blog_notes(
    subscription_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    analysis_status: Optional[str] = None,
) -> Dict[str, Any]:
    """订阅下已摘录的博客链接列表。"""
    _ensure_db()
    from .creator_subscription_store import count_seen_notes_by_subscription, list_seen_notes_by_subscription

    sub = get_subscription(subscription_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}
    items = list_seen_notes_by_subscription(
        subscription_id,
        page=page,
        page_size=page_size,
        analysis_status=analysis_status,
    )
    return {
        "ok": True,
        "subscription_id": subscription_id,
        "display_name": sub.get("display_name") or "",
        "items": items,
        "total": count_seen_notes_by_subscription(subscription_id),
        "page": page,
        "page_size": page_size,
    }


def api_get_latest_creator_profile(subscription_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    from .creator_profile_store import get_latest_profile_doc, get_latest_profile_run

    doc = get_latest_profile_doc(subscription_id)
    run = get_latest_profile_run(subscription_id)
    if not doc and not run:
        return None
    return {"profile_doc": doc, "latest_run": run}


def api_get_creator_profile_run(profile_run_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    from .creator_profile_store import get_profile_run

    return get_profile_run(profile_run_id)
