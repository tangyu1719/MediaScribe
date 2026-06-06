"""收藏夹/个人号 — 强制本机 Chrome「有光」用户配置 + Cookie 守卫（禁止 Edge / 禁止新开自动化浏览器）。"""
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
)

_log = logging.getLogger("sba.xhs_owner_chrome")
_CHAIN = "小红书收藏夹-Chrome有光会话"


def _expected_gaia() -> str:
    return (os.environ.get("SBA_CHROME_EXPECTED_GAIA") or "有光").strip()


def _expected_email() -> str:
    return (os.environ.get("SBA_CHROME_EXPECTED_EMAIL") or "liyouguang2@gmail.com").strip().lower()


def _expected_xhs_nickname() -> str:
    return (os.environ.get("SBA_XHS_OWNER_NICKNAME") or "三点").strip()


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
    """校验当前 Chrome Profile 是否为「有光」Google 账号。"""
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
    # 兼容「三点、水」「三点水」
    compact = re.sub(r"[、·\s]", "", nick)
    needle_compact = re.sub(r"[、·\s]", "", needle)
    return bool(needle_compact and needle_compact in compact)


def verify_owner_xhs_cookies(cookies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """校验小红书 Cookie 是否为本人（三点、水）登录态。"""
    ck = cookies if cookies is not None else (load_cookies("xiaohongshu") or {})
    probe = probe_xhs_cookies_logged_in(ck)
    nick = str(probe.get("nickname") or "")
    logged = bool(probe.get("logged_in"))
    nick_ok = _nickname_matches(nick) if logged else False
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


def refresh_owner_xhs_cookies() -> Dict[str, Any]:
    """
    仅从 Chrome「有光」Profile 读取 Cookie（browser_cookie3），
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
        existing = load_cookies("xiaohongshu") or {}
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
            "error": "无法从 Chrome Profile 读取小红书 Cookie，请确认 Chrome 已登录小红书（三点、水）",
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
    1) Chrome Profile = 有光
    2) 小红书 = 三点、水
    3) CDP 已附着到**正在运行的 Chrome**（不新开浏览器）
    """
    cookie_res = refresh_owner_xhs_cookies()
    prof = cookie_res.get("profile_check") or verify_owner_chrome_profile()
    if not prof.get("ok"):
        raise RuntimeError(
            "SUB_OWNER_CHROME_PROFILE_MISMATCH: "
            f"请使用 Google 账号「{_expected_gaia()}」的 Chrome，当前 gaia={prof.get('gaia_name')}"
        )
    if not cookie_res.get("ok"):
        code = cookie_res.get("error_code") or "SUB_OWNER_COOKIE_UNAVAILABLE"
        raise RuntimeError(f"{code}: {cookie_res.get('error')}")

    port = find_cdp_port()
    if not port or not _cdp_ready(port):
        running = _browser_running(owner_chrome_config())
        hint = (
            "请在你正在使用的 Chrome（有光账号）上启用远程调试："
            "关闭所有 Chrome 窗口后，用快捷方式追加启动参数 "
            f"--remote-debugging-port={CDP_PORT}，再打开小红书收藏页。"
            "系统不会自动新开浏览器，以免 Cookie 丢失。"
        )
        if running:
            raise RuntimeError(f"SUB_OWNER_CDP_REQUIRED: Chrome 已在运行但未开启 CDP。{hint}")
        raise RuntimeError(f"SUB_OWNER_CHROME_NOT_RUNNING: 请先打开 Chrome（有光账号）并登录小红书。{hint}")

    _log.info(
        "[%s|xhs_owner_chrome.ensure_owner_chrome_cdp|CDP|硬编执行|就绪] port=%s; nickname=%s",
        _CHAIN,
        port,
        (cookie_res.get("nickname") or ""),
    )
    return {
        "ok": True,
        "cdp_port": port,
        "nickname": cookie_res.get("nickname"),
        "profile_check": prof,
        "cookie_source": cookie_res.get("source"),
    }


def get_owner_session_status() -> Dict[str, Any]:
    """供 API/前端展示 Chrome 有光 + 三点、水 会话状态。"""
    prof = verify_owner_chrome_profile()
    ck = verify_owner_xhs_cookies()
    port = find_cdp_port()
    return {
        "chrome_profile_ok": prof.get("ok"),
        "chrome_gaia": prof.get("gaia_name"),
        "chrome_email": prof.get("email"),
        "xhs_logged_in": ck.get("logged_in"),
        "xhs_nickname": ck.get("nickname"),
        "xhs_owner_ok": ck.get("ok"),
        "cdp_port": port,
        "cdp_ready": bool(port and _cdp_ready(port)),
        "expected_gaia": _expected_gaia(),
        "expected_xhs_nickname": _expected_xhs_nickname(),
    }


def iter_owner_chrome_configs() -> List[BrowserConfig]:
    """收藏夹链路仅允许 Chrome 有光 Profile。"""
    cfg = owner_chrome_config()
    if verify_owner_chrome_profile(cfg).get("ok"):
        return [cfg]
    return []
