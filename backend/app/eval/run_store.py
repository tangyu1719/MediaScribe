"""Eval 跑批结果持久化（output/eval/，非业务假数据）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("sba.eval.store")

_BACKEND = Path(__file__).resolve().parents[2]
_EVAL_DIR = _BACKEND.parent / "output" / "eval"
_RUNS_FILE = _EVAL_DIR / "runs.json"


def _ensure_dir() -> None:
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)


def save_run(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """追加一次 eval 运行记录并更新 last。"""
    _ensure_dir()
    entry = {
        "kind": kind,
        "ts": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }
    data: Dict[str, Any] = {"last": {}, "history": []}
    if _RUNS_FILE.is_file():
        try:
            data = json.loads(_RUNS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data.setdefault("history", [])
    data["last"][kind] = entry
    data["history"] = (data.get("history") or [])[-49:] + [entry]
    _RUNS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info(
        "[Eval-存储|eval.run_store.save_run|%s|硬编执行|完成] 已写入 runs.json; ok=%s",
        kind,
        payload.get("ok"),
    )
    return entry


def get_last_runs() -> Dict[str, Any]:
    if not _RUNS_FILE.is_file():
        return {"last": {}, "history": []}
    try:
        return json.loads(_RUNS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"last": {}, "history": []}


def get_last_run(kind: str) -> Optional[Dict[str, Any]]:
    return (get_last_runs().get("last") or {}).get(kind)
