"""Transactional journal for Agent-triggered local file mutations.

Every mutating file tool records a before snapshot, an after snapshot and a
human-readable diff.  A committed change can be rolled back as long as the
target paths have not been modified again.  Runtime artifacts live below
``backend/data`` and are excluded from source control by the sqlite/data rules.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .fs_browse import is_under_allowed_root

_LOCK = threading.RLock()
_CHANGE_ID_RE = re.compile(r"^chg_[a-f0-9]{16}$")
_MAX_BACKUP_BYTES = max(1024 * 1024, int(os.environ.get("SBA_FILE_JOURNAL_MAX_BYTES", 256 * 1024 * 1024)))
_MAX_DIFF_CHARS = max(10_000, int(os.environ.get("SBA_FILE_JOURNAL_MAX_DIFF_CHARS", 200_000)))
_MAX_MANIFEST_FILES = max(100, int(os.environ.get("SBA_FILE_JOURNAL_MAX_FILES", 20_000)))


class FileChangeJournalError(RuntimeError):
    pass


def _journal_root() -> Path:
    configured = str(os.environ.get("SBA_FILE_JOURNAL_ROOT") or "").strip()
    root = Path(configured).resolve() if configured else Path(__file__).resolve().parents[2] / "data" / "file_change_journal"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _entry_dir(change_id: str) -> Path:
    cid = str(change_id or "").strip()
    if not _CHANGE_ID_RE.fullmatch(cid):
        raise FileChangeJournalError("无效的文件变更 ID")
    return _journal_root() / cid


def _record_path(change_id: str) -> Path:
    return _entry_dir(change_id) / "change.json"


def _write_record(record: Dict[str, Any]) -> None:
    path = _record_path(str(record.get("change_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_record(change_id: str) -> Dict[str, Any]:
    path = _record_path(change_id)
    if not path.is_file():
        raise FileChangeJournalError("找不到文件变更记录")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as ex:
        raise FileChangeJournalError(f"文件变更记录损坏: {ex}") from ex
    if not isinstance(data, dict):
        raise FileChangeJournalError("文件变更记录格式无效")
    return data


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return int(path.stat().st_size)
    total = 0
    count = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise FileChangeJournalError(f"不支持对符号链接建立回退快照: {item}")
        if item.is_file():
            total += int(item.stat().st_size)
            count += 1
            if count > _MAX_MANIFEST_FILES:
                raise FileChangeJournalError(f"文件数量超过回退快照上限 {_MAX_MANIFEST_FILES}")
            if total > _MAX_BACKUP_BYTES:
                break
    return total


def _manifest(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists() or not path.is_dir():
        return {}
    rows: Dict[str, Dict[str, Any]] = {}
    for item in sorted(path.rglob("*"), key=lambda p: str(p).lower()):
        if item.is_symlink():
            continue
        if not item.is_file():
            continue
        rel = item.relative_to(path).as_posix()
        rows[rel] = {"size_bytes": int(item.stat().st_size), "sha256": _sha256(item)}
        if len(rows) >= _MAX_MANIFEST_FILES:
            break
    return rows


def _validate_target(path: Path) -> Path:
    resolved = path.resolve()
    if not is_under_allowed_root(resolved if resolved.exists() else resolved.parent):
        raise FileChangeJournalError(f"路径不在允许的白名单内: {resolved}")
    root = _journal_root()
    try:
        if resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved):
            raise FileChangeJournalError("不能通过文件工具修改变更日志自身")
    except ValueError:
        pass
    if resolved.is_symlink():
        raise FileChangeJournalError("不支持修改符号链接")
    return resolved


def _snapshot(path: Path, *, entry: Path, label: str, backup: bool) -> Dict[str, Any]:
    target = _validate_target(path)
    snap: Dict[str, Any] = {"path": str(target), "exists": target.exists(), "type": "missing"}
    if not target.exists():
        return snap
    if target.is_file():
        size = int(target.stat().st_size)
        snap.update({"type": "file", "size_bytes": size, "sha256": _sha256(target)})
    elif target.is_dir():
        size = _tree_size(target)
        snap.update({"type": "dir", "size_bytes": size, "manifest": _manifest(target)})
    else:
        raise FileChangeJournalError(f"不支持的路径类型: {target}")
    if backup:
        if int(snap.get("size_bytes") or 0) > _MAX_BACKUP_BYTES:
            raise FileChangeJournalError(
                f"目标大小超过可回退快照上限 {_MAX_BACKUP_BYTES} 字节，已拒绝修改: {target}"
            )
        safe_label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label)[:80] or "path"
        backup_path = entry / "before" / safe_label
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            shutil.copy2(target, backup_path)
        else:
            shutil.copytree(target, backup_path)
        snap["backup_rel"] = backup_path.relative_to(entry).as_posix()
    return snap


def _same_snapshot(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if bool(left.get("exists")) != bool(right.get("exists")):
        return False
    if not left.get("exists"):
        return True
    if left.get("type") != right.get("type"):
        return False
    if left.get("type") == "file":
        return left.get("sha256") == right.get("sha256")
    return left.get("manifest") == right.get("manifest")


def _read_text(path: Path) -> Optional[list[str]]:
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        return None
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def _text_diff(before_path: Optional[Path], after_path: Optional[Path], label: str) -> str:
    before = _read_text(before_path) if before_path and before_path.is_file() else []
    after = _read_text(after_path) if after_path and after_path.is_file() else []
    if before is None or after is None:
        return ""
    diff = "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"before/{label}",
            tofile=f"after/{label}",
            lineterm="\n",
        )
    )
    return diff[:_MAX_DIFF_CHARS]


def _diff_one(label: str, before: Dict[str, Any], after: Dict[str, Any], entry: Path) -> Dict[str, Any]:
    if _same_snapshot(before, after):
        return {"label": label, "path": after.get("path") or before.get("path"), "status": "unchanged"}
    if not before.get("exists") and after.get("exists"):
        status = "added"
    elif before.get("exists") and not after.get("exists"):
        status = "removed"
    else:
        status = "modified"
    out: Dict[str, Any] = {
        "label": label,
        "path": after.get("path") or before.get("path"),
        "status": status,
        "before_type": before.get("type"),
        "after_type": after.get("type"),
    }
    if before.get("type") == "dir" or after.get("type") == "dir":
        bm = before.get("manifest") if isinstance(before.get("manifest"), dict) else {}
        am = after.get("manifest") if isinstance(after.get("manifest"), dict) else {}
        bkeys, akeys = set(bm), set(am)
        out["files_added"] = sorted(akeys - bkeys)
        out["files_removed"] = sorted(bkeys - akeys)
        out["files_modified"] = sorted(k for k in bkeys & akeys if bm.get(k) != am.get(k))
        return out
    before_path = entry / str(before.get("backup_rel")) if before.get("backup_rel") else None
    after_path = Path(str(after.get("path"))) if after.get("exists") and after.get("type") == "file" else None
    diff = _text_diff(before_path, after_path, label)
    if diff:
        out["unified_diff"] = diff
    else:
        out["before_sha256"] = before.get("sha256")
        out["after_sha256"] = after.get("sha256")
    return out


def begin_file_change(operation: str, paths: Dict[str, Path | str], *, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Create before snapshots. Raises before any user path is mutated."""
    change_id = "chg_" + uuid.uuid4().hex[:16]
    entry = _entry_dir(change_id)
    entry.mkdir(parents=True, exist_ok=False)
    try:
        from .span_orchestration import get_active_span_context

        ctx = get_active_span_context() or {}
    except Exception:
        ctx = {}
    try:
        before = {
            str(label): _snapshot(Path(path), entry=entry, label=str(label), backup=True)
            for label, path in paths.items()
        }
        record: Dict[str, Any] = {
            "change_id": change_id,
            "operation": str(operation or "unknown"),
            "status": "prepared",
            "created_at": _now(),
            "updated_at": _now(),
            "session_id": str(ctx.get("session_id") or ""),
            "task_id": str(ctx.get("task_id") or ""),
            "trace_id": str(ctx.get("trace_id") or ""),
            "metadata": dict(metadata or {}),
            "before": before,
            "after": {},
            "diff": [],
            "rollback_available": True,
        }
        _write_record(record)
        return change_id
    except Exception:
        shutil.rmtree(entry, ignore_errors=True)
        raise


