"""订阅链接简化卡片 — Redis 热存（完成后再异步写 MySQL，当前仅 Redis/本地回退）。"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("sba.sub_link_card")

_HERE = Path(__file__).resolve()
_LOCAL_ROOT = _HERE.parent / "data" / "sub_link_cards"
_PREFIX = "sb:sub:link_card"
_INDEX_PREFIX = "sb:sub:link_cards"
_TTL_SEC = 30 * 24 * 3600
_lock = threading.RLock()
_redis_client: Any = None
_redis_error = ""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _init_redis() -> None:
    global _redis_client, _redis_error
    if _redis_client is not None:
        return
    root = _HERE.parents[3]
    cfg: Dict[str, Any] = {}
    for cp in [root / "config.json", root.parent / "src" / "agent" / "config.json"]:
        if cp.exists():
            try:
                cfg = json.loads(cp.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    if not cfg.get("redis_cache_enabled", False):
        _redis_error = "redis_cache_enabled=false"
        return
    url = str(cfg.get("redis_url", "redis://127.0.0.1:6379/0")).strip()
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        _redis_client = client
        _redis_error = ""
    except Exception as ex:
        _redis_client = None
        _redis_error = f"{type(ex).__name__}: {ex}"


def _card_key(subscription_id: str, url_hash: str) -> str:
    return f"{_PREFIX}:{subscription_id}:{url_hash}"


def _index_key(subscription_id: str) -> str:
    return f"{_INDEX_PREFIX}:{subscription_id}"


def _local_path(subscription_id: str, url_hash: str) -> Path:
    d = _LOCAL_ROOT / subscription_id
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in url_hash)
    return d / f"{safe}.json"


def _artifact_status_from_task(task: Dict[str, Any]) -> Dict[str, str]:
    stages = task.get("pipeline_stages") or {}

    def _st(sid: str, path_key: str, url_key: str = "") -> str:
        if task.get(path_key) or task.get(url_key):
            return "ready"
        row = stages.get(sid) or {}
        st = str(row.get("status") or "")
        if st == "completed":
            return "ready"
        if st == "failed":
            return "failed"
        if st == "in_progress":
            return "running"
        return "off"

    return {
        "md_status": _st("generate_md", "doc_path"),
        "html_status": _st("html", "html_path"),
        "feishu_status": _st("feishu_upload", "feishu_doc_url", "feishu_doc_token"),
    }


def _score_from_card(card: Dict[str, Any]) -> float:
    pub = card.get("published_at") or card.get("updated_at") or card.get("created_at") or ""
    try:
        return datetime.fromisoformat(str(pub).replace("Z", "")).timestamp()
    except Exception:
        return time.time()


def _persist_card(card: Dict[str, Any]) -> None:
    sid = str(card.get("subscription_id") or "")
    uh = str(card.get("url_hash") or "")
    if not sid or not uh:
        return
    payload = json.dumps(card, ensure_ascii=False, default=str)
    _init_redis()
    if _redis_client is not None:
        try:
            key = _card_key(sid, uh)
            _redis_client.setex(key, _TTL_SEC, payload)
            score = _score_from_card(card)
            _redis_client.zadd(_index_key(sid), {uh: score})
            return
        except Exception as ex:
            _log.warning(
                "[社媒订阅-链接卡片|subscription_link_card_store._persist_card|redis|硬编执行|失败] "
                "error_type=%s; error_message=%s",
                type(ex).__name__,
                str(ex)[:120],
            )
    try:
        _local_path(sid, uh).write_text(payload, encoding="utf-8")
    except Exception:
        pass


def _load_card(subscription_id: str, url_hash: str) -> Optional[Dict[str, Any]]:
    _init_redis()
    if _redis_client is not None:
        try:
            raw = _redis_client.get(_card_key(subscription_id, url_hash))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    p = _local_path(subscription_id, url_hash)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def upsert_link_card(
    *,
    subscription_id: str,
    platform: str,
    note_id: str,
    canonical_url: str,
    url_hash: str,
    title: str = "",
    content_type: str = "",
    published_at: str = "",
    task_id: Optional[str] = None,
    analysis_status: str = "pending",
    task_note: str = "",
    task: Optional[Dict[str, Any]] = None,
    error_message: str = "",
    author_name: str = "",
    author_id: str = "",
    import_source: str = "",
    source_label: str = "",
) -> Dict[str, Any]:
    """写入/更新订阅简化卡片。"""
    uh = (url_hash or "").strip()
    if not uh or not subscription_id:
        return {}
    prev = _load_card(subscription_id, uh) or {}
    card: Dict[str, Any] = {
        "subscription_id": subscription_id,
        "platform": platform or prev.get("platform") or "",
        "note_id": note_id or prev.get("note_id") or "",
        "canonical_url": canonical_url or prev.get("canonical_url") or "",
        "url_hash": uh,
        "title": title or prev.get("title") or "",
        "content_type": content_type or prev.get("content_type") or "",
        "published_at": published_at or prev.get("published_at") or "",
        "task_id": task_id or prev.get("task_id") or "",
        "analysis_status": analysis_status or prev.get("analysis_status") or "pending",
        "task_note": task_note or prev.get("task_note") or "",
        "error_message": error_message or prev.get("error_message") or "",
        "created_at": prev.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "md_status": prev.get("md_status") or "off",
        "html_status": prev.get("html_status") or "off",
        "feishu_status": prev.get("feishu_status") or "off",
        "doc_path": prev.get("doc_path") or "",
        "html_path": prev.get("html_path") or "",
        "feishu_doc_url": prev.get("feishu_doc_url") or "",
        "mysql_synced": bool(prev.get("mysql_synced")),
        "author_name": author_name or prev.get("author_name") or "",
        "author_id": author_id or prev.get("author_id") or "",
        "import_source": import_source or prev.get("import_source") or "",
        "source_label": source_label or prev.get("source_label") or "",
    }
    if task:
        card["task_id"] = str(task.get("task_id") or card.get("task_id") or "")
        card["title"] = card["title"] or task.get("link_title") or task.get("doc_title") or ""
        card["task_note"] = str(task.get("task_note") or card.get("task_note") or "")
        card["analysis_status"] = str(task.get("status") or card.get("analysis_status") or "")
        card["doc_path"] = task.get("doc_path") or card.get("doc_path") or ""
        card["html_path"] = task.get("html_path") or card.get("html_path") or ""
        card["feishu_doc_url"] = task.get("feishu_doc_url") or card.get("feishu_doc_url") or ""
        card["author_name"] = str(task.get("author_name") or card.get("author_name") or "")
        card["author_id"] = str(task.get("author_id") or card.get("author_id") or "")
        card["import_source"] = str(task.get("import_source") or card.get("import_source") or "")
        card["source_label"] = str(task.get("source_label") or card.get("source_label") or "")
        arts = _artifact_status_from_task(task)
        card.update(arts)
        if (task.get("status") or "") == "completed":
            schedule_mysql_promote(card)
    with _lock:
        _persist_card(card)
    return card


def sync_link_card_from_task(subscription_id: str, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not subscription_id or not task:
        return None
    uh = str(task.get("url_hash") or "").strip()
    if not uh:
        return None
    return upsert_link_card(
        subscription_id=subscription_id,
        platform=str(task.get("platform") or ""),
        note_id="",
        canonical_url=str(task.get("link") or ""),
        url_hash=uh,
        title=str(task.get("link_title") or task.get("doc_title") or ""),
        task_id=str(task.get("task_id") or ""),
        analysis_status=str(task.get("status") or ""),
        task_note=str(task.get("task_note") or ""),
        task=task,
        error_message=str(task.get("error") or ""),
        author_name=str(task.get("author_name") or ""),
        author_id=str(task.get("author_id") or ""),
        import_source=str(task.get("import_source") or ""),
        source_label=str(task.get("source_label") or ""),
    )


def schedule_mysql_promote(card: Dict[str, Any]) -> None:
    """完成态卡片预留 MySQL 落盘（当前仅标记，异步任务后续接入）。"""
    if card.get("mysql_synced"):
        return
    card["mysql_sync_pending"] = True
    _log.info(
        "[社媒订阅-链接卡片|subscription_link_card_store.schedule_mysql_promote|card|硬编执行|排队] "
        "subscription_id=%s; url_hash=%s; task_id=%s",
        card.get("subscription_id"),
        (card.get("url_hash") or "")[:12],
        card.get("task_id"),
    )


def list_link_cards(
    subscription_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 20)), 100)
    _init_redis()
    hashes: List[str] = []
    if _redis_client is not None:
        try:
            start = (page - 1) * page_size
            end = start + page_size - 1
            hashes = list(
                _redis_client.zrevrange(_index_key(subscription_id), start, end)
            )
            total = int(_redis_client.zcard(_index_key(subscription_id)) or 0)
        except Exception:
            hashes = []
            total = 0
    else:
        d = _LOCAL_ROOT / subscription_id
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []
        total = len(files)
        start = (page - 1) * page_size
        hashes = [json.loads(p.read_text(encoding="utf-8")).get("url_hash", p.stem) for p in files[start:start + page_size]]

    items: List[Dict[str, Any]] = []
    if _redis_client is not None and hashes:
        try:
            keys = [_card_key(subscription_id, str(uh)) for uh in hashes if uh]
            for raw in _redis_client.mget(keys) if keys else []:
                if not raw:
                    continue
                try:
                    items.append(json.loads(raw))
                except Exception:
                    pass
        except Exception:
            items = []
    if not items:
        for uh in hashes:
            if not uh:
                continue
            card = _load_card(subscription_id, str(uh))
            if card:
                items.append(card)
    if _redis_client is None:
        total = len(list((_LOCAL_ROOT / subscription_id).glob("*.json")) if (_LOCAL_ROOT / subscription_id).is_dir() else [])
    return {
        "ok": True,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "storage": "redis" if _redis_client is not None else "local",
    }


def rebuild_index_from_local(subscription_id: str) -> int:
    """启动时从本地 JSON 回填 Redis 索引（可选）。"""
    _init_redis()
    if _redis_client is None:
        return 0
    d = _LOCAL_ROOT / subscription_id
    if not d.is_dir():
        return 0
    n = 0
    for p in d.glob("*.json"):
        try:
            card = json.loads(p.read_text(encoding="utf-8"))
            uh = str(card.get("url_hash") or p.stem)
            if uh:
                _persist_card(card)
                n += 1
        except Exception:
            pass
    return n
