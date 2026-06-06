"""小红书收藏夹拉取 — CDP + __INITIAL_STATE__ 解析。"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .creator_feed_adapter import (
    FeedItem,
    _build_note_url,
    _note_type_to_content,
    resolve_xhs_red_id,
)
from .link_hash import normalize_link_for_hash, url_hash as link_url_hash

_log = logging.getLogger("sba.xhs_favorites_adapter")
_CHAIN = "小红书收藏夹-增量拉取-习惯画像"
_PLATFORM = "xiaohongshu_favorites"


@dataclass
class FavoritesFeedItem(FeedItem):
    author_followers: int = 0
    collected_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def _extract_favorites_from_state(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 __INITIAL_STATE__ 多路径提取收藏笔记列表。"""
    candidates: List[Any] = []

    user = data.get("user") or {}
    if isinstance(user, dict):
        for key in (
            "collects",
            "collectNotes",
            "collectedNotes",
            "favorites",
            "favoriteNotes",
            "notes",
            "noteList",
        ):
            if user.get(key):
                candidates.append(user.get(key))
        for nested_key in ("userPage", "userPageData", "collectPage", "favoritePage"):
            nested = user.get(nested_key) or {}
            if isinstance(nested, dict):
                for key in ("notes", "noteList", "feeds", "items", "collects"):
                    if nested.get(key):
                        candidates.append(nested.get(key))

    for top_key in ("collect", "collection", "favorites", "favorite"):
        block = data.get(top_key) or {}
        if isinstance(block, dict):
            for key in ("notes", "noteList", "feeds", "items"):
                if block.get(key):
                    candidates.append(block.get(key))

    flat: List[Dict[str, Any]] = []
    for c in candidates:
        if isinstance(c, list):
            flat.extend([x for x in c if isinstance(x, dict)])
        elif isinstance(c, dict):
            for v in c.values():
                if isinstance(v, dict) and (v.get("noteId") or v.get("id") or v.get("note_id")):
                    flat.append(v)
                elif isinstance(v, list):
                    flat.extend([x for x in v if isinstance(x, dict)])

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for n in flat:
        nid = str(n.get("noteId") or n.get("id") or n.get("note_id") or "").strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out.append(n)

    if out:
        return out

    blob = json.dumps(data, ensure_ascii=False)
    for nid in re.findall(r'"noteId"\s*:\s*"([a-f0-9]{24})"', blob, re.I):
        if nid in seen:
            continue
        seen.add(nid)
        title_m = re.search(
            rf'"noteId"\s*:\s*"{re.escape(nid)}".{{0,800}}?"title"\s*:\s*"([^"]*)"',
            blob,
            re.S,
        )
        display_m = re.search(
            rf'"noteId"\s*:\s*"{re.escape(nid)}".{{0,800}}?"displayTitle"\s*:\s*"([^"]*)"',
            blob,
            re.S,
        )
        title = (title_m.group(1) if title_m else "") or (display_m.group(1) if display_m else "")
        token_m = re.search(
            rf'"noteId"\s*:\s*"{re.escape(nid)}".{{0,2000}}?"xsecToken"\s*:\s*"([^"]+)"',
            blob,
            re.S | re.I,
        )
        xsec = token_m.group(1) if token_m else ""
        out.append({"noteId": nid, "title": title or f"笔记 {nid[:8]}", "xsecToken": xsec})
    return out


def _author_followers(n: Dict[str, Any]) -> int:
    author = n.get("user") or n.get("author") or {}
    if not isinstance(author, dict):
        return 0
    for key in ("fans", "fanCount", "follows", "followerCount", "followers"):
        val = author.get(key)
        if isinstance(val, (int, float)) and val >= 0:
            return int(val)
    return 0


