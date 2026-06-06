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
    insert_seen_note,
    is_note_seen,
    list_sync_run_items,
    save_digest,
    update_subscription_cursor,
    update_sync_run,
    update_sync_run_item,
)
from .favorites_digest import generate_favorites_digest
from .favorites_habit import get_habit, update_habit_from_batch
from .pipeline_scheduler import run_pipeline_with_slot
from .task_manager import create_task, get_task
from .video_pipeline import process_video_pipeline
from .xhs_favorites_adapter import FavoritesFeedItem, fetch_favorites_catalog

_log = logging.getLogger("sba.favorites_sync_runner")
_CHAIN = "小红书收藏夹-增量拉取-单条分析-批次digest"
_PLATFORM = "xiaohongshu_favorites"

_SUB_LOCKS: Dict[str, asyncio.Lock] = {}
_FETCH_LIMIT = int(os.environ.get("FAVORITES_FETCH_LIMIT", "80"))
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
        sync_run = create_sync_run(subscription_id, trigger=trigger)
        sync_run_id = sync_run["sync_run_id"]
        update_sync_run(sync_run_id, status="fetching")

        _log.info(
            "[%s|favorites_sync_runner.run_favorites_sync|%s|Agent执行|开始] subscription_id=%s; trigger=%s; force=%s",
            _CHAIN,
            sync_run_id,
            subscription_id,
            trigger,
            force_analyze_latest,
        )

        try:
            from .xhs_owner_chrome import ensure_owner_chrome_cdp

            session = ensure_owner_chrome_cdp()
            _log.info(
                "[%s|favorites_sync_runner|%s|硬编执行|Chrome会话] nickname=%s; cdp=%s",
                _CHAIN,
                sync_run_id,
                session.get("nickname"),
                session.get("cdp_port"),
            )

            creator_id = sub.get("creator_id") or ""
            profile_url = sub.get("profile_url") or ""
            loop = asyncio.get_event_loop()
            items, _ok = await loop.run_in_executor(
                None,
                lambda: fetch_favorites_catalog(creator_id, profile_url=profile_url, limit=_FETCH_LIMIT),
            )

            backfill_done = bool(sub.get("initial_backfill_done"))
            red_id = ""
            for tag in sub.get("tags") or []:
                if isinstance(tag, str) and tag.startswith("red_id:"):
                    red_id = tag.split(":", 1)[1]
                    break

            # 回归模式：强制分析最新 N 篇
            if force_analyze_latest > 0:
                new_items = items[:force_analyze_latest]
            elif not backfill_done:
                baseline = 0
                for it in items:
                    if not is_note_seen(_PLATFORM, it.note_id):
                        insert_seen_note(
                            subscription_id=subscription_id,
                            platform=_PLATFORM,
                            note_id=it.note_id,
                            canonical_url=it.canonical_url,
                            url_hash=it.url_hash,
                            content_type=it.content_type,
                            title=it.title,
                            analysis_task_id=None,
                            analysis_status="baseline",
                        )
                        baseline += 1
                update_subscription_cursor(
                    subscription_id,
                    cursor_offset=0,
                    last_note_id=items[0].note_id if items else sub.get("last_note_id"),
                    cursor_published_at=None,
                    mark_backfill_done=True,
                    reset_failures=True,
                )
                save_digest(
                    sync_run_id=sync_run_id,
                    subscription_id=subscription_id,
                    digest_md=f"首次基线：已记录 {baseline} 篇历史收藏，后续仅分析新增项。",
                    digest_json={
                        "summary_one_liner": f"基线完成，记录 {baseline} 篇",
                        "topic_buckets": [],
                        "items": [],
                        "baseline": True,
                        "baseline_count": baseline,
                    },
                    llm_model="",
                    rag_degraded=False,
                )
                update_sync_run(
                    sync_run_id,
                    status="completed",
                    new_count=0,
                    analyzed_count=0,
                    failed_count=0,
                )
                return {
                    "ok": True,
                    "sync_run_id": sync_run_id,
                    "status": "completed",
                    "baseline": True,
                    "baseline_count": baseline,
                    "new_count": 0,
                }
            else:
                new_items = [it for it in items if not is_note_seen(_PLATFORM, it.note_id)]

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
                    },
                    llm_model="",
                    rag_degraded=False,
                )
                update_sync_run(sync_run_id, status="completed", new_count=0)
                update_subscription_cursor(
                    subscription_id,
                    cursor_offset=0,
                    last_note_id=items[0].note_id if items else sub.get("last_note_id"),
                    cursor_published_at=None,
                    mark_backfill_done=False,
                    reset_failures=True,
                )
                return {
                    "ok": True,
                    "sync_run_id": sync_run_id,
                    "status": "completed",
                    "new_count": 0,
                    "habit": habit_row,
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
                    analysis_status="pending",
                )

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

                task_id = create_task("小红书", it.canonical_url, "", comments_dict)
                update_sync_run_item(
                    sync_run_id, it.note_id, analysis_task_id=task_id, analysis_status="running"
                )

                async def _run_pipeline(tid: str = task_id):
                    await run_pipeline_with_slot(tid, lambda: process_video_pipeline(tid))

                asyncio.create_task(_run_pipeline())
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
                cursor_offset=0,
                last_note_id=items[0].note_id if items else sub.get("last_note_id"),
                cursor_published_at=None,
                mark_backfill_done=False,
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

            return {
                "ok": True,
                "sync_run_id": sync_run_id,
                "status": final_status,
                "new_count": len(new_items),
                "analyzed_count": analyzed,
                "failed_count": failed,
                "digest_id": (digest_record or {}).get("digest_id"),
                "items": list_sync_run_items(sync_run_id),
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
