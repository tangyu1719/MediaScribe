"""SKILL 版本链：快照 + unified diff（仿 DeskHub / git diff 页）。"""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BASE = Path(__file__).resolve().parents[3]
_VERSIONS_ROOT = _BASE / "output" / "skill_versions"


def _skill_dir(skill_id: str) -> Path:
    sid = re.sub(r"[^a-zA-Z0-9._-]", "_", (skill_id or "").strip())[:80] or "unknown"
    return _VERSIONS_ROOT / sid


def _manifest_path(skill_id: str) -> Path:
    return _skill_dir(skill_id) / "manifest.json"


def _version_path(skill_id: str, version: str) -> Path:
    ver = re.sub(r"[^0-9a-zA-Z._-]", "_", (version or "").strip())[:32] or "0.0.0"
    return _skill_dir(skill_id) / f"{ver}.json"


def _parse_version(v: str) -> Tuple[int, int, int]:
    parts = re.findall(r"\d+", (v or "0.0.0"))
    while len(parts) < 3:
        parts.append("0")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _bump_patch(version: str) -> str:
    a, b, c = _parse_version(version)
    return f"{a}.{b}.{c + 1}"


def list_versions(skill_id: str) -> List[Dict[str, Any]]:
    mp = _manifest_path(skill_id)
    if not mp.exists():
        return []
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        return list(data.get("versions") or [])
    except Exception:
        return []


def get_version_snapshot(skill_id: str, version: str) -> Optional[Dict[str, Any]]:
    p = _version_path(skill_id, version)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def record_version(
    skill_id: str,
    snapshot: Dict[str, Any],
    *,
    message: str = "",
    bump: bool = True,
) -> str:
    """写入新版本快照；返回版本号。"""
    _skill_dir(skill_id).mkdir(parents=True, exist_ok=True)
    versions = list_versions(skill_id)
    current = (snapshot.get("version") or "1.0.0").strip() or "1.0.0"
    if versions:
        latest = versions[-1].get("version") or current
        if bump:
            current = _bump_patch(latest)
    snap = {
        "version": current,
        "created_at": datetime.now().isoformat(),
        "message": (message or "更新").strip()[:200],
        "name": snapshot.get("name", ""),
        "description": snapshot.get("description", ""),
        "command": snapshot.get("command", ""),
        "body_md": snapshot.get("body_md", ""),
    }
    _version_path(skill_id, current).write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    entry = {
        "version": current,
        "created_at": snap["created_at"],
        "message": snap["message"],
    }
    if versions and versions[-1].get("version") == current:
        versions[-1] = entry
    else:
        versions.append(entry)
    _manifest_path(skill_id).write_text(
        json.dumps({"versions": versions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return current


def ensure_initial_version(skill_row: Dict[str, Any]) -> str:
    sid = skill_row.get("id") or ""
    if not sid:
        return skill_row.get("version") or "1.0.0"
    if list_versions(sid):
        return skill_row.get("version") or "1.0.0"
    ver = (skill_row.get("version") or "1.0.0").strip() or "1.0.0"
    record_version(sid, {**skill_row, "version": ver}, message="初始导入", bump=False)
    return ver


def diff_versions(skill_id: str, from_ver: str, to_ver: str) -> Dict[str, Any]:
    a = get_version_snapshot(skill_id, from_ver)
    b = get_version_snapshot(skill_id, to_ver)
    if not a or not b:
        raise ValueError("版本不存在")
    text_a = _snapshot_to_diff_text(a)
    text_b = _snapshot_to_diff_text(b)
    unified = difflib.unified_diff(
        text_a.splitlines(keepends=True),
        text_b.splitlines(keepends=True),
        fromfile=f"SKILL@{from_ver}",
        tofile=f"SKILL@{to_ver}",
        lineterm="",
    )
    unified_str = "".join(unified)
    hunks = _parse_unified_hunks(unified_str)
    return {
        "from": from_ver,
        "to": to_ver,
        "unified": unified_str,
        "hunks": hunks,
        "from_snapshot": {
            "version": a.get("version"),
            "name": a.get("name"),
            "created_at": a.get("created_at"),
        },
        "to_snapshot": {
            "version": b.get("version"),
            "name": b.get("name"),
            "created_at": b.get("created_at"),
        },
    }


def _snapshot_to_diff_text(s: Dict[str, Any]) -> str:
    parts = [
        f"--- name: {s.get('name', '')}",
        f"--- version: {s.get('version', '')}",
        f"--- command: {s.get('command', '')}",
        "",
        f"# description",
        (s.get("description") or "").strip(),
        "",
        f"# body",
        (s.get("body_md") or "").strip(),
    ]
    return "\n".join(parts)


def _parse_unified_hunks(unified: str) -> List[Dict[str, Any]]:
    hunks: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for line in (unified or "").splitlines():
        if line.startswith("@@"):
            if cur:
                hunks.append(cur)
            cur = {"header": line, "lines": []}
        elif cur is not None:
            kind = "context"
            if line.startswith("+") and not line.startswith("+++"):
                kind = "add"
            elif line.startswith("-") and not line.startswith("---"):
                kind = "del"
            cur["lines"].append({"kind": kind, "text": line})
    if cur:
        hunks.append(cur)
    return hunks