def commit_file_change(change_id: str, paths: Dict[str, Path | str], *, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with _LOCK:
        record = _load_record(change_id)
        entry = _entry_dir(change_id)
        after = {
            str(label): _snapshot(Path(path), entry=entry, label=str(label), backup=False)
            for label, path in paths.items()
        }
        before = record.get("before") if isinstance(record.get("before"), dict) else {}
        labels: Iterable[str] = dict.fromkeys([*before.keys(), *after.keys()])
        diffs = [_diff_one(label, before.get(label, {}), after.get(label, {}), entry) for label in labels]
        record.update(
            {
                "status": "committed",
                "updated_at": _now(),
                "committed_at": _now(),
                "after": after,
                "diff": diffs,
                "result": dict(result or {}),
            }
        )
        _write_record(record)
        changed = [row for row in diffs if row.get("status") != "unchanged"]
        return {
            "change_id": change_id,
            "rollback_available": True,
            "file_diff": changed,
            "changed_paths": len(changed),
        }


def fail_file_change(change_id: str, error: str) -> None:
    if not change_id:
        return
    with _LOCK:
        try:
            record = _load_record(change_id)
            record.update({"status": "failed", "updated_at": _now(), "error": str(error or "")[:1000]})
            _write_record(record)
        except Exception:
            return


def get_file_change(change_id: str) -> Dict[str, Any]:
    with _LOCK:
        return _load_record(change_id)


def list_file_changes(*, limit: int = 50, session_id: str = "", task_id: str = "") -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    root = _journal_root()
    candidates = sorted(root.glob("chg_*/change.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if session_id and str(row.get("session_id") or "") != str(session_id):
            continue
        if task_id and str(row.get("task_id") or "") != str(task_id):
            continue
        rows.append(
            {
                "change_id": row.get("change_id"),
                "operation": row.get("operation"),
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "session_id": row.get("session_id"),
                "task_id": row.get("task_id"),
                "changed_paths": len([x for x in row.get("diff") or [] if x.get("status") != "unchanged"]),
                "rollback_available": bool(row.get("rollback_available")) and row.get("status") == "committed",
            }
        )
        if len(rows) >= max(1, min(int(limit or 50), 200)):
            break
    return rows


def _remove_current(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _restore_snapshot(snapshot: Dict[str, Any], entry: Path) -> None:
    target = _validate_target(Path(str(snapshot.get("path") or "")))
    _remove_current(target)
    if not snapshot.get("exists"):
        return
    backup_rel = str(snapshot.get("backup_rel") or "")
    backup = (entry / backup_rel).resolve()
    if not backup_rel or not backup.exists() or not backup.is_relative_to(entry.resolve()):
        raise FileChangeJournalError(f"回退备份缺失: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.get("type") == "file":
        shutil.copy2(backup, target)
    elif snapshot.get("type") == "dir":
        shutil.copytree(backup, target)
    else:
        raise FileChangeJournalError(f"不支持的回退类型: {snapshot.get('type')}")


def rollback_file_change(change_id: str, *, force: bool = False) -> Dict[str, Any]:
    with _LOCK:
        record = _load_record(change_id)
        if record.get("status") == "rolled_back":
            return {"ok": True, "change_id": change_id, "rolled_back": True, "already_rolled_back": True}
        if record.get("status") != "committed":
            raise FileChangeJournalError(f"只有 committed 变更可回退，当前状态: {record.get('status')}")
        entry = _entry_dir(change_id)
        after = record.get("after") if isinstance(record.get("after"), dict) else {}
        conflicts: list[str] = []
        for label, snap in after.items():
            current = _snapshot(Path(str(snap.get("path") or "")), entry=entry, label=label, backup=False)
            if not _same_snapshot(snap, current):
                conflicts.append(str(snap.get("path") or label))
        if conflicts and not force:
            return {
                "ok": False,
                "change_id": change_id,
                "rolled_back": False,
                "error": "目标在该变更之后又被修改，已拒绝覆盖；如确认可覆盖请使用 force=true",
                "conflicts": conflicts,
            }
        before = record.get("before") if isinstance(record.get("before"), dict) else {}
        # Remove every after path first, then restore all before snapshots.  This
        # ordering is important for move operations where source/dest overlap.
        for snap in after.values():
            _remove_current(_validate_target(Path(str(snap.get("path") or ""))))
        for snap in before.values():
            _restore_snapshot(snap, entry)
        record.update(
            {
                "status": "rolled_back",
                "updated_at": _now(),
                "rolled_back_at": _now(),
                "rollback_force": bool(force),
                "rollback_conflicts": conflicts,
            }
        )
        _write_record(record)
        return {
            "ok": True,
            "change_id": change_id,
            "rolled_back": True,
            "restored_paths": [str(s.get("path") or "") for s in before.values()],
            "forced": bool(force),
        }
