"""小红书无状态（访客）会话 — Cookie/Chrome 获取失败 3 次后降级使用。"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import string
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from .cookie_manager import load_cookies, save_cookies
from .creator_feed_adapter import (
    _CHAIN_RESOLVE,
    _find_user_by_red_id_in_obj,
    _parse_init_state,
    _user_dict_to_resolved,
)

_log = logging.getLogger("sba.xhs_stateless")

_MAX_ATTEMPTS = max(1, int(os.environ.get("SBA_XHS_COOKIE_MAX_ATTEMPTS", "3") or "3"))
_cookie_attempts = 0
_stateless_active = False

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)


def cookie_attempts() -> int:
    return _cookie_attempts


def stateless_active() -> bool:
    if os.environ.get("SBA_XHS_SKIP_CHROME", "").strip() in ("1", "true", "yes"):
        return True
    return _stateless_active or os.environ.get("SBA_XHS_STATELESS", "").strip() in ("1", "true", "yes")


def should_use_stateless() -> bool:
    if stateless_active():
        return True
    return _cookie_attempts >= _MAX_ATTEMPTS


def record_cookie_attempt(*, ok: bool = False) -> int:
    """记录一次 Cookie/Chrome 获取尝试；成功则清零计数。"""
    global _cookie_attempts, _stateless_active
    if ok:
        _cookie_attempts = 0
        return 0
    _cookie_attempts += 1
    if _cookie_attempts >= _MAX_ATTEMPTS:
        _stateless_active = True
        _log.warning(
            "[社媒订阅-小红书会话|xhs_stateless.record_cookie_attempt|Cookie|硬编执行|降级] "
            "Cookie 获取已失败 %s 次，切换无状态访客模式; max=%s",
            _cookie_attempts,
            _MAX_ATTEMPTS,
        )
    return _cookie_attempts


def _gen_a1() -> str:
    return "1" + "".join(random.choices(string.ascii_lowercase + string.digits, k=51))


def bootstrap_stateless_session() -> requests.Session:
    """初始化访客 Session（不依赖登录 Cookie）。"""
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": _UA,
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    seed = load_cookies("xiaohongshu") or {}
    for k, v in seed.items():
        if v:
            sess.cookies.set(k, str(v), domain=".xiaohongshu.com")
    if not sess.cookies.get("a1"):
        sess.cookies.set("a1", _gen_a1(), domain=".xiaohongshu.com")
    if not sess.cookies.get("webId"):
        sess.cookies.set("webId", uuid.uuid4().hex, domain=".xiaohongshu.com")
    sess.cookies.set("xsecappid", "xhs-pc-web", domain=".xiaohongshu.com")

    try:
        sess.get("https://www.xiaohongshu.com/explore", timeout=30)
        time.sleep(0.5)
    except Exception as ex:
        _log.warning(
            "[社媒订阅-小红书会话|xhs_stateless.bootstrap_stateless_session|explore|硬编执行|失败] error=%s",
            ex,
        )

    out: Dict[str, str] = {}
    for c in sess.cookies:
        if "xiaohongshu" in (c.domain or ""):
            out[c.name] = c.value
    if out:
        save_cookies("xiaohongshu", out)
    return sess


def _profile_red_id(sess: requests.Session, creator_id: str) -> Optional[str]:
    url = f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    try:
        r = sess.get(url, timeout=30)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    state = _parse_init_state(r.text)
    if not state:
        return None
    user = state.get("user") or {}
    if isinstance(user, dict):
        ui = user.get("userInfo") or user.get("basicInfo") or user
        if isinstance(ui, dict):
            rid = str(ui.get("redId") or ui.get("red_id") or "")
            if rid:
                return rid
    blob = r.text
    m = re.search(r'"redId"\s*:\s*"(\d{6,20})"', blob)
    return m.group(1) if m else None


def resolve_red_id_stateless(red_id: str, *, display_name: str = "") -> Dict[str, Any]:
    """无状态解析小红书号 → user_id（HTML/SSR，不要求登录）。"""
    red_id = (red_id or "").strip()
    display_name = (display_name or os.environ.get("PROFILE_REGRESSION_DISPLAY_NAME") or "").strip()
    if not red_id:
        raise ValueError("SUB_INVALID_URL")

    override = (os.environ.get("TEST_XHS_CREATOR_ID") or os.environ.get(f"SBA_XHS_CREATOR_ID_{red_id}") or "").strip()
    if override and re.fullmatch(r"[a-f0-9]{24}", override, re.I):
        return {
            "creator_id": override,
            "display_name": display_name or red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{override}",
            "red_id": red_id,
            "source": "stateless_env_creator_id",
        }

    cached = _load_red_id_cache().get(red_id)
    if cached and re.fullmatch(r"[a-f0-9]{24}", str(cached), re.I):
        return {
            "creator_id": cached,
            "display_name": display_name or red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{cached}",
            "red_id": red_id,
            "source": "stateless_cache",
        }

    _log.info(
        "[社媒订阅-小红书号解析|xhs_stateless.resolve_red_id_stateless|%s|硬编执行|开始] 无状态访客模式; red_id=%s",
        red_id,
        red_id,
    )
    sess = bootstrap_stateless_session()

    keywords = [red_id]
    if display_name and display_name not in keywords:
        keywords.insert(0, display_name)

    for kw in keywords:
        for suffix in ("&type=user", ""):
            search_url = f"https://www.xiaohongshu.com/search_result?keyword={kw}{suffix}"
            r = sess.get(search_url, timeout=30)
            if r.status_code != 200:
                continue
            got = _parse_search_html(r.text, red_id, sess)
            if got:
                _save_red_id_cache(red_id, got["creator_id"])
                return got
            got = _parse_note_search_for_author(r.text, red_id, sess)
            if got:
                _save_red_id_cache(red_id, got["creator_id"])
                return got

    raise RuntimeError(
        f"SUB_RED_ID_NOT_FOUND: 无状态模式未找到小红书号 {red_id}。"
        "可设置 TEST_XHS_CREATOR_ID=24位user_id 或提供 profile 链接直接订阅。"
    )


def _cache_path() -> Path:
    return Path(__file__).resolve().parents[2] / "output" / ".xhs_red_id_cache.json"


def _load_red_id_cache() -> Dict[str, str]:
    p = _cache_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_red_id_cache(red_id: str, creator_id: str) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _load_red_id_cache()
    data[red_id] = creator_id
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_search_html(html: str, red_id: str, sess: requests.Session) -> Optional[Dict[str, Any]]:
    state = _parse_init_state(html)
    if state:
        hit = _find_user_by_red_id_in_obj(state, red_id)
        if hit:
            got = _user_dict_to_resolved(hit, red_id, "stateless_search_init")
            _log.info(
                "[%s|xhs_stateless._parse_search_html|%s|硬编执行|成功] creator_id=%s; source=init_state",
                _CHAIN_RESOLVE,
                red_id,
                got["creator_id"],
            )
            return got

        blob = json.dumps(state, ensure_ascii=False)
        for pat in (
            rf'"redId"\s*:\s*"{re.escape(red_id)}".{{0,1200}}?"id"\s*:\s*"([a-f0-9]{{24}})"',
            rf'"id"\s*:\s*"([a-f0-9]{{24}})".{{0,1200}}?"redId"\s*:\s*"{re.escape(red_id)}"',
        ):
            m = re.search(pat, blob, re.S)
            if m:
                uid = m.group(1)
                return {
                    "creator_id": uid,
                    "display_name": red_id,
                    "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
                    "red_id": red_id,
                    "source": "stateless_search_regex",
                }

    profile_ids = list(dict.fromkeys(re.findall(r"/user/profile/([a-f0-9]{24})", html, re.I)))
    for uid in profile_ids:
        rid = _profile_red_id(sess, uid)
        if rid == red_id:
            return {
                "creator_id": uid,
                "display_name": red_id,
                "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
                "red_id": red_id,
                "source": "stateless_profile_verify",
            }

    if profile_ids:
        uid = profile_ids[0]
        _log.warning(
            "[社媒订阅-小红书号解析|xhs_stateless._parse_search_html|%s|硬编执行|弱匹配] "
            "未校验 red_id，使用首个 profile 链接; creator_id=%s",
            red_id,
            uid,
        )
        return {
            "creator_id": uid,
            "display_name": red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
            "red_id": red_id,
            "source": "stateless_profile_link",
        }
    return None


def _parse_note_search_for_author(html: str, red_id: str, sess: requests.Session) -> Optional[Dict[str, Any]]:
    """综合搜索页：从笔记作者信息反查 user_id。"""
    state = _parse_init_state(html)
    if not state:
        return None
    blob = json.dumps(state, ensure_ascii=False)
    if red_id not in blob:
        return None
    hit = _find_user_by_red_id_in_obj(state, red_id)
    if hit:
        return _user_dict_to_resolved(hit, red_id, "stateless_note_search")
    for pat in (
        rf'"redId"\s*:\s*"{re.escape(red_id)}".{{0,800}}?"userId"\s*:\s*"([a-f0-9]{{24}})"',
        rf'"userId"\s*:\s*"([a-f0-9]{{24}})".{{0,800}}?"redId"\s*:\s*"{re.escape(red_id)}"',
    ):
        m = re.search(pat, blob, re.S)
        if m:
            uid = m.group(1)
            return {
                "creator_id": uid,
                "display_name": red_id,
                "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
                "red_id": red_id,
                "source": "stateless_note_author",
            }
    return None
