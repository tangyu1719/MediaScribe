"""收藏夹/个人号 — 强制使用已配置 Chrome 用户 + Cookie 守卫（禁止 Edge / 禁止新开自动化浏览器）。"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from .cookie_manager import (
    find_cdp_port,
    load_cookies,
    probe_xhs_cookies_logged_in,
    save_cookies_if_better,
)
from .xhs_local_browser import (
    CDP_PORT,
    BrowserConfig,
    _browser_config_chrome,
    _browser_running,
    _cdp_ready,
    _try_import_browser_cookies_live,
    is_browser_google_signed_in,
    maybe_bring_page_to_front,
    pick_xhs_page,
    xhs_cdp_attach_only,
)

_log = logging.getLogger("sba.xhs_owner_chrome")
_CHAIN = "小红书收藏夹-Chrome本人会话"


def _expected_gaia() -> str:
    return (os.environ.get("SBA_CHROME_EXPECTED_GAIA") or "").strip()


def _expected_email() -> str:
    return (os.environ.get("SBA_CHROME_EXPECTED_EMAIL") or "").strip().lower()


def _expected_xhs_nickname() -> str:
    return (os.environ.get("SBA_XHS_OWNER_NICKNAME") or "").strip()


def owner_chrome_config() -> BrowserConfig:
    return _browser_config_chrome()


def read_chrome_profile_identity(cfg: BrowserConfig) -> Dict[str, str]:
    """读取 Chrome Profile 绑定的 Google 账号信息。"""
    import json
    from pathlib import Path

    out: Dict[str, str] = {"kind": cfg.kind, "profile": cfg.profile}
    local_state = cfg.user_data_dir / "Local State"
    if local_state.is_file():
        try:
            data = json.loads(local_state.read_text(encoding="utf-8"))
            info = (data.get("profile") or {}).get("info_cache") or {}
            prof = info.get(cfg.profile) or {}
            out["gaia_name"] = str(prof.get("gaia_name") or prof.get("user_name") or "")
            out["user_name"] = str(prof.get("user_name") or "")
            out["account_id"] = str(prof.get("account_id") or "")
        except Exception:
            pass
    prefs = cfg.profile_dir / "Preferences"
    if prefs.is_file():
        try:
            data = json.loads(prefs.read_text(encoding="utf-8"))
            acct = data.get("account_info") or []
            if isinstance(acct, list) and acct:
                first = acct[0] if isinstance(acct[0], dict) else {}
                out["email"] = str(first.get("email") or "").lower()
                if not out.get("gaia_name"):
                    out["gaia_name"] = str(first.get("full_name") or first.get("given_name") or "")
        except Exception:
            pass
    return out


def verify_owner_chrome_profile(cfg: Optional[BrowserConfig] = None) -> Dict[str, Any]:
    """校验当前 Chrome Profile 是否为环境变量指定的 Google 账号。"""
    cfg = cfg or owner_chrome_config()
    ident = read_chrome_profile_identity(cfg)
    gaia = ident.get("gaia_name") or ident.get("user_name") or ""
    email = ident.get("email") or ""
    exp_gaia = _expected_gaia()
    exp_email = _expected_email()
    gaia_ok = bool(exp_gaia and exp_gaia in gaia)
    email_ok = bool(exp_email and exp_email in email)
    signed = is_browser_google_signed_in(cfg)
    ok = signed and (gaia_ok or email_ok)
    result = {
        "ok": ok,
        "browser": cfg.kind,
        "profile": cfg.profile,
        "gaia_name": gaia,
        "email": email,
        "expected_gaia": exp_gaia,
        "expected_email": exp_email,
        "google_signed_in": signed,
    }
    if not ok:
        _log.error(
            "[%s|xhs_owner_chrome.verify_owner_chrome_profile|Chrome|硬编执行|校验失败] "
            "gaia=%s; email=%s; expected_gaia=%s",
            _CHAIN,
            gaia,
            email,
            exp_gaia,
        )
    else:
        _log.info(
            "[%s|xhs_owner_chrome.verify_owner_chrome_profile|Chrome|硬编执行|校验通过] gaia=%s; email=%s",
            _CHAIN,
            gaia,
            email,
        )
    return result


def _nickname_matches(nickname: str) -> bool:
    needle = _expected_xhs_nickname()
    if not needle:
        return True
    nick = (nickname or "").strip()
    if needle in nick:
        return True
    # 兼容昵称中的顿号、间隔点与空格
    compact = re.sub(r"[、·\s]", "", nick)
    needle_compact = re.sub(r"[、·\s]", "", needle)
    return bool(needle_compact and needle_compact in compact)


def _cookies_indicate_xhs_login(cookies: Dict[str, str]) -> bool:
    """web_session/id_token 存在时视为浏览器内已登录小红书。"""
    if not cookies:
        return False
    if cookies.get("web_session") and cookies.get("a1"):
        return True
    return bool(cookies.get("id_token"))


def verify_owner_xhs_cookies(cookies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """校验小红书 Cookie 是否为已配置的本人登录态。"""
    ck = cookies if cookies is not None else (load_cookies("xiaohongshu") or {})
    probe = probe_xhs_cookies_logged_in(ck)
    nick = str(probe.get("nickname") or "")
    # 仅以 probe 结果为准；禁止 web_session+a1 存在即视为已登录（访客态也会带）
    logged = bool(probe.get("logged_in")) and not bool(probe.get("guest"))
    nick_ok = _nickname_matches(nick) if nick else logged
    ok = logged and nick_ok
    result = {
        "ok": ok,
        "logged_in": logged,
        "nickname": nick,
        "expected_nickname": _expected_xhs_nickname(),
        "count": len(ck),
    }
    if logged and not nick_ok:
        _log.error(
            "[%s|xhs_owner_chrome.verify_owner_xhs_cookies|Cookie|硬编执行|账号不符] "
            "nickname=%s; expected_contains=%s",
            _CHAIN,
            nick,
            _expected_xhs_nickname(),
        )
    return result


def _extract_xhs_user_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    user = state.get("user") if isinstance(state.get("user"), dict) else {}
    info = user.get("userInfo") or user.get("basicInfo") or {}
    if not isinstance(info, dict):
        info = {}
    guest = bool(info.get("guest"))
    logged_in = bool(user.get("loggedIn")) and not guest
    nick = str(info.get("nickname") or info.get("name") or "")
    red_id = str(info.get("redId") or info.get("red_id") or "")
    user_id = str(info.get("userId") or info.get("user_id") or "")
    return {
        "logged_in": logged_in,
        "guest": guest,
        "nickname": nick,
        "red_id": red_id,
        "user_id": user_id,
    }


def _probe_xhs_session_via_cdp_sync() -> Dict[str, Any]:
    """同步：纯 CDP WebSocket 解析登录态（禁止 Playwright，避免 data: 标签）。"""
    port = find_cdp_port()
    if not port:
        return {"ok": False, "logged_in": False, "error": "no_cdp"}
    prefer_cid = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "").strip()
    try:
        from .creator_feed_adapter import _parse_init_state
        from .xhs_local_browser import (
            cdp_list_tabs,
            cdp_pick_owner_tab,
            cdp_session_looks_like_guest_or_automation,
            cdp_tab_get_html,
            cdp_tab_get_xhs_cookies,
            xhs_cdp_attach_only,
        )

        tabs = cdp_list_tabs(port)
        if cdp_session_looks_like_guest_or_automation(tabs):
            return {
                "ok": False,
                "logged_in": False,
                "error": "guest_or_automation_chrome",
                "cdp_port": port,
            }
        tab = cdp_pick_owner_tab(port, prefer_cid=prefer_cid)
        if tab is None:
            return {"ok": False, "logged_in": False, "error": "no_xhs_tab"}
        tab_url = str(tab.get("url") or "")
        if "/login" in tab_url:
            return {"ok": False, "logged_in": False, "error": "login_tab", "cdp_port": port}
        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            return {"ok": False, "logged_in": False, "error": "no_ws_url"}

        html = cdp_tab_get_html(ws_url)
        parsed = _extract_xhs_user_from_state(_parse_init_state(html) or {})
        cookies = cdp_tab_get_xhs_cookies(ws_url)
        logged_in = bool(parsed.get("logged_in")) and not bool(parsed.get("guest"))
        guest = bool(parsed.get("guest")) or not logged_in
        nick = parsed.get("nickname") or ""
        user_id = parsed.get("user_id") or ""
        red_id = parsed.get("red_id") or ""
        if logged_in and cookies:
            save_cookies_if_better(
                "xiaohongshu", cookies, owner_nickname=_expected_xhs_nickname()
            )
        return {
            "ok": True,
            "logged_in": logged_in,
            "guest": guest,
            "nickname": nick,
            "red_id": red_id,
            "user_id": user_id,
            "cdp_port": port,
            "cookie_count": len(cookies),
            "cookie_logged": logged_in,
        }
    except Exception as ex:
        _log.warning(
            "[%s|xhs_owner_chrome.probe_xhs_session_via_cdp|CDP|Agent执行|失败] error=%s",
            _CHAIN,
            ex,
        )
        return {"ok": False, "logged_in": False, "error": str(ex)}


def probe_xhs_session_via_cdp() -> Dict[str, Any]:
    """通过 CDP 附着页面解析小红书登录态（兼容 asyncio 环境）。"""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        in_async = True
    except RuntimeError:
        in_async = False
    if not in_async:
        return _probe_xhs_session_via_cdp_sync()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_probe_xhs_session_via_cdp_sync).result()


def refresh_owner_xhs_cookies() -> Dict[str, Any]:
    """
    仅从环境变量指定的 Chrome Profile 读取 Cookie（browser_cookie3），
    不启动 Edge、不 launch_persistent、不杀 Chrome。
    """
    cfg = owner_chrome_config()
    prof = verify_owner_chrome_profile(cfg)
    if not prof.get("ok"):
        return {
            "ok": False,
            "error_code": "SUB_OWNER_CHROME_PROFILE_MISMATCH",
            "error": (
                f"Chrome 须为 Google 账号「{_expected_gaia()}」（{_expected_email()}），"
                f"当前 gaia={prof.get('gaia_name')} email={prof.get('email')}"
            ),
            "profile_check": prof,
        }

    fresh = _try_import_browser_cookies_live(cfg)
    if not fresh:
        from .cookie_manager import extract_platform_cookies_via_cdp, find_cdp_port

        port = find_cdp_port()
        if port and not xhs_cdp_attach_only():
            fresh = extract_platform_cookies_via_cdp("xiaohongshu", navigate=True) or {}
            if fresh:
                _log.info(
                    "[%s|xhs_owner_chrome.refresh_owner_xhs_cookies|CDP|硬编执行|读取] port=%s; count=%s",
                    _CHAIN,
                    port,
                    len(fresh),
                )
        elif port and xhs_cdp_attach_only():
            fresh = extract_platform_cookies_via_cdp("xiaohongshu", navigate=False) or {}

    live = probe_xhs_session_via_cdp() if find_cdp_port() else {}
    if live.get("logged_in"):
        nick_ok = _nickname_matches(live.get("nickname") or "") or bool(live.get("cookie_logged"))
        if nick_ok:
            saved = load_cookies("xiaohongshu") or fresh or {}
            return {
                "ok": True,
                "logged_in": True,
                "source": "cdp_page_probe",
                "count": len(saved),
                "nickname": live.get("nickname"),
                "profile_check": prof,
                "xhs_check": {
                    "ok": True,
                    "logged_in": True,
                    "nickname": live.get("nickname"),
                    "expected_nickname": _expected_xhs_nickname(),
                },
            }

    if not fresh:
        existing = load_cookies("xiaohongshu") or {}
        if _cookies_indicate_xhs_login(existing):
            xhs = verify_owner_xhs_cookies(existing)
            if xhs.get("logged_in"):
                return {
                    "ok": True,
                    "logged_in": True,
                    "source": "file_cache:web_session",
                    "count": len(existing),
                    "nickname": xhs.get("nickname") or "",
                    "profile_check": prof,
                    "xhs_check": xhs,
                }
        xhs = verify_owner_xhs_cookies(existing)
        if xhs.get("ok"):
            return {
                "ok": True,
                "logged_in": True,
                "source": "file_cache",
                "count": len(existing),
                "nickname": xhs.get("nickname"),
                "profile_check": prof,
                "xhs_check": xhs,
            }
        return {
            "ok": False,
            "error_code": "SUB_OWNER_COOKIE_READ_FAILED",
            "error": "无法从 Chrome Profile 读取小红书 Cookie，请确认 Chrome 已登录已配置的小红书账号",
            "profile_check": prof,
            "xhs_check": xhs,
        }

    saved = save_cookies_if_better("xiaohongshu", fresh, owner_nickname=_expected_xhs_nickname())
    xhs = verify_owner_xhs_cookies(saved)
    if xhs.get("ok"):
        return {
            "ok": True,
            "logged_in": True,
            "source": "browser_cookie3:chrome",
            "count": len(saved),
            "nickname": xhs.get("nickname"),
            "profile_check": prof,
            "xhs_check": xhs,
        }
    return {
        "ok": False,
        "error_code": "SUB_OWNER_XHS_ACCOUNT_MISMATCH",
        "error": (
            f"Chrome Cookie 小红书账号不是「{_expected_xhs_nickname()}」，"
            f"当前 nickname={xhs.get('nickname')}"
        ),
        "profile_check": prof,
        "xhs_check": xhs,
    }


def ensure_owner_chrome_cdp() -> Dict[str, Any]:
    """
    收藏夹操作前检查：
    1) Chrome Profile = 环境变量指定用户
    2) 小红书 = 环境变量指定本人账号
    3) CDP 已附着到**正在运行的 Chrome**（不新开浏览器）
    """
    from .xhs_local_browser import assert_plan_a_owner_browser_ops

    assert_plan_a_owner_browser_ops(caller="ensure_owner_chrome_cdp")
    cookie_res = refresh_owner_xhs_cookies()
    prof = cookie_res.get("profile_check") or verify_owner_chrome_profile()
    if not prof.get("ok"):
        raise RuntimeError(
            "SUB_OWNER_CHROME_PROFILE_MISMATCH: "
            f"请使用 Google 账号「{_expected_gaia()}」的 Chrome，当前 gaia={prof.get('gaia_name')}"
        )
    port = find_cdp_port()
    if not port or not _cdp_ready(port):
        cfg = owner_chrome_config()
        running = _browser_running(cfg)
        if running:
            _log.warning(
                "[%s|xhs_owner_chrome.ensure_owner_chrome_cdp|CDP|硬编执行|未就绪] "
                "Chrome 在运行但未开 CDP；请完全退出后用桌面快捷方式重启",
                _CHAIN,
            )
        raise RuntimeError(
            f"SUB_OWNER_CDP_REQUIRED: 请手动双击桌面 Google Chrome.lnk"
            f"（含 --remote-debugging-port={CDP_PORT}）启动 Chrome。"
            "方案 A 禁止 Agent 杀进程/冷启动/Playwright。"
        )

    live = probe_xhs_session_via_cdp()
    nick = live.get("nickname") or cookie_res.get("nickname") or ""
    if not live.get("logged_in"):
        raise RuntimeError(
            "SUB_OWNER_XHS_LOGIN_REQUIRED: Chrome 已是配置的 Google Default Profile，"
            "但小红书当前为访客/未登录（loggedIn=False）。"
            "请在 CDP Chrome 窗口手动登录已配置的小红书账号并打开收藏页 tab=fav 后重试。"
            f" tab={str(live.get('tab_url') or '')[:120]}"
        )
    if nick and not _nickname_matches(nick):
        raise RuntimeError(
            f"SUB_OWNER_XHS_ACCOUNT_MISMATCH: 小红书账号应为「{_expected_xhs_nickname()}」，"
            f"当前 nickname={nick}"
        )

    _log.info(
        "[%s|xhs_owner_chrome.ensure_owner_chrome_cdp|CDP|硬编执行|就绪] port=%s; nickname=%s; gaia=%s",
        _CHAIN,
        port,
        nick,
        prof.get("gaia_name"),
    )
    return {
        "ok": True,
        "cdp_port": port,
        "nickname": nick,
        "profile_check": prof,
        "cookie_source": cookie_res.get("source"),
        "xhs_live": live,
    }


def get_owner_session_status() -> Dict[str, Any]:
    """供 API/前端展示已配置 Chrome + 小红书本人会话状态。"""
    from .cookie_manager import diagnose_xhs_cookies
    from .chrome_profile_prep import cdp_chrome_user_data_dir
    from .xhs_local_browser import should_prefer_cookie_favorites_fetch

    prof = verify_owner_chrome_profile()
    port = find_cdp_port()
    cookie_probe = probe_xhs_cookies_logged_in(load_cookies("xiaohongshu") or {}) if load_cookies("xiaohongshu") else {"logged_in": False}
    prefer_cookies = should_prefer_cookie_favorites_fetch()
    diag = diagnose_xhs_cookies()
    # Cookie 已登录时跳过 CDP 探测（避免 /subscription 阻塞 15s+）
    live: Dict[str, Any] = {}
    if port and not (cookie_probe.get("logged_in") and prefer_cookies):
        live = probe_xhs_session_via_cdp()
    if live.get("logged_in"):
        nick = live.get("nickname") or ""
        ck = {
            "ok": _nickname_matches(nick) or bool(live.get("cookie_logged")),
            "logged_in": True,
            "nickname": nick,
            "expected_nickname": _expected_xhs_nickname(),
        }
    else:
        ck = verify_owner_xhs_cookies()
    fetch_mode = "cookie" if prefer_cookies else ("cdp" if (port and _cdp_ready(port)) else "cookie" if cookie_probe.get("logged_in") else "none")
    return {
        "chrome_profile_ok": prof.get("ok"),
        "chrome_gaia": prof.get("gaia_name"),
        "chrome_email": prof.get("email"),
        "xhs_logged_in": ck.get("logged_in") or live.get("logged_in") or cookie_probe.get("logged_in"),
        "xhs_nickname": ck.get("nickname") or live.get("nickname") or cookie_probe.get("nickname") or "",
        "xhs_owner_ok": ck.get("ok") or (
            live.get("logged_in")
            and (_nickname_matches(live.get("nickname") or "") or live.get("cookie_logged"))
        ) or bool(cookie_probe.get("logged_in")),
        "cdp_port": port,
        "cdp_ready": bool(port and _cdp_ready(port)),
        "cookie_logged_in": bool(cookie_probe.get("logged_in")),
        "prefer_cookie_fetch": prefer_cookies,
        "fetch_mode": fetch_mode,
        "expected_gaia": _expected_gaia(),
        "expected_xhs_nickname": _expected_xhs_nickname(),
        "xhs_guest": live.get("guest") if live else None,
        "login_hint": (
            ""
            if cookie_probe.get("logged_in")
            else (
                "CDP 已就绪，请点击「从 Chrome 同步 Cookie」。"
                if diag.get("cdp_port")
                else (
                    f"请双击桌面「Google Chrome CDP 9223」启动（目录 {cdp_chrome_user_data_dir()}）。"
                    if diag.get("cdp_blocked_default_profile") or not diag.get("cdp_port")
                    else (
                        "请在 CDP Chrome 收藏页确认已登录配置的小红书账号。"
                        if not (ck.get("ok") or live.get("logged_in"))
                        else "请点击「从 Chrome 同步 Cookie」。"
                    )
                )
            )
        ),
    }


def resolve_owner_creator_id_from_cdp() -> Dict[str, Any]:
    """从 CDP 会话读取当前登录用户 user_id（收藏夹须用本人 uid）。"""
    ensure_owner_chrome_cdp()
    live = probe_xhs_session_via_cdp()
    uid = (live.get("user_id") or "").strip()
    if not uid:
        expected_red = (os.environ.get("XHS_FAVORITES_RED_ID") or "").strip()
        override = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "").strip()
        if override and re.fullmatch(r"[a-f0-9]{24}", override, re.I):
            uid = override
        else:
            raise RuntimeError(
                f"SUB_OWNER_USER_ID_UNKNOWN: CDP 未解析到登录 user_id，red_id={expected_red}"
            )
    nick = live.get("nickname") or _display_name_fallback()
    profile_url = f"https://www.xiaohongshu.com/user/profile/{uid}?tab=fav"
    return {
        "creator_id": uid,
        "display_name": nick,
        "profile_url": profile_url,
        "favorites_url": profile_url,
        "red_id": live.get("red_id") or os.environ.get("XHS_FAVORITES_RED_ID", ""),
        "source": "cdp_logged_in_user",
    }


def _display_name_fallback() -> str:
    return (os.environ.get("XHS_FAVORITES_DISPLAY_NAME") or "我的收藏夹").strip()


def _list_cdp_tabs(port: int) -> List[Dict[str, Any]]:
    import requests

    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def verify_plan_a_owner_session() -> Dict[str, Any]:
    """
    方案 A 只读校验：CDP + 已配置 Profile + 小红书登录 + 收藏 Tab。
    不启动浏览器、不 new_context/new_page、不 goto。
    """
    from .xhs_local_browser import is_usable_xhs_tab_url, xhs_cdp_attach_only

    hints: List[str] = []
    prefer_cid = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "").strip()
    prof = verify_owner_chrome_profile()
    if not prof.get("ok"):
        return {
            "ok": False,
            "error_code": "SUB_OWNER_CHROME_PROFILE_MISMATCH",
            "error": (
                f"磁盘 Chrome Profile 不是「{_expected_gaia()}」"
                f"（gaia={prof.get('gaia_name')} email={prof.get('email')}）"
            ),
            "profile_check": prof,
            "hints": [
                "须使用环境变量指定的 Default Profile",
                "禁止 Agent 用 Start-Process 冷启动 Chrome（会丢 Cookie 变成灰色访客）",
                "请完全退出 Chrome 后，Win+R 粘贴 scripts/plan_a_launch_chrome.ps1 打印的启动命令",
            ],
        }

    port = find_cdp_port()
    if not port or not _cdp_ready(port):
        return {
            "ok": False,
            "error_code": "SUB_OWNER_CDP_REQUIRED",
            "error": f"CDP 未就绪（期望端口 {CDP_PORT}）",
            "profile_check": prof,
            "hints": [
                "在你已登录的 Chrome 手动加 --remote-debugging-port=9223 --remote-allow-origins=*",
                "或双击桌面 Google Chrome.lnk（已含 9223）",
            ],
        }

    tabs = _list_cdp_tabs(port)
    from .xhs_local_browser import cdp_session_looks_like_guest_or_automation
    from .chrome_profile_prep import dismiss_chrome_restore_prompt

    try:
        dismiss_chrome_restore_prompt(port)
    except Exception as ex:
        _log.debug("dismiss restore prompt skip: %s", ex)

    if cdp_session_looks_like_guest_or_automation(tabs):
        return {
            "ok": False,
            "error_code": "SUB_OWNER_CHROME_GUEST_SESSION",
            "error": (
                "附着的不是配置的日常 Chrome：右上角须为指定用户，不能是「登录 Chrome」/灰色访客。"
                "请关闭此窗口，在您平时的 Chrome 快捷方式目标后追加 "
                f"--remote-debugging-port={CDP_PORT} --remote-allow-origins=*，再从任务栏打开。"
            ),
            "cdp_port": port,
            "profile_check": prof,
            "hints": [
                "禁止 Agent/脚本 Start-Process 冷启动 Chrome",
                "关闭 data: 空白标签后再试",
                f"收藏页: profile/{prefer_cid}?tab=fav",
            ],
        }

    data_tabs = [t for t in tabs if str(t.get("url") or "").startswith("data:")]
    login_tabs = [t for t in tabs if "/login" in str(t.get("url") or "")]
    fav_tabs = [
        t
        for t in tabs
        if prefer_cid in str(t.get("url") or "")
        and ("tab=fav" in str(t.get("url") or "") or "tab=collect" in str(t.get("url") or ""))
        and "/login" not in str(t.get("url") or "")
    ]
    usable_xhs = [t for t in tabs if is_usable_xhs_tab_url(str(t.get("url") or ""))]

    if data_tabs:
        hints.append(f"关闭 {len(data_tabs)} 个 data: 空白标签（Playwright 误连产物）")
    if login_tabs and not fav_tabs and not usable_xhs:
        return {
            "ok": False,
            "error_code": "SUB_OWNER_XHS_LOGIN_REQUIRED",
            "error": "CDP 附着的是未登录小红书会话（登录页），不是配置的本人 Chrome 会话",
            "cdp_port": port,
            "profile_check": prof,
            "bad_tabs": [{"title": t.get("title"), "url": (t.get("url") or "")[:120]} for t in login_tabs[:3]],
            "hints": hints
            + [
                "右上角若是灰色头像 = 错误 Profile/冷启动，须完全退出后用手动命令重开",
                "确认收藏页 URL: .../<XHS_FAVORITES_CREATOR_ID>?tab=fav",
            ],
        }

    live = probe_xhs_session_via_cdp()
    nick = live.get("nickname") or ""
    logged = bool(live.get("logged_in")) and not bool(live.get("guest"))
    # 收藏 Tab 上 __INITIAL_STATE__ 的 loggedIn 为准（比 Cookie 文件更可靠）
    if fav_tabs and not logged:
        try:
            from .creator_feed_adapter import _parse_init_state
            from .xhs_local_browser import cdp_tab_get_html

            ws = fav_tabs[0].get("webSocketDebuggerUrl")
            if ws:
                st = _extract_xhs_user_from_state(_parse_init_state(cdp_tab_get_html(str(ws))) or {})
                if st.get("logged_in"):
                    logged = True
                    nick = st.get("nickname") or nick
        except Exception:
            pass
    nick_ok = _nickname_matches(nick) if nick else logged
    if not logged or not nick_ok:
        return {
            "ok": False,
            "error_code": "SUB_OWNER_XHS_LOGIN_REQUIRED"
            if not logged
            else "SUB_OWNER_XHS_ACCOUNT_MISMATCH",
            "error": (
                f"小红书未登录「{_expected_xhs_nickname()}」（请在 CDP Chrome 收藏页登录）"
                if not logged
                else f"小红书账号不是「{_expected_xhs_nickname()}」（nickname={nick}）"
            ),
            "cdp_port": port,
            "profile_check": prof,
            "xhs_probe": live,
            "hints": hints
            + [
                "禁止 Agent/脚本执行 plan_a_open_my_chrome.ps1 或 plan_a_auto_open_and_bind 冷启动",
                "灰色头像 = 非你日常 Chrome，请完全退出后用桌面快捷方式重开",
                "在已附着的 Chrome 收藏页确认登录配置的小红书账号",
                "userNoteFetchingStatus=rejected 表示未登录或 Cookie 失效",
                "勿用 Edge；勿让 Agent 新开浏览器",
            ],
        }

    if not fav_tabs:
        hints.append(f"请手动打开收藏页 profile/{prefer_cid}?tab=fav（attach-only 禁止自动 goto）")

    return {
        "ok": bool(fav_tabs) and logged and nick_ok,
        "error_code": ""
        if (fav_tabs and logged and nick_ok)
        else ("SUB_OWNER_OPEN_FAV_TAB" if not fav_tabs else "SUB_OWNER_XHS_LOGIN_REQUIRED"),
        "error": ""
        if (fav_tabs and logged and nick_ok)
        else (
            "未检测到收藏 Tab，请在 Chrome 打开 tab=fav"
            if not fav_tabs
            else f"小红书未登录「{_expected_xhs_nickname()}」"
        ),
        "cdp_port": port,
        "profile_check": prof,
        "xhs_nickname": nick,
        "fav_tab_count": len(fav_tabs),
        "usable_xhs_tab_count": len(usable_xhs),
        "data_tab_count": len(data_tabs),
        "login_tab_count": len(login_tabs),
        "attach_only": xhs_cdp_attach_only(),
        "hints": hints,
    }


def iter_owner_chrome_configs() -> List[BrowserConfig]:
    """收藏夹链路仅允许环境变量指定的 Chrome Profile。"""
    cfg = owner_chrome_config()
    if verify_owner_chrome_profile(cfg).get("ok"):
        return [cfg]
    return []
