"""订阅链接选取：HASH 去重 + 发布时间顺序 + 连续性 + 失效链接阻断。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from .link_hash import url_hash as link_url_hash
from .subscription_import_guard import check_already_imported

_log = logging.getLogger("sba.subscription_link_order")

_PROBE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_PROBE_TIMEOUT = float(__import__("os").environ.get("SUB_LINK_PROBE_TIMEOUT_SEC", "10"))


def parse_published_at(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00").split("+")[0])
    except Exception:
        return None


def probe_link_accessible(url: str) -> Tuple[bool, str]:
    """探测链接是否可访问（失效则不可跳过）。"""
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False, "invalid_url"
    try:
        r = requests.head(
            u,
            allow_redirects=True,
            timeout=_PROBE_TIMEOUT,
            headers={"User-Agent": _PROBE_UA},
        )
        if r.status_code < 400:
            return True, ""
        r2 = requests.get(
            u,
            allow_redirects=True,
            timeout=_PROBE_TIMEOUT,
            stream=True,
            headers={"User-Agent": _PROBE_UA},
        )
        if r2.status_code < 400:
            return True, ""
        return False, f"http_{r2.status_code}"
    except Exception as ex:
        return False, f"{type(ex).__name__}:{str(ex)[:80]}"


def _item_pub(it: Any) -> datetime:
    return parse_published_at(getattr(it, "published_at", None)) or datetime.min


def _item_note_id(it: Any) -> str:
    return str(getattr(it, "note_id", "") or "").strip()


def _item_url_hash(it: Any) -> str:
    uh = str(getattr(it, "url_hash", "") or "").strip()
    if uh:
        return uh
    url = str(getattr(it, "canonical_url", "") or "").strip()
    return link_url_hash(url) if url else ""


def _is_seen(it: Any, seen_hashes: Set[str], seen_note_ids: Set[str]) -> bool:
    nid = _item_note_id(it)
    uh = _item_url_hash(it)
    if nid and nid in seen_note_ids:
        return True
    if uh and uh in seen_hashes:
        return True
    return False


def select_items_for_subscription_sync(
    feed_items: List[Any],
    *,
    platform: str,
    seen_url_hashes: Set[str],
    seen_note_ids: Set[str],
    anchor_published_at: Optional[datetime],
    limit: int,
    probe_links: bool = True,
) -> Tuple[List[Any], List[Dict[str, Any]], str]:
    """
    按发布时间连续性选取待处理链接。

    - 无锚点（系统无已订阅链接）：从 feed 最新端向旧端扩展，遇失效即停，不可跳过。
    - 有锚点：仅处理发布时间严格晚于锚点的链接，从靠近锚点向更新方向连续扩展。
    - 已存在 HASH/note_id 去重跳过，不视为断裂。
    """
    if not feed_items:
        return [], [], ""

    limit = max(1, int(limit or 1))
    skipped: List[Dict[str, Any]] = []
    dedup_hashes = set(seen_url_hashes)
    dedup_notes = set(seen_note_ids)

    if anchor_published_at is None:
        pool = list(feed_items)
    else:
        pool = [it for it in feed_items if _item_pub(it) > anchor_published_at]

    if not pool:
        return [], skipped, ""

    selected: List[Any] = []
    stop_reason = ""

    if anchor_published_at is None:
        # 无已订阅链接：从最新向旧扩展，遇失效即停（不可跳过）
        walk_items = sorted(pool, key=_item_pub, reverse=True)
    else:
        # 有锚点：从锚点向更新方向连续扩展
        walk_items = sorted(pool, key=_item_pub)

    for it in walk_items:
        nid = _item_note_id(it)
        uh = _item_url_hash(it)
        plat = str(getattr(it, "platform", "") or platform or "").strip()

        imported, reason = check_already_imported(
            plat,
            nid,
            uh,
            canonical_url=str(getattr(it, "canonical_url", "") or ""),
        )
        if imported or _is_seen(it, dedup_hashes, dedup_notes):
            skipped.append({"note_id": nid, "url_hash": uh, "reason": reason or "seen"})
            if uh:
                dedup_hashes.add(uh)
            if nid:
                dedup_notes.add(nid)
            continue

        if probe_links:
            ok, err = probe_link_accessible(str(getattr(it, "canonical_url", "") or ""))
            if not ok:
                stop_reason = f"link_invalid:{nid}:{err}"
                _log.warning(
                    "[社媒订阅-链接选取|subscription_link_order.select_items|link|硬编执行|失效阻断] "
                    "note_id=%s; error=%s",
                    nid,
                    err,
                )
                break

        selected.append(it)
        if uh:
            dedup_hashes.add(uh)
        if nid:
            dedup_notes.add(nid)
        if len(selected) >= limit:
            break

    selected.sort(key=_item_pub, reverse=True)
    return selected, skipped, stop_reason
