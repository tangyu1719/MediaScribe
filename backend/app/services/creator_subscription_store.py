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


def _migrate_sync_run_columns(engine) -> None:
    """补齐 creator_sync_runs 新增列（create_all 不 ALTER 已有表）。"""
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        if "creator_sync_runs" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("creator_sync_runs")}
        stmts = []
        add_int = [
            ("latest_limit", "INT DEFAULT 0"),
            ("catalog_count", "INT DEFAULT 0"),
        ]
        add_str = [
            ("digest_status", "VARCHAR(16) DEFAULT ''"),
            ("error_code", "VARCHAR(64) DEFAULT ''"),
        ]
        add_text = [
            ("digest_json", "TEXT"),
            ("digest_md", "TEXT"),
            ("error_message", "TEXT"),
        ]
        add_dt = ["started_at", "finished_at"]
        for name, ddl in add_int:
            if name not in cols:
                stmts.append(f"ALTER TABLE creator_sync_runs ADD COLUMN {name} {ddl}")
        for name, ddl in add_str:
            if name not in cols:
                stmts.append(f"ALTER TABLE creator_sync_runs ADD COLUMN {name} {ddl}")
        for name, ddl in add_text:
            if name not in cols:
                stmts.append(f"ALTER TABLE creator_sync_runs ADD COLUMN {name} {ddl}")
        for name in add_dt:
            if name not in cols:
                stmts.append(f"ALTER TABLE creator_sync_runs ADD COLUMN {name} DATETIME NULL")
        if not stmts:
            return
        with engine.begin() as conn:
            for sql in stmts:
                conn.execute(text(sql))
        _log.info(
            "[社媒订阅-持久化|creator_subscription_store._migrate_sync_run_columns|creator_sync_runs|硬编执行|迁移] count=%s",
            len(stmts),
        )
    except Exception as ex:
        _log.warning(
            "[社媒订阅-持久化|creator_subscription_store._migrate_sync_run_columns|creator_sync_runs|硬编执行|跳过] error=%s",
            ex,
        )


def _migrate_sync_run_item_columns(engine) -> None:
    """补齐 creator_sync_run_items 元数据列。"""
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        if "creator_sync_run_items" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("creator_sync_run_items")}
        stmts = []
        additions = [
            ("published_at", "VARCHAR(64) DEFAULT ''"),
            ("published_date", "VARCHAR(16) DEFAULT ''"),
            ("like_count", "INT DEFAULT 0"),
            ("comment_count", "INT DEFAULT 0"),
            ("hashtags_json", "TEXT"),
            ("cover_url", "VARCHAR(1024) DEFAULT ''"),
            ("author_id", "VARCHAR(128) DEFAULT ''"),
            ("author_name", "VARCHAR(256) DEFAULT ''"),
            ("author_followers", "INT DEFAULT 0"),
        ]
        for name, ddl in additions:
            if name not in cols:
                stmts.append(f"ALTER TABLE creator_sync_run_items ADD COLUMN {name} {ddl}")
        if not stmts:
            return
        with engine.begin() as conn:
            for sql in stmts:
                conn.execute(text(sql))
        _log.info(
            "[社媒订阅-持久化|creator_subscription_store._migrate_sync_run_item_columns|creator_sync_run_items|硬编执行|迁移] count=%s",
            len(stmts),
        )
    except Exception as ex:
        _log.warning(
            "[社媒订阅-持久化|creator_subscription_store._migrate_sync_run_item_columns|creator_sync_run_items|硬编执行|跳过] error=%s",
            ex,
        )


def _migrate_seen_analysis_status(engine) -> None:
    """seen / sync_run_items 的 analysis_status 扩至 32（兼容 already_imported 等）。"""
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        for table in ("creator_subscription_seen_notes", "creator_sync_run_items"):
            if table not in insp.get_table_names():
                continue
            cols = {c["name"]: c for c in insp.get_columns(table)}
            col = cols.get("analysis_status")
            if not col:
                continue
            type_str = str(col.get("type") or "").lower()
            if "32" in type_str or "64" in type_str or "varchar(32" in type_str:
                continue
            with engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE {table} MODIFY COLUMN analysis_status VARCHAR(32) DEFAULT 'pending'")
                )
            _log.info(
                "[社媒订阅-持久化|creator_subscription_store._migrate_seen_analysis_status|%s|硬编执行|迁移] analysis_status→VARCHAR(32)",
                table,
            )
    except Exception as ex:
        _log.warning(
            "[社媒订阅-持久化|creator_subscription_store._migrate_seen_analysis_status|analysis_status|硬编执行|跳过] error=%s",
            ex,
        )


