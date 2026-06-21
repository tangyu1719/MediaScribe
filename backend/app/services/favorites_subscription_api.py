"""收藏夹订阅 API。"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from .creator_subscription_store import (
    SubscriptionDbError,
    get_latest_digest,
    get_subscription,
    init_db,
    list_subscriptions,
)
from .favorites_habit import get_habit
from .favorites_scheduler import ensure_default_favorites_subscription, get_scheduler_status
from .cookie_manager import diagnose_xhs_cookies
from .xhs_owner_chrome import get_owner_session_status
from .favorites_sync_runner import run_favorites_sync

_log = logging.getLogger("sba.favorites_subscription_api")


def _ensure_db() -> None:
    init_db()


def _pick_favorites_subscription(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """多条收藏夹订阅时优先选 env 指定 / 非可疑 creator_id。"""
    if not items:
        return None
    prefer = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "").strip()
    if prefer:
        for row in items:
            if (row.get("creator_id") or "").strip() == prefer:
                return row
    _FAKE_CREATOR = "60dc2e340000000000000000"
    for row in items:
        cid = (row.get("creator_id") or "").strip()
        if cid and cid != _FAKE_CREATOR and re.fullmatch(r"[a-f0-9]{24}", cid, re.I):
            return row
    return items[0]


def api_ensure_favorites_subscription() -> Dict[str, Any]:
    _ensure_db()
    session = get_owner_session_status()
    sub: Optional[Dict[str, Any]] = None
    sub_err = ""
    try:
        sub = ensure_default_favorites_subscription()
    except Exception as ex:
        sub_err = str(ex)
        _log.warning(
            "[收藏夹订阅-API|favorites_subscription_api.api_ensure|subscription|硬编执行|降级] error=%s",
            sub_err,
        )
        from .creator_subscription_store import list_subscriptions

        data = list_subscriptions(platform="xiaohongshu_favorites", status="active", page=1, page_size=10)
        items = data.get("items") or []
        sub = _pick_favorites_subscription(items)

    habit = get_habit(sub["subscription_id"]) if sub else {"habit_json": {}, "persona_md": ""}
    digest = get_latest_digest(sub["subscription_id"]) if sub else None
    out: Dict[str, Any] = {
        "subscription": sub,
        "habit": habit,
        "latest_digest": digest,
        "scheduler": get_scheduler_status(),
        "owner_session": session,
        "cookie_diagnosis": diagnose_xhs_cookies(),
    }
    if sub_err:
        out["subscription_error"] = sub_err
    return out


async def api_trigger_favorites_sync(
    *,
    subscription_id: Optional[str] = None,
    force_analyze_latest: int = 0,
    sync_batch_size: int = 0,
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
        sync_batch_size=sync_batch_size,
    )


def api_refresh_favorites_cookies() -> Dict[str, Any]:
    """从运行中 Chrome（CDP）同步小红书 Cookie，不杀 Chrome。"""
    from .xhs_local_browser import refresh_xhs_cookies_cdp_only

    result = refresh_xhs_cookies_cdp_only()
    result["owner_session"] = get_owner_session_status()
    result["cookie_diagnosis"] = diagnose_xhs_cookies()
    return result


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


def _enrich_sync_items_with_tasks(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from .task_manager import get_task

    out: List[Dict[str, Any]] = []
    for row in items:
        d = dict(row)
        tid = (d.get("analysis_task_id") or "").strip()
        if tid:
            t = get_task(tid) or {}
            d["task_status"] = t.get("status") or ""
            d["task_progress"] = t.get("progress") or 0
            d["doc_path"] = t.get("doc_path") or ""
            d["doc_title"] = t.get("doc_title") or t.get("link_title") or ""
            d["html_path"] = t.get("html_path") or ""
        out.append(d)
    return out


def _attach_catalog_seq(items: List[Dict[str, Any]], catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为 sync 条目补上收藏夹序号（与 catalog 卡片 #1 #2 对齐）。"""
    seq_map = {(c.get("note_id") or "").strip(): int(c.get("seq") or 0) for c in catalog}
    meta_map = { (c.get("note_id") or "").strip(): c for c in catalog }
    out: List[Dict[str, Any]] = []
    for it in items:
        d = dict(it)
        nid = (d.get("note_id") or "").strip()
        if nid in seq_map and seq_map[nid]:
            d["seq"] = seq_map[nid]
        meta = meta_map.get(nid) or {}
        d.setdefault("title", meta.get("title") or "")
        d.setdefault("author_name", meta.get("author_name") or "")
        d.setdefault("content_type", meta.get("content_type") or "")
        d.setdefault("canonical_url", meta.get("canonical_url") or "")
        out.append(d)
    return out


