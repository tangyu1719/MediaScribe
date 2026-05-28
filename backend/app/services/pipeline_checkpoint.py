"""流水线步骤产物磁盘缓存 —— 任务未正式完成前保留，供断点恢复。"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from .task_manager import get_task, get_output_dir

logger = logging.getLogger(__name__)


def _cache_dir(url_hash: str) -> Path:
    uh = (url_hash or "unknown").strip() or "unknown"
    d = get_output_dir() / ".pipeline_cache" / uh
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_stage_payload(task_id: str, stage_id: str, payload: Any, *, url_hash: str = "") -> str:
    """写入步骤缓存文件，返回绝对路径。"""
    task = get_task(task_id) or {}
    uh = url_hash or task.get("url_hash") or task_id
    path = _cache_dir(uh) / f"{stage_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(
        "[链接沉淀-断点缓存|pipeline_checkpoint.save_stage_payload|%s|硬编执行|写入] 步骤缓存已保存; stage=%s; path=%s",
        uh[:12],
        stage_id,
        path.name,
    )
    return str(path)


def load_stage_payload(task_id: str, stage_id: str, *, url_hash: str = "") -> Optional[Any]:
    task = get_task(task_id) or {}
    uh = url_hash or task.get("url_hash") or task_id
    path = _cache_dir(uh) / f"{stage_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "[链接沉淀-断点缓存|pipeline_checkpoint.load_stage_payload|%s|硬编执行|读取失败] stage=%s; error=%s",
            uh[:12],
            stage_id,
            exc,
        )
        return None


def has_stage_payload(task_id: str, stage_id: str, *, url_hash: str = "") -> bool:
    task = get_task(task_id) or {}
    uh = url_hash or task.get("url_hash") or task_id
    return (_cache_dir(uh) / f"{stage_id}.json").is_file()


def clear_pipeline_cache(url_hash: str) -> None:
    """仅在整个流程正式完成后清除。"""
    uh = (url_hash or "").strip()
    if not uh:
        return
    d = _cache_dir(uh)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        logger.info(
            "[链接沉淀-断点缓存|pipeline_checkpoint.clear_pipeline_cache|%s|硬编执行|清除] 步骤缓存已清除",
            uh[:12],
        )


def list_cached_stage_ids(url_hash: str) -> list[str]:
    d = _cache_dir(url_hash)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))