def _migrate_follow_pull_columns(engine) -> None:
    """creator_subscriptions 增加收藏博主拉取 cursor。"""
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        if "creator_subscriptions" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("creator_subscriptions")}
        stmts: List[str] = []
        if "follow_pull_note_offset" not in cols:
            stmts.append(
                "ALTER TABLE creator_subscriptions ADD COLUMN follow_pull_note_offset INT DEFAULT 0"
            )
        if "follow_pull_done" not in cols:
            stmts.append(
                "ALTER TABLE creator_subscriptions ADD COLUMN follow_pull_done TINYINT(1) DEFAULT 0"
            )
        with engine.begin() as conn:
            for sql in stmts:
                conn.execute(text(sql))
        if stmts:
            _log.info(
                "[社媒订阅-持久化|creator_subscription_store._migrate_follow_pull_columns|creator_subscriptions|硬编执行|迁移] count=%s",
                len(stmts),
            )
    except Exception as ex:
        _log.warning(
            "[社媒订阅-持久化|creator_subscription_store._migrate_follow_pull_columns|creator_subscriptions|硬编执行|跳过] error=%s",
            ex,
        )


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = require_mariadb_url()
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        CreatorSubBase.metadata.create_all(_engine)
        _migrate_sync_run_columns(_engine)
        _migrate_sync_run_item_columns(_engine)
        _migrate_seen_analysis_status(_engine)
        _migrate_follow_pull_columns(_engine)
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
        "follow_pull_note_offset": int(getattr(row, "follow_pull_note_offset", 0) or 0),
        "follow_pull_done": bool(getattr(row, "follow_pull_done", False)),
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


def is_url_hash_seen(platform: str, url_hash: str) -> bool:
    """按稳定 url_hash 判重（与历史库、队列卡片对齐）。"""
    uh = (url_hash or "").strip()
    if not uh:
        return False
    with session_scope() as db:
        row = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.platform == platform,
                SubscriptionSeenNote.url_hash == uh,
            )
        ).scalar_one_or_none()
        return row is not None


def is_url_hash_seen_any(url_hash: str) -> bool:
    """跨 platform 按 url_hash 判重（收藏/UP 订阅等同链接不重复导入）。"""
    uh = (url_hash or "").strip()
    if not uh:
        return False
    with session_scope() as db:
        row = db.execute(
            select(SubscriptionSeenNote).where(SubscriptionSeenNote.url_hash == uh)
        ).scalar_one_or_none()
        return row is not None


def is_note_seen_any(note_id: str) -> bool:
    """跨 platform 按 note_id 判重。"""
    nid = (note_id or "").strip()
    if not nid:
        return False
    with session_scope() as db:
        row = db.execute(
            select(SubscriptionSeenNote).where(SubscriptionSeenNote.note_id == nid)
        ).scalar_one_or_none()
        return row is not None


def delete_seen_notes_by_prefix(platform: str, note_id_prefix: str = "fav_") -> int:
    """删除 note_id 前缀匹配的 seen 记录（用于 fav_* 假 ID 清理后重跑）。"""
    prefix = (note_id_prefix or "").strip()
    if not prefix:
        return 0
    with session_scope() as db:
        rows = list(
            db.execute(
                select(SubscriptionSeenNote).where(
                    SubscriptionSeenNote.platform == platform,
                    SubscriptionSeenNote.note_id.like(f"{prefix}%"),
                )
            ).scalars().all()
        )
        for row in rows:
            db.delete(row)
        if rows:
            _log.info(
                "[社媒订阅-持久化|delete_seen_notes_by_prefix|%s|硬编执行|删除] count=%s; prefix=%s",
                platform,
                len(rows),
                prefix,
            )
        return len(rows)


def delete_seen_notes_invalid(platform: str) -> int:
    """删除假 noteId（非 24 位 hex）或 explore/fav_ 无效链接的 seen 记录。"""
    import re

    valid_re = re.compile(r"^[a-f0-9]{24}$", re.I)
    with session_scope() as db:
        rows = list(
            db.execute(
                select(SubscriptionSeenNote).where(
                    SubscriptionSeenNote.platform == platform,
                )
            ).scalars().all()
        )
        doomed = []
        for row in rows:
            nid = (row.note_id or "").strip()
            url = (row.canonical_url or "").strip().lower()
            if not valid_re.fullmatch(nid):
                doomed.append(row)
                continue
            if "explore/fav_" in url or "/fav_" in url:
                doomed.append(row)
        for row in doomed:
            db.delete(row)
        if doomed:
            _log.info(
                "[社媒订阅-持久化|delete_seen_notes_invalid|%s|硬编执行|删除] count=%s",
                platform,
                len(domed),
            )
        return len(doomed)


