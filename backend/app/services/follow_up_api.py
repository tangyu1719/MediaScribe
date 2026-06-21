"""关注 UP 列表 API — 拉取、筛选、可选订阅/画像。"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from .creator_profile_store import get_latest_profile_doc
from .creator_subscription_api import api_create_subscription, api_run_creator_profile
from .creator_subscription_store import (
    get_subscription_by_platform_creator,
    init_db,
    list_subscriptions,
    list_sync_run_items,
    list_sync_runs,
    update_follow_pull_cursor,
)
from .favorites_scheduler import ensure_default_favorites_subscription
from .follow_up_favorites_pull import (
    extract_new_authors_batch_from_notes,
    scroll_rounds_for_note_offset,
)
from .follow_up_search import expand_search_terms, filter_follow_ups
from .follow_up_store import (
    delete_follow_up,
    get_follow_up_by_creator,
    list_all_follow_ups,
    list_follow_up_creator_ids,
    upsert_follow_ups_from_authors,
)
from .xhs_favorites_adapter import fetch_favorites_catalog
from .xhs_local_browser import should_prefer_cookie_favorites_fetch
from .xhs_owner_chrome import get_owner_session_status

_log = logging.getLogger("sba.follow_up_api")
_CHAIN = "小红书关注UP-列表-拉取筛选"

_PULL_BATCH_DEFAULT = 20
_PULL_BATCH_MAX = 20


def _ensure_db() -> None:
    init_db()


def _enrich_follow_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """附加订阅/画像状态，供前端决策。"""
    subs = {
        (s.get("creator_id") or "").strip(): s
        for s in (list_subscriptions(platform="xiaohongshu", page=1, page_size=500).get("items") or [])
    }
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        cid = (row.get("creator_id") or "").strip()
        sub = subs.get(cid)
        row["already_subscribed"] = bool(sub)
        row["subscription_id"] = (sub or {}).get("subscription_id") or ""
        row["has_profile"] = False
        sid = row.get("subscription_id") or ""
        if sid:
            try:
                doc = get_latest_profile_doc(sid)
                row["has_profile"] = bool(doc and (doc.get("profile_md") or doc.get("persona_summary")))
            except Exception:
                row["has_profile"] = False
        out.append(row)
    return out


def api_list_follow_ups(
    *,
    query: str = "",
    subscribed: str = "all",
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """关注列表查询（近义词扩展 + 是否已订阅筛选）。"""
    _ensure_db()
    all_rows = _enrich_follow_rows(list_all_follow_ups(limit=500))
    filtered = filter_follow_ups(all_rows, query=query, subscribed=subscribed)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 500))
    start = (page - 1) * page_size
    items = filtered[start : start + page_size]
    terms = expand_search_terms(query)
    return {
        "ok": True,
        "items": items,
        "total": len(filtered),
        "page": page,
        "page_size": page_size,
        "query": (query or "").strip(),
        "expanded_terms": terms[:20],
        "subscribed_filter": subscribed,
    }


def _get_favorites_subscription_light() -> Dict[str, Any]:
    """读库获取收藏订阅，避免 ensure 路径触发 CDP Cookie 刷新。"""
    data = list_subscriptions(platform="xiaohongshu_favorites", status="active", page=1, page_size=5)
    items = data.get("items") or []
    if items:
        return items[0]
    return ensure_default_favorites_subscription()


def _ordered_notes_from_sync_db(subscription_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
    """从收藏 sync 历史按 run 顺序去重聚合笔记（无 Chrome 时的兜底顺序源）。"""
    runs = list_sync_runs(subscription_id=subscription_id, page=1, page_size=12)
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for run in runs.get("items") or []:
        sid = (run.get("sync_run_id") or "").strip()
        if not sid:
            continue
        for it in list_sync_run_items(sid):
            nid = str(it.get("note_id") or "").strip()
            if not nid or nid in seen:
                continue
            if not re.fullmatch(r"[a-f0-9]{24}", nid, re.I):
                continue
            seen.add(nid)
            out.append(it)
            if len(out) >= limit:
                return out
    return out


def _fetch_favorite_notes_ordered(
    sub: Dict[str, Any],
    *,
    note_offset: int,
    prefer_cookies: bool = True,
) -> tuple[List[Any], str]:
    """
    拉取有序收藏笔记列表（新→旧）。
    优先 Chrome 收藏 Tab；失败时读 sync 历史。
    """
    cid = (sub.get("creator_id") or "").strip()
    profile_url = (sub.get("profile_url") or "").strip()
    sid = (sub.get("subscription_id") or "").strip()
    scroll_rounds = scroll_rounds_for_note_offset(note_offset)
    fetch_limit = min(80, max(40, note_offset + _PULL_BATCH_MAX * 4))

    try:
        items, _ = fetch_favorites_catalog(
            cid,
            profile_url=profile_url,
            limit=fetch_limit,
            scroll_rounds=scroll_rounds,
            prefer_cookies=prefer_cookies,
        )
        if items:
            _log.info(
                "[%s|follow_up_api._fetch_favorite_notes_ordered|%s|Agent执行|Chrome收藏] "
                "count=%s; scroll=%s; offset=%s",
                _CHAIN,
                cid,
                len(items),
                scroll_rounds,
                note_offset,
            )
            return items, "favorites_catalog"
    except Exception as ex:
        _log.warning(
            "[%s|follow_up_api._fetch_favorite_notes_ordered|%s|Agent执行|Chrome失败] error=%s",
            _CHAIN,
            cid,
            ex,
        )

    if sid:
        db_notes = _ordered_notes_from_sync_db(sid, limit=500)
        if db_notes:
            _log.info(
                "[%s|follow_up_api._fetch_favorite_notes_ordered|%s|硬编执行|DB兜底] count=%s",
                _CHAIN,
                cid,
                len(db_notes),
            )
            return db_notes, "sync_db"
    return [], "none"


def api_pull_follow_ups(
    *,
    limit: int = _PULL_BATCH_DEFAULT,
    merge: bool = True,
    fast: bool = False,
    reset: bool = False,
) -> Dict[str, Any]:
    """
    从收藏夹笔记按 cursor 分批拉取**未入库**博主（每批最多 20 个不重复新作者）。

    - 从 follow_pull_note_offset 起扫描收藏笔记链接（新→旧）
    - 满 batch 或扫完列表后推进 cursor
    - reset=1 时 cursor 归零
    """
    _ensure_db()
    batch_size = max(1, min(int(limit or _PULL_BATCH_DEFAULT), _PULL_BATCH_MAX))
    sub = _get_favorites_subscription_light()
    sid = (sub.get("subscription_id") or "").strip()
    cid = (sub.get("creator_id") or "").strip()
    profile_url = (sub.get("profile_url") or "").strip()

    if reset and sid:
        update_follow_pull_cursor(sid, note_offset=0, reset=True)
        sub = _get_favorites_subscription_light()

    note_offset = int(sub.get("follow_pull_note_offset") or 0)
    pull_done = bool(sub.get("follow_pull_done"))
    session: Dict[str, Any] = {}

    if pull_done and not reset:
        enriched = _enrich_follow_rows(list_all_follow_ups(limit=500))
        return {
            "ok": True,
            "pull": {
                "notes_scanned": 0,
                "authors_pulled": 0,
                "created": 0,
                "updated": 0,
                "source": "already_done",
                "note_offset": note_offset,
                "next_note_offset": note_offset,
                "catalog_total": 0,
                "batch_size": batch_size,
                "pull_done": True,
            },
            "authors": [],
            "items": enriched,
            "total": len(enriched),
            "owner_session": session,
            "favorites_subscription_id": sid,
            "fast_mode": fast,
        }

    known_ids = list_follow_up_creator_ids()
    prefer_ck = bool(should_prefer_cookie_favorites_fetch())
    notes: List[Any] = []
    pull_source = "none"

    if fast:
        notes, pull_source = [], "none"
        if sid:
            db_notes = _ordered_notes_from_sync_db(sid, limit=500)
            if db_notes and note_offset < len(db_notes):
                notes = db_notes
                pull_source = "sync_db_fast"
    if not notes:
        session = get_owner_session_status()
        notes, pull_source = _fetch_favorite_notes_ordered(
            sub,
            note_offset=note_offset,
            prefer_cookies=prefer_ck,
        )

    if not notes:
        enriched = _enrich_follow_rows(list_all_follow_ups(limit=500))
        return {
            "ok": False,
            "error_code": "FOLLOW_UP_NO_FAVORITES",
            "error": "无法获取收藏笔记列表。请确认 CDP Chrome 已开收藏 Tab 且已登录，或先完成收藏同步。",
            "pull": {
                "notes_scanned": 0,
                "authors_pulled": 0,
                "created": 0,
                "updated": 0,
                "source": pull_source,
                "note_offset": note_offset,
                "next_note_offset": note_offset,
                "catalog_total": 0,
                "batch_size": batch_size,
                "pull_done": pull_done,
            },
            "items": enriched,
            "total": len(enriched),
            "owner_session": session,
            "favorites_subscription_id": sid,
        }

    if note_offset >= len(notes):
        catalog_capped = pull_source == "favorites_catalog" and len(notes) >= 80
        if catalog_capped:
            enriched = _enrich_follow_rows(list_all_follow_ups(limit=500))
            return {
                "ok": False,
                "error_code": "FOLLOW_UP_CATALOG_CAP",
                "error": (
                    f"收藏页本轮仅加载 {len(notes)} 条，cursor 已到 #{note_offset}。"
                    "请先做一次收藏同步（写入 DB）或增大 scroll 后再拉；也可点「从头拉取」重置。"
                ),
                "pull": {
                    "notes_scanned": 0,
                    "authors_pulled": 0,
                    "created": 0,
                    "updated": 0,
                    "source": pull_source,
                    "note_offset": note_offset,
                    "next_note_offset": note_offset,
                    "catalog_total": len(notes),
                    "batch_size": batch_size,
                    "pull_done": False,
                    "catalog_capped": True,
                },
                "items": enriched,
                "total": len(enriched),
                "owner_session": session,
                "favorites_subscription_id": sid,
                "fast_mode": fast,
            }
        if sid:
            update_follow_pull_cursor(sid, note_offset=len(notes), pull_done=True)
        enriched = _enrich_follow_rows(list_all_follow_ups(limit=500))
        return {
            "ok": True,
            "pull": {
                "notes_scanned": 0,
                "authors_pulled": 0,
                "created": 0,
                "updated": 0,
                "source": pull_source,
                "note_offset": note_offset,
                "next_note_offset": len(notes),
                "catalog_total": len(notes),
                "batch_size": batch_size,
                "pull_done": True,
            },
            "authors": [],
            "items": enriched,
            "total": len(enriched),
            "owner_session": session,
            "favorites_subscription_id": sid,
            "fast_mode": fast,
        }

    authors, next_offset, notes_scanned, exhausted = extract_new_authors_batch_from_notes(
        notes,
        start_offset=note_offset,
        batch_size=batch_size,
        owner_creator_id=cid,
        known_creator_ids=known_ids,
    )

    new_pull_done = exhausted and len(authors) < batch_size
    if sid:
        update_follow_pull_cursor(
            sid,
            note_offset=next_offset,
            pull_done=new_pull_done,
        )

    created = updated = 0
    if merge and authors:
        created, updated, _ = upsert_follow_ups_from_authors(authors, source="favorite_note")

    enriched = _enrich_follow_rows(list_all_follow_ups(limit=500))
    _log.info(
        "[%s|follow_up_api.api_pull_follow_ups|%s|硬编执行|批次完成] source=%s; offset=%s→%s; "
        "scanned=%s; new_authors=%s; created=%s; exhausted=%s",
        _CHAIN,
        sid,
        pull_source,
        note_offset,
        next_offset,
        notes_scanned,
        len(authors),
        created,
        exhausted,
    )
    return {
        "ok": True,
        "pull": {
            "notes_scanned": notes_scanned,
            "authors_pulled": len(authors),
            "created": created,
            "updated": updated,
            "source": pull_source,
            "note_offset": note_offset,
            "next_note_offset": next_offset,
            "catalog_total": len(notes),
            "batch_size": batch_size,
            "pull_done": new_pull_done,
        },
        "authors": authors,
        "items": enriched,
        "total": len(enriched),
        "owner_session": session,
        "favorites_subscription_id": sid,
        "fast_mode": fast,
    }


async def api_subscribe_follow_up(
    creator_id: str,
    *,
    sync_after: bool = False,
) -> Dict[str, Any]:
    """将关注列表中的 UP 加入订阅（用户显式操作）。"""
    _ensure_db()
    cid = (creator_id or "").strip()
    row = get_follow_up_by_creator(cid)
    if not row:
        raise ValueError("FOLLOW_UP_NOT_FOUND")
    existing = get_subscription_by_platform_creator("xiaohongshu", cid)
    if existing:
        return {
            "ok": True,
            "already_subscribed": True,
            "subscription": existing,
            "subscription_id": existing.get("subscription_id"),
        }
    sub_row = api_create_subscription(
        {
            "platform": "xiaohongshu",
            "profile_url": row.get("profile_url")
            or f"https://www.xiaohongshu.com/user/profile/{cid}",
            "display_name": (row.get("display_name") or cid).strip(),
            "tags": ["source:follow_up_list"],
        }
    )
    synced = False
    if sync_after:
        from .creator_sync_runner import run_sync

        sid = (sub_row.get("subscription_id") or "").strip()
        if sid:
            await run_sync(sid, trigger="manual")
            synced = True
    return {
        "ok": True,
        "already_subscribed": False,
        "subscription": sub_row,
        "subscription_id": sub_row.get("subscription_id"),
        "synced": synced,
    }


async def api_profile_follow_up(creator_id: str) -> Dict[str, Any]:
    """对关注列表中的 UP 生成画像（若无订阅则先创建订阅，不自动同步作品）。"""
    _ensure_db()
    sub_result = await api_subscribe_follow_up(creator_id, sync_after=False)
    sid = (sub_result.get("subscription_id") or "").strip()
    if not sid:
        raise RuntimeError("FOLLOW_UP_SUBSCRIBE_FAILED")
    profile = await api_run_creator_profile(sid)
    return {
        "ok": bool(profile.get("ok")),
        "subscription_id": sid,
        "profile": profile,
    }


def api_remove_follow_up(creator_id: str) -> Dict[str, Any]:
    """从关注列表移除（不影响已存在订阅）。"""
    _ensure_db()
    ok = delete_follow_up(creator_id)
    return {"ok": ok, "creator_id": creator_id}
