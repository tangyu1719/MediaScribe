"""链接沉淀流水线历史 — MariaDB/MySQL 持久化（SBA_DATABASE_URL）。"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import create_engine, delete, desc, func, select
from sqlalchemy.orm import Session, sessionmaker

from .link_hash import links_same_identity, url_hash as link_url_hash
from .pipeline_history_models import PipelineHistoryBase, PipelineTaskHistory

_log = logging.getLogger("sba.pipeline_history_store")
_engine = None
_SessionLocal = None
_migrated_from_json = False


def is_enabled() -> bool:
    """配置了 SBA_DATABASE_URL 时启用库表持久化（推荐 mysql+pymysql）。"""
    return bool((os.environ.get("SBA_DATABASE_URL") or "").strip())


def _db_url() -> str:
    url = (os.environ.get("SBA_DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("SBA_DATABASE_URL 未配置")
    return url


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = _db_url()
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(
            url, pool_pre_ping=True, connect_args=connect_args, future=True
        )
        PipelineHistoryBase.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(_engine, expire_on_commit=False, class_=Session)
        driver = "sqlite" if url.startswith("sqlite") else "mysql"
        _log.info(
            "[链接沉淀-历史持久化|pipeline_history_store.get_engine|pipeline_task_history|硬编执行|初始化] "
            "完成; ok=true; driver=%s",
            driver,
        )
    return _engine


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _parse_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val.strip():
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            pass
    return datetime.utcnow()


def _row_to_dict(row: PipelineTaskHistory) -> Dict[str, Any]:
    try:
        pipeline_stages = json.loads(row.pipeline_stages_json or "{}")
    except Exception:
        pipeline_stages = {}
    try:
        resume_context = json.loads(row.resume_context_json or "{}")
    except Exception:
        resume_context = {}
    comments = None
    if row.comments_json:
        try:
            comments = json.loads(row.comments_json)
        except Exception:
            comments = None
    try:
        logs = json.loads(row.logs_json or "[]")
    except Exception:
        logs = []
    return {
        "id": row.task_id,
        "task_id": row.task_id,
        "link": row.link or "",
        "url_hash": row.url_hash or "",
        "normalized_link": row.normalized_link or "",
        "platform": row.platform or "",
        "status": row.status or "",
        "stage": row.stage or "",
        "progress": row.progress or 0,
        "title": row.title or "",
        "link_title": row.link_title or "",
        "doc_title": row.doc_title or "",
        "content_type": row.content_type or "",
        "cover_url": row.cover_url or "",
        "route_type": row.route_type or "",
        "pipeline_route": row.pipeline_route or "",
        "pipeline_stages": pipeline_stages,
        "failed_stage": row.failed_stage or "",
        "failed_stage_label": row.failed_stage_label or "",
        "resume_from": row.resume_from or "",
        "resume_context": resume_context,
        "user_prompt": row.user_prompt or "",
        "comments": comments,
        "transcribe_error_code": row.transcribe_error_code or "",
        "doc_filename": row.doc_filename,
        "doc_path": row.doc_path,
        "html_path": row.html_path,
        "html_status": row.html_status or "",
        "html_message": row.html_message or "",
        "error": row.error,
        "logs": logs,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _apply_dict(row: PipelineTaskHistory, entry: Dict[str, Any]) -> None:
    tid = (entry.get("id") or entry.get("task_id") or "").strip()
    if not tid:
        raise ValueError("task_id required")
    link = entry.get("link") or ""
    uh = (entry.get("url_hash") or "").strip() or link_url_hash(link)
    row.task_id = tid
    row.url_hash = uh
    row.link = link
    row.normalized_link = entry.get("normalized_link") or ""
    row.platform = entry.get("platform") or ""
    row.status = entry.get("status") or ""
    row.stage = entry.get("stage") or ""
    row.progress = int(entry.get("progress") or 0)
    row.title = (
        entry.get("title")
        or entry.get("doc_title")
        or entry.get("link_title")
        or ""
    )
    row.link_title = entry.get("link_title") or ""
    row.doc_title = entry.get("doc_title") or ""
    row.content_type = entry.get("content_type") or ""
    row.cover_url = entry.get("cover_url") or ""
    row.route_type = entry.get("route_type") or ""
    row.pipeline_route = entry.get("pipeline_route") or entry.get("route_type") or ""
    row.pipeline_stages_json = json.dumps(
        entry.get("pipeline_stages") or {}, ensure_ascii=False
    )
    row.failed_stage = entry.get("failed_stage") or ""
    row.failed_stage_label = entry.get("failed_stage_label") or ""
    row.resume_from = entry.get("resume_from") or ""
    row.resume_context_json = json.dumps(
        entry.get("resume_context") or {}, ensure_ascii=False
    )
    row.user_prompt = entry.get("user_prompt") or ""
    comments = entry.get("comments")
    row.comments_json = (
        json.dumps(comments, ensure_ascii=False) if comments is not None else None
    )
    row.transcribe_error_code = entry.get("transcribe_error_code") or ""
    row.doc_filename = entry.get("doc_filename")
    row.doc_path = entry.get("doc_path")
    row.html_path = entry.get("html_path")
    row.html_status = entry.get("html_status") or ""
    row.html_message = entry.get("html_message") or ""
    err = entry.get("error")
    row.error = str(err) if err is not None else None
    row.logs_json = json.dumps(entry.get("logs") or [], ensure_ascii=False)
    row.created_at = _parse_dt(entry.get("created_at"))
    row.updated_at = _parse_dt(entry.get("updated_at"))


def upsert_task(entry: Dict[str, Any]) -> None:
    """按 url_hash 去重：同链接更新原 task_id。"""
    link = entry.get("link") or ""
    uh = (entry.get("url_hash") or "").strip() or link_url_hash(link)
    entry = dict(entry)
    entry["url_hash"] = uh
    tid = (entry.get("id") or entry.get("task_id") or "").strip()
    if not tid:
        return

    with session_scope() as db:
        by_hash = db.execute(
            select(PipelineTaskHistory).where(PipelineTaskHistory.url_hash == uh)
        ).scalar_one_or_none()
        by_id = db.execute(
            select(PipelineTaskHistory).where(PipelineTaskHistory.task_id == tid)
        ).scalar_one_or_none()

        if by_hash and by_hash.task_id != tid:
            entry["id"] = by_hash.task_id
            entry["task_id"] = by_hash.task_id
            entry["created_at"] = (
                by_hash.created_at.isoformat() if by_hash.created_at else entry.get("created_at")
            )
            tid = by_hash.task_id
            if by_id and by_id.task_id != by_hash.task_id:
                db.delete(by_id)

        row = by_hash or by_id
        if row is None:
            row = PipelineTaskHistory(task_id=tid, url_hash=uh)
            db.add(row)
        elif row.task_id != tid:
            old_id = row.task_id
            db.delete(row)
            db.flush()
            row = PipelineTaskHistory(task_id=tid, url_hash=uh)
            db.add(row)
            entry["id"] = tid
            entry["task_id"] = tid
            _log.debug("history task_id realigned %s -> %s", old_id, tid)

        _apply_dict(row, entry)


def get_by_link_or_hash(
    link: str = "", url_hash: str = ""
) -> Optional[Dict[str, Any]]:
    target_hash = (url_hash or "").strip() or (link_url_hash(link) if link else "")
    with session_scope() as db:
        if target_hash:
            row = db.execute(
                select(PipelineTaskHistory)
                .where(PipelineTaskHistory.url_hash == target_hash)
                .order_by(desc(PipelineTaskHistory.updated_at))
            ).scalars().first()
            if row:
                return _row_to_dict(row)
        if link:
            for row in db.execute(
                select(PipelineTaskHistory).order_by(desc(PipelineTaskHistory.updated_at))
            ).scalars():
                if links_same_identity(row.link or "", link):
                    return _row_to_dict(row)
    return None


def get_by_task_id(task_id: str) -> Optional[Dict[str, Any]]:
    tid = (task_id or "").strip()
    if not tid:
        return None
    with session_scope() as db:
        row = db.execute(
            select(PipelineTaskHistory).where(PipelineTaskHistory.task_id == tid)
        ).scalar_one_or_none()
        return _row_to_dict(row) if row else None


def list_tasks(limit: int = 400) -> List[Dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(PipelineTaskHistory)
            .order_by(desc(PipelineTaskHistory.updated_at))
            .limit(max(1, limit))
        ).scalars().all()
        return [_row_to_dict(r) for r in rows]


def delete_by_link_or_hash(link: str = "", url_hash: str = "") -> bool:
    target_hash = (url_hash or "").strip() or (link_url_hash(link) if link else "")
    with session_scope() as db:
        if target_hash:
            res = db.execute(
                delete(PipelineTaskHistory).where(
                    PipelineTaskHistory.url_hash == target_hash
                )
            )
            return res.rowcount > 0
        if link:
            removed = False
            for row in db.execute(select(PipelineTaskHistory)).scalars():
                if links_same_identity(row.link or "", link):
                    db.delete(row)
                    removed = True
            return removed
    return False


def clear_completed() -> int:
    with session_scope() as db:
        res = db.execute(
            delete(PipelineTaskHistory).where(
                PipelineTaskHistory.status == "completed"
            )
        )
        return int(res.rowcount or 0)


def consolidate_by_url_hash() -> int:
    """同 url_hash 只保留 updated_at 最新一行。"""
    removed = 0
    with session_scope() as db:
        rows = list(db.execute(select(PipelineTaskHistory)).scalars())
        groups: Dict[str, List[PipelineTaskHistory]] = {}
        for row in rows:
            uh = (row.url_hash or "").strip()
            if not uh:
                continue
            groups.setdefault(uh, []).append(row)
        for uh, items in groups.items():
            if len(items) <= 1:
                continue
            items.sort(key=lambda r: r.updated_at or datetime.min)
            for dup in items[:-1]:
                db.delete(dup)
                removed += 1
    return removed


def count_tasks() -> int:
    with session_scope() as db:
        return int(db.execute(select(func.count()).select_from(PipelineTaskHistory)).scalar() or 0)


def migrate_from_json_tasks(tasks: List[Dict[str, Any]]) -> int:
    """将 history.json 任务批量写入库（已存在 url_hash 则更新）。"""
    n = 0
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = (t.get("id") or t.get("task_id") or "").strip()
        if not tid:
            continue
        try:
            upsert_task(t)
            n += 1
        except Exception as ex:
            _log.warning(
                "[链接沉淀-历史持久化|pipeline_history_store.migrate_from_json_tasks|"
                "pipeline_task_history|硬编执行|跳过] task_id=%s; error=%s",
                tid,
                ex,
            )
    return n


def ensure_migrated_from_json_file(json_path) -> int:
    """库为空时从 history.json 导入一次。"""
    global _migrated_from_json
    if _migrated_from_json or not is_enabled():
        return 0
    from pathlib import Path

    p = Path(json_path)
    if not p.is_file():
        _migrated_from_json = True
        return 0
    try:
        if count_tasks() > 0:
            _migrated_from_json = True
            return 0
        import json as _json

        data = _json.loads(p.read_text(encoding="utf-8"))
        tasks = data.get("tasks") or []
        n = migrate_from_json_tasks(tasks)
        _migrated_from_json = True
        _log.info(
            "[链接沉淀-历史持久化|pipeline_history_store.ensure_migrated_from_json_file|"
            "history.json|硬编执行|完成] imported=%s; path=%s",
            n,
            p,
        )
        return n
    except Exception as ex:
        _log.exception(
            "[链接沉淀-历史持久化|pipeline_history_store.ensure_migrated_from_json_file|"
            "history.json|硬编执行|失败] error=%s",
            ex,
        )
        return 0
