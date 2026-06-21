"""定时任务 — MariaDB 持久化。"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import Session, sessionmaker

from .scheduled_job_models import ScheduledJob, ScheduledJobBase, ScheduledJobRun

_log = logging.getLogger("sba.scheduled_job_store")
_engine = None
_SessionLocal = None


def is_enabled() -> bool:
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
        _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args, future=True)
        ScheduledJobBase.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(_engine, expire_on_commit=False, class_=Session)
        _log.info(
            "[定时任务-持久化|scheduled_job_store.get_engine|scheduled_jobs|硬编执行|初始化] ok=true",
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


def _job_to_dict(row: ScheduledJob) -> Dict[str, Any]:
    return {
        "job_key": row.job_key,
        "name": row.name,
        "category": row.category,
        "description": row.description,
        "frequency_preset": row.frequency_preset,
        "custom_cron": row.custom_cron or "",
        "custom_interval_minutes": int(row.custom_interval_minutes or 0),
        "daily_hour": int(row.daily_hour or 0),
        "daily_minute": int(row.daily_minute or 0),
        "enabled": bool(row.enabled),
        "params_json": row.params_json or "{}",
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else "",
        "last_status": row.last_status or "",
        "last_error": (row.last_error or "")[:500],
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _run_to_dict(row: ScheduledJobRun) -> Dict[str, Any]:
    return {
        "run_id": row.run_id,
        "job_key": row.job_key,
        "trigger": row.trigger,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else "",
        "finished_at": row.finished_at.isoformat() if row.finished_at else "",
        "duration_ms": int(row.duration_ms or 0),
        "summary": row.summary or "",
        "result_json": row.result_json or "{}",
        "error_message": (row.error_message or "")[:1000],
    }


def list_jobs() -> List[Dict[str, Any]]:
    if not is_enabled():
        return []
    with session_scope() as db:
        rows = db.execute(select(ScheduledJob).order_by(ScheduledJob.category, ScheduledJob.job_key)).scalars().all()
        return [_job_to_dict(r) for r in rows]


def get_job(job_key: str) -> Optional[Dict[str, Any]]:
    if not is_enabled():
        return None
    with session_scope() as db:
        row = db.get(ScheduledJob, job_key)
        return _job_to_dict(row) if row else None


def upsert_job(job: Dict[str, Any]) -> Dict[str, Any]:
    key = str(job.get("job_key") or "").strip()
    if not key:
        raise ValueError("job_key 必填")
    now = datetime.utcnow()
    with session_scope() as db:
        row = db.get(ScheduledJob, key)
        if row is None:
            row = ScheduledJob(job_key=key, created_at=now, updated_at=now)
            db.add(row)
        row.name = str(job.get("name") or row.name or key)
        row.category = str(job.get("category") or row.category or "system")
        row.description = str(job.get("description") or row.description or "")
        if "frequency_preset" in job:
            row.frequency_preset = str(job["frequency_preset"])
        if "custom_cron" in job:
            row.custom_cron = str(job.get("custom_cron") or "") or None
        if "custom_interval_minutes" in job:
            row.custom_interval_minutes = int(job.get("custom_interval_minutes") or 0)
        if "daily_hour" in job:
            row.daily_hour = int(job.get("daily_hour") or 0)
        if "daily_minute" in job:
            row.daily_minute = int(job.get("daily_minute") or 0)
        if "enabled" in job:
            row.enabled = bool(job.get("enabled"))
        if "params_json" in job:
            pj = job.get("params_json")
            row.params_json = pj if isinstance(pj, str) else json.dumps(pj or {}, ensure_ascii=False)
        row.updated_at = now
        db.flush()
        return _job_to_dict(row)


def update_job(job_key: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    patch = dict(patch)
    patch["job_key"] = job_key
    if not get_job(job_key):
        return None
    return upsert_job(patch)


def mark_job_run(job_key: str, status: str, error: str = "") -> None:
    if not is_enabled():
        return
    now = datetime.utcnow()
    with session_scope() as db:
        row = db.get(ScheduledJob, job_key)
        if not row:
            return
        row.last_run_at = now
        row.last_status = status
        row.last_error = error or ""
        row.updated_at = now


def create_run(job_key: str, trigger: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    now = datetime.utcnow()
    with session_scope() as db:
        db.add(
            ScheduledJobRun(
                run_id=run_id,
                job_key=job_key,
                trigger=trigger,
                status="running",
                started_at=now,
            )
        )
    return run_id


def finish_run(
    run_id: str,
    *,
    status: str,
    summary: str = "",
    result: Optional[Dict[str, Any]] = None,
    error_message: str = "",
    duration_ms: int = 0,
) -> None:
    now = datetime.utcnow()
    with session_scope() as db:
        row = db.get(ScheduledJobRun, run_id)
        if not row:
            return
        row.status = status
        row.finished_at = now
        row.duration_ms = int(duration_ms or 0)
        row.summary = (summary or "")[:500]
        row.result_json = json.dumps(result or {}, ensure_ascii=False, default=str)
        row.error_message = error_message or ""


def list_runs(job_key: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    if not is_enabled():
        return []
    limit = max(1, min(200, int(limit or 50)))
    with session_scope() as db:
        q = select(ScheduledJobRun).order_by(desc(ScheduledJobRun.started_at)).limit(limit)
        if job_key:
            q = q.where(ScheduledJobRun.job_key == job_key)
        rows = db.execute(q).scalars().all()
        return [_run_to_dict(r) for r in rows]
