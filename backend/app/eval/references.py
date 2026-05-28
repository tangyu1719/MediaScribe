"""轨迹 golden reference 管理（仅读 eval_fixtures/，显式 opt-in）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "references"


def list_references() -> List[Dict[str, Any]]:
    if not _FIXTURES.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for fp in sorted(_FIXTURES.glob("*.json")):
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            out.append(
                {
                    "id": fp.stem,
                    "name": raw.get("name") or fp.stem,
                    "description": raw.get("description") or "",
                    "message_count": len(raw.get("reference_outputs") or []),
                }
            )
        except Exception:
            continue
    return out


def load_reference(ref_id: str) -> Dict[str, Any]:
    safe = Path(ref_id).stem
    fp = _FIXTURES / f"{safe}.json"
    if not fp.is_file():
        return {"ok": False, "error": "reference 不存在"}
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    refs = raw.get("reference_outputs") or []
    if not isinstance(refs, list):
        return {"ok": False, "error": "reference_outputs 须为数组"}
    return {"ok": True, "id": safe, "name": raw.get("name"), "reference_outputs": refs}
