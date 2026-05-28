"""流水线任务日志持久化 —— JSONL 落盘 + 与 history.json 同步（截取防膨胀）。"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
for _p in _HERE.parents:
    if (_p / "src" / "agent").is_dir():
        _AGENT_DIR = (_p / "src" / "agent").resolve()
        break

_LOG_DIR = (_AGENT_DIR / "pipeline_logs") if _AGENT_DIR else (_HERE.parents[2] / "pipeline_logs")
_PIPELINE_LOG = (_AGENT_DIR / "pipeline.log") if _AGENT_DIR else (_HERE.parents[2] / "pipeline.log")

_MAX_MESSAGE_CHARS = 4000
_MAX_LINES_PER_TASK = 800
_LOCK = threading.Lock()


def _safe_tid(task_id: str) -> str:
    tid = re.sub(r"[^\w\-]", "_", (task_id or "").strip())[:64]
    return tid or "unknown"


def _log_path(task_id: str) -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR / f"{_safe_tid(task_id)}.jsonl"


def _truncate_message(msg: str) -> str:
    s = (msg or "").replace("\r\n", "\n")
    if len(s) <= _MAX_MESSAGE_CHARS:
        return s
    return s[:_MAX_MESSAGE_CHARS] + f"\n…(已截取，原长 {len(s)} 字)"


def append_persistent_log(
    task_id: str,
    *,
    timestamp: str,
    level: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """追加一条任务日志到 JSONL（线程安全）。"""
    entry = {
        "timestamp": timestamp or datetime.now().strftime("%H:%M:%S"),
        "level": (level or "INFO").upper(),
        "message": _truncate_message(message),
    }
    if extra:
        entry["extra"] = {k: v for k, v in extra.items() if v is not None}

    path = _log_path(task_id)
    line = json.dumps(entry, ensure_ascii=False)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        _trim_file(path)
    return entry


def _trim_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= _MAX_LINES_PER_TASK:
            return
        keep = lines[-_MAX_LINES_PER_TASK:]
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except Exception:
        pass


def load_persistent_logs(task_id: str, *, limit: int = _MAX_LINES_PER_TASK) -> List[Dict[str, Any]]:
    path = _log_path(task_id)
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"timestamp": "", "level": "INFO", "message": line})
    except Exception:
        return []
    if len(out) > limit:
        out = out[-limit:]
    return out


def tail_pipeline_log_for_task(task_id: str, *, max_lines: int = 300) -> List[Dict[str, Any]]:
    """从全局 pipeline.log 尾部筛选含 task_id 的行（历史补全）。"""
    tid = (task_id or "").strip()
    if not tid or not _PIPELINE_LOG.is_file():
        return []
    try:
        raw = _PIPELINE_LOG.read_text(encoding="utf-8", errors="ignore")
        lines = [ln for ln in raw.splitlines() if tid in ln]
        lines = lines[-max_lines:]
        out = []
        for ln in lines:
            m = re.match(r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)$", ln)
            if m:
                out.append({"timestamp": m.group(1)[-8:], "level": "INFO", "message": _truncate_message(m.group(3))})
            else:
                out.append({"timestamp": "", "level": "INFO", "message": _truncate_message(ln)})
        return out
    except Exception:
        return []


def merge_task_logs(
    task_id: str,
    *,
    memory_logs: Optional[List[Dict]] = None,
    history_logs: Optional[List[Dict]] = None,
) -> List[Dict[str, Any]]:
    """合并内存 / JSONL / history / pipeline.log，按时间顺序去重。"""
    seen = set()
    merged: List[Dict[str, Any]] = []

    def _add(rows: Optional[List[Dict]]) -> None:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            msg = _truncate_message(str(row.get("message") or ""))
            key = (row.get("timestamp"), row.get("level"), msg[:200])
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "timestamp": row.get("timestamp") or "",
                    "level": (row.get("level") or "INFO").upper(),
                    "message": msg,
                    "extra": row.get("extra"),
                }
            )

    _add(memory_logs)
    _add(load_persistent_logs(task_id))
    _add(history_logs)
    if len(merged) < 20:
        _add(tail_pipeline_log_for_task(task_id))
    return merged[-_MAX_LINES_PER_TASK:]


def extract_error_logs(logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    err_levels = {"ERROR", "ERR", "CRITICAL", "FATAL"}
    out = []
    for row in logs:
        lv = str(row.get("level") or "").upper()
        msg = str(row.get("message") or "")
        if lv in err_levels or "失败" in msg or "异常" in msg or "MOCK" in msg or "ERROR" in msg:
            out.append(row)
    return out
