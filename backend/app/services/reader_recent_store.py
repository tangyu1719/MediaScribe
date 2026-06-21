"""文本阅读器「最近打开」列表：按 output 文件 mtime 排序，服务端全量持久化。"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .output_file_io import output_file_mtime_ms, safe_output_basename

_log = logging.getLogger("sba.reader_recent")
_LOCK = threading.Lock()
_DATA_DIR = Path(__file__).resolve().parent / "data" / "reader_recent"
_STORE_FILE = _DATA_DIR / "store.json"
RECENT_MAX = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            "[文本阅读-最近打开|reader_recent_store|store.json|硬编执行|读取] 解析失败; error_message=%s",
            ex,
        )
    return {"users": {}}


def _save_root(root: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STORE_FILE.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")


def _user_items(root: dict[str, Any], user_id: str) -> List[dict[str, Any]]:
    users = root.setdefault("users", {})
    bucket = users.setdefault(user_id, {"items": [], "updated_at": _now_iso()})
    items = bucket.get("items")
    return items if isinstance(items, list) else []


def _norm_item(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("file") or "").strip()
    file_name = str(raw.get("file") or name).strip()
    if not file_name:
        return None
    try:
        base = safe_output_basename(file_name)
    except ValueError:
        return None
    mtime = int(raw.get("mtime") or 0)
    opened_at = int(raw.get("opened_at") or mtime or 0)
    disk_mtime = output_file_mtime_ms(base)
    if disk_mtime is not None:
        mtime = disk_mtime
    elif mtime <= 0:
        mtime = int(time.time() * 1000)
    return {
        "id": f"out:{base}",
        "name": name or base,
        "file": base,
        "source": "output",
        "mtime": mtime,
        "opened_at": opened_at or mtime,
    }


def _merge_items(*groups: List[Any]) -> List[dict[str, Any]]:
    merged: Dict[str, dict[str, Any]] = {}
    for group in groups:
        for raw in group or []:
            row = _norm_item(raw)
            if not row:
                continue
            key = row["file"].lower()
            prev = merged.get(key)
            if not prev:
                merged[key] = row
                continue
            row["opened_at"] = max(int(prev.get("opened_at") or 0), int(row.get("opened_at") or 0))
            row["mtime"] = max(int(prev.get("mtime") or 0), int(row.get("mtime") or 0))
            disk_mtime = output_file_mtime_ms(row["file"])
            if disk_mtime is not None:
                row["mtime"] = disk_mtime
            merged[key] = row
    out = list(merged.values())
    out.sort(key=lambda x: int(x.get("mtime") or 0), reverse=True)
    return out[:RECENT_MAX]


def list_recent(user_id: str) -> List[dict[str, Any]]:
    with _LOCK:
        root = _load_root()
        items = _user_items(root, user_id)
        merged = _merge_items(items)
        if merged != items:
            users = root.setdefault("users", {})
            users[user_id] = {"items": merged, "updated_at": _now_iso()}
            _save_root(root)
        return merged


def touch_recent(user_id: str, file_name: str, *, opened_at: Optional[int] = None) -> List[dict[str, Any]]:
    base = safe_output_basename((file_name or "").strip())
    disk_mtime = output_file_mtime_ms(base)
    if disk_mtime is None:
        raise FileNotFoundError(f"文件不存在: {base}")
    now_ms = int(time.time() * 1000)
    entry = {
        "id": f"out:{base}",
        "name": base,
        "file": base,
        "source": "output",
        "mtime": disk_mtime,
        "opened_at": int(opened_at or now_ms),
    }
    with _LOCK:
        root = _load_root()
        items = _user_items(root, user_id)
        merged = _merge_items(items, [entry])
        users = root.setdefault("users", {})
        users[user_id] = {"items": merged, "updated_at": _now_iso()}
        _save_root(root)
        _log.info(
            "[文本阅读-最近打开|reader_recent_store.touch_recent|%s|硬编执行|写入] ok=true; user_id=%s; total=%s",
            base,
            user_id,
            len(merged),
        )
        return merged


def replace_recent(user_id: str, items: List[Any]) -> List[dict[str, Any]]:
    with _LOCK:
        root = _load_root()
        existing = _user_items(root, user_id)
        merged = _merge_items(existing, items if isinstance(items, list) else [])
        users = root.setdefault("users", {})
        users[user_id] = {"items": merged, "updated_at": _now_iso()}
        _save_root(root)
        _log.info(
            "[文本阅读-最近打开|reader_recent_store.replace_recent|recent|硬编执行|合并] ok=true; user_id=%s; total=%s",
            user_id,
            len(merged),
        )
        return merged


def stat_recent_file(file_name: str) -> dict[str, Any]:
    base = safe_output_basename((file_name or "").strip())
    mtime = output_file_mtime_ms(base)
    if mtime is None:
        raise FileNotFoundError(f"文件不存在: {base}")
    return {"ok": True, "file": base, "mtime": mtime}
