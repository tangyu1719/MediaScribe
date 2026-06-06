"""RSS 订阅同步 SSE — 阶段性进度推送（与 AI 问答 thought_step 事件对齐）。"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Callable, Optional

import feedparser

from .rss_reader import (
    _LOCK,
    _MAX_ITEMS_TOTAL,
    _merge_item_states,
    _parse_feed_entries,
    _save_root,
    _user_bucket,
    list_feeds,
    map_feed_item_documents,
    resolve_user_id,
)

ProgressCb = Callable[[str, dict[str, Any]], None]


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _step_start(
    trace: str,
    step_id: str,
    *,
    step_name: str,
    description: str = "",
    input_text: str = "",
) -> str:
    return _sse(
        "thought_step_start",
        {
            "step_id": step_id,
            "step_name": step_name,
            "description": description,
            "input_text": input_text,
            "status": "running",
            "phase": "rss_sync",
            "node_kind": "sub_task",
            "step_lane": "execution",
            "llm_powered": False,
            "trace_id": trace,
        },
    )


def _step_end(
    trace: str,
    step_id: str,
    *,
    step_name: str,
    status: str = "completed",
    output_text: str = "",
    description: str = "",
) -> str:
    return _sse(
        "thought_step_end",
        {
            "step_id": step_id,
            "step_name": step_name,
            "status": status,
            "output_text": output_text,
            "description": description,
            "phase": "rss_sync",
            "node_kind": "sub_task",
            "step_lane": "execution",
            "llm_powered": False,
            "trace_id": trace,
        },
    )


async def _run_sync_feed_body(
    user_id: str,
    feed_id: str,
    *,
    emit: ProgressCb,
) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    trace = uuid.uuid4().hex[:10]

    emit(
        "thought_step_start",
        {
            "step_id": f"{trace}_prep",
            "step_name": "准备同步",
            "description": "正在读取订阅配置…",
            "status": "running",
            "phase": "rss_sync",
            "trace_id": trace,
        },
    )
    with _LOCK:
        from .rss_reader import _load_root

        root = _load_root()
        bucket = _user_bucket(root, uid)
        feeds = bucket.setdefault("feeds", [])
        idx = next((i for i, f in enumerate(feeds) if f.get("id") == feed_id), -1)
        if idx < 0:
            emit(
                "thought_step_end",
                {
                    "step_id": f"{trace}_prep",
                    "step_name": "准备同步",
                    "status": "failed",
                    "output_text": "订阅不存在",
                    "phase": "rss_sync",
                    "trace_id": trace,
                },
            )
            raise ValueError("订阅不存在")
        feed_row = dict(feeds[idx])
        old_items = [i for i in (bucket.get("items") or []) if i.get("feed_id") == feed_id]

    feed_title = feed_row.get("title") or feed_row.get("url") or feed_id
    emit(
        "thought_step_end",
        {
            "step_id": f"{trace}_prep",
            "step_name": "准备同步",
            "status": "completed",
            "output_text": f"订阅：{feed_title}",
            "phase": "rss_sync",
            "trace_id": trace,
        },
    )

    url = feed_row.get("url") or ""
    emit(
        "thought_step_start",
        {
            "step_id": f"{trace}_fetch",
            "step_name": "拉取订阅源",
            "description": f"正在连接并下载 Feed…",
            "input_text": url,
            "status": "running",
            "phase": "rss_sync",
            "trace_id": trace,
        },
    )
    loop = asyncio.get_running_loop()
    try:
        parsed = await loop.run_in_executor(None, feedparser.parse, url)
        if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
            bozo_exc = getattr(parsed, "bozo_exception", None)
            raise ValueError(f"Feed 解析失败: {bozo_exc or 'unknown'}")
        updated, new_items = _parse_feed_entries(feed_row, parsed)
        new_items = _merge_item_states(old_items, new_items)
        entry_count = len(new_items)
        emit(
            "thought_step_end",
            {
                "step_id": f"{trace}_fetch",
                "step_name": "拉取订阅源",
                "status": "completed",
                "output_text": f"拉取到 {entry_count} 篇文章",
                "phase": "rss_sync",
                "trace_id": trace,
            },
        )
    except Exception as ex:
        err = str(ex) or ex.__class__.__name__
        emit(
            "thought_step_end",
            {
                "step_id": f"{trace}_fetch",
                "step_name": "拉取订阅源",
                "status": "failed",
                "output_text": err,
                "phase": "rss_sync",
                "trace_id": trace,
            },
        )
        with _LOCK:
            from .rss_reader import _load_root

            root = _load_root()
            bucket = _user_bucket(root, uid)
            feeds = bucket.setdefault("feeds", [])
            idx = next((i for i, f in enumerate(feeds) if f.get("id") == feed_id), -1)
            if idx >= 0:
                feeds[idx]["error"] = err
                from .rss_reader import _now_iso

                feeds[idx]["last_sync"] = _now_iso()
                _save_root(root)
        raise ValueError(err) from ex

    emit(
        "thought_step_start",
        {
            "step_id": f"{trace}_map",
            "step_name": "映射本地文档",
            "description": f"正在为 {len(new_items)} 篇文章建立/复用本地 MD…",
            "status": "running",
            "phase": "rss_sync",
            "trace_id": trace,
        },
    )

    mapped_stats = {"skip": 0, "linked": 0, "stub": 0}
    last_title = ""

    def _on_map(row: dict[str, Any]) -> None:
        nonlocal last_title
        last_title = (row.get("title") or "")[:60]
        action = row.get("action") or ""
        mapped_stats[action] = mapped_stats.get(action, 0) + 1
        emit(
            "sync_item_progress",
            {
                "trace_id": trace,
                "index": row.get("index"),
                "total": row.get("total"),
                "title": last_title,
                "action": action,
                "doc_filename": row.get("doc_filename"),
            },
        )

    new_items, doc_stats = await loop.run_in_executor(
        None,
        lambda: map_feed_item_documents(
            new_items,
            feed_title=updated.get("title") or feed_title,
            progress_cb=_on_map,
        ),
    )
    mapped_stats.update(doc_stats)
    emit(
        "thought_step_end",
        {
            "step_id": f"{trace}_map",
            "step_name": "映射本地文档",
            "status": "completed",
            "output_text": (
                f"已有 {mapped_stats.get('skip', 0)} · "
                f"复用 {mapped_stats.get('linked', 0)} · "
                f"新建摘录 {mapped_stats.get('stub', 0)}"
            ),
            "phase": "rss_sync",
            "trace_id": trace,
        },
    )

    with _LOCK:
        from .rss_reader import _load_root

        root = _load_root()
        bucket = _user_bucket(root, uid)
        feeds = bucket.setdefault("feeds", [])
        idx = next((i for i, f in enumerate(feeds) if f.get("id") == feed_id), -1)
        if idx >= 0:
            feeds[idx] = updated
        items = bucket.setdefault("items", [])
        items = [i for i in items if i.get("feed_id") != feed_id]
        items.extend(new_items)
        items.sort(key=lambda i: i.get("published") or "", reverse=True)
        bucket["items"] = items[:_MAX_ITEMS_TOTAL]
        _save_root(root)

    return {
        "ok": True,
        "feed": updated,
        "item_count": len(new_items),
        "doc_stats": mapped_stats,
        "trace_id": trace,
    }


async def stream_sync_feed(user_id: str, feed_id: str) -> AsyncIterator[str]:
    """单源同步 SSE 流。"""
    events: list[tuple[str, dict[str, Any]]] = []
    done = asyncio.Event()
    result: dict[str, Any] = {}
    error: Optional[BaseException] = None

    def _emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    async def _worker() -> None:
        nonlocal result, error
        try:
            result = await _run_sync_feed_body(user_id, feed_id, emit=_emit)
        except BaseException as ex:
            error = ex
        finally:
            done.set()

    task = asyncio.create_task(_worker())
    sent = 0
    while not done.is_set() or sent < len(events):
        while sent < len(events):
            ev, data = events[sent]
            yield _sse(ev, data)
            sent += 1
        if not done.is_set():
            await asyncio.sleep(0.05)
    await task
    if error:
        yield _sse("error", {"ok": False, "error": str(error)})
        return
    yield _sse("sync_complete", result)


async def stream_sync_all_feeds(user_id: str) -> AsyncIterator[str]:
    """全部订阅源顺序同步 SSE 流。"""
    feeds = list_feeds(resolve_user_id(user_id))
    trace = uuid.uuid4().hex[:10]
    yield _step_start(trace, f"{trace}_all", step_name="全部同步", description=f"共 {len(feeds)} 个订阅源")
    ok = 0
    failed: list[dict[str, str]] = []
    for pos, feed in enumerate(feeds, 1):
        fid = feed.get("id") or ""
        title = feed.get("title") or feed.get("url") or fid
        if not fid:
            continue
        yield _step_start(
            trace,
            f"{trace}_{fid}",
            step_name=f"同步源 {pos}/{len(feeds)}",
            description=f"正在同步：{title}",
            input_text=fid,
        )
        try:
            feed_failed = False
            feed_err = ""
            async for chunk in stream_sync_feed(resolve_user_id(user_id), fid):
                if chunk.startswith("event: sync_complete"):
                    ok += 1
                elif chunk.startswith("event: error"):
                    feed_failed = True
                    try:
                        line = next((ln for ln in chunk.split("\n") if ln.startswith("data:")), "")
                        feed_err = json.loads(line[5:].strip()).get("error") or "同步失败"
                    except Exception:
                        feed_err = "同步失败"
                elif chunk.startswith("event: thought_step") or chunk.startswith("event: sync_item_progress"):
                    yield chunk
            if feed_failed:
                raise ValueError(feed_err or "同步失败")
            yield _step_end(
                trace,
                f"{trace}_{fid}",
                step_name=f"同步源 {pos}/{len(feeds)}",
                output_text=f"{title} 完成",
            )
        except Exception as ex:
            failed.append({"feed_id": fid, "title": title, "error": str(ex)})
            yield _step_end(
                trace,
                f"{trace}_{fid}",
                step_name=f"同步源 {pos}/{len(feeds)}",
                status="failed",
                output_text=str(ex),
            )
    yield _step_end(
        trace,
        f"{trace}_all",
        step_name="全部同步",
        output_text=f"成功 {ok} · 失败 {len(failed)}",
    )
    yield _sse(
        "sync_complete",
        {
            "ok": True,
            "ok_count": ok,
            "fail_count": len(failed),
            "failed": failed,
            "total": len(feeds),
        },
    )
