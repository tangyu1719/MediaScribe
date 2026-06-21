"""从收藏夹笔记按 cursor 分批提取未入库博主（每批最多 N 个不重复新作者）。"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Set, Tuple

_log = logging.getLogger("sba.follow_up_favorites_pull")
_CHAIN = "小红书关注UP-收藏笔记批次拉取"

_AUTHOR_RE = re.compile(r"^[a-f0-9]{24}$", re.I)


def _author_from_note(raw: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """从笔记 dict / FavoritesFeedItem 提取 author_id、昵称、标题、链接。"""
    aid = str(
        raw.get("author_id")
        or (raw.get("author") or {}).get("userId")
        or (raw.get("author") or {}).get("id")
        or ""
    ).strip()
    name = str(
        raw.get("author_name")
        or (raw.get("author") or {}).get("nickname")
        or (raw.get("author") or {}).get("name")
        or ""
    ).strip()
    title = str(raw.get("title") or raw.get("displayTitle") or "").strip()
    url = str(
        raw.get("canonical_url")
        or raw.get("note_url")
        or raw.get("url")
        or ""
    ).strip()
    return aid, name, title, url


def extract_new_authors_batch_from_notes(
    notes: List[Any],
    *,
    start_offset: int,
    batch_size: int,
    owner_creator_id: str = "",
    known_creator_ids: Set[str] | None = None,
) -> Tuple[List[Dict[str, Any]], int, int, bool]:
    """
    从有序收藏笔记列表（新→旧）按 note 下标扫描，收集 batch_size 个**尚未入库**的博主。

    返回 (authors, next_note_offset, notes_scanned_this_batch, catalog_exhausted)
    - next_note_offset: 下次从该 note 下标继续
    - catalog_exhausted: 已扫到列表末尾
    """
    batch_size = max(1, min(int(batch_size or 20), 20))
    offset = max(0, int(start_offset or 0))
    owner = (owner_creator_id or "").strip().lower()
    known = {c.strip().lower() for c in (known_creator_ids or set()) if c}
    batch_seen: Set[str] = set()
    authors: List[Dict[str, Any]] = []
    notes_scanned = 0
    i = offset

    while i < len(notes) and len(authors) < batch_size:
        raw = notes[i]
        if hasattr(raw, "author_id"):
            note = {
                "author_id": getattr(raw, "author_id", ""),
                "author_name": getattr(raw, "author_name", ""),
                "title": getattr(raw, "title", ""),
                "canonical_url": getattr(raw, "canonical_url", "") or getattr(raw, "note_url", ""),
            }
        elif isinstance(raw, dict):
            note = raw
        else:
            i += 1
            notes_scanned += 1
            continue

        aid, name, title, url = _author_from_note(note)
        i += 1
        notes_scanned += 1

        if not aid or not _AUTHOR_RE.fullmatch(aid):
            continue
        key = aid.lower()
        if owner and key == owner:
            continue
        if key in known or key in batch_seen:
            continue

        batch_seen.add(key)
        authors.append(
            {
                "creator_id": aid,
                "display_name": name or aid[:8],
                "profile_url": f"https://www.xiaohongshu.com/user/profile/{aid}",
                "note_count": 1,
                "sample_titles": [title] if title else [],
                "source_note_url": url,
            }
        )

    exhausted = i >= len(notes)
    return authors, i, notes_scanned, exhausted


def scroll_rounds_for_note_offset(note_offset: int) -> int:
    """按 cursor 估算收藏页 scroll 轮数（最多 12 轮）。"""
    off = max(0, int(note_offset or 0))
    return min(12, max(3, (off + 30) // 12 + 2))
