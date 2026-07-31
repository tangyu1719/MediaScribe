"""小红书收藏夹拉取 — CDP + __INITIAL_STATE__ 解析。"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .creator_feed_adapter import (
    FeedItem,
    _build_favorites_note_url,
    _build_note_url,
    _note_type_to_content,
    extract_xhs_note_id_from_url,
    is_valid_xhs_note_id,
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
    published_date: Optional[str] = None
    like_count: int = 0
    comment_count: int = 0
    hashtags: List[str] = None  # type: ignore[assignment]
    cover_url: str = ""
    note_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("hashtags") is None:
            d["hashtags"] = []
        return d


def _flatten_dict_list(nodes: List[Any]) -> List[Dict[str, Any]]:
    """递归展开 list[list[dict]] 等收藏页分页结构。"""
    out: List[Dict[str, Any]] = []
    for node in nodes:
        if isinstance(node, dict):
            out.append(node)
        elif isinstance(node, list):
            out.extend(_flatten_dict_list(node))
    return out


def _note_id_from_dict(n: Dict[str, Any]) -> str:
    """从 state 节点或嵌套 url/href 解析真实 24 位 noteId；禁止伪造 fav_*。"""
    for key in ("noteId", "id", "note_id"):
        val = str(n.get(key) or "").strip()
        if is_valid_xhs_note_id(val):
            return val
    for key in ("url", "link", "href", "noteUrl", "note_url"):
        val = str(n.get(key) or "").strip()
        if val:
            nid = extract_xhs_note_id_from_url(val)
            if is_valid_xhs_note_id(nid):
                return nid
    card = (
        n.get("noteCard")
        if isinstance(n.get("noteCard"), dict)
        else n.get("note_card")
        if isinstance(n.get("note_card"), dict)
        else {}
    )
    if card:
        for key in ("noteId", "id", "note_id"):
            val = str(card.get(key) or "").strip()
            if is_valid_xhs_note_id(val):
                return val
        for key in ("url", "link", "href"):
            val = str(card.get(key) or "").strip()
            if val:
                nid = extract_xhs_note_id_from_url(val)
                if is_valid_xhs_note_id(nid):
                    return nid
    return ""


def _normalize_favorite_note_dict(n: Dict[str, Any]) -> Dict[str, Any]:
    """合并 noteCard 字段；无真实 noteId 时保留原结构供上层跳过（不再生成 fav_* 假 ID）。"""
    card = (
        n.get("noteCard")
        if isinstance(n.get("noteCard"), dict)
        else n.get("note_card")
        if isinstance(n.get("note_card"), dict)
        else {}
    )
    merged: Dict[str, Any] = dict(n)
    if card:
        merged.setdefault("noteCard", card)
        for key in ("noteId", "id", "note_id", "displayTitle", "title", "type", "xsecToken", "xsec_token"):
            val = card.get(key)
            if val and not merged.get(key):
                merged[key] = val
        card_user = card.get("user") if isinstance(card.get("user"), dict) else {}
        if card_user and not merged.get("user"):
            merged["user"] = card_user
    for source, target in (
        ("display_title", "displayTitle"),
        ("note_id", "noteId"),
        ("note_card", "noteCard"),
        ("interact_info", "interactInfo"),
        ("xsec_token", "xsecToken"),
    ):
        if merged.get(source) is not None and not merged.get(target):
            merged[target] = merged.get(source)
    nid = _note_id_from_dict(merged)
    if nid:
        merged["noteId"] = nid
    return merged


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
            flat.extend(_flatten_dict_list(c))
        elif isinstance(c, dict):
            for v in c.values():
                if isinstance(v, dict):
                    flat.append(v)
                elif isinstance(v, list):
                    flat.extend(_flatten_dict_list(v))

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for raw in flat:
        n = _normalize_favorite_note_dict(raw)
        nid = _note_id_from_dict(n)
        if not is_valid_xhs_note_id(nid):
            continue
        if nid in seen:
            continue
        seen.add(nid)
        n["noteId"] = nid
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


def _extract_favorites_meta_from_state(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """收藏页 SSR 元数据（允许 noteId 为空，供点击解析后按序合并 author/title）。"""
    user = data.get("user") or {}
    if isinstance(user, dict):
        notes = user.get("notes")
        if isinstance(notes, list):
            for block in notes:
                if not isinstance(block, list) or not block:
                    continue
                flat = _flatten_dict_list(block)
                if not flat:
                    continue
                first = flat[0] if isinstance(flat[0], dict) else {}
                card = (
                    first.get("noteCard")
                    if isinstance(first.get("noteCard"), dict)
                    else first.get("note_card")
                    if isinstance(first.get("note_card"), dict)
                    else {}
                )
                if not (
                    first.get("displayTitle")
                    or first.get("display_title")
                    or card.get("displayTitle")
                    or card.get("display_title")
                    or first.get("noteId")
                    or first.get("note_id")
                    or card.get("noteId")
                    or card.get("note_id")
                ):
                    continue
                return [_normalize_favorite_note_dict(x) for x in flat if isinstance(x, dict)]

    candidates: List[Any] = []
    if isinstance(user, dict):
        for key in ("collects", "collectNotes", "collectedNotes", "favorites", "favoriteNotes", "notes", "noteList"):
            if user.get(key):
                candidates.append(user.get(key))
    flat: List[Dict[str, Any]] = []
    for c in candidates:
        if isinstance(c, list):
            flat.extend(_flatten_dict_list(c))
    if flat:
        return [_normalize_favorite_note_dict(x) for x in flat if isinstance(x, dict)]
    return []


def parse_favorites_meta_from_init_state(
    data: Dict[str, Any],
    *,
    owner_creator_id: str,
    profile_url: str,
    fetch_source: str = "favorites_meta",
) -> List[FavoritesFeedItem]:
    """解析收藏元数据（可无 noteId），用于与点击采集结果按序合并。"""
    items: List[FavoritesFeedItem] = []
    for n in _extract_favorites_meta_from_state(data):
        title = (
            n.get("title")
            or n.get("displayTitle")
            or n.get("display_title")
            or (n.get("noteCard") or {}).get("displayTitle")
            or (n.get("noteCard") or {}).get("display_title")
            or ""
        )
        title = str(title).strip()
        card = n.get("noteCard") if isinstance(n.get("noteCard"), dict) else {}
        author = n.get("user") or n.get("author") or card.get("user") or {}
        author_id = str(author.get("userId") or author.get("id") or author.get("user_id") or "")
        author_name = str(author.get("nickname") or author.get("name") or "")
        token = str(n.get("xsecToken") or n.get("xsec_token") or "")
        nid = _note_id_from_dict(n)
        url = _build_favorites_note_url(nid, token) if nid else ""
        items.append(
            FavoritesFeedItem(
                platform=_PLATFORM,
                note_id=nid,
                canonical_url=url,
                url_hash=link_url_hash(url) if url else "",
                content_type=_note_type_to_content(n),
                title=title or (f"笔记 {nid[:8]}" if nid else "收藏笔记"),
                published_at=None,
                author_id=author_id,
                author_name=author_name,
                fetch_source=fetch_source,
                author_followers=_author_followers(n),
                collected_at=None,
            )
        )
    return items


def _author_followers(n: Dict[str, Any]) -> int:
    author = n.get("user") or n.get("author") or {}
    if not isinstance(author, dict):
        return 0
    for key in (
        "fans",
        "fanCount",
        "fan_count",
        "follows",
        "followerCount",
        "follower_count",
        "followers",
    ):
        val = author.get(key)
        if isinstance(val, (int, float)) and val >= 0:
            return int(val)
    return 0


def _cover_url_from_note(n: Dict[str, Any], card: Dict[str, Any]) -> str:
    for value in (n.get("cover"), card.get("cover")):
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("url_default", "url_pre", "url", "urlDefault", "urlPre"):
                url = str(value.get(key) or "").strip()
                if url:
                    return url
            for info in value.get("info_list") or value.get("infoList") or []:
                if isinstance(info, dict):
                    url = str(info.get("url") or info.get("url_default") or "").strip()
                    if url:
                        return url
    for key in ("imageList", "image_list"):
        images = card.get(key)
        if isinstance(images, list) and images and isinstance(images[0], dict):
            url = str(
                images[0].get("url")
                or images[0].get("url_default")
                or images[0].get("urlDefault")
                or ""
            ).strip()
            if url:
                return url
    return ""


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
        nid = _note_id_from_dict(n)
        if not is_valid_xhs_note_id(nid):
            continue
        title = (
            n.get("title")
            or n.get("displayTitle")
            or n.get("display_title")
            or (n.get("noteCard") or {}).get("displayTitle")
            or (n.get("noteCard") or {}).get("display_title")
            or ""
        )
        title = str(title).strip() or f"笔记 {nid[:8]}"
        token = str(n.get("xsecToken") or n.get("xsec_token") or "")
        url = _build_favorites_note_url(nid, token)
        if not url:
            continue
        uh = link_url_hash(url)
        pub = (
            n.get("time")
            or n.get("createTime")
            or n.get("create_time")
            or n.get("publishTime")
            or n.get("publish_time")
            or n.get("collectTime")
            or n.get("collect_time")
        )
        pub_str = None
        pub_date = None
        if pub:
            try:
                if isinstance(pub, (int, float)):
                    pub_str = datetime.fromtimestamp(
                        int(pub) / 1000 if pub > 1e12 else int(pub)
                    ).isoformat()
                else:
                    pub_str = str(pub)
                pub_date = (pub_str or "")[:10]
            except Exception:
                pub_str = str(pub)
                pub_date = str(pub)[:10]
        card = n.get("noteCard") if isinstance(n.get("noteCard"), dict) else {}
        author = n.get("user") or n.get("author") or card.get("user") or {}
        author_id = str(author.get("userId") or author.get("id") or author.get("user_id") or "")
        author_name = str(author.get("nickname") or author.get("name") or "")
        stats = (
            n.get("interactInfo")
            if isinstance(n.get("interactInfo"), dict)
            else n.get("interact_info")
            if isinstance(n.get("interact_info"), dict)
            else {}
        )
        like_count = 0
        comment_count = 0
        for key in ("likedCount", "liked_count", "likeCount", "like_count", "likes", "liked"):
            val = stats.get(key) if stats else None
            if isinstance(val, (int, float)):
                like_count = int(val)
                break
        for key in (
            "commentCount",
            "comment_count",
            "commentsCount",
            "comments_count",
            "comments",
            "commented",
        ):
            val = stats.get(key) if stats else None
            if isinstance(val, (int, float)):
                comment_count = int(val)
                break
        hashtags: List[str] = []
        for key in ("hashtags", "tagList", "tag_list", "topics", "keywords"):
            vals = n.get(key) or card.get(key)
            if isinstance(vals, list):
                for v in vals:
                    if isinstance(v, dict):
                        txt = str(v.get("name") or v.get("tagName") or v.get("keyword") or "").strip()
                    else:
                        txt = str(v).strip()
                    if txt and txt not in hashtags:
                        hashtags.append(txt)
        if not hashtags:
            text_blob = " ".join([title, str(n.get("desc") or ""), str(n.get("noteCard", {}).get("desc") if isinstance(n.get("noteCard"), dict) else "")])
            hashtags = re.findall(r"#([\w\u4e00-\u9fff\-]+)", text_blob)
        cover_url = _cover_url_from_note(n, card)
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
                published_date=pub_date,
                like_count=like_count,
                comment_count=comment_count,
                hashtags=hashtags,
                cover_url=cover_url,
                note_url=url,
            )
        )
    return items


def parse_favorites_from_response_capture(
    capture: Dict[str, Any],
    *,
    owner_creator_id: str,
    profile_url: str,
    fetch_source: str = "collect_page",
) -> List[FavoritesFeedItem]:
    """解析页面自身已签名的收藏分页响应，不在浏览器外重算签名。

    ``capture`` 由 ``xhs_local_browser`` 在真实页面上下文拦截
    ``/api/sns/web/v2/note/collect/page`` 后产生。这里只消费响应体中的公开
    笔记结构；请求头、Cookie 和签名字段不会离开浏览器上下文。
    """
    pages = capture.get("pages") if isinstance(capture, dict) else []
    if not isinstance(pages, list):
        return []

    by_note: Dict[str, FavoritesFeedItem] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        notes = page.get("items")
        if not isinstance(notes, list) or not notes:
            continue
        transport = str(page.get("transport") or "xhr").strip().lower()
        source = f"{fetch_source}_{transport}"
        parsed = parse_favorites_from_init_state(
            {"collect": {"notes": notes}},
            owner_creator_id=owner_creator_id,
            profile_url=profile_url,
            fetch_source=source,
        )
        for item in parsed:
            current = by_note.get(item.note_id)
            if current is None:
                by_note[item.note_id] = item
                continue
            current_score = (
                (8 if "xsec_token" in (current.canonical_url or "") else 0)
                + (4 if current.author_id else 0)
                + (2 if current.title and not current.title.startswith("笔记 ") else 0)
                + (1 if current.cover_url else 0)
            )
            item_score = (
                (8 if "xsec_token" in (item.canonical_url or "") else 0)
                + (4 if item.author_id else 0)
                + (2 if item.title and not item.title.startswith("笔记 ") else 0)
                + (1 if item.cover_url else 0)
            )
            if item_score > current_score:
                by_note[item.note_id] = item
    return list(by_note.values())


def favorites_catalog_metrics(items: List[FavoritesFeedItem]) -> Dict[str, Any]:
    """生成无敏感值的覆盖率指标，便于同参数比较旧路径与响应捕获路径。"""
    total = len(items)
    source_counts: Dict[str, int] = {}
    for item in items:
        source = str(getattr(item, "fetch_source", "") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    def _ratio(count: int) -> float:
        return round(count / total, 4) if total else 0.0

    note_ids = sum(1 for item in items if is_valid_xhs_note_id(item.note_id))
    tokens = sum(1 for item in items if "xsec_token" in (item.canonical_url or ""))
    titles = sum(1 for item in items if item.title and not item.title.startswith("笔记 "))
    authors = sum(1 for item in items if item.author_id or item.author_name)
    return {
        "count": total,
        "source_counts": source_counts,
        "note_id_complete": note_ids,
        "note_id_rate": _ratio(note_ids),
        "xsec_token_complete": tokens,
        "xsec_token_rate": _ratio(tokens),
        "title_complete": titles,
        "title_rate": _ratio(titles),
        "author_complete": authors,
        "author_rate": _ratio(authors),
    }


def enrich_favorites_feed_items_with_xsec(
    items: List[FavoritesFeedItem],
    *,
    creator_id: str,
    profile_url: str = "",
) -> List[FavoritesFeedItem]:
    """对缺少 xsec_token 的收藏笔记，在收藏页逐条点击补全真实链接。"""
    if not items:
        return items

    bare = [
        it
        for it in items
        if is_valid_xhs_note_id(getattr(it, "note_id", "") or "")
        and "xsec_token" not in (getattr(it, "canonical_url", "") or "")
    ]
    if not bare:
        return items

    cid = (creator_id or "").strip()
    fav_url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note" if cid else "")
    if cid and not fav_url.rstrip("/").endswith(("tab=fav", "tab=collect", "tab=favorite")) and "tab=fav" not in fav_url:
        fav_url = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"

    from .xhs_local_browser import resolve_bare_favorites_note_links_via_click

    try:
        resolved = resolve_bare_favorites_note_links_via_click(
            fav_url,
            [it.note_id for it in bare],
            creator_id=cid,
            max_clicks=max(len(items), len(bare) + 5, 20),
        )
    except Exception as ex:
        _log.warning(
            "[%s|enrich_favorites_feed_items_with_xsec|%s|Agent执行|点击补token失败] error=%s",
            _CHAIN,
            cid,
            ex,
        )
        resolved = {}

    enriched = 0
    for it in items:
        url = resolved.get(it.note_id)
        if not url or "xsec_token" not in url:
            continue
        it.canonical_url = url
        it.note_url = url
        it.url_hash = link_url_hash(url)
        enriched += 1

    _log.info(
        "[%s|enrich_favorites_feed_items_with_xsec|%s|Agent执行|补全] bare=%s; enriched=%s; with_token=%s",
        _CHAIN,
        cid,
        len(bare),
        enriched,
        sum(1 for it in items if "xsec_token" in (it.canonical_url or "")),
    )
    return items


def fetch_favorites_catalog(
    creator_id: str,
    *,
    profile_url: str = "",
    limit: int = 80,
    scroll_rounds: int = 6,
    prefer_cookies: bool = False,
) -> Tuple[List[FavoritesFeedItem], bool]:
    """
    拉取收藏夹笔记列表（最新在前）。
    返回 (items, ok)；失败时 items 可能为空。
    """
    from .xhs_local_browser import scrape_favorites_feed_items

    started_at = time.perf_counter()
    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    try:
        items = scrape_favorites_feed_items(
            url,
            creator_id=creator_id,
            scroll_rounds=scroll_rounds,
            prefer_cookies=prefer_cookies,
        )
    except Exception as ex:
        _log.error(
            "[%s|xhs_favorites_adapter.fetch_favorites_catalog|%s|Agent执行|CDP] 失败; error=%s",
            _CHAIN,
            creator_id,
            ex,
        )
        return [], False

    if not items:
        _log.error(
            "[%s|xhs_favorites_adapter.fetch_favorites_catalog|%s|Agent执行|空列表] 所有采集策略均失败或返回空",
            _CHAIN,
            creator_id,
        )
        return [], False

    valid_items = [
        it
        for it in items
        if is_valid_xhs_note_id(getattr(it, "note_id", "") or "")
        and extract_xhs_note_id_from_url(getattr(it, "canonical_url", "") or "") == it.note_id
    ]
    if not valid_items:
        raise RuntimeError(
            "SUB_FAVORITES_INVALID_LINKS: 收藏页采集到的链接均非真实笔记 ID（疑似 fav_* 伪造或 DOM 未加载）。"
            "请确认 Chrome 收藏 Tab 已打开且页面已滚动加载。"
        )
    items = valid_items[:limit]
    items = enrich_favorites_feed_items_with_xsec(
        items,
        creator_id=creator_id,
        profile_url=url,
    )

    metrics = favorites_catalog_metrics(items)
    metrics["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
    _log.info(
        "[%s|xhs_favorites_adapter.fetch_favorites_catalog|%s|Agent执行|拉取] "
        "完成; metrics=%s",
        _CHAIN,
        creator_id,
        json.dumps(metrics, ensure_ascii=False, sort_keys=True),
    )
    return items[:limit], True


def aggregate_favorite_up_authors(
    items: List[FavoritesFeedItem],
    *,
    owner_creator_id: str = "",
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """从收藏笔记列表去重聚合博主（收藏 UP）。"""
    owner = (owner_creator_id or "").strip().lower()
    by_author: Dict[str, Dict[str, Any]] = {}
    for it in items:
        aid = str(it.author_id or "").strip()
        if not aid or not re.fullmatch(r"[a-f0-9]{24}", aid, re.I):
            continue
        if owner and aid.lower() == owner:
            continue
        name = str(it.author_name or "").strip() or aid[:8]
        row = by_author.get(aid)
        if not row:
            row = {
                "creator_id": aid,
                "display_name": name,
                "profile_url": f"https://www.xiaohongshu.com/user/profile/{aid}",
                "note_count": 0,
                "sample_titles": [],
            }
            by_author[aid] = row
        row["note_count"] = int(row.get("note_count") or 0) + 1
        if name and (not row.get("display_name") or row["display_name"] == aid[:8]):
            row["display_name"] = name
        titles = row.get("sample_titles") or []
        title = str(it.title or "").strip()
        if title and title not in titles and len(titles) < 3:
            titles.append(title)
        row["sample_titles"] = titles

    ranked = sorted(by_author.values(), key=lambda x: int(x.get("note_count") or 0), reverse=True)
    return ranked[: max(1, limit)]


def fetch_favorite_up_authors(
    creator_id: str,
    *,
    profile_url: str = "",
    limit: int = 40,
    scroll_rounds: int = 6,
    prefer_cookies: bool = False,
) -> Dict[str, Any]:
    """拉取收藏夹中的博主列表（收藏 UP）。"""
    from .xhs_local_browser import scrape_favorites_feed_items, should_prefer_cookie_favorites_fetch

    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    prefer = bool(prefer_cookies or should_prefer_cookie_favorites_fetch())
    items = scrape_favorites_feed_items(
        url,
        creator_id=creator_id,
        scroll_rounds=scroll_rounds,
        prefer_cookies=prefer,
    )
    authors = aggregate_favorite_up_authors(
        items,
        owner_creator_id=creator_id,
        limit=limit,
    )
    _log.info(
        "[%s|xhs_favorites_adapter.fetch_favorite_up_authors|%s|Agent执行|完成] "
        "notes=%s; authors=%s",
        _CHAIN,
        creator_id,
        len(items),
        len(authors),
    )
    return {
        "ok": True,
        "creator_id": creator_id,
        "notes_scanned": len(items),
        "authors": authors,
        "author_count": len(authors),
    }


def resolve_favorites_owner(red_id: str, *, display_name: str = "") -> Dict[str, Any]:
    """解析收藏夹所属用户（小红书号 → creator_id）；强制已配置 Chrome 会话。"""
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