def parse_favorites_from_init_state(
    data: Dict[str, Any],
    *,
    owner_creator_id: str,
    profile_url: str,
    fetch_source: str = "favorites_crawler",
) -> List[FavoritesFeedItem]:
    raw_notes = _extract_favorites_from_state(data)
    items: List[FavoritesFeedItem] = []
    for n in raw_notes:
        nid = str(n.get("noteId") or n.get("id") or n.get("note_id") or "").strip()
        if not nid:
            continue
        title = (
            n.get("title")
            or n.get("displayTitle")
            or (n.get("noteCard") or {}).get("displayTitle")
            or ""
        )
        title = str(title).strip() or f"笔记 {nid[:8]}"
        token = str(n.get("xsecToken") or n.get("xsec_token") or "")
        url = _build_note_url(nid, token)
        uh = link_url_hash(url)
        pub = n.get("time") or n.get("createTime") or n.get("publishTime") or n.get("collectTime")
        pub_str = None
        if pub:
            try:
                if isinstance(pub, (int, float)):
                    pub_str = datetime.fromtimestamp(
                        int(pub) / 1000 if pub > 1e12 else int(pub)
                    ).isoformat()
                else:
                    pub_str = str(pub)
            except Exception:
                pub_str = str(pub)
        author = n.get("user") or n.get("author") or {}
        author_id = str(author.get("userId") or author.get("id") or author.get("user_id") or "")
        author_name = str(author.get("nickname") or author.get("name") or "")
        items.append(
            FavoritesFeedItem(
                platform=_PLATFORM,
                note_id=nid,
                canonical_url=url,
                url_hash=uh,
                content_type=_note_type_to_content(n),
                title=title,
                published_at=pub_str,
                author_id=author_id,
                author_name=author_name,
                fetch_source=fetch_source,
                author_followers=_author_followers(n),
                collected_at=pub_str,
            )
        )
    return items


def fetch_favorites_catalog(
    creator_id: str,
    *,
    profile_url: str = "",
    limit: int = 80,
) -> Tuple[List[FavoritesFeedItem], bool]:
    """
    拉取收藏夹笔记列表（最新在前）。
    返回 (items, ok)；失败时 items 可能为空。
    """
    from .xhs_local_browser import scrape_favorites_note_links_via_cdp

    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    link_map: Dict[str, str] = {}
    try:
        link_map = scrape_favorites_note_links_via_cdp(url, creator_id=creator_id)
    except Exception as ex:
        _log.error(
            "[%s|xhs_favorites_adapter.fetch_favorites_catalog|%s|Agent执行|CDP] 失败; error=%s",
            _CHAIN,
            creator_id,
            ex,
        )
        raise RuntimeError("SUB_FAVORITES_FETCH_FAILED") from ex

    if not link_map:
        raise RuntimeError("SUB_FAVORITES_EMPTY")

    items: List[FavoritesFeedItem] = []
    for idx, (nid, note_url) in enumerate(link_map.items()):
        if idx >= limit:
            break
        items.append(
            FavoritesFeedItem(
                platform=_PLATFORM,
                note_id=nid,
                canonical_url=note_url,
                url_hash=link_url_hash(note_url),
                content_type="unknown",
                title=f"笔记 {nid[:8]}",
                published_at=None,
                author_id="",
                author_name="",
                fetch_source="cdp_favorites",
                author_followers=0,
                collected_at=None,
            )
        )

    _log.info(
        "[%s|xhs_favorites_adapter.fetch_favorites_catalog|%s|Agent执行|拉取] 完成; count=%s; with_token=%s",
        _CHAIN,
        creator_id,
        len(items),
        sum(1 for it in items if "xsec_token" in it.canonical_url),
    )
    return items[:limit], True


def resolve_favorites_owner(red_id: str, *, display_name: str = "") -> Dict[str, Any]:
    """解析收藏夹所属用户（小红书号 → creator_id）；强制 Chrome 有光会话。"""
    from .xhs_owner_chrome import refresh_owner_xhs_cookies

    refresh_owner_xhs_cookies()
    import os

    os.environ["SBA_BROWSER"] = "chrome"
    resolved = resolve_xhs_red_id(red_id, display_name=display_name)
    cid = resolved.get("creator_id") or ""
    profile_url = resolved.get("profile_url") or f"https://www.xiaohongshu.com/user/profile/{cid}"
    fav_profile = f"{profile_url.rstrip('/')}?tab=fav"
    return {
        **resolved,
        "profile_url": fav_profile,
        "favorites_url": fav_profile,
    }
