"""UP 主页目录摘录 — 写入 seen 表（博客信息）并可选入队链接流水线。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .creator_feed_adapter import FeedItem, get_feed_adapter
from .creator_subscription_store import (
    add_sync_run_item,
    create_sync_run,
    get_or_create_subscription,
    get_subscription,
    get_subscription_by_platform_creator,
    init_db,
    insert_seen_note,
    list_seen_notes_by_subscription,
    update_sync_run,
    upsert_seen_note_url,
)
from .link_hash import url_hash as link_url_hash
from .pipeline_scheduler import request_video_pipeline, request_video_pipeline_async
from .subscription_import_guard import check_already_imported, record_skipped_import
from .subscription_batch_gate import finalize_subscription_batch
from .task_manager import create_task
from .task_source_meta import SOURCE_CATALOG, source_meta_kwargs

_log = logging.getLogger("sba.creator_catalog_seed")
_CHAIN = "社媒订阅-目录摘录-博客信息-入队"


def _sync_repaired_link_to_task(task_id: str, new_url: str) -> bool:
    """裸链修复后，同步更新队列/历史里绑定的 task 链接。"""
    tid = (task_id or "").strip()
    url = (new_url or "").strip()
    if not tid or not url or "xsec_token" not in url:
        return False
    try:
        from .task_manager import get_task, update_task, _sync_link_identity_fields

        task = get_task(tid)
        if not task:
            return False
        old = str(task.get("link") or "")
        if "xsec_token" in old:
            return False
        if old == url:
            return False
        _sync_link_identity_fields(task, url)
        update_task(
            tid,
            link=task["link"],
            normalized_link=task.get("normalized_link") or "",
            url_hash=task.get("url_hash") or "",
        )
        try:
            from .span_audit import update_task as span_update_task

            span_update_task(tid, user_query=url)
        except Exception:
            pass
        try:
            from .history_manager import add_or_update_task_in_history

            add_or_update_task_in_history(dict(task))
        except Exception:
            pass
        _log.info(
            "[%s|creator_catalog_seed|task:%s|硬编执行|同步链接] 裸链已补 token; ok=true",
            _CHAIN,
            tid,
        )
        return True
    except Exception as ex:
        _log.warning(
            "[%s|creator_catalog_seed|task:%s|硬编执行|同步链接] 失败; error_type=%s; error_message=%s",
            _CHAIN,
            tid,
            type(ex).__name__,
            str(ex)[:200],
        )
        return False


async def seed_subscription_catalog(
    *,
    subscription_id: str = "",
    creator_id: str = "",
    display_name: str = "",
    profile_url: str = "",
    limit: int = 20,
    enqueue: bool = False,
    dry_run: bool = False,
    trigger: str = "catalog_seed",
) -> Dict[str, Any]:
    """
    拉取 UP 主页可见笔记目录，写入 creator_subscription_seen_notes（analysis_status=catalog）。
    enqueue=True 时为 pending 条目创建链接任务并入队（不阻塞等待完成）。
    """
    init_db()
    limit = max(1, min(int(limit or 20), 200))

    sub: Optional[Dict[str, Any]] = None
    if subscription_id:
        sub = get_subscription(subscription_id)
    elif creator_id:
        sub = get_subscription_by_platform_creator("xiaohongshu", creator_id)
        if not sub:
            sub = get_or_create_subscription(
                platform="xiaohongshu",
                creator_id=creator_id,
                profile_url=profile_url
                or f"https://www.xiaohongshu.com/user/profile/{creator_id}",
                display_name=display_name or creator_id,
            )
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在且无法创建"}

    subscription_id = sub["subscription_id"]
    creator_id = sub.get("creator_id") or creator_id
    profile_url = sub.get("profile_url") or profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    display_name = sub.get("display_name") or display_name

    _log.info(
        "[%s|creator_catalog_seed.seed_subscription_catalog|%s|Agent执行|开始] display_name=%s; limit=%s; enqueue=%s",
        _CHAIN,
        subscription_id,
        display_name,
        limit,
        enqueue,
    )

    adapter = get_feed_adapter(sub["platform"])
    loop = asyncio.get_event_loop()
    try:
        catalog_items: List[FeedItem] = await loop.run_in_executor(
            None,
            lambda: adapter.fetch_catalog(creator_id, profile_url=profile_url, min_count=limit),
        )
    except Exception as ex:
        _log.error(
            "[%s|creator_catalog_seed.seed_subscription_catalog|%s|Agent执行|拉取失败] error=%s",
            _CHAIN,
            subscription_id,
            ex,
        )
        return {
            "ok": False,
            "subscription_id": subscription_id,
            "display_name": display_name,
            "creator_id": creator_id,
            "error_code": "CATALOG_FETCH_FAILED",
            "error": str(ex),
        }

    selected = catalog_items[:limit]
    if not selected:
        return {
            "ok": False,
            "subscription_id": subscription_id,
            "display_name": display_name,
            "creator_id": creator_id,
            "catalog_total": 0,
            "error_code": "CATALOG_EMPTY",
            "error": "主页未解析到笔记",
        }

    # 补齐带 xsec_token 的真实链接（与 UP 画像 resolve_links 同路径）
    from .creator_feed_adapter import resolve_note_links_for_selection

    sel_dicts = [
        {
            "note_id": it.note_id,
            "title": it.title,
            "content_type": it.content_type,
            "canonical_url": it.canonical_url,
        }
        for it in selected
    ]
    try:
        resolved_notes = await loop.run_in_executor(
            None,
            lambda: resolve_note_links_for_selection(
                sel_dicts,
                creator_id=creator_id,
                profile_url=profile_url,
                catalog=[it.to_dict() for it in catalog_items],
            ),
        )
        url_by_id = {
            str(n.get("note_id") or ""): str(n.get("canonical_url") or "")
            for n in resolved_notes
            if n.get("note_id")
        }
        enriched: List[FeedItem] = []
        for it in selected:
            href = url_by_id.get(it.note_id) or it.canonical_url
            enriched.append(
                FeedItem(
                    platform=it.platform,
                    note_id=it.note_id,
                    canonical_url=href,
                    url_hash=it.url_hash if href == it.canonical_url else link_url_hash(href),
                    content_type=it.content_type,
                    title=it.title,
                    published_at=it.published_at,
                    author_id=it.author_id,
                    author_name=it.author_name,
                    fetch_source=it.fetch_source,
                )
            )
        selected = enriched
    except Exception as ex:
        _log.warning(
            "[%s|creator_catalog_seed|%s|Agent执行|链接解析降级] error=%s",
            _CHAIN,
            subscription_id,
            ex,
        )

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "subscription_id": subscription_id,
            "display_name": display_name,
            "creator_id": creator_id,
            "catalog_total": len(catalog_items),
            "seed_count": len(selected),
            "items": [it.to_dict() for it in selected],
        }

    sync_run = create_sync_run(
        subscription_id,
        trigger=trigger,
        latest_limit=limit,
        catalog_count=len(catalog_items),
    )
    sync_run_id = sync_run["sync_run_id"]
    update_sync_run(sync_run_id, status="catalogging")

    seeded = 0
    skipped = 0
    enqueued = 0
    enqueued_task_ids: List[str] = []
    items_out: List[Dict[str, Any]] = []

    read_comments = bool(sub.get("read_comments"))
    comments_dict = {"enabled": read_comments, "count": 10, "sort": "hot"}

    for it in selected:
        imported, imp_reason = check_already_imported(
            it.platform, it.note_id, it.url_hash, canonical_url=it.canonical_url
        )
        row_status = "catalog"
        task_id: Optional[str] = None

        if imported:
            record_skipped_import(
                subscription_id=subscription_id,
                platform=it.platform,
                note_id=it.note_id,
                canonical_url=it.canonical_url,
                url_hash=it.url_hash,
                content_type=it.content_type,
                title=it.title,
                reason=imp_reason,
            )
            row_status = "already_imported"
            skipped += 1
        else:
            inserted = insert_seen_note(
                subscription_id=subscription_id,
                platform=it.platform,
                note_id=it.note_id,
                canonical_url=it.canonical_url,
                url_hash=it.url_hash,
                content_type=it.content_type,
                title=it.title,
                analysis_task_id=None,
                analysis_status="catalog",
            )
            if inserted:
                seeded += 1
            else:
                if upsert_seen_note_url(
                    subscription_id=subscription_id,
                    platform=it.platform,
                    note_id=it.note_id,
                    canonical_url=it.canonical_url,
                    url_hash=it.url_hash,
                    title=it.title,
                    content_type=it.content_type,
                ):
                    seeded += 1
                row_status = "already_cataloged"
                skipped += 1

        link_ok = "xsec_token" in (it.canonical_url or "")
        if not link_ok:
            _log.warning(
                "[%s|creator_catalog_seed|%s|Agent执行|裸链无token] note_id=%s; url=%s",
                _CHAIN,
                subscription_id,
                it.note_id,
                (it.canonical_url or "")[:120],
            )

        if enqueue and row_status == "catalog" and not imported and link_ok:
            src_meta = source_meta_kwargs(
                SOURCE_CATALOG,
                display_name=display_name,
                platform=it.platform,
                author_name=str(it.author_name or display_name or ""),
                author_id=str(it.author_id or creator_id or ""),
                subscription_id=subscription_id,
            )
            task_id = create_task(
                "小红书",
                it.canonical_url,
                "",
                comments_dict,
                skip_bootstrap_meta=True,
                skip_done_hist_check=False,
                **src_meta,
            )
            from .creator_subscription_store import update_seen_analysis
            from .pipeline_scheduler import request_video_pipeline

            update_seen_analysis(it.platform, it.note_id, task_id, "queued")
            add_sync_run_item(
                sync_run_id,
                note_id=it.note_id,
                canonical_url=it.canonical_url,
                content_type=it.content_type,
                title=it.title,
                analysis_task_id=task_id,
                analysis_status="queued",
            )
            request_video_pipeline(task_id)
            enqueued += 1
            enqueued_task_ids.append(task_id)
        else:
            add_sync_run_item(
                sync_run_id,
                note_id=it.note_id,
                canonical_url=it.canonical_url,
                content_type=it.content_type,
                title=it.title,
                analysis_status=row_status,
                error_message=imp_reason if imported else "",
            )

        items_out.append(
            {
                "note_id": it.note_id,
                "title": it.title,
                "canonical_url": it.canonical_url,
                "analysis_status": row_status,
                "analysis_task_id": task_id,
            }
        )

    update_sync_run(
        sync_run_id,
        status="partial" if enqueued else "completed",
        new_count=seeded,
        analyzed_count=enqueued,
        failed_count=0,
    )

    if enqueued > 0:
        asyncio.create_task(
            finalize_subscription_batch(
                subscription_id,
                sync_run_id,
                expected_total=limit,
                task_ids=enqueued_task_ids,
            )
        )

    seen_total = len(list_seen_notes_by_subscription(subscription_id))

    _log.info(
        "[%s|creator_catalog_seed.seed_subscription_catalog|%s|Agent执行|完成] catalog_total=%s; seeded=%s; skipped=%s; enqueued=%s; seen_total=%s",
        _CHAIN,
        subscription_id,
        len(catalog_items),
        seeded,
        skipped,
        enqueued,
        seen_total,
    )

    return {
        "ok": True,
        "subscription_id": subscription_id,
        "display_name": display_name,
        "creator_id": creator_id,
        "sync_run_id": sync_run_id,
        "catalog_total": len(catalog_items),
        "seed_count": seeded,
        "skipped_count": skipped,
        "enqueued_count": enqueued,
        "seen_total": seen_total,
        "limit": limit,
        "items": items_out,
    }


async def enqueue_pending_catalog_notes(
    *,
    subscription_id: str = "",
    creator_id: str = "",
    limit: int = 200,
) -> Dict[str, Any]:
    """将 seen 表中 analysis_status=catalog 的链接入队（不重新拉主页）。"""
    init_db()
    sub: Optional[Dict[str, Any]] = None
    if subscription_id:
        sub = get_subscription(subscription_id)
    elif creator_id:
        sub = get_subscription_by_platform_creator("xiaohongshu", creator_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}

    subscription_id = sub["subscription_id"]
    rows = list_seen_notes_by_subscription(
        subscription_id,
        page=1,
        page_size=min(max(1, int(limit or 200)), 200),
        analysis_status="catalog",
    )
    read_comments = bool(sub.get("read_comments"))
    comments_dict = {"enabled": read_comments, "count": 10, "sort": "hot"}
    enqueued = 0
    for row in rows:
        url = str(row.get("canonical_url") or "").strip()
        if not url:
            continue
        src_meta = source_meta_kwargs(
            SOURCE_CATALOG,
            display_name=str(sub.get("display_name") or ""),
            platform=str(row.get("platform") or "xiaohongshu"),
            author_name=str(row.get("author_name") or sub.get("display_name") or ""),
            author_id=str(row.get("author_id") or sub.get("creator_id") or ""),
            subscription_id=subscription_id,
        )
        task_id = create_task(
            "小红书",
            url,
            "",
            comments_dict,
            skip_bootstrap_meta=True,
            **src_meta,
        )
        from .creator_subscription_store import update_seen_analysis
        from .pipeline_scheduler import request_video_pipeline

        update_seen_analysis(
            str(row.get("platform") or "xiaohongshu"),
            str(row.get("note_id") or ""),
            task_id,
            "queued",
        )
        request_video_pipeline(task_id)
        enqueued += 1

    return {
        "ok": True,
        "subscription_id": subscription_id,
        "display_name": sub.get("display_name") or "",
        "enqueued_count": enqueued,
        "pending_catalog": len(rows),
    }


async def enqueue_catalog_notes(
    subscription_id: str,
    *,
    limit: int = 0,
    trigger: str = "catalog_enqueue",
) -> Dict[str, Any]:
    """将 seen 表中 analysis_status=catalog 的链接入队链接流水线（不阻塞等待）。"""
    init_db()
    sub = get_subscription(subscription_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}

    notes = list_seen_notes_by_subscription(subscription_id, page=1, page_size=200, analysis_status="catalog")
    if limit > 0:
        notes = notes[:limit]

    if not notes:
        return {
            "ok": True,
            "subscription_id": subscription_id,
            "display_name": sub.get("display_name") or "",
            "enqueued_count": 0,
            "message": "无待入队的 catalog 链接",
        }

    sync_run = create_sync_run(subscription_id, trigger=trigger, catalog_count=len(notes))
    sync_run_id = sync_run["sync_run_id"]
    update_sync_run(sync_run_id, status="analyzing")

    read_comments = bool(sub.get("read_comments"))
    comments_dict = {"enabled": read_comments, "count": 10, "sort": "hot"}
    enqueued = 0
    task_ids: List[str] = []

    from .creator_subscription_store import update_seen_analysis

    for n in notes:
        url = n.get("canonical_url") or ""
        if not url or "xsec_token" not in url:
            _log.warning(
                "[%s|creator_catalog_seed.enqueue_catalog_notes|%s|Agent执行|跳过裸链] note_id=%s",
                _CHAIN,
                subscription_id,
                n.get("note_id"),
            )
            continue
        src_meta = source_meta_kwargs(
            SOURCE_CATALOG,
            display_name=str(sub.get("display_name") or ""),
            platform=str(sub.get("platform") or "xiaohongshu"),
            author_name=str(n.get("author_name") or sub.get("display_name") or ""),
            author_id=str(n.get("author_id") or sub.get("creator_id") or ""),
            subscription_id=subscription_id,
        )
        task_id = create_task("小红书", url, "", comments_dict, **src_meta)
        update_seen_analysis(sub["platform"], n["note_id"], task_id, "queued")
        add_sync_run_item(
            sync_run_id,
            note_id=n["note_id"],
            canonical_url=url,
            content_type=n.get("content_type") or "unknown",
            title=n.get("title") or "",
            analysis_task_id=task_id,
            analysis_status="queued",
        )
        asyncio.create_task(request_video_pipeline_async(task_id))
        enqueued += 1
        task_ids.append(task_id)

    update_sync_run(sync_run_id, status="partial", new_count=enqueued, analyzed_count=0)

    _log.info(
        "[%s|creator_catalog_seed.enqueue_catalog_notes|%s|Agent执行|入队] enqueued=%s",
        _CHAIN,
        subscription_id,
        enqueued,
    )

    if enqueued > 0 and sync_run_id:
        expected = int(sub.get("latest_limit") or 0) or len(notes) or enqueued
        asyncio.create_task(
            finalize_subscription_batch(
                subscription_id,
                sync_run_id,
                expected_total=expected,
                task_ids=task_ids,
            )
        )

    return {
        "ok": True,
        "subscription_id": subscription_id,
        "display_name": sub.get("display_name") or "",
        "sync_run_id": sync_run_id,
        "enqueued_count": enqueued,
        "task_ids": task_ids,
    }


async def repair_subscription_note_links(
    subscription_id: str,
    *,
    limit: int = 200,
) -> Dict[str, Any]:
    """重新解析 seen 表中的裸 explore 链接，补全 xsec_token（需 CDP + 小红书登录态）。"""
    init_db()
    sub = get_subscription(subscription_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}

    creator_id = sub.get("creator_id") or ""
    profile_url = sub.get("profile_url") or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    rows = list_seen_notes_by_subscription(subscription_id, page_size=min(limit, 200))
    bare = [
        r
        for r in rows
        if r.get("note_id") and "xsec_token" not in (r.get("canonical_url") or "")
    ]
    if not bare:
        return {
            "ok": True,
            "subscription_id": subscription_id,
            "repaired": 0,
            "bare_total": 0,
            "message": "无需修复",
        }

    from .creator_feed_adapter import resolve_note_links_for_selection

    loop = asyncio.get_event_loop()
    resolved = await loop.run_in_executor(
        None,
        lambda: resolve_note_links_for_selection(
            [{"note_id": r["note_id"], "title": r.get("title"), "canonical_url": r.get("canonical_url")} for r in bare],
            creator_id=creator_id,
            profile_url=profile_url,
        ),
    )
    repaired = 0
    still_bare = 0
    tasks_updated = 0
    row_by_nid = {str(r.get("note_id") or ""): r for r in bare}
    for note in resolved:
        nid = str(note.get("note_id") or "")
        url = str(note.get("canonical_url") or "")
        if not nid or not url:
            continue
        if "xsec_token" not in url:
            still_bare += 1
            continue
        if upsert_seen_note_url(
            subscription_id=subscription_id,
            platform=sub.get("platform") or "xiaohongshu",
            note_id=nid,
            canonical_url=url,
            url_hash=link_url_hash(url),
            title=str(note.get("title") or ""),
        ):
            repaired += 1
            row = row_by_nid.get(nid) or {}
            tid = str(row.get("analysis_task_id") or "").strip()
            if tid and _sync_repaired_link_to_task(tid, url):
                tasks_updated += 1

    return {
        "ok": True,
        "subscription_id": subscription_id,
        "display_name": sub.get("display_name") or "",
        "bare_total": len(bare),
        "repaired": repaired,
        "still_bare": still_bare,
        "tasks_updated": tasks_updated,
    }
