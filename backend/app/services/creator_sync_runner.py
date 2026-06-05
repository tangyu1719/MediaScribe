"""社媒订阅 sync_run 编排 — 拉取、判重、单条分析、digest。"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from .creator_digest import generate_digest
from .creator_feed_adapter import FeedItem, get_feed_adapter
from .creator_subscription_store import (
    add_sync_run_item,
    create_sync_run,
    insert_seen_note,
    is_note_seen,
    list_sync_run_items,
    save_digest,
    update_subscription_cursor,
    update_sync_run,
    update_sync_run_item,
    get_subscription,
)
from .pipeline_scheduler import run_pipeline_with_slot
from .task_manager import create_task, get_task
from .video_pipeline import process_video_pipeline

_log = logging.getLogger("sba.creator_sync_runner")
_CHAIN = "社媒订阅-增量拉取-单条分析-批次digest"

_SUB_LOCKS: Dict[str, asyncio.Lock] = {}
_INITIAL_FETCH_LIMIT = int(os.environ.get("SUBSCRIPTION_INITIAL_FETCH_LIMIT", "20"))
_INCREMENTAL_FETCH_LIMIT = int(os.environ.get("SUBSCRIPTION_INCREMENTAL_FETCH_LIMIT", "30"))
_DIGEST_WAIT_SEC = int(os.environ.get("SUBSCRIPTION_DIGEST_WAIT_SEC", "1800"))


def _lock_for(subscription_id: str) -> asyncio.Lock:
    if subscription_id not in _SUB_LOCKS:
        _SUB_LOCKS[subscription_id] = asyncio.Lock()
    return _SUB_LOCKS[subscription_id]


def _parse_published_at(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
    except Exception:
        return None


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


async def run_sync(subscription_id: str, trigger: str = "manual") -> Dict[str, Any]:
    sub = get_subscription(subscription_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}
    if sub.get("status") not in ("active", "error"):
        return {"ok": False, "error_code": "SUB_INACTIVE", "error": "订阅未启用"}

    lock = _lock_for(subscription_id)
    if lock.locked():
        return {"ok": False, "error_code": "SUB_SYNC_BUSY", "error": "该订阅已有 sync 在进行"}

    async with lock:
        sync_run = create_sync_run(subscription_id, trigger=trigger)
        sync_run_id = sync_run["sync_run_id"]
        update_sync_run(sync_run_id, status="fetching")

        _log.info(
            "[%s|creator_sync_runner.run_sync|%s|Agent执行|开始] sync 启动; subscription_id=%s; trigger=%s",
            _CHAIN,
            sync_run_id,
            subscription_id,
            trigger,
        )

        try:
            adapter = get_feed_adapter(sub["platform"])
            backfill_done = bool(sub.get("initial_backfill_done"))
            # 历史回填：按 cursor_offset 分页扫主页 INITIAL_STATE 列表
            # 增量更新：始终从列表头部拉取，靠 note_id 判重（避免新发笔记被 offset 跳过）
            if backfill_done:
                cursor = 0
                limit = _INCREMENTAL_FETCH_LIMIT
            else:
                cursor = int(sub.get("cursor_offset") or 0)
                limit = _INITIAL_FETCH_LIMIT
            profile_url = sub.get("profile_url") or ""
            creator_id = sub.get("creator_id") or ""

            loop = asyncio.get_event_loop()
            items, next_cursor, has_more = await loop.run_in_executor(
                None,
                lambda: adapter.fetch_page(creator_id, cursor, limit, profile_url=profile_url),
            )

            new_items: List[FeedItem] = []
            for it in items:
                if not is_note_seen(it.platform, it.note_id):
                    new_items.append(it)

            update_sync_run(sync_run_id, status="analyzing", new_count=len(new_items))
            _log.info(
                "[%s|creator_sync_runner.run_sync|%s|Agent执行|拉取] 完成; fetched=%s; new=%s",
                _CHAIN,
                sync_run_id,
                len(items),
                len(new_items),
            )

            analyzed = 0
            failed = 0
            digest_items: List[Dict[str, Any]] = []
            read_comments = bool(sub.get("read_comments"))
            comments_dict = {"enabled": read_comments, "count": 10, "sort": "hot"}
            per_item_timeout = max(120, _DIGEST_WAIT_SEC // max(1, len(new_items) or 1))

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
                        platform=it.platform,
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
                        {
                            "note_id": it.note_id,
                            "title": it.title,
                            "content_type": it.content_type,
                            "canonical_url": it.canonical_url,
                            "analysis_status": "skipped",
                            "summary": it.title,
                        }
                    )
                    continue

                task_id = create_task("小红书", it.canonical_url, "", comments_dict)
                update_sync_run_item(sync_run_id, it.note_id, analysis_task_id=task_id, analysis_status="running")

                async def _run_pipeline(tid: str = task_id):
                    await run_pipeline_with_slot(tid, lambda: process_video_pipeline(tid))

                asyncio.create_task(_run_pipeline())
                wait = await _wait_task(task_id, per_item_timeout)
                task = wait.get("task") or {}
                st = wait.get("status")
                summary = task.get("doc_title") or task.get("link_title") or it.title
                if task.get("doc_path"):
                    try:
                        from pathlib import Path

                        p = Path(task["doc_path"])
                        if p.is_file():
                            text = p.read_text(encoding="utf-8", errors="ignore")
                            summary = text[:1500]
                    except Exception:
                        pass

                if st == "completed":
                    analyzed += 1
                    insert_seen_note(
                        subscription_id=subscription_id,
                        platform=it.platform,
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
                    update_sync_run_item(
                        sync_run_id,
                        it.note_id,
                        analysis_status="failed",
                        error_message=str(err),
                    )

                digest_items.append(
                    {
                        "note_id": it.note_id,
                        "title": it.title,
                        "content_type": it.content_type,
                        "canonical_url": it.canonical_url,
                        "analysis_task_id": task_id,
                        "analysis_status": "completed" if st == "completed" else "failed",
                        "summary": summary,
                        "error_message": task.get("error") or "",
                    }
                )

            newest_note = new_items[0] if new_items else (items[0] if items else None)
            last_pub = _parse_published_at(newest_note.published_at) if newest_note else None
            if backfill_done:
                update_subscription_cursor(
                    subscription_id,
                    cursor_offset=int(sub.get("cursor_offset") or 0),
                    last_note_id=newest_note.note_id if newest_note else sub.get("last_note_id"),
                    cursor_published_at=last_pub,
                    mark_backfill_done=False,
                    reset_failures=True,
                )
            else:
                update_subscription_cursor(
                    subscription_id,
                    cursor_offset=next_cursor,
                    last_note_id=newest_note.note_id if newest_note else sub.get("last_note_id"),
                    cursor_published_at=last_pub,
                    mark_backfill_done=not has_more,
                    reset_failures=True,
                )

            update_sync_run(sync_run_id, status="digesting")
            digest_record = None
            digest_err = ""
            if digest_items:
                dig = generate_digest(subscription=sub, sync_run_id=sync_run_id, items=digest_items)
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
                    _log.error(
                        "[%s|creator_sync_runner.run_sync|%s|Agent执行|digest] 失败; error=%s",
                        _CHAIN,
                        sync_run_id,
                        digest_err,
                    )
            else:
                digest_record = save_digest(
                    sync_run_id=sync_run_id,
                    subscription_id=subscription_id,
                    digest_md="本次 sync 未发现新笔记。",
                    digest_json={"summary_one_liner": "无新内容", "topic_buckets": [], "items": []},
                    llm_model="",
                    rag_degraded=False,
                )

            final_status = "completed"
            if failed and analyzed:
                final_status = "partial"
            elif failed and not analyzed and new_items:
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
            }

        except Exception as ex:
            err_code = "SUB_FETCH_FAILED"
            msg = str(ex)
            if "SUB_FETCH_AUTH_FAILED" in msg:
                err_code = "SUB_FETCH_AUTH_FAILED"
            elif "SUB_PROFILE" in msg:
                err_code = "SUB_PROFILE_UNREACHABLE"
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
                "[%s|creator_sync_runner.run_sync|%s|Agent执行|失败] error_code=%s; error=%s",
                _CHAIN,
                sync_run_id,
                err_code,
                msg,
            )
            return {"ok": False, "sync_run_id": sync_run_id, "error_code": err_code, "error": msg}


async def run_sync_all(trigger: str = "scheduled") -> Dict[str, Any]:
    from .creator_subscription_store import list_active_subscriptions

    subs = list_active_subscriptions()
    results = []
    for s in subs:
        r = await run_sync(s["subscription_id"], trigger=trigger)
        results.append({"subscription_id": s["subscription_id"], **r})
    return {"ok": True, "count": len(results), "results": results}
