"""启动后一次性：用 CDP 把当前 Tab 导航到收藏页（仅 open 脚本，非 scrape 主路径）。"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

_log = logging.getLogger("sba.chrome_profile_prep")


def cdp_navigate_tab(ws_url: str, url: str, *, timeout_sec: float = 30.0) -> bool:
    import websocket as _ws

    ws = _ws.create_connection(ws_url, timeout=int(timeout_sec))
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        nav_id = 2
        ws.send(
            json.dumps(
                {
                    "id": nav_id,
                    "method": "Page.navigate",
                    "params": {"url": url},
                }
            )
        )
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == nav_id:
                return "error" not in (msg.get("result") or {})
        return False
    finally:
        try:
            ws.close()
        except Exception:
            pass


def cdp_inject_cookies(ws_url: str, cookies: Dict[str, str]) -> int:
    """通过 CDP Network.setCookie 注入小红书 Cookie（不杀 Chrome）。"""
    import websocket as _ws

    if not cookies:
        return 0
    ws = _ws.create_connection(ws_url, timeout=15)
    injected = 0
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        msg_id = 2
        for name, val in cookies.items():
            if not name or val is None:
                continue
            ws.send(
                json.dumps(
                    {
                        "id": msg_id,
                        "method": "Network.setCookie",
                        "params": {
                            "name": name,
                            "value": str(val),
                            "domain": ".xiaohongshu.com",
                            "path": "/",
                            "secure": True,
                            "httpOnly": name in ("web_session", "id_token"),
                        },
                    }
                )
            )
            msg_id += 1
            injected += 1
        time.sleep(0.5)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return injected


def cdp_pick_xhs_tab_ws(port: int) -> Optional[str]:
    from .xhs_local_browser import cdp_list_tabs

    for t in cdp_list_tabs(port):
        u = str(t.get("url") or "")
        if u.startswith("chrome://") or u.startswith("devtools://") or u.startswith("data:"):
            continue
        if "sw.js" in u:
            continue
        ws = t.get("webSocketDebuggerUrl")
        if ws:
            return str(ws)
    return None


def wait_for_xhs_login_via_cdp(
    *,
    port: Optional[int] = None,
    timeout_sec: float = 600.0,
    poll_sec: float = 4.0,
    open_fav_on_success: bool = True,
) -> Dict[str, Any]:
    """
    轮询 CDP 附着页直到小红书登录成功（或超时）。
    不杀 Chrome、不新开 Profile；超时返回 logged_in=False。
    """
    from .cookie_manager import find_cdp_port, load_cookies, save_cookies_if_better
    from .xhs_owner_chrome import _extract_xhs_user_from_state, _expected_xhs_nickname, _nickname_matches
    from .creator_feed_adapter import _parse_init_state
    from .xhs_local_browser import cdp_list_tabs, cdp_tab_get_html, cdp_tab_get_xhs_cookies

    port = port or find_cdp_port()
    if not port:
        return {"ok": False, "logged_in": False, "error": "no_cdp"}

    cid = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "60dc2e340000000001008a1f").strip()
    fav = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"
    ws = cdp_pick_xhs_tab_ws(port)
    if ws:
        cdp_navigate_tab(ws, fav, timeout_sec=45)
        time.sleep(3)

    deadline = time.time() + timeout_sec
    last: Dict[str, Any] = {"logged_in": False}
    while time.time() < deadline:
        tabs = cdp_list_tabs(port)
        tab = None
        for t in tabs:
            u = str(t.get("url") or "")
            if "xiaohongshu.com" in u and "sw.js" not in u:
                tab = t
                break
        if not tab:
            time.sleep(poll_sec)
            continue
        ws_url = str(tab.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            time.sleep(poll_sec)
            continue
        html = cdp_tab_get_html(ws_url)
        parsed = _extract_xhs_user_from_state(_parse_init_state(html) or {})
        cookies = cdp_tab_get_xhs_cookies(ws_url)
        logged = bool(parsed.get("logged_in")) and not parsed.get("guest")
        nick = str(parsed.get("nickname") or "")
        if logged:
            save_cookies_if_better("xiaohongshu", cookies, owner_nickname=_expected_xhs_nickname())
            nick_ok = _nickname_matches(nick) if nick else True
            last = {
                "ok": nick_ok,
                "logged_in": True,
                "nickname": nick,
                "user_id": parsed.get("user_id"),
                "tab_url": tab.get("url"),
            }
            if open_fav_on_success and cid and "tab=fav" not in str(tab.get("url") or ""):
                cdp_navigate_tab(ws_url, fav, timeout_sec=45)
                time.sleep(4)
                last["tab_url"] = fav
            return last
        last = {
            "ok": False,
            "logged_in": False,
            "guest": parsed.get("guest"),
            "tab_url": tab.get("url"),
        }
        time.sleep(poll_sec)

    last.setdefault("error", "login_wait_timeout")
    return last


def cdp_close_data_tabs(port: int) -> int:
    """关闭 data: 自动化残留标签，避免被误判为「非用户态浏览器」。"""
    import requests as _req

    from .xhs_local_browser import cdp_list_tabs

    closed = 0
    for tab in cdp_list_tabs(port):
        url = str(tab.get("url") or "")
        if not url.startswith("data:"):
            continue
        tid = tab.get("id")
        if not tid:
            continue
        try:
            _req.get(f"http://127.0.0.1:{port}/json/close/{tid}", timeout=3)
            closed += 1
        except Exception:
            pass
    if closed:
        _log.info(
            "[小红书收藏夹-Chrome启动预处理|chrome_cdp_open.cdp_close_data_tabs|Tab|硬编执行|关闭] count=%s; port=%s",
            closed,
            port,
        )
    return closed


def warm_xhs_owner_session_via_cdp(
    *,
    port: Optional[int] = None,
    settle_sec: float = 10.0,
) -> Dict[str, Any]:
    """
    冷启动后：注入已缓存 Cookie → 先打开 explore 再进收藏页，促使 Profile 登录态进入页面。
    不杀 Chrome、不新开 Profile。
    """
    from .cookie_manager import find_cdp_port, load_cookies
    from .xhs_owner_chrome import probe_xhs_session_via_cdp

    port = port or find_cdp_port()
    if not port:
        return {"ok": False, "logged_in": False, "error": "no_cdp"}

    cid = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "60dc2e340000000001008a1f").strip()
    explore = "https://www.xiaohongshu.com/explore"
    fav = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"

    cdp_close_data_tabs(port)
    ws = cdp_pick_xhs_tab_ws(port)
    if not ws:
        return {"ok": False, "logged_in": False, "error": "no_xhs_tab"}

    # 禁止注入磁盘缓存 Cookie（可能是访客态），仅依赖 Profile 内真实登录态
    half = max(3.0, settle_sec / 2)
    cdp_navigate_tab(ws, explore, timeout_sec=45)
    time.sleep(half)
    ws = cdp_pick_xhs_tab_ws(port) or ws
    cdp_navigate_tab(ws, fav, timeout_sec=45)
    time.sleep(half)

    live = probe_xhs_session_via_cdp()
    _log.info(
        "[小红书收藏夹-Chrome启动预处理|chrome_cdp_open.warm_xhs_owner_session|CDP|硬编执行|完成] "
        "logged_in=%s; guest=%s; nickname=%s",
        live.get("logged_in"),
        live.get("guest"),
        live.get("nickname"),
    )
    return live


def open_favorites_tab_if_needed(port: Optional[int] = None) -> Dict[str, Any]:
    from .cookie_manager import find_cdp_port
    from .xhs_local_browser import cdp_list_tabs, is_usable_xhs_tab_url

    port = port or find_cdp_port()
    if not port:
        return {"ok": False, "error": "no_cdp"}
    cid = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "60dc2e340000000001008a1f").strip()
    fav = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"
    tabs = cdp_list_tabs(port)
    for t in tabs:
        u = str(t.get("url") or "")
        if cid in u and "tab=fav" in u and "/login" not in u:
            return {"ok": True, "action": "already_on_fav", "url": u}
    pick = None
    for t in tabs:
        u = str(t.get("url") or "")
        if u.startswith("chrome://") or u.startswith("devtools://") or u.startswith("data:"):
            continue
        if "sw.js" in u:
            continue
        pick = t
        break
    if not pick or not pick.get("webSocketDebuggerUrl"):
        return {"ok": False, "error": "no_tab_to_navigate"}
    ok = cdp_navigate_tab(pick["webSocketDebuggerUrl"], fav)
    time.sleep(3.0)
    return {"ok": ok, "action": "navigated", "url": fav}
