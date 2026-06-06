"""社媒博主订阅 — MariaDB 持久化（须 mysql+pymysql URL）。"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple

from sqlalchemy import create_engine, select, func, desc
from sqlalchemy.orm import Session, sessionmaker

from .creator_subscription_models import (
    CreatorSubBase,
    CreatorDigest,
    FavoritesHabit,
    Subscription,
    SubscriptionSeenNote,
    SyncRun,
    SyncRunItem,
)

_log = logging.getLogger("sba.creator_subscription_store")
_engine = None
_SessionLocal = None


class SubscriptionDbError(RuntimeError):
    pass


def require_mariadb_url() -> str:
    url = (os.environ.get("SBA_DATABASE_URL") or "").strip()
    if not url or "mysql" not in url.lower():
        raise SubscriptionDbError(
            "社媒订阅模块要求配置 MariaDB/MySQL：环境变量 SBA_DATABASE_URL="
            "mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4"
        )
    return url


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = require_mariadb_url()
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        CreatorSubBase.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(_engine, expire_on_commit=False, class_=Session)
        _log.info(
            "[社媒订阅-持久化|creator_subscription_store.get_engine|MariaDB|硬编执行|初始化] 完成; ok=true"
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


def init_db() -> Dict[str, Any]:
    get_engine()
    return {"ok": True, "driver": "mysql"}


def db_health() -> Dict[str, Any]:
    try:
        with session_scope() as db:
            db.execute(select(func.count()).select_from(Subscription))
        return {"ok": True}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _sub_to_dict(row: Subscription) -> Dict[str, Any]:
    tags: List[str] = []
    try:
        tags = json.loads(row.tags_json or "[]")
    except Exception:
        tags = []
    return {
        "subscription_id": row.subscription_id,
        "platform": row.platform,
        "creator_id": row.creator_id,
        "profile_url": row.profile_url,
        "display_name": row.display_name,
        "status": row.status,
        "cron_override": row.cron_override,
        "read_comments": row.read_comments,
        "auto_analyze": row.auto_analyze,
        "tags": tags,
        "owner_user_id": row.owner_user_id,
        "last_fetch_at": row.last_fetch_at.isoformat() if row.last_fetch_at else None,
        "cursor_offset": row.cursor_offset,
        "last_note_id": row.last_note_id,
        "cursor_published_at": row.cursor_published_at.isoformat() if row.cursor_published_at else None,
        "consecutive_failures": row.consecutive_failures,
        "initial_backfill_done": row.initial_backfill_done,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def create_subscription(
    *,
    platform: str,
    creator_id: str,
    profile_url: str,
    display_name: str = "",
    cron_override: Optional[str] = None,
    read_comments: bool = False,
    auto_analyze: bool = True,
    tags: Optional[List[str]] = None,
    owner_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    sid = _new_id("sub")
    with session_scope() as db:
        exists = db.execute(
            select(Subscription).where(
                Subscription.platform == platform,
                Subscription.creator_id == creator_id,
                Subscription.status != "deleted",
            )
        ).scalar_one_or_none()
        if exists:
            raise ValueError("SUB_DUPLICATE")
        row = Subscription(
            subscription_id=sid,
            platform=platform,
            creator_id=creator_id,
            profile_url=profile_url,
            display_name=display_name or creator_id,
            cron_override=cron_override,
            read_comments=read_comments,
            auto_analyze=auto_analyze,
            tags_json=json.dumps(tags or [], ensure_ascii=False),
            owner_user_id=owner_user_id,
            status="active",
        )
        db.add(row)
        db.flush()
        return _sub_to_dict(row)


def list_subscriptions(
    *,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    with session_scope() as db:
        q = select(Subscription).where(Subscription.status != "deleted")
        if platform:
            q = q.where(Subscription.platform == platform)
        if status:
            q = q.where(Subscription.status == status)
        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = db.execute(
            q.order_by(desc(Subscription.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        return {
            "items": [_sub_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.get(Subscription, subscription_id)
        if not row or row.status == "deleted":
            return None
        return _sub_to_dict(row)


def update_subscription(subscription_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.get(Subscription, subscription_id)
        if not row or row.status == "deleted":
            return None
        for key in ("display_name", "status", "cron_override", "read_comments", "auto_analyze"):
            if key in patch and patch[key] is not None:
                setattr(row, key, patch[key])
        if "tags" in patch and patch["tags"] is not None:
            row.tags_json = json.dumps(patch["tags"], ensure_ascii=False)
        row.updated_at = datetime.utcnow()
        db.flush()
        return _sub_to_dict(row)


def delete_subscription(subscription_id: str) -> bool:
    with session_scope() as db:
        row = db.get(Subscription, subscription_id)
        if not row or row.status == "deleted":
            return False
        row.status = "deleted"
        row.updated_at = datetime.utcnow()
        return True


def is_note_seen(platform: str, note_id: str) -> bool:
    with session_scope() as db:
        row = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.platform == platform,
                SubscriptionSeenNote.note_id == note_id,
            )
        ).scalar_one_or_none()
        return row is not None


def insert_seen_note(
    *,
    subscription_id: str,
    platform: str,
    note_id: str,
    canonical_url: str,
    url_hash: str,
    content_type: str,
    title: str,
    analysis_task_id: Optional[str],
    analysis_status: str,
) -> bool:
    """插入 seen；若已存在返回 False。"""
    with session_scope() as db:
        exists = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.platform == platform,
                SubscriptionSeenNote.note_id == note_id,
            )
        ).scalar_one_or_none()
        if exists:
            return False
        db.add(
            SubscriptionSeenNote(
                subscription_id=subscription_id,
                platform=platform,
                note_id=note_id,
                canonical_url=canonical_url,
                url_hash=url_hash,
                content_type=content_type,
                title=title,
                analysis_task_id=analysis_task_id,
                analysis_status=analysis_status,
            )
        )
        return True


def update_seen_analysis(platform: str, note_id: str, task_id: str, status: str) -> None:
    with session_scope() as db:
        row = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.platform == platform,
                SubscriptionSeenNote.note_id == note_id,
            )
        ).scalar_one_or_none()
        if row:
            row.analysis_task_id = task_id
            row.analysis_status = status


def create_sync_run(subscription_id: str, trigger: str = "manual") -> Dict[str, Any]:
    rid = _new_id("sync")
    with session_scope() as db:
        row = SyncRun(
            sync_run_id=rid,
            subscription_id=subscription_id,
            trigger=trigger,
            status="pending",
            started_at=datetime.utcnow(),
        )
        db.add(row)
        db.flush()
        return _sync_to_dict(row)


def update_sync_run(sync_run_id: str, **fields) -> None:
    with session_scope() as db:
        row = db.get(SyncRun, sync_run_id)
        if not row:
            return
        for k, v in fields.items():
            if hasattr(row, k):
                setattr(row, k, v)
        if fields.get("status") in ("completed", "partial", "failed") and not row.finished_at:
            row.finished_at = datetime.utcnow()


def _sync_to_dict(row: SyncRun) -> Dict[str, Any]:
    return {
        "sync_run_id": row.sync_run_id,
        "subscription_id": row.subscription_id,
        "trigger": row.trigger,
        "status": row.status,
        "new_count": row.new_count,
        "analyzed_count": row.analyzed_count,
        "failed_count": row.failed_count,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_sync_runs(
    *,
    subscription_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    with session_scope() as db:
        q = select(SyncRun)
        if subscription_id:
            q = q.where(SyncRun.subscription_id == subscription_id)
        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = db.execute(
            q.order_by(desc(SyncRun.created_at)).offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()
        return {
            "items": [_sync_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


def add_sync_run_item(
    sync_run_id: str,
    *,
    note_id: str,
    canonical_url: str,
    content_type: str,
    title: str,
    analysis_task_id: Optional[str] = None,
    analysis_status: str = "pending",
    error_message: str = "",
) -> None:
    with session_scope() as db:
        db.add(
            SyncRunItem(
                sync_run_id=sync_run_id,
                note_id=note_id,
                canonical_url=canonical_url,
                content_type=content_type,
                title=title,
                analysis_task_id=analysis_task_id,
                analysis_status=analysis_status,
                error_message=error_message,
            )
        )


def update_sync_run_item(
    sync_run_id: str,
    note_id: str,
    *,
    analysis_task_id: Optional[str] = None,
    analysis_status: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    with session_scope() as db:
        row = db.execute(
            select(SyncRunItem).where(
                SyncRunItem.sync_run_id == sync_run_id,
                SyncRunItem.note_id == note_id,
            )
        ).scalar_one_or_none()
        if not row:
            return
        if analysis_task_id is not None:
            row.analysis_task_id = analysis_task_id
        if analysis_status is not None:
            row.analysis_status = analysis_status
        if error_message is not None:
            row.error_message = error_message


def list_sync_run_items(sync_run_id: str) -> List[Dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(SyncRunItem).where(SyncRunItem.sync_run_id == sync_run_id)
        ).scalars().all()
        return [
            {
                "note_id": r.note_id,
                "canonical_url": r.canonical_url,
                "content_type": r.content_type,
                "title": r.title,
                "analysis_task_id": r.analysis_task_id,
                "analysis_status": r.analysis_status,
                "error_message": r.error_message,
            }
            for r in rows
        ]


def update_subscription_cursor(
    subscription_id: str,
    *,
    cursor_offset: int,
    last_note_id: Optional[str],
    cursor_published_at: Optional[datetime],
    mark_backfill_done: bool = False,
    reset_failures: bool = False,
    increment_failures: bool = False,
) -> None:
    with session_scope() as db:
        row = db.get(Subscription, subscription_id)
        if not row:
            return
        row.cursor_offset = cursor_offset
        row.last_note_id = last_note_id
        row.cursor_published_at = cursor_published_at
        row.last_fetch_at = datetime.utcnow()
        if mark_backfill_done:
            row.initial_backfill_done = True
        if reset_failures:
            row.consecutive_failures = 0
        if increment_failures:
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            if row.consecutive_failures >= 3:
                row.status = "error"
        row.updated_at = datetime.utcnow()


def list_active_subscriptions() -> List[Dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(Subscription).where(Subscription.status == "active")
        ).scalars().all()
        return [_sub_to_dict(r) for r in rows]


def save_digest(
    *,
    sync_run_id: str,
    subscription_id: str,
    digest_md: str,
    digest_json: Dict[str, Any],
    llm_model: str,
    rag_degraded: bool,
) -> Dict[str, Any]:
    did = _new_id("dig")
    with session_scope() as db:
        row = CreatorDigest(
            digest_id=did,
            sync_run_id=sync_run_id,
            subscription_id=subscription_id,
            digest_md=digest_md,
            digest_json=json.dumps(digest_json, ensure_ascii=False),
            llm_model=llm_model,
            rag_degraded=rag_degraded,
        )
        db.add(row)
        db.flush()
        return _digest_to_dict(row)


def _digest_to_dict(row: CreatorDigest) -> Dict[str, Any]:
    try:
        dj = json.loads(row.digest_json or "{}")
    except Exception:
        dj = {}
    return {
        "digest_id": row.digest_id,
        "sync_run_id": row.sync_run_id,
        "subscription_id": row.subscription_id,
        "digest_md": row.digest_md,
        "digest_json": dj,
        "llm_model": row.llm_model,
        "rag_degraded": row.rag_degraded,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def get_digest(digest_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.get(CreatorDigest, digest_id)
        if not row:
            return None
        return _digest_to_dict(row)


def get_subscription_by_platform_creator(platform: str, creator_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.execute(
            select(Subscription).where(
                Subscription.platform == platform,
                Subscription.creator_id == creator_id,
                Subscription.status != "deleted",
            )
        ).scalar_one_or_none()
        if not row:
            return None
        return _sub_to_dict(row)


def get_or_create_subscription(
    *,
    platform: str,
    creator_id: str,
    profile_url: str,
    display_name: str = "",
    cron_override: Optional[str] = None,
    read_comments: bool = False,
    auto_analyze: bool = True,
    tags: Optional[List[str]] = None,
    owner_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    existing = get_subscription_by_platform_creator(platform, creator_id)
    if existing:
        return existing
    try:
        return create_subscription(
            platform=platform,
            creator_id=creator_id,
            profile_url=profile_url,
            display_name=display_name,
            cron_override=cron_override,
            read_comments=read_comments,
            auto_analyze=auto_analyze,
            tags=tags,
            owner_user_id=owner_user_id,
        )
    except ValueError as ex:
        if str(ex) == "SUB_DUPLICATE":
            got = get_subscription_by_platform_creator(platform, creator_id)
            if got:
                return got
        raise


def get_favorites_habit(subscription_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.get(FavoritesHabit, subscription_id)
        if not row:
            return None
        try:
            habit = json.loads(row.habit_json or "{}")
        except Exception:
            habit = {}
        return {
            "subscription_id": row.subscription_id,
            "red_id": row.red_id,
            "habit_json": habit,
            "persona_md": row.persona_md or "",
            "total_collected": row.total_collected or 0,
            "llm_model": row.llm_model or "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def save_favorites_habit(
    *,
    subscription_id: str,
    red_id: str,
    habit_json: Dict[str, Any],
    persona_md: str = "",
    total_collected: int = 0,
    llm_model: str = "",
) -> Dict[str, Any]:
    with session_scope() as db:
        row = db.get(FavoritesHabit, subscription_id)
        if row is None:
            row = FavoritesHabit(
                subscription_id=subscription_id,
                red_id=red_id,
                habit_json=json.dumps(habit_json, ensure_ascii=False),
                persona_md=persona_md,
                total_collected=total_collected,
                llm_model=llm_model,
            )
            db.add(row)
        else:
            row.red_id = red_id or row.red_id
            row.habit_json = json.dumps(habit_json, ensure_ascii=False)
            row.persona_md = persona_md or row.persona_md
            row.total_collected = total_collected
            row.llm_model = llm_model or row.llm_model
            row.updated_at = datetime.utcnow()
        db.flush()
        try:
            habit = json.loads(row.habit_json or "{}")
        except Exception:
            habit = {}
        return {
            "subscription_id": row.subscription_id,
            "red_id": row.red_id,
            "habit_json": habit,
            "persona_md": row.persona_md or "",
            "total_collected": row.total_collected or 0,
            "llm_model": row.llm_model or "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def get_latest_digest(subscription_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        q = select(CreatorDigest).order_by(desc(CreatorDigest.created_at))
        if subscription_id:
            q = q.where(CreatorDigest.subscription_id == subscription_id)
        row = db.execute(q.limit(1)).scalar_one_or_none()
        if not row:
            return None
        return _digest_to_dict(row)
