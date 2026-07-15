"""阅读器 Agent 会话存储：内存为主，JSON 落盘（一文档一会话）。"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger("sba.reader_session")
_ROOT = Path(__file__).resolve().parents[2]
_STORE_DIR = _ROOT / "data" / "reader_sessions"
_LOCK = threading.RLock()
_MEM: Dict[str, Dict[str, Any]] = {}
_DIRTY: set[str] = set()
_LAST_FLUSH_TS = 0.0
_FLUSH_INTERVAL_SEC = 600.0


def _ensure_dir() -> None:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)


def _path(doc_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (doc_id or "unknown"))
    return _STORE_DIR / f"{safe}.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stable_doc_id_from_name(doc_name: str) -> str:
    """与前端 reader_agent.js 一致：按 output 文件名稳定生成 doc_id。"""
    name = (doc_name or "").strip()
    if not name:
        return "unknown"
    base = Path(name).name
    raw = f"sba-reader-doc|{base}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _empty_session(doc_id: str, doc_name: str = "") -> Dict[str, Any]:
    return {
        "doc_id": doc_id,
        "doc_name": doc_name,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "messages": [],
        "prefs": {
            "rag_prefetch": False,
            "web_search": False,
            "deep_think": False,
        },
    }


def load_from_disk(doc_id: str) -> Optional[Dict[str, Any]]:
    p = _path(doc_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as ex:
        _LOG.warning(
            "[文本阅读-会话|reader_session_store.load_from_disk|%s|硬编执行|读取] 失败; error=%s",
            doc_id,
            ex,
        )
        return None


def find_best_session_by_doc_name(doc_name: str) -> Optional[Dict[str, Any]]:
    """按 doc_name 在落盘目录中找消息最多的会话（兼容旧 content-hash doc_id）。"""
    name = Path((doc_name or "").strip()).name
    if not name:
        return None
    best: Optional[Dict[str, Any]] = None
    best_n = -1
    _ensure_dir()
    for p in _STORE_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            if Path(str(data.get("doc_name") or "")).name != name:
                continue
            n = len(data.get("messages") or [])
            if n > best_n:
                best_n = n
                best = data
        except Exception:
            continue
    return best


def lookup_session_for_file(doc_name: str) -> Dict[str, Any]:
    """打开 MD 时解析会话：优先文件名稳定 id，必要时从旧 id 迁移。"""
    base = Path((doc_name or "").strip()).name
    if not base:
        return _empty_session("unknown", doc_name)
    primary_id = stable_doc_id_from_name(base)
    row = get_session(primary_id, doc_name=base)
    msgs = row.get("messages") or []
    if msgs:
        return row
    legacy = find_best_session_by_doc_name(base)
    if not legacy or not (legacy.get("messages") or []):
        return row
    legacy_id = str(legacy.get("doc_id") or "").strip()
    if legacy_id and legacy_id != primary_id:
        upsert_session(
            primary_id,
            doc_name=base,
            messages=legacy.get("messages") or [],
            prefs=legacy.get("prefs") if isinstance(legacy.get("prefs"), dict) else None,
        )
        flush_session(primary_id)
        _LOG.info(
            "[文本阅读-会话|reader_session_store.lookup_session_for_file|%s|硬编执行|迁移] "
            "ok=true; from=%s; messages=%s",
            base,
            legacy_id,
            len(legacy.get("messages") or []),
        )
        return get_session(primary_id, doc_name=base)
    return legacy


def get_session(doc_id: str, *, doc_name: str = "") -> Dict[str, Any]:
    did = (doc_id or "").strip()
    if not did:
        return _empty_session("unknown", doc_name)
    with _LOCK:
        if did in _MEM:
            row = dict(_MEM[did])
            if doc_name and not row.get("doc_name"):
                row["doc_name"] = doc_name
            return row
        disk = load_from_disk(did)
        if disk:
            _MEM[did] = disk
            return dict(disk)
        row = _empty_session(did, doc_name)
        _MEM[did] = row
        return dict(row)


def upsert_session(
    doc_id: str,
    *,
    doc_name: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    prefs: Optional[Dict[str, Any]] = None,
    mark_dirty: bool = True,
) -> Dict[str, Any]:
    did = (doc_id or "").strip()
    if not did:
        raise ValueError("doc_id 不能为空")
    with _LOCK:
        row = get_session(did, doc_name=doc_name or "")
        if doc_name:
            row["doc_name"] = doc_name
        if messages is not None:
            row["messages"] = messages
        if prefs and isinstance(prefs, dict):
            base = dict(row.get("prefs") or {})
            base.update({k: v for k, v in prefs.items() if k in ("rag_prefetch", "web_search", "deep_think")})
            row["prefs"] = base
        row["updated_at"] = _now_iso()
        _MEM[did] = row
        if mark_dirty:
            _DIRTY.add(did)
        return dict(row)


def flush_session(doc_id: str) -> bool:
    did = (doc_id or "").strip()
    if not did:
        return False
    with _LOCK:
        row = _MEM.get(did)
        if not row:
            row = load_from_disk(did)
            if not row:
                return False
        try:
            _ensure_dir()
            _path(did).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            _DIRTY.discard(did)
            _LOG.info(
                "[文本阅读-会话|reader_session_store.flush_session|%s|硬编执行|落盘] ok=true; messages=%s",
                did,
                len(row.get("messages") or []),
            )
            return True
        except Exception as ex:
            _LOG.warning(
                "[文本阅读-会话|reader_session_store.flush_session|%s|硬编执行|落盘] ok=false; error=%s",
                did,
                ex,
            )
            return False


def flush_all_dirty() -> int:
    global _LAST_FLUSH_TS
    with _LOCK:
        ids = list(_DIRTY)
    n = 0
    for did in ids:
        if flush_session(did):
            n += 1
    _LAST_FLUSH_TS = time.time()
    return n


def maybe_periodic_flush() -> int:
    if time.time() - _LAST_FLUSH_TS < _FLUSH_INTERVAL_SEC:
        return 0
    return flush_all_dirty()
