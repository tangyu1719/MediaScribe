"""收藏夹 sync 编排 — 拉取、基线、增量分析、习惯画像、digest。"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .creator_subscription_store import (
    add_sync_run_item,
    create_sync_run,
    get_subscription,
    get_subscription_sync_anchor,
    insert_seen_note,
    list_sync_run_items,
    save_digest,
    update_subscription_cursor,
    update_sync_run,
    update_sync_run_item,
)
from .subscription_import_guard import check_already_imported, record_skipped_import
from .subscription_link_order import select_items_for_subscription_sync
from .subscription_link_card_store import sync_link_card_from_task, upsert_link_card
from .favorites_digest import generate_favorites_digest
from .favorites_habit import get_habit, update_habit_from_batch
from .pipeline_scheduler import request_video_pipeline_async
from .task_manager import create_task, get_task
from .task_source_meta import SOURCE_SUB_FAVORITES, source_meta_kwargs
from .video_pipeline import process_video_pipeline
from .xhs_favorites_adapter import FavoritesFeedItem, fetch_favorites_catalog

_log = logging.getLogger("sba.favorites_sync_runner")
_CHAIN = "小红书收藏夹-增量拉取-单条分析-批次digest"
_PLATFORM = "xiaohongshu_favorites"

_SUB_LOCKS: Dict[str, asyncio.Lock] = {}
_FETCH_LIMIT = int(os.environ.get("FAVORITES_FETCH_LIMIT", "80"))
_SYNC_BATCH_LIMIT = int(os.environ.get("FAVORITES_SYNC_BATCH", "20"))
_DIGEST_WAIT_SEC = int(os.environ.get("FAVORITES_DIGEST_WAIT_SEC", "2400"))


def _lock_for(subscription_id: str) -> asyncio.Lock:
    if subscription_id not in _SUB_LOCKS:
        _SUB_LOCKS[subscription_id] = asyncio.Lock()
    return _SUB_LOCKS[subscription_id]


async def _wait_task(task_id: str, timeout_sec: int) -> Dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        t = get_task(task_id)
        if not t:
            return {"status": "missing", "task": None}
        st = t.get("status")
        if st in ("completed", "failed", "cancelled"):
            return {"status": st, "task": t}
        await asyncio.sleep(2)
    t = get_task(task_id)
    return {"status": "timeout", "task": t}


def _extract_summary_and_chars(task: Dict[str, Any], fallback_title: str) -> tuple[str, int]:
    summary = task.get("doc_title") or task.get("link_title") or fallback_title
    text_chars = 0
    doc_path = task.get("doc_path")
    if doc_path:
        try:
            p = Path(doc_path)
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="ignore")
                text_chars = len(text.strip())
                summary = text[:1500]
        except Exception:
            pass
    return summary, text_chars


def _item_to_digest_row(
    it: FavoritesFeedItem,
    *,
    analysis_task_id: Optional[str] = None,
    analysis_status: str,
    summary: str = "",
    text_chars: int = 0,
    error_message: str = "",
) -> Dict[str, Any]:
    return {
        "note_id": it.note_id,
        "title": it.title,
        "content_type": it.content_type,
        "canonical_url": it.canonical_url,
        "author_id": it.author_id,
        "author_name": it.author_name,
        "author_followers": it.author_followers,
        "text_chars": text_chars,
        "analysis_task_id": analysis_task_id,
        "analysis_status": analysis_status,
        "summary": summary or it.title,
        "error_message": error_message,
    }


async def run_favorites_sync(
    subscription_id: str,
    *,
    trigger: str = "manual",
    force_analyze_latest: int = 0,
    sync_batch_size: int = 0,
) -> Dict[str, Any]:
    sub = get_subscription(subscription_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "收藏订阅不存在"}
    if sub.get("platform") != _PLATFORM:
        return {"ok": False, "error_code": "SUB_WRONG_PLATFORM", "error": "非收藏夹订阅"}
    if sub.get("status") not in ("active", "error"):
        return {"ok": False, "error_code": "SUB_INACTIVE", "error": "订阅未启用"}

    lock = _lock_for(subscription_id)
    if lock.locked():
        return {"ok": False, "error_code": "SUB_SYNC_BUSY", "error": "收藏夹 sync 进行中"}

    async with lock:
        sync_run = create_sync_run(
            subscription_id,
            trigger=trigger,
            latest_limit=sync_batch_size or force_analyze_latest,
        )
        sync_run_id = sync_run["sync_run_id"]
        update_sync_run(sync_run_id, status="fetching")

        _log.info(
            "[%s|favorites_sync_runner.run_favorites_sync|%s|Agent执行|开始] subscription_id=%s; trigger=%s; force=%s; batch=%s",
            _CHAIN,
            sync_run_id,
            subscription_id,
            trigger,
            force_analyze_latest,
            sync_batch_size,
        )

        try:
            from .chrome_profile_prep import dismiss_chrome_restore_prompt
            from .cookie_manager import find_cdp_port
            from .xhs_local_browser import (
                favorites_playwright_fallback_enabled,
                probe_xhs_cookies_logged_in,
                _resolve_xhs_cookies_for_scrape,
            )
            from .xhs_owner_chrome import refresh_owner_xhs_cookies

            from .xhs_local_browser import xhs_cdp_attach_only
            from .xhs_owner_chrome import ensure_owner_chrome_cdp

            session: Dict[str, Any] = {"nickname": "", "cdp_port": None, "mode": "unknown"}
            _p = find_cdp_port()
            if _p:
                dismiss_chrome_restore_prompt(_p)

            pre_ck = _resolve_xhs_cookies_for_scrape()
            pre_probe = probe_xhs_cookies_logged_in(pre_ck) if pre_ck else {"logged_in": False}
            if not pre_ck or not pre_probe.get("logged_in"):
                from .cookie_manager import diagnose_xhs_cookies
                from .xhs_local_browser import refresh_xhs_cookies_cdp_only

                diag0 = diagnose_xhs_cookies()
                if diag0.get("guest") or not pre_probe.get("logged_in"):
                    if diag0.get("cdp_port") or diag0.get("chrome_running"):
                        ref = refresh_xhs_cookies_cdp_only()
                        if ref.get("ok"):
                            pre_ck = _resolve_xhs_cookies_for_scrape()
                            pre_probe = (
                                probe_xhs_cookies_logged_in(pre_ck)
                                if pre_ck
                                else {"logged_in": False}
                            )
                            _log.info(
                                "[%s|favorites_sync_runner|%s|硬编执行|CDP刷新Cookie] ok=%s; nick=%s",
                                _CHAIN,
                                sync_run_id,
                                ref.get("ok"),
                                ref.get("nickname") or "",
                            )
            if not pre_ck or not pre_probe.get("logged_in"):
                from .cookie_manager import diagnose_xhs_cookies

                diag = diagnose_xhs_cookies()
                code = "SUB_XHS_GUEST_SESSION" if diag.get("guest") else "SUB_OWNER_XHS_LOGIN_REQUIRED"
                msg = diag.get("hint") or "小红书未登录，无法同步收藏夹"
                update_sync_run(sync_run_id, status="failed", error_message=msg[:500])
                return {"ok": False, "error_code": code, "error": msg, "cookie_diagnosis": diag}
            if pre_probe.get("logged_in") and favorites_playwright_fallback_enabled():
                session = {
                    "nickname": pre_probe.get("nickname") or "",
                    "cdp_port": None,
                    "mode": "cookie_playwright",
                    "cookie_count": len(pre_ck),
                }
                _log.info(
                    "[%s|favorites_sync_runner|%s|硬编执行|Cookie会话] 已登录 Cookie=%s，跳过 CDP 前置",
                    _CHAIN,
                    sync_run_id,
                    len(pre_ck),
                )
            else:
                try:
                    session = {**ensure_owner_chrome_cdp(), "mode": "cdp"}
                except Exception as ex:
                    if not favorites_playwright_fallback_enabled():
                        raise RuntimeError(
                            f"SUB_OWNER_CDP_REQUIRED: 收藏夹同步需要 CDP 或 Playwright 兜底。"
                            f"请设置 SBA_XHS_FAVORITES_PLAYWRIGHT_FALLBACK=1。原因: {ex}"
                        ) from ex
                    _log.warning(
                        "[%s|favorites_sync_runner|%s|硬编执行|CDP会话失败] "
                        "将走 Playwright 兜底; error=%s",
                        _CHAIN,
                        sync_run_id,
                        ex,
                    )
                    ck = refresh_owner_xhs_cookies()
                    session = {
                        "nickname": ck.get("nickname") or "",
                        "cdp_port": None,
                        "mode": "playwright_fallback",
                        "cookie_source": ck.get("source") or "",
                    }
            if session.get("mode") not in ("cdp", "cookie_playwright") and not favorites_playwright_fallback_enabled():
                raise RuntimeError(
                    "SUB_OWNER_CDP_REQUIRED: CDP 未就绪且方案 A 禁止 Playwright 兜底"
                )
            _log.info(
                "[%s|favorites_sync_runner|%s|硬编执行|Chrome会话] mode=%s; nickname=%s; cdp=%s",
                _CHAIN,
                sync_run_id,
                session.get("mode"),
                session.get("nickname"),
                session.get("cdp_port"),
            )

            creator_id = sub.get("creator_id") or ""
            profile_url = sub.get("profile_url") or ""
            loop = asyncio.get_event_loop()
            prefer_ck = session.get("mode") in ("cookie_playwright", "playwright_fallback")
            items, ok = await loop.run_in_executor(
                None,
                lambda: fetch_favorites_catalog(
                    creator_id,
                    profile_url=profile_url,
                    limit=_FETCH_LIMIT,
                    prefer_cookies=prefer_ck,
                ),
            )
            if not ok:
                msg = "SUB_FAVORITES_FETCH_FAILED: 收藏夹采集失败，CDP/Playwright/Cookie 全部重试后仍不可用"
                update_sync_run(sync_run_id, status="failed", error_code="SUB_FAVORITES_FETCH_FAILED", error_message=msg)
                return {"ok": False, "error_code": "SUB_FAVORITES_FETCH_FAILED", "error": msg}
            catalog_count = len(items)

            backfill_done = bool(sub.get("initial_backfill_done"))
            red_id = ""
            for tag in sub.get("tags") or []:
                if isinstance(tag, str) and tag.startswith("red_id:"):
                    red_id = tag.split(":", 1)[1]
                    break

            skipped_imported: List[Dict[str, Any]] = []
            # sync_batch_size=0（默认）→ 全量：offset 剩余全部 / 增量全部新项；>0 才限制每批条数
            limit = max(0, int(sync_batch_size or 0))
            legacy_n = max(0, int(force_analyze_latest or 0))

            if legacy_n > 0 and limit <= 0:
                effective_limit = legacy_n
                candidate_items = items[:effective_limit]
                next_cursor = min(effective_limit, len(items))
                has_more = next_cursor < len(items)
                mark_backfill = not has_more
            elif not backfill_done:
                cursor = int(sub.get("cursor_offset") or 0)
                if limit > 0:
                    candidate_items = items[cursor : cursor + limit]
                else:
                    candidate_items = items[cursor:]
                next_cursor = cursor + len(candidate_items)
                has_more = next_cursor < len(items)
                mark_backfill = not has_more
            else:
                candidate_items = items[:_FETCH_LIMIT]
                next_cursor = 0
                has_more = False
                mark_backfill = False

            batch_label = limit if limit > 0 else len(candidate_items)

            new_items: List[FavoritesFeedItem] = []
            anchor = get_subscription_sync_anchor(subscription_id)
            sel_limit = limit if limit > 0 else max(len(candidate_items), 1)
            picked, order_skipped, stop_reason = select_items_for_subscription_sync(
                candidate_items,
                platform=_PLATFORM,
                seen_url_hashes=set(anchor.get("url_hashes") or []),
                seen_note_ids=set(anchor.get("note_ids") or []),
                anchor_published_at=anchor.get("published_at"),
                limit=sel_limit,
            )
            for sk in order_skipped:
                skipped_imported.append(
                    {
                        "note_id": sk.get("note_id"),
                        "title": "",
                        "url_hash": sk.get("url_hash"),
                        "reason": sk.get("reason") or "seen",
                    }
                )
            new_items = picked
            if stop_reason:
                _log.warning(
                    "[%s|favorites_sync_runner|%s|Agent执行|连续性阻断] reason=%s",
                    _CHAIN,
                    sync_run_id,
                    stop_reason,
                )

            _log.info(
                "[%s|favorites_sync_runner|%s|Agent执行|批次] backfill_done=%s; cursor=%s; batch=%s; "
                "candidates=%s; new=%s; skipped=%s",
                _CHAIN,
                sync_run_id,
                backfill_done,
                int(sub.get("cursor_offset") or 0),
                batch_label,
                len(candidate_items),
                len(new_items),
                len(skipped_imported),
            )

            update_sync_run(sync_run_id, status="analyzing", new_count=len(new_items))
            _log.info(
                "[%s|favorites_sync_runner|%s|Agent执行|拉取] fetched=%s; new=%s",
                _CHAIN,
                sync_run_id,
                len(items),
                len(new_items),
            )

            if not new_items:
                habit_row = get_habit(subscription_id)
                save_digest(
                    sync_run_id=sync_run_id,
                    subscription_id=subscription_id,
                    digest_md="本次 sync 未发现新增收藏。",
                    digest_json={
                        "summary_one_liner": "无新增收藏",
                        "topic_buckets": [],
                        "items": [],
                        "task_snapshot": {"new_count": 0, "analyzed_count": 0, "failed_count": 0},
                    },
                    llm_model="",
                    rag_degraded=False,
                )
                update_sync_run(sync_run_id, status="completed", new_count=0)
                update_subscription_cursor(
                    subscription_id,
                    cursor_offset=next_cursor if not backfill_done else 0,
                    last_note_id=items[0].note_id if items else sub.get("last_note_id"),
                    cursor_published_at=None,
                    mark_backfill_done=mark_backfill if not backfill_done else False,
                    reset_failures=True,
                )
                return {
                    "ok": True,
                    "sync_run_id": sync_run_id,
                    "status": "completed",
                    "new_count": 0,
                    "skipped_imported_count": len(skipped_imported),
                    "cursor_offset": next_cursor if not backfill_done else 0,
                    "batch_size": limit,
                    "full_sync": limit <= 0,
                }

            analyzed = 0
            failed = 0
            digest_items: List[Dict[str, Any]] = []
            read_comments = bool(sub.get("read_comments"))
            comments_dict = {"enabled": read_comments, "count": 10, "sort": "hot"}
            per_item_timeout = max(180, _DIGEST_WAIT_SEC // max(1, len(new_items)))

            for it in new_items:
                add_sync_run_item(
                    sync_run_id,
                    note_id=it.note_id,
                    canonical_url=it.canonical_url,
                    content_type=it.content_type,
                    title=it.title,
                    published_at=str(it.published_at or ""),
                    published_date=str(getattr(it, "published_date", "") or ""),
                    like_count=int(getattr(it, "like_count", 0) or 0),
                    comment_count=int(getattr(it, "comment_count", 0) or 0),
                    hashtags=list(getattr(it, "hashtags", []) or []),
                    cover_url=str(getattr(it, "cover_url", "") or ""),
                    author_id=str(getattr(it, "author_id", "") or ""),
                    author_name=str(getattr(it, "author_name", "") or ""),
                    author_followers=int(getattr(it, "author_followers", 0) or 0),
                    analysis_status="pending",
                )
                upsert_link_card(
                    subscription_id=subscription_id,
                    platform=_PLATFORM,
                    note_id=it.note_id,
                    canonical_url=it.canonical_url,
                    url_hash=it.url_hash,
                    title=it.title,
                    content_type=it.content_type,
                    published_at=str(it.published_at or ""),
                    analysis_status="pending",
                    author_name=str(getattr(it, "author_name", "") or ""),
                    import_source=SOURCE_SUB_FAVORITES,
                    source_label=source_meta_kwargs(
                        SOURCE_SUB_FAVORITES,
                        display_name=str(sub.get("display_name") or ""),
                        platform=_PLATFORM,
                    )["source_label"],
                )

                imported, imp_reason = check_already_imported(
                    _PLATFORM,
                    it.note_id,
                    it.url_hash,
                    canonical_url=it.canonical_url,
                )
                if imported:
                    record_skipped_import(
                        subscription_id=subscription_id,
                        platform=_PLATFORM,
                        note_id=it.note_id,
                        canonical_url=it.canonical_url,
                        url_hash=it.url_hash,
                        content_type=it.content_type,
                        title=it.title,
                        reason=imp_reason,
                    )
                    update_sync_run_item(
                        sync_run_id,
                        it.note_id,
                        analysis_status="already_imported",
                        error_message=f"已在历史库/seen 中（{imp_reason}），跳过分析",
                    )
                    digest_items.append(
                        _item_to_digest_row(
                            it,
                            analysis_status="already_imported",
                            summary=f"已导入，跳过（{imp_reason}）",
                        )
                    )
                    continue

                if not sub.get("auto_analyze", True):
                    insert_seen_note(
                        subscription_id=subscription_id,
                        platform=_PLATFORM,
                        note_id=it.note_id,
                        canonical_url=it.canonical_url,
                        url_hash=it.url_hash,
                        content_type=it.content_type,
                        title=it.title,
                        analysis_task_id=None,
                        analysis_status="skipped",
                    )
                    update_sync_run_item(sync_run_id, it.note_id, analysis_status="skipped")
                    digest_items.append(
                        _item_to_digest_row(it, analysis_status="skipped", summary=it.title)
                    )
                    continue

                src_meta = source_meta_kwargs(
                    SOURCE_SUB_FAVORITES,
                    display_name=str(sub.get("display_name") or ""),
                    platform=_PLATFORM,
                    author_name=str(getattr(it, "author_name", "") or ""),
                    author_id=str(getattr(it, "author_id", "") or ""),
                    subscription_id=subscription_id,
                )
                task_id = create_task(
                    "小红书",
                    it.canonical_url,
                    "",
                    comments_dict,
                    **src_meta,
                )
                update_sync_run_item(
                    sync_run_id, it.note_id, analysis_task_id=task_id, analysis_status="running"
                )
                upsert_link_card(
                    subscription_id=subscription_id,
                    platform=_PLATFORM,
                    note_id=it.note_id,
                    canonical_url=it.canonical_url,
                    url_hash=it.url_hash,
                    title=it.title,
                    content_type=it.content_type,
                    published_at=str(it.published_at or ""),
                    task_id=task_id,
                    analysis_status="running",
                    author_name=src_meta.get("author_name") or "",
                    import_source=SOURCE_SUB_FAVORITES,
                    source_label=src_meta.get("source_label") or "",
                )

                asyncio.create_task(request_video_pipeline_async(task_id))
                wait = await _wait_task(task_id, per_item_timeout)
                task = wait.get("task") or {}
                st = wait.get("status")
                summary, text_chars = _extract_summary_and_chars(task, it.title)

                if st == "completed":
                    analyzed += 1
                    insert_seen_note(
                        subscription_id=subscription_id,
                        platform=_PLATFORM,
                        note_id=it.note_id,
                        canonical_url=it.canonical_url,
                        url_hash=it.url_hash,
                        content_type=it.content_type,
                        title=it.title,
                        analysis_task_id=task_id,
                        analysis_status="completed",
                    )
                    update_sync_run_item(sync_run_id, it.note_id, analysis_status="completed")
                    sync_link_card_from_task(subscription_id, task)
                else:
                    failed += 1
                    err = task.get("error") or f"analysis_{st}"
                    if force_analyze_latest <= 0:
                        pass
                    update_sync_run_item(
                        sync_run_id,
                        it.note_id,
                        analysis_status="failed",
                        error_message=str(err),
                    )
                    sync_link_card_from_task(subscription_id, task)

                digest_items.append(
                    _item_to_digest_row(
                        it,
                        analysis_task_id=task_id,
                        analysis_status="completed" if st == "completed" else "failed",
                        summary=summary,
                        text_chars=text_chars,
                        error_message=task.get("error") or "",
                    )
                )

            habit_row = update_habit_from_batch(
                subscription_id=subscription_id,
                red_id=red_id,
                items=digest_items,
            )
            habit_payload = {
                **habit_row,
                "subscription_id": subscription_id,
                "red_id": red_id,
            }

            update_sync_run(sync_run_id, status="digesting")
            digest_record = None
            digest_err = ""
            if digest_items:
                dig = generate_favorites_digest(
                    subscription=sub,
                    sync_run_id=sync_run_id,
                    items=digest_items,
                    habit=habit_payload,
                )
                if dig.get("ok"):
                    digest_record = save_digest(
                        sync_run_id=sync_run_id,
                        subscription_id=subscription_id,
                        digest_md=dig.get("digest_md") or "",
                        digest_json=dig.get("digest_json") or {},
                        llm_model=dig.get("llm_model") or "",
                        rag_degraded=bool(dig.get("rag_degraded")),
                    )
                else:
                    digest_err = dig.get("error") or "digest_failed"

            update_subscription_cursor(
                subscription_id,
                cursor_offset=next_cursor if not backfill_done else 0,
                last_note_id=items[0].note_id if items else sub.get("last_note_id"),
                cursor_published_at=None,
                mark_backfill_done=mark_backfill if not backfill_done else False,
                reset_failures=True,
            )

            final_status = "completed"
            if failed and analyzed:
                final_status = "partial"
            elif failed and not analyzed:
                final_status = "failed"
            if digest_err and not digest_record:
                final_status = "partial" if analyzed else final_status

            update_sync_run(
                sync_run_id,
                status=final_status,
                analyzed_count=analyzed,
                failed_count=failed,
                error_message=digest_err,
            )

            from .favorites_subscription_api import (
                _attach_catalog_seq,
                _enrich_sync_items_with_tasks,
            )

            run_items = _enrich_sync_items_with_tasks(list_sync_run_items(sync_run_id))
            cat_cards: List[Dict[str, Any]] = []
            for i, it in enumerate(items[: max(len(new_items), batch_label if limit > 0 else len(items), 20)], start=1):
                d = it.to_dict()
                d["seq"] = i
                cat_cards.append(d)
            run_items = _attach_catalog_seq(run_items, cat_cards)

            return {
                "ok": True,
                "sync_run_id": sync_run_id,
                "status": final_status,
                "new_count": len(new_items),
                "skipped_imported_count": len(skipped_imported),
                "skipped_imported": skipped_imported,
                "analyzed_count": analyzed,
                "failed_count": failed,
                "cursor_offset": next_cursor if not backfill_done else 0,
                "batch_size": limit,
                "full_sync": limit <= 0,
                "digest_id": (digest_record or {}).get("digest_id"),
                "items": run_items,
                "summary": {
                    "one_liner": (digest_record or {}).get("digest_json", {}).get("summary_one_liner")
                    if digest_record
                    else "",
                },
                "digest_md": (digest_record or {}).get("digest_md") or "",
                "habit": get_habit(subscription_id),
            }

        except Exception as ex:
            err_code = "SUB_FAVORITES_FETCH_FAILED"
            msg = str(ex)
            update_sync_run(
                sync_run_id,
                status="failed",
                error_code=err_code,
                error_message=msg,
            )
            update_subscription_cursor(
                subscription_id,
                cursor_offset=int(sub.get("cursor_offset") or 0),
                last_note_id=sub.get("last_note_id"),
                cursor_published_at=None,
                increment_failures=True,
            )
            _log.error(
                "[%s|favorites_sync_runner|%s|Agent执行|失败] error=%s",
                _CHAIN,
                sync_run_id,
                msg,
            )
            return {"ok": False, "sync_run_id": sync_run_id, "error_code": err_code, "error": msg}


async def run_favorites_sync_all(trigger: str = "scheduled") -> Dict[str, Any]:
    from .creator_subscription_store import list_subscriptions

    data = list_subscriptions(platform=_PLATFORM, status="active", page=1, page_size=50)
    subs = data.get("items") or []
    results = []
    for s in subs:
        r = await run_favorites_sync(s["subscription_id"], trigger=trigger)
        results.append({"subscription_id": s["subscription_id"], **r})
    return {"ok": True, "count": len(results), "results": results}
