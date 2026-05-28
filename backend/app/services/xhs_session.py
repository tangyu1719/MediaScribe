"""小红书 Web 会话探测（访客 / 已登录）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from .creator_feed_adapter import _parse_init_state

_log = logging.getLogger("sba.xhs_session")


def probe_xhs_session(sess: requests.Session) -> Dict[str, Any]:
    """GET 探索页并解析 __INITIAL_STATE__ 中的登录态。"""
    resp = sess.get("https://www.xiaohongshu.com/explore", timeout=30)
    if resp.status_code != 200:
        return {
            "ok": False,
            "guest": True,
            "logged_in": False,
            "http_status": resp.status_code,
            "error": f"HTTP {resp.status_code}",
        }
    state = _parse_init_state(resp.text) or {}
    user = state.get("user") if isinstance(state.get("user"), dict) else {}
    info = user.get("userInfo") or user.get("basicInfo") or {}
    if not isinstance(info, dict):
        info = {}
    guest = bool(info.get("guest"))
    logged_in = bool(user.get("loggedIn"))
    if not logged_in and guest:
        guest = True
    return {
        "ok": True,
        "guest": guest,
        "logged_in": logged_in and not guest,
        "user_id": str(info.get("userId") or info.get("user_id") or ""),
        "nickname": str(info.get("nickname") or info.get("name") or ""),
    }


def require_xhs_logged_in(sess: requests.Session) -> Dict[str, Any]:
    """已登录则返回探测信息；访客或未登录则抛错。"""
    info = probe_xhs_session(sess)
    if not info.get("ok"):
        raise RuntimeError(
            f"SUB_XHS_SESSION_UNREACHABLE: 无法探测小红书会话（{info.get('error', 'unknown')}）"
        )
    if info.get("guest") or not info.get("logged_in"):
        raise RuntimeError(
            "SUB_XHS_GUEST_SESSION: 当前为访客 Cookie，无法搜索用户或拉取博主作品。"
            "请在 Chrome 登录小红书后执行：设置页提取 Cookie，或运行 "
            "backend/scripts/refresh_xhs_cookies.py"
        )
    return info
