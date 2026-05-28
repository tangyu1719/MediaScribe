"""RAG 知识库「逻辑库」元数据（切片策略、metadata 模板）；与底层 kb_manager 解耦，先落配置与激活态。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _BASE = _p
        break
else:
    _BASE = _HERE.parents[3]

_LIB_FILE = _BASE / "rag_libraries.json"


def _default_data() -> Dict[str, Any]:
    lid = "lib_default"
    return {
        "active_id": lid,
        "libraries": [
            {
                "id": lid,
                "name": "默认库",
                "slice_method": "auto",
                "metadata_json": '{\n  "source": "",\n  "tags": []\n}',
                "recall_filter_json": "{}",
                "created_at": datetime.now().isoformat(),
            }
        ],
    }


def _load() -> Dict[str, Any]:
    if not _LIB_FILE.exists():
        data = _default_data()
        _save(data)
        return data
    try:
        data = json.loads(_LIB_FILE.read_text(encoding="utf-8"))
        if not data.get("libraries"):
            return _default_data()
        if not data.get("active_id") and data.get("libraries"):
            data["active_id"] = data["libraries"][0]["id"]
        return data
    except Exception:
        return _default_data()


def _save(data: Dict[str, Any]) -> None:
    _LIB_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LIB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_libraries() -> List[Dict[str, Any]]:
    return list(_load().get("libraries") or [])


def get_active_id() -> str:
    d = _load()
    return d.get("active_id") or (d.get("libraries") or [{}])[0].get("id", "lib_default")


def set_active(library_id: str) -> bool:
    d = _load()
    ids = {x.get("id") for x in d.get("libraries") or []}
    if library_id not in ids:
        return False
    d["active_id"] = library_id
    _save(d)
    return True


def create_library(name: str) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("库名称不能为空")
    d = _load()
    lid = "lib_" + uuid.uuid4().hex[:8]
    row = {
        "id": lid,
        "name": name,
        "slice_method": "recursive",
        "metadata_json": '{\n  "source": "",\n  "tags": []\n}',
        "recall_filter_json": "{}",
        "created_at": datetime.now().isoformat(),
    }
    d.setdefault("libraries", []).append(row)
    d["active_id"] = lid
    _save(d)
    return row


def update_library(
    library_id: str,
    *,
    slice_method: Optional[str] = None,
    metadata_json: Optional[str] = None,
    recall_filter_json: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    d = _load()
    for lib in d.get("libraries") or []:
        if lib.get("id") != library_id:
            continue
        if slice_method is not None:
            lib["slice_method"] = slice_method.strip() or "recursive"
        if metadata_json is not None:
            try:
                json.loads(metadata_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"metadata 不是合法 JSON: {e}") from e
            lib["metadata_json"] = metadata_json
        if recall_filter_json is not None:
            try:
                json.loads(recall_filter_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"recall_filter 不是合法 JSON: {e}") from e
            lib["recall_filter_json"] = recall_filter_json
        _save(d)
        return lib
    return None


def delete_library(library_id: str) -> bool:
    if library_id == "lib_default":
        return False
    d = _load()
    libs = [x for x in d.get("libraries") or [] if x.get("id") != library_id]
    if len(libs) == len(d.get("libraries") or []):
        return False
    d["libraries"] = libs
    if d.get("active_id") == library_id:
        d["active_id"] = libs[0]["id"] if libs else ""
    _save(d)
    return True
