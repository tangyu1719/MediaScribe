"""订阅同步判重：note_id、url_hash、历史持久化库（MariaDB/history.json）。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .creator_subscription_store import (
    is_note_seen,
    is_note_seen_any,
    is_url_hash_seen,
    is_url_hash_seen_any,
    insert_seen_note,
)
from .history_manager import get_task_history
from .link_hash import extract_link_fields


def _history_doc_exists(hist: Dict[str, Any]) -> bool:
    from .history_manager import _resolve_doc_path

    ref = (hist.get("doc_path") or hist.get("doc_filename") or "").strip()
    if not ref:
        return False
    try:
        return _resolve_doc_path(ref).is_file()
    except Exception:
        from pathlib import Path

        return Path(ref).is_file()


def check_already_imported(
    platform: str,
    note_id: str,
    url_hash: str,
    *,
    canonical_url: str = "",
) -> Tuple[bool, str]:
    """
    判断是否已在订阅 seen 表或历史库中完成导入。
    返回 (True, reason)：reason 为 seen_note_id | seen_url_hash | history_completed。
    """
    nid = (note_id or "").strip()
    uh = (url_hash or "").strip()
    plat = (platform or "").strip()

    if nid and (is_note_seen(plat, nid) or is_note_seen_any(nid)):
        return True, "seen_note_id"
    if uh and (is_url_hash_seen(plat, uh) or is_url_hash_seen_any(uh)):
        return True, "seen_url_hash"

    hist = get_task_history(url_hash=uh, link=canonical_url or "")
    if not hist and canonical_url:
        fields = extract_link_fields(canonical_url)
        if fields:
            hist = get_task_history(link=canonical_url)
    if hist and (hist.get("status") or "").strip() == "completed":
        if _history_doc_exists(hist):
            return True, "history_completed"

    return False, ""


def record_skipped_import(
    *,
    subscription_id: str,
    platform: str,
    note_id: str,
    canonical_url: str,
    url_hash: str,
    content_type: str,
    title: str,
    reason: str,
) -> None:
    """已导入项写入 seen，避免仅靠 offset 再次触发分析。"""
    if not is_note_seen(platform, note_id):
        insert_seen_note(
            subscription_id=subscription_id,
            platform=platform,
            note_id=note_id,
            canonical_url=canonical_url,
            url_hash=url_hash,
            content_type=content_type,
            title=title,
            analysis_task_id=None,
            analysis_status="already_imported",
        )
