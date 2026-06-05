"""RSS 订阅阅读 — 抓取 Feed、本地 JSON 存储、OPML、已读/星标、Agent 上下文。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from xml.dom import minidom

import feedparser

_log = logging.getLogger("sba.rss_reader")
_LOCK = threading.Lock()
_DATA_DIR = Path(__file__).resolve().parent / "data" / "rss"
_STORE_FILE = _DATA_DIR / "store.json"
_MAX_FEEDS = 50
_MAX_ITEMS_PER_FEED = 80
_MAX_ITEMS_TOTAL = 500
_TAG_RE = re.compile(r"<[^>]+>")
_CHAT_USER: ContextVar[str] = ContextVar("rss_chat_user", default="anonymous")


def bind_chat_user(user_id: Optional[str]) -> None:
    _CHAT_USER.set(str(user_id).strip() if user_id else "anonymous")


def resolve_user_id(explicit: Optional[str] = None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    return _CHAT_USER.get() or "anonymous"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    plain = _TAG_RE.sub(" ", unescape(text))
    return re.sub(r"\s+", " ", plain).strip()


def _load_root() -> dict[str, Any]:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _STORE_FILE.is_file():
        return {"users": {}}
    try:
        raw = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "users" in raw:
            return raw
    except Exception as ex:
        _log.warning(
            "[RSS订阅阅读-存储|rss_reader|store.json|硬编执行|读取] 解析失败; error_message=%s",
            ex,
        )
    return {"users": {}}


def _save_root(root: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STORE_FILE.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")


def _user_bucket(root: dict[str, Any], user_id: str) -> dict[str, Any]:
    users = root.setdefault("users", {})
    if user_id not in users:
        users[user_id] = {"feeds": [], "items": []}
    return users[user_id]


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ValueError("订阅地址不能为空")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https 订阅地址")
    if not parsed.netloc:
        raise ValueError("订阅地址格式无效")
    return u


def _item_key(feed_id: str, link: str, title: str) -> str:
    base = f"{feed_id}|{link or ''}|{title or ''}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _normalize_item(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["read"] = bool(out.get("read"))
    out["starred"] = bool(out.get("starred"))
    return out


def _merge_item_states(old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old_map = {i.get("id"): i for i in old_items if i.get("id")}
    merged: list[dict[str, Any]] = []
    for item in new_items:
        row = dict(item)
        prev = old_map.get(row.get("id"))
        if prev:
            row["read"] = bool(prev.get("read"))
            row["starred"] = bool(prev.get("starred"))
        else:
            row["read"] = False
            row["starred"] = False
        merged.append(row)
    return merged


def list_all_user_ids() -> list[str]:
    with _LOCK:
        root = _load_root()
        return sorted((root.get("users") or {}).keys())


def list_feeds(user_id: str) -> list[dict[str, Any]]:
    uid = resolve_user_id(user_id)
    with _LOCK:
        bucket = _user_bucket(_load_root(), uid)
        feeds = list(bucket.get("feeds") or [])
        feeds.sort(key=lambda f: f.get("title") or f.get("url") or "")
        return feeds


def list_items(
    user_id: str,
    feed_id: str = "",
    *,
    unread_only: bool = False,
    starred_only: bool = False,
    query: str = "",
) -> list[dict[str, Any]]:
    uid = resolve_user_id(user_id)
    with _LOCK:
        bucket = _user_bucket(_load_root(), uid)
        items = [_normalize_item(i) for i in (bucket.get("items") or [])]
    if feed_id:
        items = [i for i in items if i.get("feed_id") == feed_id]
    if unread_only:
        items = [i for i in items if not i.get("read")]
    if starred_only:
        items = [i for i in items if i.get("starred")]
    q = (query or "").strip().lower()
    if q:
        items = [
            i
            for i in items
            if q in (i.get("title") or "").lower() or q in (i.get("summary") or "").lower()
        ]
    items.sort(key=lambda i: i.get("published") or "", reverse=True)
    return items[: _MAX_ITEMS_TOTAL]


def add_feed(user_id: str, url: str, *, sync: bool = True) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    feed_url = _normalize_url(url)
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        feeds: list[dict[str, Any]] = bucket.setdefault("feeds", [])
        if any(f.get("url") == feed_url for f in feeds):
            raise ValueError("该订阅已存在")
        if len(feeds) >= _MAX_FEEDS:
            raise ValueError(f"订阅数量已达上限 {_MAX_FEEDS}")
        fid = str(uuid.uuid4())
        row = {
            "id": fid,
            "url": feed_url,
            "title": feed_url,
            "site_url": "",
            "last_sync": "",
            "item_count": 0,
            "error": "",
            "created_at": _now_iso(),
        }
        feeds.append(row)
        _save_root(root)
    if sync:
        return sync_feed(uid, fid)
    return row


def delete_feed(user_id: str, feed_id: str) -> bool:
    uid = resolve_user_id(user_id)
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        feeds: list[dict[str, Any]] = bucket.setdefault("feeds", [])
        before = len(feeds)
        bucket["feeds"] = [f for f in feeds if f.get("id") != feed_id]
        items: list[dict[str, Any]] = bucket.setdefault("items", [])
        bucket["items"] = [i for i in items if i.get("feed_id") != feed_id]
        ok = len(bucket["feeds"]) < before
        if ok:
            _save_root(root)
        return ok


def set_item_read(user_id: str, item_id: str, read: bool = True) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        items: list[dict[str, Any]] = bucket.setdefault("items", [])
        for idx, row in enumerate(items):
            if row.get("id") == item_id:
                updated = dict(row)
                updated["read"] = bool(read)
                items[idx] = updated
                _save_root(root)
                return _normalize_item(updated)
    raise ValueError("文章不存在")


def set_item_starred(user_id: str, item_id: str, starred: bool = True) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        items: list[dict[str, Any]] = bucket.setdefault("items", [])
        for idx, row in enumerate(items):
            if row.get("id") == item_id:
                updated = dict(row)
                updated["starred"] = bool(starred)
                items[idx] = updated
                _save_root(root)
                return _normalize_item(updated)
    raise ValueError("文章不存在")


def _parse_feed_entries(feed_row: dict[str, Any], parsed: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    feed_meta = parsed.feed if parsed else None
    title = (getattr(feed_meta, "title", None) or feed_row.get("title") or feed_row.get("url") or "").strip()
    site_url = (getattr(feed_meta, "link", None) or "").strip()
    updated = feed_row.copy()
    updated["title"] = title or feed_row.get("url", "")
    updated["site_url"] = site_url
    updated["last_sync"] = _now_iso()
    updated["error"] = ""

    items: list[dict[str, Any]] = []
    entries = list(getattr(parsed, "entries", None) or [])[: _MAX_ITEMS_PER_FEED]
    for entry in entries:
        link = (entry.get("link") or entry.get("id") or "").strip()
        entry_title = (entry.get("title") or "（无标题）").strip()
        summary_raw = entry.get("summary") or entry.get("description") or ""
        published = entry.get("published") or entry.get("updated") or ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
        items.append(
            {
                "id": _item_key(feed_row["id"], link, entry_title),
                "feed_id": feed_row["id"],
                "title": entry_title,
                "link": link,
                "summary": _strip_html(summary_raw)[:1200],
                "published": published,
                "read": False,
                "starred": False,
            }
        )
    updated["item_count"] = len(items)
    return updated, items


def sync_feed(user_id: str, feed_id: str) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        feeds: list[dict[str, Any]] = bucket.setdefault("feeds", [])
        idx = next((i for i, f in enumerate(feeds) if f.get("id") == feed_id), -1)
        if idx < 0:
            raise ValueError("订阅不存在")
        feed_row = dict(feeds[idx])
        old_items = [i for i in (bucket.get("items") or []) if i.get("feed_id") == feed_id]

    url = feed_row.get("url") or ""
    try:
        parsed = feedparser.parse(url)
        if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
            bozo_exc = getattr(parsed, "bozo_exception", None)
            raise ValueError(f"Feed 解析失败: {bozo_exc or 'unknown'}")
        updated, new_items = _parse_feed_entries(feed_row, parsed)
        new_items = _merge_item_states(old_items, new_items)
    except Exception as ex:
        err = str(ex) or ex.__class__.__name__
        with _LOCK:
            root = _load_root()
            bucket = _user_bucket(root, uid)
            feeds = bucket.setdefault("feeds", [])
            idx = next((i for i, f in enumerate(feeds) if f.get("id") == feed_id), -1)
            if idx >= 0:
                feeds[idx]["error"] = err
                feeds[idx]["last_sync"] = _now_iso()
                _save_root(root)
                feed_row = feeds[idx]
        _log.warning(
            "[RSS订阅阅读-同步|rss_reader.sync_feed|feed:%s|工具执行|抓取] 失败; user_id=%s; error_message=%s",
            feed_id,
            uid,
            err,
        )
        raise ValueError(err) from ex

    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        feeds = bucket.setdefault("feeds", [])
        idx = next((i for i, f in enumerate(feeds) if f.get("id") == feed_id), -1)
        if idx >= 0:
            feeds[idx] = updated
        items: list[dict[str, Any]] = bucket.setdefault("items", [])
        items = [i for i in items if i.get("feed_id") != feed_id]
        items.extend(new_items)
        items.sort(key=lambda i: i.get("published") or "", reverse=True)
        bucket["items"] = items[: _MAX_ITEMS_TOTAL]
        _save_root(root)
    _log.info(
        "[RSS订阅阅读-同步|rss_reader.sync_feed|feed:%s|工具执行|抓取] 完成; user_id=%s; items=%s; ok=true",
        feed_id,
        uid,
        len(new_items),
    )
    return updated


def sync_all_feeds(user_id: str) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    feeds = list_feeds(uid)
    ok = 0
    failed: list[dict[str, str]] = []
    for f in feeds:
        fid = f.get("id") or ""
        if not fid:
            continue
        try:
            sync_feed(uid, fid)
            ok += 1
        except Exception as ex:
            failed.append({"feed_id": fid, "title": f.get("title") or "", "error": str(ex)})
    return {"ok_count": ok, "fail_count": len(failed), "failed": failed, "total": len(feeds)}


def sync_all_users_feeds(*, trigger: str = "manual") -> dict[str, Any]:
    user_ids = list_all_user_ids()
    ok = 0
    fail = 0
    for uid in user_ids:
        result = sync_all_feeds(uid)
        ok += int(result.get("ok_count") or 0)
        fail += int(result.get("fail_count") or 0)
    return {
        "trigger": trigger,
        "user_count": len(user_ids),
        "ok_count": ok,
        "fail_count": fail,
    }


def rss_stats(user_id: str) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    feeds = list_feeds(uid)
    items = list_items(uid)
    unread = sum(1 for i in items if not i.get("read"))
    starred = sum(1 for i in items if i.get("starred"))
    last_sync = max((f.get("last_sync") or "" for f in feeds), default="")
    return {
        "feed_count": len(feeds),
        "item_count": len(items),
        "unread_count": unread,
        "starred_count": starred,
        "last_sync": last_sync,
    }


def _collect_opml_urls(node: ET.Element, acc: list[dict[str, str]]) -> None:
    xml_url = (node.attrib.get("xmlUrl") or node.attrib.get("xmlurl") or "").strip()
    if xml_url:
        acc.append({"url": xml_url, "title": (node.attrib.get("title") or node.attrib.get("text") or xml_url).strip()})
    for child in list(node):
        if child.tag.lower().endswith("outline"):
            _collect_opml_urls(child, acc)


def import_opml(user_id: str, content: str) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    text = (content or "").strip()
    if not text:
        raise ValueError("OPML 内容为空")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as ex:
        raise ValueError(f"OPML 解析失败: {ex}") from ex
    outlines: list[dict[str, str]] = []
    body = root.find("body")
    if body is not None:
        for node in body.findall("outline"):
            _collect_opml_urls(node, outlines)
    else:
        _collect_opml_urls(root, outlines)
    if not outlines:
        raise ValueError("OPML 中未找到 xmlUrl 订阅")

    added = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    existing = {f.get("url") for f in list_feeds(uid)}
    for row in outlines:
        url = row.get("url") or ""
        try:
            if url in existing:
                skipped += 1
                continue
            add_feed(uid, url, sync=True)
            existing.add(url)
            added += 1
        except Exception as ex:
            errors.append({"url": url, "error": str(ex)})
    return {"added": added, "skipped": skipped, "errors": errors, "total": len(outlines)}


def export_opml(user_id: str) -> str:
    uid = resolve_user_id(user_id)
    feeds = list_feeds(uid)
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "SuperBizAgent RSS Subscriptions"
    ET.SubElement(head, "dateCreated").text = _now_iso()
    body = ET.SubElement(opml, "body")
    for feed in feeds:
        ET.SubElement(
            body,
            "outline",
            {
                "text": feed.get("title") or feed.get("url") or "",
                "title": feed.get("title") or feed.get("url") or "",
                "type": "rss",
                "xmlUrl": feed.get("url") or "",
                "htmlUrl": feed.get("site_url") or "",
            },
        )
    rough = ET.tostring(opml, encoding="unicode")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")


def build_chat_context_block(
    user_id: str,
    *,
    limit: int = 12,
    query: str = "",
    starred_only: bool = False,
    unread_only: bool = False,
) -> str:
    uid = resolve_user_id(user_id)
    items = list_items(
        uid,
        query=query,
        starred_only=starred_only,
        unread_only=unread_only,
    )[: max(1, min(int(limit or 12), 30))]
    if not items:
        return ""
    feed_titles = {f.get("id"): f.get("title") or "" for f in list_feeds(uid)}
    lines = [
        "【RSS 订阅 · 近期文章】",
        "以下为当前用户 RSS 阅读器中的真实条目，回答时须基于这些内容，勿编造未出现的标题或链接。",
        "",
    ]
    for idx, it in enumerate(items, 1):
        feed_name = feed_titles.get(it.get("feed_id") or "", "")
        mark = []
        if it.get("starred"):
            mark.append("星标")
        if it.get("read"):
            mark.append("已读")
        suffix = f" ({', '.join(mark)})" if mark else ""
        lines.append(f"{idx}. [{feed_name}] {it.get('title')}{suffix}")
        pub = (it.get("published") or "")[:10]
        if pub:
            lines.append(f"   时间: {pub}")
        summary = (it.get("summary") or "").strip()
        if summary:
            lines.append(f"   摘要: {summary[:260]}")
        link = (it.get("link") or "").strip()
        if link:
            lines.append(f"   链接: {link}")
        lines.append("")
    return "\n".join(lines).strip()


def rss_list_for_tool(
    *,
    limit: int = 10,
    query: str = "",
    starred_only: bool = False,
    unread_only: bool = False,
) -> dict[str, Any]:
    uid = resolve_user_id(None)
    items = list_items(
        uid,
        query=query,
        starred_only=starred_only,
        unread_only=unread_only,
    )[: max(1, min(int(limit or 10), 30))]
    return {
        "ok": True,
        "user_id": uid,
        "count": len(items),
        "items": items,
        "stats": rss_stats(uid),
    }


def _find_item_row(user_id: str, item_id: str) -> tuple[dict[str, Any], dict[str, Any], int]:
    uid = resolve_user_id(user_id)
    iid = (item_id or "").strip()
    if not iid:
        raise ValueError("缺少文章 ID")
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        items: list[dict[str, Any]] = bucket.setdefault("items", [])
        for idx, row in enumerate(items):
            if row.get("id") == iid:
                return root, dict(row), idx
    raise ValueError("文章不存在")


def get_item_by_id(user_id: str, item_id: str) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    with _LOCK:
        bucket = _user_bucket(_load_root(), uid)
        feeds = {f.get("id"): f for f in (bucket.get("feeds") or [])}
    _, row, _ = _find_item_row(uid, item_id)
    out = _normalize_item(row)
    feed = feeds.get(out.get("feed_id") or "") or {}
    out["feed_title"] = feed.get("title") or feed.get("url") or ""
    return out


def attach_item_document(
    user_id: str,
    item_id: str,
    *,
    doc_path: str,
    doc_filename: str,
    task_id: str = "",
) -> dict[str, Any]:
    uid = resolve_user_id(user_id)
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, uid)
        items: list[dict[str, Any]] = bucket.setdefault("items", [])
        idx = next((i for i, r in enumerate(items) if r.get("id") == item_id), -1)
        if idx < 0:
            raise ValueError("文章不存在")
        updated = dict(items[idx])
        updated["doc_path"] = (doc_path or "").strip()
        updated["doc_filename"] = (doc_filename or "").strip()
        updated["doc_task_id"] = (task_id or "").strip()
        updated["doc_status"] = "completed"
        items[idx] = updated
        _save_root(root)
        return _normalize_item(updated)


def enqueue_item_document(
    user_id: str,
    item_id: str,
    *,
    user_prompt: str = "",
) -> dict[str, Any]:
    """将 RSS 条目提交到链接沉淀流水线（同链接复用任务卡片）。"""
    item = get_item_by_id(user_id, item_id)
    link = (item.get("link") or "").strip()
    if not link:
        raise ValueError("该文章无原文链接，无法沉淀")

    from .task_manager import reuse_or_enqueue_task

    task_id, reused = reuse_or_enqueue_task(
        "RSS",
        link,
        user_prompt=(user_prompt or "").strip(),
        pipeline_route="rss_article",
        action="start",
    )
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, resolve_user_id(user_id))
        items: list[dict[str, Any]] = bucket.setdefault("items", [])
        for idx, row in enumerate(items):
            if row.get("id") == item_id:
                updated = dict(row)
                updated["doc_task_id"] = task_id
                updated["doc_status"] = "running"
                items[idx] = updated
                _save_root(root)
                break

    _log.info(
        "[RSS订阅阅读-沉淀|rss_reader.enqueue_item_document|item:%s|硬编执行|入队] 已提交; task_id=%s; reused=%s; link=%s",
        item_id,
        task_id,
        reused,
        link[:120],
    )
    return {
        "task_id": task_id,
        "reused": reused,
        "link": link,
        "item_id": item_id,
        "rss_item_id": item_id,
        "title": item.get("title") or "",
    }


def message_wants_rss_context(message: str) -> bool:
    q = (message or "").strip().lower()
    if not q:
        return False
    hints = (
        "rss", "feed", "订阅", "资讯", "新闻源", "阅读器", "未读", "星标",
        "订阅源", "文章列表", "最新文章",
    )
    return any(h in q for h in hints)