def api_get_favorites_catalog(*, limit: int = 20) -> Dict[str, Any]:
    """拉取收藏夹目录（按小红书收藏顺序）供前端卡片展示。"""
    _ensure_db()
    from .creator_subscription_store import get_subscription, update_sync_run
    from .xhs_favorites_adapter import fetch_favorites_catalog
    from .xhs_local_browser import should_prefer_cookie_favorites_fetch

    sub = ensure_default_favorites_subscription()
    limit = min(max(1, limit), 80)
    items, ok = fetch_favorites_catalog(
        sub.get("creator_id") or "",
        profile_url=sub.get("profile_url") or "",
        limit=limit,
        prefer_cookies=should_prefer_cookie_favorites_fetch(),
    )
    cards = []
    for i, it in enumerate(items, start=1):
        d = it.to_dict()
        d["seq"] = i
        cards.append(d)
    return {
        "ok": bool(ok),
        "subscription_id": sub.get("subscription_id"),
        "items": cards,
        "total": len(cards),
        "cache": "persisted",
    }


def api_get_favorites_latest_sync(subscription_id: Optional[str] = None) -> Dict[str, Any]:
    """最近一次收藏 sync 运行结果 + 条目任务状态 + digest 分析。"""
    from .creator_subscription_store import (
        get_digest_by_sync_run_id,
        list_sync_run_items,
        list_sync_runs,
    )

    _ensure_db()
    if not subscription_id:
        sub = ensure_default_favorites_subscription()
        subscription_id = sub["subscription_id"]
    runs = list_sync_runs(subscription_id=subscription_id, page=1, page_size=1)
    run = (runs.get("items") or [None])[0]
    if not run:
        return {"ok": True, "run": None, "items": [], "digest": None, "summary": {}}
    sync_run_id = run.get("sync_run_id") or ""
    items = _enrich_sync_items_with_tasks(list_sync_run_items(sync_run_id))
    try:
        cat = api_get_favorites_catalog(limit=40)
        items = _attach_catalog_seq(items, cat.get("items") or [])
    except Exception:
        pass
    digest = get_digest_by_sync_run_id(sync_run_id)
    dj = (digest or {}).get("digest_json") or {}
    summary = {
        "one_liner": dj.get("summary_one_liner") or "",
        "topic_buckets": dj.get("topic_buckets") or [],
        "analyzed_count": run.get("analyzed_count"),
        "failed_count": run.get("failed_count"),
        "new_count": run.get("new_count"),
    }
    return {
        "ok": True,
        "run": run,
        "items": items,
        "digest": digest,
        "summary": summary,
    }


def api_pull_favorite_up_authors(*, limit: int = 40) -> Dict[str, Any]:
    """兼容旧路由：拉取并写入关注列表（不自动订阅）。"""
    from .follow_up_api import api_pull_follow_ups

    return api_pull_follow_ups(limit=limit, merge=True)


async def api_import_favorite_ups(body: Dict[str, Any]) -> Dict[str, Any]:
    """将拉取到的收藏 UP 批量加入 UP 订阅。"""
    _ensure_db()
    from .creator_subscription_api import api_create_subscription
    from .creator_sync_runner import run_sync

    authors = body.get("authors") or []
    if not isinstance(authors, list) or not authors:
        raise ValueError("FAV_UP_EMPTY")
    sync_after = bool(body.get("sync_after", True))
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    existing = {
        (row.get("creator_id") or "").strip()
        for row in (list_subscriptions(platform="xiaohongshu", page=1, page_size=300).get("items") or [])
    }
    for raw in authors:
        if not isinstance(raw, dict):
            continue
        cid = (raw.get("creator_id") or "").strip()
        if not cid:
            continue
        if cid in existing:
            skipped.append({"creator_id": cid, "reason": "already_subscribed"})
            continue
        try:
            row = api_create_subscription(
                {
                    "platform": "xiaohongshu",
                    "profile_url": raw.get("profile_url")
                    or f"https://www.xiaohongshu.com/user/profile/{cid}",
                    "display_name": (raw.get("display_name") or cid).strip(),
                    "tags": ["source:favorite_up"],
                }
            )
            existing.add(cid)
            created.append(row)
        except Exception as ex:
            errors.append({"creator_id": cid, "error": str(ex)[:200]})

    synced: List[str] = []
    if sync_after:
        for row in created:
            sid = (row.get("subscription_id") or "").strip()
            if not sid:
                continue
            try:
                await run_sync(sid, trigger="manual")
                synced.append(sid)
            except Exception as ex:
                errors.append({"subscription_id": sid, "error": f"sync_failed:{ex}"[:200]})

    return {
        "ok": True,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "synced_subscription_ids": synced,
    }


def health() -> Dict[str, Any]:
    try:
        _ensure_db()
        from .creator_subscription_store import db_health

        return db_health()
    except SubscriptionDbError as ex:
        return {"ok": False, "error": str(ex)}
