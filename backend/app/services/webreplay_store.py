"""WebReplay 脚本库与扩展桥接配置（服务端存储，供前端管理）。"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("sba.webreplay_store")
_LOCK = threading.Lock()
_DATA_DIR = Path(__file__).resolve().parent / "data" / "webreplay"
_STORE_FILE = _DATA_DIR / "store.json"


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
        _log.warning("[浏览器自动化-脚本库|webreplay_store|store.json|硬编执行|读取] 解析失败; error_message=%s", ex)
    return {"users": {}}


def _save_root(root: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STORE_FILE.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")


def _user_bucket(root: dict[str, Any], user_id: str) -> dict[str, Any]:
    users = root.setdefault("users", {})
    if user_id not in users:
        users[user_id] = {"scripts": [], "bridge": {"extensionId": ""}, "runs": []}
    return users[user_id]


def list_scripts(user_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, user_id)
        return list(bucket.get("scripts") or [])


def get_script(user_id: str, script_id: str) -> Optional[dict[str, Any]]:
    for s in list_scripts(user_id):
        if s.get("id") == script_id:
            return s
    return None


def upsert_script(user_id: str, script: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, user_id)
        scripts: list[dict[str, Any]] = bucket.setdefault("scripts", [])
        sid = script.get("id") or str(uuid.uuid4())
        now = _now_iso()
        row = {**script, "id": sid, "updatedAt": now}
        if not row.get("createdAt"):
            row["createdAt"] = now
        idx = next((i for i, x in enumerate(scripts) if x.get("id") == sid), -1)
        if idx >= 0:
            row["createdAt"] = scripts[idx].get("createdAt") or now
            scripts[idx] = row
        else:
            scripts.append(row)
        _save_root(root)
        _log.info(
            "[浏览器自动化-脚本库|webreplay_store.upsert_script|script:%s|硬编执行|写入] 已保存; user_id=%s; steps=%s",
            sid,
            user_id,
            len(row.get("steps") or []),
        )
        return row


def delete_script(user_id: str, script_id: str) -> bool:
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, user_id)
        scripts: list[dict[str, Any]] = bucket.setdefault("scripts", [])
        before = len(scripts)
        bucket["scripts"] = [s for s in scripts if s.get("id") != script_id]
        ok = len(bucket["scripts"]) < before
        if ok:
            _save_root(root)
        return ok


def import_scripts(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    incoming = payload.get("scripts")
    if not isinstance(incoming, list):
        raise ValueError("payload.scripts 必须为数组")
    merged = 0
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, user_id)
        scripts: list[dict[str, Any]] = bucket.setdefault("scripts", [])
        by_id = {s.get("id"): s for s in scripts if s.get("id")}
        for item in incoming:
            if not isinstance(item, dict):
                continue
            sid = item.get("id") or str(uuid.uuid4())
            item = {**item, "id": sid, "updatedAt": _now_iso()}
            if sid in by_id:
                item["createdAt"] = by_id[sid].get("createdAt") or _now_iso()
            else:
                item.setdefault("createdAt", _now_iso())
            by_id[sid] = item
            merged += 1
        bucket["scripts"] = list(by_id.values())
        _save_root(root)
    return {"ok": True, "imported": merged, "total": len(by_id)}


def export_scripts(user_id: str) -> dict[str, Any]:
    scripts = list_scripts(user_id)
    return {"scripts": scripts, "exportedAt": _now_iso()}


def get_bridge(user_id: str) -> dict[str, Any]:
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, user_id)
        bridge = bucket.get("bridge") or {}
        return {
            "extensionId": str(bridge.get("extensionId") or ""),
            "origin": str(bridge.get("origin") or ""),
            "updatedAt": bridge.get("updatedAt"),
        }


def save_bridge(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, user_id)
        bridge = {
            "extensionId": str(body.get("extensionId") or "").strip(),
            "origin": str(body.get("origin") or "").strip(),
            "updatedAt": _now_iso(),
        }
        bucket["bridge"] = bridge
        _save_root(root)
        return get_bridge(user_id)


def append_run(user_id: str, script_id: str, record: dict[str, Any]) -> None:
    with _LOCK:
        root = _load_root()
        bucket = _user_bucket(root, user_id)
        runs: list[dict[str, Any]] = bucket.setdefault("runs", [])
        runs.append({**record, "scriptId": script_id, "at": _now_iso()})
        if len(runs) > 200:
            bucket["runs"] = runs[-200:]
        scripts: list[dict[str, Any]] = bucket.setdefault("scripts", [])
        for i, s in enumerate(scripts):
            if s.get("id") == script_id:
                script_runs = list(s.get("runs") or [])
                script_runs.append(record)
                scripts[i] = {**s, "runs": script_runs[-50:], "updatedAt": _now_iso()}
                break
        _save_root(root)
