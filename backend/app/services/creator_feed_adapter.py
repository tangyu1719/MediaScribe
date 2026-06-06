"""社媒博主作品列表拉取 — 小红书 MVP（requests + Cookie）。"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urlparse

import requests

from .link_hash import normalize_link_for_hash, url_hash as link_url_hash

_log = logging.getLogger("sba.creator_feed_adapter")

_XHS_PROFILE_RE = re.compile(
    r"xiaohongshu\.com/user/profile/([a-zA-Z0-9]{6,64})",
    re.I,
)


@dataclass
class FeedItem:
    platform: str
    note_id: str
    canonical_url: str
    url_hash: str
    content_type: str
    title: str
    published_at: Optional[str]
    author_id: str
    author_name: str
    fetch_source: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CreatorFeedAdapter(Protocol):
    def fetch_page(
        self, creator_id: str, cursor: int, limit: int, *, profile_url: str = ""
    ) -> Tuple[List[FeedItem], int, bool]:
        """返回 (items, next_cursor, has_more)。"""
        ...


def parse_xiaohongshu_profile_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ValueError("SUB_INVALID_URL")
    if re.fullmatch(r"\d{6,20}", u):
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    m = _XHS_PROFILE_RE.search(u)
    if not m:
        raise ValueError("SUB_INVALID_URL")
    return m.group(1)


def _parse_init_state(html: str) -> Optional[Dict[str, Any]]:
    """解析页面 __INITIAL_STATE__（兼容 undefined、超长 JSON）。"""
    raw = None
    for pat in (
        r"window\.__INITIAL_STATE__\s*=\s*(\{.+)",
        r"__INITIAL_STATE__\s*=\s*(\{.+)",
    ):
        match = re.search(pat, html, re.DOTALL)
        if match:
            raw = match.group(1)
            break
    if not raw:
        return None
    depth = 0
    end = 0
    for i, ch in enumerate(raw):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end <= 0:
        return None
    chunk = raw[:end]
    chunk = re.sub(r"\bundefined\b", "null", chunk)
    chunk = re.sub(r",\s*}", "}", chunk)
    chunk = re.sub(r",\s*]", "]", chunk)
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        return None


def _find_user_by_red_id_in_obj(obj: Any, red_id: str) -> Optional[Dict[str, Any]]:
    """在任意 JSON 结构中查找 redId/red_id 匹配的用户对象。"""
    if isinstance(obj, dict):
        rid = str(obj.get("redId") or obj.get("red_id") or "")
        if rid == red_id:
            uid = obj.get("id") or obj.get("userId") or obj.get("user_id")
            if uid and re.fullmatch(r"[a-f0-9]{24}", str(uid), re.I):
                return obj
        for v in obj.values():
            found = _find_user_by_red_id_in_obj(v, red_id)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_user_by_red_id_in_obj(item, red_id)
            if found:
                return found
    return None


def _user_dict_to_resolved(u: Dict[str, Any], red_id: str, source: str) -> Dict[str, Any]:
    uid = str(u.get("id") or u.get("userId") or u.get("user_id") or "")
    display_name = str(u.get("nickname") or u.get("name") or red_id)
    return {
        "creator_id": uid,
        "display_name": display_name,
        "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
        "red_id": red_id,
        "source": source,
    }


_CHAIN_RESOLVE = "社媒订阅-小红书号解析"


def _post_search_usersearch(sess: requests.Session, red_id: str) -> Optional[Dict[str, Any]]:
    """调用 PC Web 用户搜索 API（须已登录 Cookie）。"""
    body = {
        "searchUserRequest": {
            "keyword": red_id,
            "page": 1,
            "pageSize": 20,
            "searchId": uuid.uuid4().hex,
            "requestId": uuid.uuid4().hex,
        }
    }
    headers = {
        "Referer": f"https://www.xiaohongshu.com/search_result?keyword={red_id}&type=user",
        "Origin": "https://www.xiaohongshu.com",
        "Content-Type": "application/json;charset=UTF-8",
    }
    r = sess.post(
        "https://www.xiaohongshu.com/api/sns/web/v1/search/usersearch",
        json=body,
        headers=headers,
        timeout=30,
    )
    if r.status_code != 200:
        _log.info(
            "[%s|creator_feed_adapter._post_search_usersearch|usersearch|工具执行|请求] "
            "用户搜索 HTTP 非 200; status=%s; body=%s",
            _CHAIN_RESOLVE,
            r.status_code,
            (r.text or "")[:200],
        )
        return None
    try:
        return r.json()
    except json.JSONDecodeError:
        return None


def resolve_xhs_red_id(red_id: str, *, display_name: str = "") -> Dict[str, Any]:
    """
    将「小红书号」解析为内部 user_id 与 profile_url。
    依赖有效 Cookie（backend/output/.xhs_cookies.json 或 SBA_XHS_COOKIE）。
    """
    red_id = (red_id or "").strip()
    if not red_id:
        raise ValueError("SUB_INVALID_URL")
    # 已是 24 位 hex 主页 id 则直接返回
    if re.fullmatch(r"[a-f0-9]{24}", red_id, re.I):
        return {
            "creator_id": red_id,
            "display_name": red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{red_id}",
            "red_id": red_id,
            "source": "direct_hex_id",
        }

    adapter = XiaohongshuFeedAdapter()
    sess = adapter._session()
    from .cookie_manager import load_cookies
    from .xhs_session import require_xhs_logged_in

    cookies = load_cookies("xiaohongshu") or {}
    if not cookies:
        raise RuntimeError(
            "SUB_FETCH_AUTH_FAILED: 无小红书 Cookie，请配置 backend/output/.xhs_cookies.json"
        )

    override = (os.environ.get("TEST_XHS_CREATOR_ID") or "").strip()
    if override and re.fullmatch(r"[a-f0-9]{24}", override, re.I):
        _log.warning(
            "[%s|creator_feed_adapter.resolve_xhs_red_id|%s|硬编执行|兜底] "
            "使用 TEST_XHS_CREATOR_ID; red_id=%s; creator_id=%s",
            _CHAIN_RESOLVE,
            red_id,
            red_id,
            override,
        )
        return {
            "creator_id": override,
            "display_name": red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{override}",
            "red_id": red_id,
            "source": "env_test_creator_id",
        }

    from .xhs_session import probe_xhs_session
    from .xhs_stateless import (
        cookie_attempts,
        record_cookie_attempt,
        resolve_red_id_stateless,
        should_use_stateless,
    )

    if should_use_stateless():
        return resolve_red_id_stateless(red_id, display_name=display_name)

    probe = probe_xhs_session(sess)
    if probe.get("guest") or not probe.get("logged_in"):
        from .xhs_local_browser import refresh_xhs_cookies_from_system

        refresh_xhs_cookies_from_system()
        cookies = load_cookies("xiaohongshu") or {}
        for k, v in cookies.items():
            sess.cookies.set(k, v, domain=".xiaohongshu.com")
        probe = probe_xhs_session(sess)
    if probe.get("guest") or not probe.get("logged_in"):
        from .xhs_stateless import _MAX_ATTEMPTS as _max_att
        from .xhs_local_browser import resolve_red_id_via_local_chrome

        _log.info(
            "[%s|creator_feed_adapter.resolve_xhs_red_id|%s|Agent执行|回退] "
            "Cookie 非登录态，尝试本机 Chrome（最多 %s 次后无状态）; red_id=%s",
            _CHAIN_RESOLVE,
            red_id,
            _max_att,
            red_id,
        )
        try:
            got = resolve_red_id_via_local_chrome(red_id)
            record_cookie_attempt(ok=True)
            return got
        except Exception as ex:
            record_cookie_attempt(ok=False)
            if should_use_stateless():
                return resolve_red_id_stateless(red_id, display_name=display_name)
            raise RuntimeError(
                f"SUB_XHS_COOKIE_UNAVAILABLE: Chrome Cookie 获取失败（已尝试 {cookie_attempts()} 次）: {ex}"
            ) from ex

    require_xhs_logged_in(sess)

    # 1) 官方 POST usersearch（与前端 searchUserRequest 一致）
    api_data = _post_search_usersearch(sess, red_id)
    if api_data:
        hit = _find_user_by_red_id_in_obj(api_data, red_id)
        if hit:
            _log.info(
                "[%s|creator_feed_adapter.resolve_xhs_red_id|%s|Agent执行|解析] "
                "小红书号解析成功; source=usersearch_api; red_id=%s",
                _CHAIN_RESOLVE,
                red_id,
                red_id,
            )
            return _user_dict_to_resolved(hit, red_id, "usersearch_api")

    # 2) 搜索页 HTML / INITIAL_STATE（部分环境 SSR 会带上 userLists）
    r = sess.get(
        f"https://www.xiaohongshu.com/search_result?keyword={red_id}&type=user",
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"SUB_PROFILE_UNREACHABLE: search HTTP {r.status_code}")
    state = _parse_init_state(r.text)
    if state:
        hit = _find_user_by_red_id_in_obj(state, red_id)
        if hit:
            return _user_dict_to_resolved(hit, red_id, "search_init_state")

        blob = json.dumps(state, ensure_ascii=False)
        m = re.search(
            rf'"redId"\s*:\s*"{re.escape(red_id)}".{{0,1200}}?"id"\s*:\s*"([a-f0-9]{{24}})"',
            blob,
            re.S,
        )
        if not m:
            m = re.search(
                rf'"id"\s*:\s*"([a-f0-9]{{24}})".{{0,1200}}?"redId"\s*:\s*"{re.escape(red_id)}"',
                blob,
                re.S,
            )
        if m:
            uid = m.group(1)
            return {
                "creator_id": uid,
                "display_name": red_id,
                "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
                "red_id": red_id,
                "source": "search_init_state_regex",
            }

    try:
        from .xhs_local_browser import resolve_red_id_via_local_chrome
        from .xhs_stateless import record_cookie_attempt, resolve_red_id_stateless, should_use_stateless

        if should_use_stateless():
            return resolve_red_id_stateless(red_id, display_name=display_name)
        try:
            got = resolve_red_id_via_local_chrome(red_id)
            record_cookie_attempt(ok=True)
            return got
        except Exception as ex2:
            record_cookie_attempt(ok=False)
            if should_use_stateless():
                return resolve_red_id_stateless(red_id)
            raise RuntimeError(
                f"SUB_RED_ID_NOT_FOUND: 未找到小红书号 {red_id} 对应的用户（{ex2}）。"
                "请确认号正确；或在 App 打开博主主页复制 profile 链接（含 24 位 user_id）直接订阅。"
            ) from ex2
    except RuntimeError:
        raise
    except Exception as ex:
        from .xhs_stateless import resolve_red_id_stateless, should_use_stateless

        if should_use_stateless():
            return resolve_red_id_stateless(red_id, display_name=display_name)
        raise RuntimeError(
            f"SUB_RED_ID_NOT_FOUND: 未找到小红书号 {red_id} 对应的用户（{ex}）。"
            "请确认号正确；或在 App 打开博主主页复制 profile 链接（含 24 位 user_id）直接订阅。"
        ) from ex


def _note_type_to_content(note: Dict[str, Any]) -> str:
    t = (note.get("type") or note.get("noteType") or "").lower()
    if t in ("video", "normal_video"):
        return "video"
    if "video" in t:
        return "video"
    return "graphic"


def _build_note_url(note_id: str, xsec_token: str = "") -> str:
    base = f"https://www.xiaohongshu.com/explore/{note_id}"
    if xsec_token:
        return f"{base}?xsec_token={xsec_token}&xsec_source=pc_user"
    return base


def _extract_notes_from_state(
    data: Dict[str, Any], creator_id: str, profile_url: str
) -> List[Dict[str, Any]]:
    """从 __INITIAL_STATE__ 多路径尝试提取笔记列表。"""
    candidates: List[Any] = []

    user = data.get("user") or {}
    if isinstance(user, dict):
        for key in ("notes", "noteList", "posted", "postedNotes"):
            if user.get(key):
                candidates.append(user.get(key))
        user_page = user.get("userPage") or user.get("userPageData") or {}
        if isinstance(user_page, dict):
            for key in ("notes", "noteList", "feeds", "items"):
                if user_page.get(key):
                    candidates.append(user_page.get(key))

    profile = data.get("profile") or data.get("userProfile") or {}
    if isinstance(profile, dict):
        for key in ("notes", "noteList", "feeds"):
            if profile.get(key):
                candidates.append(profile.get(key))

    notes_map = data.get("notes") or {}
    if isinstance(notes_map, dict):
        for key in ("notes", "noteList", "feeds", creator_id):
            if notes_map.get(key):
                candidates.append(notes_map.get(key))

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

    # 去重 note_id
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
    # 浏览器 SSR 结构变更时，从 JSON blob 兜底提取 noteId
    blob = json.dumps(data, ensure_ascii=False)
    for nid in re.findall(r'"noteId"\s*:\s*"([a-f0-9]{24})"', blob, re.I):
        if nid in seen:
            continue
        seen.add(nid)
        title_m = re.search(
            rf'"noteId"\s*:\s*"{re.escape(nid)}".{{0,800}}?"title"\s*:\s*"([^"]*)"', blob, re.S
        )
        display_m = re.search(
            rf'"noteId"\s*:\s*"{re.escape(nid)}".{{0,800}}?"displayTitle"\s*:\s*"([^"]*)"', blob, re.S
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


def parse_feed_from_init_state(
    data: Dict[str, Any],
    *,
    creator_id: str,
    profile_url: str,
    xsec_token: str = "",
    fetch_source: str = "crawler",
) -> List[FeedItem]:
    raw_notes = _extract_notes_from_state(data, creator_id, profile_url)
    items: List[FeedItem] = []
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
        token = xsec_token or str(n.get("xsecToken") or n.get("xsec_token") or "")
        url = _build_note_url(nid, token)
        norm = normalize_link_for_hash(url)
        uh = link_url_hash(url)
        pub = n.get("time") or n.get("createTime") or n.get("publishTime")
        pub_str = None
        if pub:
            try:
                if isinstance(pub, (int, float)):
                    pub_str = datetime.fromtimestamp(int(pub) / 1000 if pub > 1e12 else int(pub)).isoformat()
                else:
                    pub_str = str(pub)
            except Exception:
                pub_str = str(pub)
        author = n.get("user") or {}
        items.append(
            FeedItem(
                platform="xiaohongshu",
                note_id=nid,
                canonical_url=url,
                url_hash=uh,
                content_type=_note_type_to_content(n),
                title=title,
                published_at=pub_str,
                author_id=creator_id,
                author_name=str(author.get("nickname") or author.get("name") or ""),
                fetch_source=fetch_source,
            )
        )
    return items


class XiaohongshuFeedAdapter:
    """小红书博主作品列表（主页 HTML 解析）。"""

    def __init__(self):
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def _session(self) -> requests.Session:
        from .cookie_manager import load_cookies

        sess = requests.Session()
        sess.headers.update(self._headers)
        cookies = load_cookies("xiaohongshu") or {}
        for k, v in cookies.items():
            sess.cookies.set(k, v, domain=".xiaohongshu.com")
        return sess

    def fetch_profile_meta(self, profile_url: str) -> Dict[str, Any]:
        from .xhs_session import probe_xhs_session
        from .xhs_stateless import bootstrap_stateless_session, should_use_stateless

        creator_id = parse_xiaohongshu_profile_url(profile_url)
        if should_use_stateless():
            sess = bootstrap_stateless_session()
        else:
            sess = self._session()
            probe = probe_xhs_session(sess)
            if probe.get("guest") or not probe.get("logged_in"):
                from .xhs_stateless import record_cookie_attempt

                record_cookie_attempt(ok=False)
                sess = bootstrap_stateless_session()
            else:
                from .xhs_session import require_xhs_logged_in

                require_xhs_logged_in(sess)
        resp = sess.get(profile_url, timeout=30, allow_redirects=True)
        data = _parse_init_state(resp.text)
        if not data:
            if resp.status_code != 200:
                raise RuntimeError(f"SUB_PROFILE_UNREACHABLE: HTTP {resp.status_code}")
            raise RuntimeError("SUB_PROFILE_PARSE_FAILED")
        if resp.status_code not in (200, 404) and "登录" in resp.text[:2000]:
            raise RuntimeError("SUB_FETCH_AUTH_FAILED")
        display_name = creator_id
        user = data.get("user") or {}
        if isinstance(user, dict):
            ui = user.get("userInfo") or user.get("basicInfo") or user
            if isinstance(ui, dict):
                display_name = str(ui.get("nickname") or ui.get("name") or display_name)
        xsec = ""
        m = re.search(r"xsec_token=([A-Za-z0-9_=-]+)", profile_url)
        if m:
            xsec = m.group(1)
        return {
            "creator_id": creator_id,
            "display_name": display_name,
            "xsec_token": xsec,
            "init_state": data,
        }

    def fetch_page(
        self,
        creator_id: str,
        cursor: int,
        limit: int,
        *,
        profile_url: str = "",
        init_state: Optional[Dict[str, Any]] = None,
        xsec_token: str = "",
    ) -> Tuple[List[FeedItem], int, bool]:
        limit = min(max(1, limit), 50)
        data = init_state
        token = xsec_token
        if data is None:
            url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
            meta = self.fetch_profile_meta(url)
            data = meta["init_state"]
            token = token or meta.get("xsec_token") or ""
        items = parse_feed_from_init_state(
            data,
            creator_id=creator_id,
            profile_url=profile_url,
            xsec_token=token,
            fetch_source="crawler",
        )
        start = max(0, cursor)
        page_items = items[start : start + limit]
        next_cursor = start + len(page_items)
        has_more = next_cursor < len(items)
        return page_items, next_cursor, has_more

    def fetch_catalog(
        self,
        creator_id: str,
        *,
        profile_url: str = "",
    ) -> List[FeedItem]:
        """拉取主页可见的全部笔记（用于 UP 画像目录）。"""
        url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
        meta = self.fetch_profile_meta(url)
        items = parse_feed_from_init_state(
            meta["init_state"],
            creator_id=creator_id,
            profile_url=url,
            xsec_token=meta.get("xsec_token") or "",
            fetch_source="catalog",
        )
        if items:
            return items
        _log.warning(
            "[社媒订阅-博主Feed|creator_feed_adapter.fetch_catalog|%s|Agent执行|回退] "
            "requests 未解析到笔记，尝试浏览器兜底; creator_id=%s",
            creator_id,
            creator_id,
        )
        from .xhs_local_browser import fetch_catalog_via_browser

        return fetch_catalog_via_browser(creator_id, profile_url=url)


def get_feed_adapter(platform: str) -> CreatorFeedAdapter:
    if platform == "xiaohongshu":
        return XiaohongshuFeedAdapter()
    raise ValueError(f"不支持的平台: {platform}")


def resolve_note_links_for_selection(
    selected_notes: List[Dict[str, Any]],
    *,
    creator_id: str,
    profile_url: str = "",
    catalog: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """从博主主页收集带 xsec_token 的真实笔记链接，再交给链接分析流水线。"""
    profile_url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    url_map: Dict[str, str] = {}

    def _put(nid: str, href: str, *, prefer_token: bool = True) -> None:
        if not nid or not href:
            return
        cur = url_map.get(nid) or ""
        if not cur:
            url_map[nid] = href
            return
        if prefer_token and "xsec_token" in href and "xsec_token" not in cur:
            url_map[nid] = href

    for it in catalog or []:
        _put(str(it.get("note_id") or ""), str(it.get("canonical_url") or ""))

    missing = [
        str(n.get("note_id") or "")
        for n in selected_notes
        if str(n.get("note_id") or "")
        and ("xsec_token" not in (url_map.get(str(n.get("note_id") or "")) or ""))
    ]
    if missing:
        try:
            from .xhs_local_browser import scrape_profile_note_links_via_cdp

            scraped = scrape_profile_note_links_via_cdp(profile_url, creator_id=creator_id)
            for nid, href in scraped.items():
                _put(nid, href)
            _log.info(
                "[社媒订阅-博主Feed|resolve_note_links_for_selection|profile|Agent执行|CDP采集] "
                "scraped=%s; still_missing=%s",
                len(scraped),
                sum(1 for nid in missing if "xsec_token" not in (url_map.get(nid) or "")),
            )
        except Exception as ex:
            _log.warning(
                "[社媒订阅-博主Feed|resolve_note_links_for_selection|profile|Agent执行|CDP失败] error=%s",
                ex,
            )

    still_missing = [
        nid for nid in missing if "xsec_token" not in (url_map.get(nid) or "")
    ]
    if still_missing:
        try:
            adapter = XiaohongshuFeedAdapter()
            meta = adapter.fetch_profile_meta(profile_url)
            refreshed = parse_feed_from_init_state(
                meta["init_state"],
                creator_id=creator_id,
                profile_url=profile_url,
                xsec_token=meta.get("xsec_token") or "",
                fetch_source="profile_link_refresh",
            )
            for it in refreshed:
                _put(it.note_id, it.canonical_url)
        except Exception as ex:
            _log.warning(
                "[社媒订阅-博主Feed|resolve_note_links_for_selection|profile|Agent执行|meta刷新失败] error=%s",
                ex,
            )

    out: List[Dict[str, Any]] = []
    for n in selected_notes:
        nid = str(n.get("note_id") or "")
        note = dict(n)
        old_url = str(note.get("canonical_url") or "")
        resolved = url_map.get(nid) or old_url or _build_note_url(nid, "")
        note["canonical_url"] = resolved
        note["pipeline_url"] = resolved
        if resolved != old_url:
            note["link_source"] = "profile_page"
        elif "xsec_token" in resolved:
            note["link_source"] = "catalog_token"
        else:
            note["link_source"] = "bare_explore"
        out.append(note)
        _log.info(
            "[社媒订阅-博主Feed|resolve_note_links_for_selection|note:%s|Agent执行|链接] "
            "source=%s; has_token=%s; url=%s",
            nid[:8],
            note.get("link_source"),
            "xsec_token" in resolved,
            resolved[:120],
        )
    return out
