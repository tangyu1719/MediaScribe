"""关注 UP 列表 — MariaDB 持久化。"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import desc, select

from .creator_subscription_models import XhsFollowUp
from .creator_subscription_store import get_engine, session_scope
from .follow_up_search import build_search_blob

_log = logging.getLogger("sba.follow_up_store")
_CHAIN = "小红书关注UP-列表持久化"


def _new_id() -> str:
    return f"fup_{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: XhsFollowUp) -> Dict[str, Any]:
    titles: List[str] = []
    try:
        titles = json.loads(row.sample_titles_json or "[]")
        if not isinstance(titles, list):
            titles = []
    except Exception:
        titles = []
    return {
        "follow_id": row.follow_id,
        "creator_id": row.creator_id,
        "display_name": row.display_name,
        "profile_url": row.profile_url,
        "source": row.source,
        "note_count": row.note_count,
        "sample_titles": titles,
        "search_blob": row.search_blob,
        "last_pulled_at": row.last_pulled_at.isoformat() if row.last_pulled_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def init_follow_up_table() -> None:
    get_engine()


def list_all_follow_ups(*, limit: int = 500) -> List[Dict[str, Any]]:
    init_follow_up_table()
    limit = max(1, min(int(limit or 500), 1000))
    with session_scope() as db:
        rows = db.execute(
            select(XhsFollowUp).order_by(desc(XhsFollowUp.updated_at)).limit(limit)
        ).scalars().all()
        return [_row_to_dict(r) for r in rows]


def get_follow_up_by_creator(creator_id: str) -> Optional[Dict[str, Any]]:
    cid = (creator_id or "").strip()
    if not cid:
        return None
    init_follow_up_table()
    with session_scope() as db:
        row = db.execute(
            select(XhsFollowUp).where(XhsFollowUp.creator_id == cid)
        ).scalar_one_or_none()
        return _row_to_dict(row) if row else None


def upsert_follow_ups_from_authors(
    authors: List[Dict[str, Any]],
    *,
    source: str = "favorite_note",
) -> Tuple[int, int, int]:
    """
    合并拉取结果到关注列表（不创建订阅）。
    返回 (created, updated, total)。
    """
    init_follow_up_table()
    created = 0
    updated = 0
    now = datetime.utcnow()
    src = (source or "favorite_note").strip()
    with session_scope() as db:
        for raw in authors or []:
            if not isinstance(raw, dict):
                continue
            cid = (raw.get("creator_id") or "").strip()
            if not cid:
                continue
            name = (raw.get("display_name") or cid).strip()
            profile = (
                raw.get("profile_url") or f"https://www.xiaohongshu.com/user/profile/{cid}"
            ).strip()
            titles = raw.get("sample_titles") or []
            if not isinstance(titles, list):
                titles = []
            titles = [str(t).strip() for t in titles if str(t).strip()][:6]
            note_count = max(int(raw.get("note_count") or 0), len(titles), 1)
            blob = build_search_blob(
                display_name=name,
                sample_titles=titles,
                source=src,
                creator_id=cid,
            )
            row = db.execute(
                select(XhsFollowUp).where(XhsFollowUp.creator_id == cid)
            ).scalar_one_or_none()
            if row:
                row.display_name = name or row.display_name
                row.profile_url = profile or row.profile_url
                row.source = src
                row.note_count = max(row.note_count, note_count)
                merged_titles = list(dict.fromkeys(titles + json.loads(row.sample_titles_json or "[]")))[:6]
                row.sample_titles_json = json.dumps(merged_titles, ensure_ascii=False)
                row.search_blob = build_search_blob(
                    display_name=row.display_name,
                    sample_titles=merged_titles,
                    source=row.source,
                    creator_id=cid,
                )
                row.last_pulled_at = now
                row.updated_at = now
                updated += 1
            else:
                db.add(
                    XhsFollowUp(
                        follow_id=_new_id(),
                        creator_id=cid,
                        display_name=name,
                        profile_url=profile,
                        source=src,
                        note_count=note_count,
                        sample_titles_json=json.dumps(titles, ensure_ascii=False),
                        search_blob=blob,
                        last_pulled_at=now,
                    )
                )
                created += 1
        db.flush()
    total = len(authors or [])
    _log.info(
        "[%s|follow_up_store.upsert_follow_ups_from_authors|MariaDB|硬编执行|合并] "
        "created=%s; updated=%s; batch=%s",
        _CHAIN,
        created,
        updated,
        total,
    )
    return created, updated, total


def list_follow_up_creator_ids(*, limit: int = 5000) -> Set[str]:
    """已入库的关注候选 creator_id 集合（用于批次去重）。"""
    rows = list_all_follow_ups(limit=limit)
    return {(r.get("creator_id") or "").strip().lower() for r in rows if r.get("creator_id")}


def delete_follow_up(creator_id: str) -> bool:
    cid = (creator_id or "").strip()
    if not cid:
        return False
    init_follow_up_table()
    with session_scope() as db:
        row = db.execute(
            select(XhsFollowUp).where(XhsFollowUp.creator_id == cid)
        ).scalar_one_or_none()
        if not row:
            return False
        db.delete(row)
        return True