def update_seen_note_identity(
    platform: str,
    old_note_id: str,
    *,
    new_note_id: str,
    canonical_url: str,
    url_hash: str,
    title: str = "",
    analysis_task_id: Optional[str] = None,
    analysis_status: str = "",
) -> bool:
    """将 fav_* 假 note_id 更新为真实 note_id（重跑成功后调用）。"""
    with session_scope() as db:
        row = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.platform == platform,
                SubscriptionSeenNote.note_id == old_note_id,
            )
        ).scalar_one_or_none()
        if not row:
            return False
        conflict = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.platform == platform,
                SubscriptionSeenNote.note_id == new_note_id,
            )
        ).scalar_one_or_none()
        if conflict and conflict.id != row.id:
            db.delete(row)
            return False
        row.note_id = new_note_id
        row.canonical_url = canonical_url
        row.url_hash = url_hash
        if title:
            row.title = title
        if analysis_task_id:
            row.analysis_task_id = analysis_task_id
        if analysis_status:
            row.analysis_status = analysis_status
        return True


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


def upsert_seen_note_url(
    *,
    subscription_id: str,
    platform: str,
    note_id: str,
    canonical_url: str,
    url_hash: str,
    title: str = "",
    content_type: str = "",
) -> bool:
    """更新已摘录笔记的真实链接（优先补全 xsec_token）。"""
    with session_scope() as db:
        row = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.platform == platform,
                SubscriptionSeenNote.note_id == note_id,
            )
        ).scalar_one_or_none()
        if not row:
            return False
        cur = (row.canonical_url or "").strip()
        new_u = (canonical_url or "").strip()
        if not new_u:
            return False
        changed = False
        if not cur or ("xsec_token" in new_u and "xsec_token" not in cur) or (
            new_u != cur and "xsec_token" in new_u
        ):
            row.canonical_url = new_u
            row.url_hash = url_hash
            changed = True
        if title and ((row.title or "").startswith("笔记 ") or not (row.title or "").strip()):
            row.title = title
            changed = True
        if content_type and (row.content_type or "") in ("", "unknown"):
            row.content_type = content_type
            changed = True
        return changed


