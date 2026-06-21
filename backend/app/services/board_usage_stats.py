"""能力看板使用热度：记录 SKILL/MCP 被 Agent 挂载与工具调用次数。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_LOG = logging.getLogger("sba.board_usage")

_HERE = Path(__file__).resolve()
_BASE: Path | None = None
for _p in _HERE.parents:
    if (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _BASE = _p
        break
if _BASE is None:
    _BASE = _HERE.parents[3]

_STATS_FILE = _BASE / "data" / "board_usage_stats.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_raw() -> Dict[str, Any]:
    if not _STATS_FILE.exists():
        return {"items": {}}
    try:
        data = json.loads(_STATS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return data
    except Exception:
        pass
    return {"items": {}}


def _save_raw(data: Dict[str, Any]) -> None:
    _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    mount = int(row.get("mount_count") or 0)
    invoke = int(row.get("invoke_count") or 0)
    total = int(row.get("total_count") or mount + invoke)
    return {
        "mount_count": mount,
        "invoke_count": invoke,
        "total_count": total,
        "last_used_at": row.get("last_used_at") or "",
        "first_used_at": row.get("first_used_at") or "",
    }


def get_stats_map() -> Dict[str, Dict[str, Any]]:
    items = _load_raw().get("items") or {}
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(items, dict):
        for k, v in items.items():
            if isinstance(v, dict):
                out[str(k)] = _normalize_row(v)
    return out


def get_item_stats(key: str) -> Dict[str, Any]:
    row = get_stats_map().get(key)
    if not row:
        return {
            "mount_count": 0,
            "invoke_count": 0,
            "total_count": 0,
            "last_used_at": "",
            "first_used_at": "",
        }
    return dict(row)


def record_usage(key: str, *, event: str = "invoke") -> None:
    """event: mount（/command 挂载）| invoke（Agent 工具调用）。"""
    k = (key or "").strip()
    if not k:
        return
    ev = (event or "invoke").strip().lower()
    if ev not in ("mount", "invoke"):
        ev = "invoke"
    data = _load_raw()
    items: Dict[str, Any] = dict(data.get("items") or {})
    row = dict(items.get(k) or {})
    now = _now_iso()
    if ev == "mount":
        row["mount_count"] = int(row.get("mount_count") or 0) + 1
    else:
        row["invoke_count"] = int(row.get("invoke_count") or 0) + 1
    row["total_count"] = int(row.get("mount_count") or 0) + int(row.get("invoke_count") or 0)
    row["last_used_at"] = now
    if not row.get("first_used_at"):
        row["first_used_at"] = now
    items[k] = row
    data["items"] = items
    data["updated_at"] = now
    try:
        _save_raw(data)
    except Exception as e:
        _LOG.warning(
            "[能力看板-使用统计|board_usage_stats.record_usage|%s|硬编执行|写入] 失败; error_type=%s; error_message=%s",
            k,
            type(e).__name__,
            str(e)[:200],
        )


def record_skill_mount(skill_id: str) -> None:
    sid = (skill_id or "").strip()
    if sid:
        record_usage(f"skill:{sid}", event="mount")


def record_skill_invoke(skill_id: str) -> None:
    sid = (skill_id or "").strip()
    if sid:
        record_usage(f"skill:{sid}", event="invoke")


def record_mcp_invoke(tool_name: str, *, server: str = "") -> None:
    name = (tool_name or "").strip()
    if not name:
        return
    srv = (server or "").strip()
    key = f"mcp:{srv}:{name}" if srv else f"mcp:{name}"
    record_usage(key, event="invoke")


def resolve_skill_id_from_tool_name(tool_name: str) -> Optional[str]:
    """skill_{id_prefix} → 完整 skill_id。"""
    fn = (tool_name or "").strip()
    if not fn.startswith("skill_"):
        return None
    prefix = fn[6:]
    if not prefix:
        return None
    try:
        from .skill_registry import list_skills

        for s in list_skills():
            sid = str(s.get("id") or "")
            if sid.startswith(prefix):
                return sid
    except Exception:
        pass
    return None


def record_tool_usage_by_name(tool_name: str, *, event: str = "invoke", server: str = "") -> None:
    sid = resolve_skill_id_from_tool_name(tool_name)
    if sid:
        record_skill_invoke(sid) if event == "invoke" else record_skill_mount(sid)
        return
    fn = (tool_name or "").strip()
    if fn and not fn.startswith("skill_"):
        record_mcp_invoke(fn, server=server)


def enrich_skill_meta(skill: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(skill.get("id") or "")
    stats = get_item_stats(f"skill:{sid}") if sid else get_item_stats("")
    out = dict(skill)
    out["usage"] = stats
    out["usage_count"] = stats["total_count"]
    out["usage_mount_count"] = stats["mount_count"]
    out["usage_invoke_count"] = stats["invoke_count"]
    out["last_used_at"] = stats["last_used_at"]
    return out
