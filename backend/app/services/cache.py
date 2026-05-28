"""缓存管理服务 —— 导入 src/agent/intermediate_cache_manager.py"""
from __future__ import annotations
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
_CACHE_ROOT = None
for _p in _HERE.parents:
    if (_AGENT_DIR is None) and (_p / "src" / "agent").is_dir():
        _AGENT_DIR = (_p / "src" / "agent").resolve()
    if (_CACHE_ROOT is None) and (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _CACHE_ROOT = _p
if _CACHE_ROOT is None:
    _CACHE_ROOT = _HERE.parents[3]
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

_cache_manager: Optional[Any] = None

# 兜底内存缓存（IntermediateCacheManager 不可用时使用）
_fallback_cache: List[Dict] = []
_fallback_next_id = 1


def _get_cache_manager():
    global _cache_manager
    if _cache_manager is not None:
        return _cache_manager
    try:
        from intermediate_cache_manager import IntermediateCacheManager
        config_path = (_AGENT_DIR or _CACHE_ROOT) / "config.json"
        cfg = {}
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        _cache_manager = IntermediateCacheManager(config=cfg, base_dir=str(_CACHE_ROOT))
        return _cache_manager
    except ImportError:
        return None


def cache_query(
    artifact: str = "",
    source: str = "",
    keyword: str = "",
    group: str = "",
    limit: int = 300,
) -> Dict:
    """查询缓存条目"""
    mgr = _get_cache_manager()
    if mgr is not None:
        try:
            result = mgr.query(
                limit=limit,
                artifact_name=artifact or "",
                source=source or "",
                keyword=keyword or "",
            )
            rows = result if isinstance(result, list) else result.get("rows", [])
            groups = sorted(set(
                f"{r.get('artifact_name','') or 'unknown'} + {r.get('source','') or 'unknown'}"
                for r in rows
            ))
            return {"rows": rows, "groups": ["全部分类"] + groups, "total": len(rows)}
        except Exception:
            pass

    # Fallback: 内存缓存
    global _fallback_cache
    rows = list(_fallback_cache)
    if artifact:
        rows = [r for r in rows if artifact.lower() in str(r.get("artifact_name", "")).lower()]
    if source:
        rows = [r for r in rows if source.lower() in str(r.get("source", "")).lower()]
    if keyword:
        rows = [r for r in rows if keyword.lower() in json.dumps(r, ensure_ascii=False).lower()]
    if group and group != "全部分类":
        rows = [r for r in rows if f"{r.get('artifact_name','')} + {r.get('source','')}" == group]
    rows = rows[:limit]
    groups = sorted(set(
        f"{r.get('artifact_name','') or 'unknown'} + {r.get('source','') or 'unknown'}"
        for r in rows
    ))
    return {"rows": rows, "groups": ["全部分类"] + groups, "total": len(rows)}


def cache_get_entry(entry_id: str) -> Optional[Dict]:
    mgr = _get_cache_manager()
    if mgr is not None:
        try:
            results = mgr.query(limit=1, artifact_name="", source="", keyword=entry_id)
            if results:
                return results[0] if isinstance(results, list) else None
        except Exception:
            pass

    for r in _fallback_cache:
        if str(r.get("id")) == entry_id:
            return r
    return None


def cache_update_entry(entry_id: str, data: Any) -> Dict:
    mgr = _get_cache_manager()
    if mgr is not None:
        try:
            mgr.update_data(entry_id, data)
            return {"ok": True}
        except Exception:
            pass

    for r in _fallback_cache:
        if str(r.get("id")) == entry_id:
            r["data"] = data
            return {"ok": True}
    raise LookupError(f"条目不存在: {entry_id}")


def cache_create_entry(
    artifact_name: str = "",
    source: str = "",
    producer: str = "",
    task_key: str = "",
    data: Any = None,
) -> Dict:
    mgr = _get_cache_manager()
    if mgr is not None:
        try:
            mgr.put(
                artifact_name=artifact_name,
                source=source,
                producer=producer,
                data=data,
                task_key=task_key,
            )
            return {"ok": True, "entry": {"artifact_name": artifact_name, "source": source}}
        except Exception:
            pass

    global _fallback_cache, _fallback_next_id
    entry = {
        "id": _fallback_next_id,
        "ts_text": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "artifact_name": artifact_name,
        "source": source,
        "producer": producer,
        "task_key": task_key,
        "data": data,
    }
    _fallback_cache.append(entry)
    _fallback_next_id += 1
    return {"ok": True, "entry": entry}


def cache_export_by_task(task_key: str) -> Dict:
    mgr = _get_cache_manager()
    if mgr is not None:
        try:
            rows = mgr.query_by_task(task_key)
            return {"ok": True, "count": len(rows), "items": rows}
        except Exception:
            pass

    rows = [r for r in _fallback_cache if task_key.lower() in str(r.get("task_key", "")).lower()]
    return {"ok": True, "count": len(rows), "items": rows}