def list_seen_notes_by_subscription(
    subscription_id: str,
    *,
    page: int = 1,
    page_size: int = 100,
    analysis_status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出订阅下已摘录的博客链接（seen 表）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    with session_scope() as db:
        q = select(SubscriptionSeenNote).where(
            SubscriptionSeenNote.subscription_id == subscription_id
        )
        if analysis_status:
            q = q.where(SubscriptionSeenNote.analysis_status == analysis_status)
        rows = db.execute(
            q.order_by(desc(SubscriptionSeenNote.first_seen_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        return [
            {
                "note_id": r.note_id,
                "canonical_url": r.canonical_url,
                "url_hash": r.url_hash,
                "content_type": r.content_type,
                "title": r.title,
                "analysis_task_id": r.analysis_task_id,
                "analysis_status": r.analysis_status,
                "has_xsec_token": "xsec_token" in (r.canonical_url or ""),
                "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
            }
            for r in rows
        ]


def count_seen_notes_by_subscription(subscription_id: str) -> int:
    with session_scope() as db:
        return (
            db.scalar(
                select(func.count()).select_from(SubscriptionSeenNote).where(
                    SubscriptionSeenNote.subscription_id == subscription_id
                )
            )
            or 0
        )


def _artifact_status_from_history(row: Any) -> Dict[str, str]:
    """从 pipeline_task_history 行推导 MD/HTML 产物状态。"""
    stages: Dict[str, Any] = {}
    try:
        stages = json.loads(getattr(row, "pipeline_stages_json", None) or "{}")
    except Exception:
        stages = {}

    def _st(stage_id: str, path_val: Any) -> str:
        if path_val:
            return "ready"
        st = str((stages.get(stage_id) or {}).get("status") or "")
        if st == "completed":
            return "ready"
        if st == "failed":
            return "failed"
        if st == "in_progress":
            return "running"
        return "off"

    html_st = str(getattr(row, "html_status", "") or "")
    if html_st == "ready" or getattr(row, "html_path", None):
        html_status = "ready"
    elif html_st in ("failed", "error"):
        html_status = "failed"
    elif html_st in ("running", "generating"):
        html_status = "running"
    else:
        html_status = _st("html", getattr(row, "html_path", None))

    return {
        "md_status": _st("generate_md", getattr(row, "doc_path", None)),
        "html_status": html_status,
        "feishu_status": _st("feishu_upload", None),
    }


def _publish_meta_for_note_ids(db: Any, note_ids: List[str]) -> Dict[str, Dict[str, str]]:
    """按 note_id 取最近一次同步条目中的发布时间（小红书原文发布，非入库时间）。"""
    ids = [str(n or "").strip() for n in (note_ids or []) if str(n or "").strip()]
    if not ids:
        return {}
    rows = db.execute(
        select(SyncRunItem.note_id, SyncRunItem.published_at, SyncRunItem.published_date)
        .where(SyncRunItem.note_id.in_(ids))
        .order_by(desc(SyncRunItem.id))
    ).all()
    out: Dict[str, Dict[str, str]] = {}
    for nid, pub_at, pub_date in rows:
        key = str(nid or "").strip()
        if not key or key in out:
            continue
        out[key] = {
            "published_at": str(pub_at or "").strip(),
            "published_date": str(pub_date or "").strip(),
        }
    return out


def _link_card_from_seen_and_history(
    sn: SubscriptionSeenNote,
    hist: Any,
    *,
    publish_meta: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """合并 seen 表与链接库 pipeline_task_history 为前端卡片结构。"""
    first_seen = sn.first_seen_at.isoformat() if sn.first_seen_at else ""
    pm = publish_meta or {}
    pub_at = str(pm.get("published_at") or "").strip()
    pub_date = str(pm.get("published_date") or "").strip()
    card: Dict[str, Any] = {
        "subscription_id": sn.subscription_id,
        "platform": sn.platform or "",
        "note_id": sn.note_id or "",
        "canonical_url": sn.canonical_url or "",
        "url_hash": sn.url_hash or "",
        "title": (sn.title or "").strip() or sn.note_id or "",
        "content_type": sn.content_type or "",
        "published_at": pub_at,
        "published_date": pub_date,
        "task_id": (sn.analysis_task_id or "").strip(),
        "analysis_status": sn.analysis_status or "pending",
        "task_note": "",
        "error_message": "",
        "created_at": first_seen,
        "updated_at": first_seen,
        "md_status": "off",
        "html_status": "off",
        "feishu_status": "off",
        "doc_path": "",
        "html_path": "",
        "feishu_doc_url": "",
        "author_name": "",
        "author_id": "",
        "import_source": "",
        "source_label": "",
    }
    if hist is None:
        return card
    card["task_id"] = (getattr(hist, "task_id", None) or card["task_id"] or "").strip()
    card["title"] = (
        (getattr(hist, "link_title", None) or getattr(hist, "doc_title", None) or getattr(hist, "title", None) or "")
        .strip()
        or card["title"]
    )
    card["analysis_status"] = str(getattr(hist, "status", None) or card["analysis_status"] or "")
    card["error_message"] = str(getattr(hist, "error", None) or "")
    card["doc_path"] = getattr(hist, "doc_path", None) or ""
    card["html_path"] = getattr(hist, "html_path", None) or ""
    card["content_type"] = str(getattr(hist, "content_type", None) or card["content_type"] or "")
    card["updated_at"] = (
        hist.updated_at.isoformat() if getattr(hist, "updated_at", None) else card["updated_at"]
    )
    card.update(_artifact_status_from_history(hist))
    return card


def list_subscription_link_cards(
    subscription_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """订阅链接卡片：seen 表一次 LEFT JOIN pipeline_task_history（链接库）。"""
    from .pipeline_history_models import PipelineTaskHistory

    sid = (subscription_id or "").strip()
    if not sid:
        return {"ok": True, "items": [], "total": 0, "page": 1, "page_size": page_size, "storage": "mysql"}

    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 20)), 100)

    with session_scope() as db:
        total = (
            db.scalar(
                select(func.count())
                .select_from(SubscriptionSeenNote)
                .where(SubscriptionSeenNote.subscription_id == sid)
            )
            or 0
        )
        rows = db.execute(
            select(SubscriptionSeenNote, PipelineTaskHistory)
            .outerjoin(
                PipelineTaskHistory,
                PipelineTaskHistory.url_hash == SubscriptionSeenNote.url_hash,
            )
            .where(SubscriptionSeenNote.subscription_id == sid)
            .order_by(desc(SubscriptionSeenNote.first_seen_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        note_ids = [str(sn.note_id or "").strip() for sn, _ in rows if str(sn.note_id or "").strip()]
        pub_map = _publish_meta_for_note_ids(db, note_ids)
        items = [
            _link_card_from_seen_and_history(
                sn,
                hist,
                publish_meta=pub_map.get(str(sn.note_id or "").strip()),
            )
            for sn, hist in rows
        ]

    return {
        "ok": True,
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "storage": "mysql",
    }


def get_subscription_sync_anchor(subscription_id: str) -> Dict[str, Any]:
    """
    同步锚点：已见 HASH/note_id + 系统内最新发布时间锚点。
    有已订阅链接时增量从锚点向更新方向连续取；无锚点则从最新开始。
    """
    from .subscription_link_order import parse_published_at

    seen_hashes: set = set()
    seen_note_ids: set = set()
    max_pub: Optional[datetime] = None
    with session_scope() as db:
        rows = db.execute(
            select(SubscriptionSeenNote).where(
                SubscriptionSeenNote.subscription_id == subscription_id
            )
        ).scalars().all()
        for r in rows:
            uh = (r.url_hash or "").strip()
            nid = (r.note_id or "").strip()
            if uh:
                seen_hashes.add(uh)
            if nid:
                seen_note_ids.add(nid)
        sub = db.execute(
            select(Subscription).where(Subscription.subscription_id == subscription_id)
        ).scalar_one_or_none()
        if sub and sub.cursor_published_at:
            max_pub = sub.cursor_published_at
        item_rows = db.execute(
            select(SyncRunItem.published_at)
            .join(SyncRun, SyncRun.sync_run_id == SyncRunItem.sync_run_id)
            .where(SyncRun.subscription_id == subscription_id)
            .where(SyncRunItem.published_at != "")
        ).scalars().all()
        for pub_s in item_rows:
            dt = parse_published_at(pub_s)
            if dt and (max_pub is None or dt > max_pub):
                max_pub = dt
    return {
        "published_at": max_pub,
        "url_hashes": seen_hashes,
        "note_ids": seen_note_ids,
        "seen_count": len(seen_note_ids),
    }


def create_sync_run(subscription_id: str, trigger: str = "manual", *, latest_limit: int = 0, catalog_count: int = 0) -> Dict[str, Any]:
    rid = _new_id("sync")
    with session_scope() as db:
        row = SyncRun(
            sync_run_id=rid,
            subscription_id=subscription_id,
            trigger=trigger,
            status="pending",
            latest_limit=latest_limit,
            catalog_count=catalog_count,
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
        "latest_limit": getattr(row, "latest_limit", 0),
        "catalog_count": getattr(row, "catalog_count", 0),
        "digest_status": getattr(row, "digest_status", ""),
        "digest_json": json.loads(row.digest_json or "{}") if getattr(row, "digest_json", None) else {},
        "digest_md": getattr(row, "digest_md", ""),
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
    published_at: str = "",
    published_date: str = "",
    like_count: int = 0,
    comment_count: int = 0,
    hashtags: Optional[List[str]] = None,
    cover_url: str = "",
    author_id: str = "",
    author_name: str = "",
    author_followers: int = 0,
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
                published_at=published_at,
                published_date=published_date,
                like_count=like_count,
                comment_count=comment_count,
                hashtags_json=json.dumps(hashtags or [], ensure_ascii=False),
                cover_url=cover_url,
                author_id=author_id,
                author_name=author_name,
                author_followers=author_followers,
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
        out = []
        for r in rows:
            try:
                hashtags = json.loads(r.hashtags_json or "[]")
            except Exception:
                hashtags = []
            out.append(
                {
                    "note_id": r.note_id,
                    "canonical_url": r.canonical_url,
                    "content_type": r.content_type,
                    "title": r.title,
                    "published_at": r.published_at,
                    "published_date": r.published_date,
                    "like_count": r.like_count,
                    "comment_count": r.comment_count,
                    "hashtags": hashtags,
                    "cover_url": r.cover_url,
                    "author_id": r.author_id,
                    "author_name": r.author_name,
                    "author_followers": r.author_followers,
                    "analysis_task_id": r.analysis_task_id,
                    "analysis_status": r.analysis_status,
                    "error_message": r.error_message,
                }
            )
        return out


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


def update_follow_pull_cursor(
    subscription_id: str,
    *,
    note_offset: int,
    pull_done: bool = False,
    reset: bool = False,
) -> None:
    """更新收藏夹→关注候选的笔记扫描 cursor。"""
    with session_scope() as db:
        row = db.get(Subscription, subscription_id)
        if not row:
            return
        if reset:
            row.follow_pull_note_offset = 0
            row.follow_pull_done = False
        else:
            row.follow_pull_note_offset = max(0, int(note_offset or 0))
            row.follow_pull_done = bool(pull_done)
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


def get_digest_by_sync_run_id(sync_run_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.execute(
            select(CreatorDigest).where(CreatorDigest.sync_run_id == sync_run_id)
        ).scalar_one_or_none()
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
