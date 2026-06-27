"""本机 Chrome/Edge 用户配置：可见窗口 + 页面内搜索（不杀正在使用的浏览器）。"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .cookie_manager import (
    PLATFORM_DOMAINS,
    extract_platform_cookies_via_cdp,
    find_cdp_port,
    load_cookies,
    probe_xhs_cookies_logged_in,
    save_cookies,
    save_cookies_if_better,
)
from .creator_feed_adapter import _find_user_by_red_id_in_obj, _parse_init_state, _user_dict_to_resolved

_log = logging.getLogger("sba.xhs_local_browser")

_CHAIN = "社媒订阅-本机浏览器解析"
CDP_PORT = int(os.environ.get("SBA_CHROME_CDP_PORT", "9223"))


def is_usable_xhs_tab_url(url: str) -> bool:
    """可附着/复用的小红书内容页（排除 sw.js、无 uid 的 profile 404 等）。"""
    u = (url or "").strip()
    if not u or u.startswith("chrome://") or u.startswith("devtools://"):
        return False
    if u.startswith("data:") or u in ("about:blank", "chrome://newtab/"):
        return False
    if "xiaohongshu.com" not in u:
        return False
    if "/login" in u:
        return False
    if "web-static" in u or u.rstrip("/").endswith("sw.js"):
        return False
    if re.search(r"/user/profile/?(\?|$)", u, re.I) and not re.search(
        r"/user/profile/[a-f0-9]{24}", u, re.I
    ):
        return False
    return True


def xhs_cdp_attach_only() -> bool:
    """方案 A：仅 CDP 附着用户已打开的 Chrome；禁止杀进程、禁止新开 Profile/浏览器。"""
    v = (os.environ.get("SBA_XHS_CDP_ATTACH_ONLY") or "1").strip().lower()
    return v not in ("0", "false", "no")


def xhs_background_mode() -> bool:
    """方案 A：后台静默；不 bring_to_front、不 context.new_page()。"""
    v = (os.environ.get("SBA_XHS_BACKGROUND") or "1").strip().lower()
    return v not in ("0", "false", "no")


def maybe_bring_page_to_front(page) -> None:
    if xhs_background_mode():
        return
    try:
        page.bring_to_front()
    except Exception:
        pass


def pick_xhs_page(context, prefer_cid: str = "", *, attach_only: Optional[bool] = None) -> Any:
    """优先复用收藏 Tab，其次任意可用小红书 Tab；attach-only 时不 fallback 到 about:blank。"""
    if attach_only is None:
        attach_only = xhs_cdp_attach_only()
    best = None
    fallback_any = None
    for pg in context.pages:
        url = pg.url or ""
        if url.startswith("chrome://") or url.startswith("devtools://"):
            continue
        if fallback_any is None:
            fallback_any = pg
        if not is_usable_xhs_tab_url(url):
            continue
        if prefer_cid and prefer_cid in url and (
            "tab=fav" in url or "tab=collect" in url or "tab=favorite" in url
        ):
            return pg
        if best is None:
            best = pg
    if best:
        return best
    if attach_only:
        return None
    return fallback_any


def attach_cdp_default_context(browser) -> Any:
    """方案 A：只附着 Chrome 已有 Context，禁止 browser.new_context()。"""
    if xhs_cdp_attach_only() and not browser.contexts:
        raise RuntimeError(
            "SUB_OWNER_CDP_NO_CONTEXT: CDP 已连接但无浏览器上下文。"
            "请在你已打开的 Chrome 中保留至少一个标签（收藏页 tab=fav）。"
        )
    if not browser.contexts:
        raise RuntimeError("SUB_OWNER_CDP_NO_CONTEXT: CDP 浏览器无可用上下文")
    return browser.contexts[0]


def assert_page_not_xhs_login(page, *, action: str = "") -> None:
    url = (page.url or "").strip()
    if "/login" in url:
        raise RuntimeError(
            "SUB_OWNER_XHS_LOGIN_REQUIRED: 当前标签在小红书登录页，说明未复用已登录会话。"
            f"请在配置的 Chrome Default Profile 手动登录本人小红书账号并打开收藏页 tab=fav；"
            f"禁止自动新开浏览器/标签。{action}"
        )
    if url.startswith("data:"):
        raise RuntimeError(
            "SUB_OWNER_BAD_TAB: 检测到 data: 空白自动化标签，请关闭该标签并在 Chrome 打开收藏页。"
        )


def _start_owner_chrome_via_shortcut() -> bool:
    """拉起 CDP Chrome（SBA-Chrome-CDP 独立实例，可与日常 Chrome 并存）。"""
    from .chrome_profile_prep import ensure_sba_cdp_chrome_running, is_cdp_ready

    if is_cdp_ready(CDP_PORT):
        return True
    try:
        ensure_sba_cdp_chrome_running(wait_sec=45.0)
        return True
    except Exception as ex:
        _log.warning(
            "[%s|start_cdp_shortcut|硬编执行|失败] error_type=%s; error_message=%s",
            _CHAIN,
            type(ex).__name__,
            str(ex)[:200],
        )
        return False


def require_cdp_port() -> int:
    port = find_cdp_port()
    if not port:
        raise RuntimeError(
            "SUB_OWNER_CDP_REQUIRED: 方案A仅附着你已打开的 Chrome，不会杀进程、不会新开 Profile 或标签。"
            f"请在你正在使用的 Chrome（配置的 Default Profile）带 "
            f"--remote-debugging-port={CDP_PORT} --remote-allow-origins=* 启动，"
            "并打开个人收藏页（tab=fav）；当前 CDP 未就绪。"
        )
    return port


def _pick_cdp_tab_meta(tabs: List[Dict[str, Any]], prefer_cid: str = "") -> Optional[Dict[str, Any]]:
    """从 CDP /json/list 选小红书 Tab（纯 HTTP，不连 Playwright）。"""
    best: Optional[Dict[str, Any]] = None
    for t in tabs:
        url = str(t.get("url") or "")
        if url.startswith("chrome://") or url.startswith("devtools://"):
            continue
        if url.startswith("data:"):
            continue
        if prefer_cid and prefer_cid in url and (
            "tab=fav" in url or "tab=collect" in url or "tab=favorite" in url
        ):
            return t
        if is_usable_xhs_tab_url(url) and best is None:
            best = t
    return best


def cdp_tab_eval(ws_url: str, expression: str, *, timeout_sec: float = 12.0) -> Any:
    """纯 CDP WebSocket Runtime.evaluate — 禁止 Playwright（避免 data: 空白标签）。"""
    import websocket as _ws

    ws = _ws.create_connection(ws_url, timeout=int(timeout_sec))
    try:
        msg_id = 1
        for method in ("Runtime.enable", "Page.enable"):
            ws.send(json.dumps({"id": msg_id, "method": method}))
            msg_id += 1
        eval_id = msg_id
        ws.send(
            json.dumps(
                {
                    "id": eval_id,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }
            )
        )
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == eval_id:
                return (msg.get("result") or {}).get("result", {}).get("value")
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def cdp_tab_get_html(ws_url: str) -> str:
    html = cdp_tab_eval(ws_url, "document.documentElement.outerHTML")
    return str(html or "")


def cdp_tab_scroll_bottom(ws_url: str, rounds: int = 3, pause_sec: float = 1.2) -> None:
    for _ in range(max(1, rounds)):
        cdp_tab_eval(ws_url, "window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause_sec)


def cdp_tab_get_xhs_cookies(ws_url: str) -> Dict[str, str]:
    """Network.getCookies via CDP WebSocket。"""
    import websocket as _ws

    cookies: Dict[str, str] = {}
    ws = _ws.create_connection(ws_url, timeout=12)
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        time.sleep(0.3)
        ws.send(
            json.dumps(
                {
                    "id": 2,
                    "method": "Network.getCookies",
                    "params": {
                        "urls": [f"https://{d}" for d in PLATFORM_DOMAINS["xiaohongshu"]],
                    },
                }
            )
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == 2:
                for c in (msg.get("result") or {}).get("cookies") or []:
                    name = c.get("name") or ""
                    val = c.get("value") or ""
                    domain = c.get("domain") or ""
                    if name and any(d in domain for d in PLATFORM_DOMAINS["xiaohongshu"]):
                        cookies[name] = val
                break
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return cookies


def cdp_list_tabs(port: int) -> List[Dict[str, Any]]:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def cdp_pick_owner_tab(port: int, prefer_cid: str = "") -> Optional[Dict[str, Any]]:
    return _pick_cdp_tab_meta(cdp_list_tabs(port), prefer_cid=prefer_cid)


def cdp_session_looks_like_guest_or_automation(tabs: List[Dict[str, Any]]) -> bool:
    """data:/about:blank 且无可用小红书 Tab = Playwright 误连或非用户态冷启动。"""
    has_data = any(str(t.get("url") or "").startswith("data:") for t in tabs)
    has_blank = any(str(t.get("url") or "").strip() in ("about:blank", "chrome://newtab/") for t in tabs)
    has_xhs = any(is_usable_xhs_tab_url(str(t.get("url") or "")) for t in tabs)
    has_login = any("/login" in str(t.get("url") or "") for t in tabs)
    if (has_data or has_blank) and not has_xhs:
        return True
    if has_login and not has_xhs:
        return True
    return False


def assert_plan_a_owner_browser_ops(*, caller: str = "") -> None:
    """收藏夹主路径入口守卫：方案 A 配置必须开启，禁止回退到开浏览器/新标签。"""
    if not xhs_cdp_attach_only():
        raise RuntimeError(
            f"SUB_PLAN_A_REQUIRED: {caller or 'owner'} 路径须 SBA_XHS_CDP_ATTACH_ONLY=1，禁止新开浏览器。"
        )
    if not xhs_background_mode():
        _log.warning(
            "[%s|assert_plan_a|%s|硬编执行|提示] 建议 SBA_XHS_BACKGROUND=1 避免抢前台",
            _CHAIN,
            caller,
        )
    from .chrome_profile_prep import is_cdp_ready as _prep_cdp_ready

    port = find_cdp_port()
    if port and _prep_cdp_ready(port):
        tabs = cdp_list_tabs(port)
        if cdp_session_looks_like_guest_or_automation(tabs):
            raise RuntimeError(
                f"SUB_OWNER_CHROME_GUEST_SESSION: {caller} 附着的不是您日常 Chrome。"
                "请关闭 about:blank / data: 标签，用桌面 Google Chrome.lnk 手动启动"
                f"（含 --remote-debugging-port={CDP_PORT}），"
                "确认右上角为配置的用户且已打开收藏页 tab=fav。"
            )
        return
    raise RuntimeError(
        f"SUB_OWNER_CDP_REQUIRED: {caller} 需要 CDP。"
        f"请手动双击桌面 Google Chrome.lnk（含 --remote-debugging-port={CDP_PORT}）"
        "打开 Chrome，登录配置的小红书账号并打开收藏页 tab=fav。"
        "方案 A 禁止 Agent 自动杀进程/新开浏览器/Playwright 兜底。"
    )


_KEEP_BROWSER_OPEN = os.environ.get("SBA_XHS_KEEP_BROWSER", "1") != "0"


@dataclass(frozen=True)
class BrowserConfig:
    kind: str  # chrome | edge
    exe: Path
    user_data_dir: Path
    profile: str
    process_name: str
    cookie3_name: str
    playwright_channel: str

    @property
    def profile_dir(self) -> Path:
        return self.user_data_dir / self.profile


def _chrome_user_data_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"


def _edge_user_data_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data"


def _chrome_profile() -> str:
    return (os.environ.get("SBA_CHROME_PROFILE") or "Default").strip() or "Default"


def _edge_profile() -> str:
    return (os.environ.get("SBA_EDGE_PROFILE") or "Default").strip() or "Default"


def _chrome_exe() -> Path:
    for p in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ):
        if p.is_file():
            return p
    raise FileNotFoundError("未找到 chrome.exe")


def _edge_exe() -> Optional[Path]:
    for p in (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ):
        if p.is_file():
            return p
    return None


def _browser_config_chrome() -> BrowserConfig:
    return BrowserConfig(
        kind="chrome",
        exe=_chrome_exe(),
        user_data_dir=_chrome_user_data_dir(),
        profile=_chrome_profile(),
        process_name="chrome.exe",
        cookie3_name="chrome",
        playwright_channel="chrome",
    )


def _browser_config_edge() -> Optional[BrowserConfig]:
    exe = _edge_exe()
    if not exe:
        return None
    return BrowserConfig(
        kind="edge",
        exe=exe,
        user_data_dir=_edge_user_data_dir(),
        profile=_edge_profile(),
        process_name="msedge.exe",
        cookie3_name="edge",
        playwright_channel="msedge",
    )


def _iter_browser_configs() -> List[BrowserConfig]:
    """按 SBA_BROWSER 返回候选浏览器（chrome / edge / auto）。"""
    pref = (os.environ.get("SBA_BROWSER") or "auto").strip().lower()
    chrome = _browser_config_chrome()
    edge = _browser_config_edge()
    if pref == "edge":
        return [c for c in (edge, chrome) if c]
    if pref == "chrome":
        return [chrome]
    # auto：优先已登录 Google 账号的配置，否则 chrome → edge
    signed = [c for c in (chrome, edge) if c and is_browser_google_signed_in(c)]
    if signed:
        return signed
    return [c for c in (chrome, edge) if c]


def is_browser_google_signed_in(cfg: BrowserConfig) -> bool:
    """检查浏览器 Profile 是否已登录 Google/Edge 账号（非访客配置）。"""
    local_state = cfg.user_data_dir / "Local State"
    if local_state.is_file():
        try:
            data = json.loads(local_state.read_text(encoding="utf-8"))
            info = (data.get("profile") or {}).get("info_cache") or {}
            prof = info.get(cfg.profile) or {}
            if prof.get("user_name") or prof.get("gaia_name") or prof.get("account_id"):
                _log.info(
                    "[%s|is_browser_google_signed_in|%s|硬编执行|已登录] gaia=%s",
                    _CHAIN,
                    cfg.kind,
                    prof.get("gaia_name") or prof.get("user_name") or "",
                )
                return True
        except Exception as ex:
            _log.warning(
                "[%s|is_browser_google_signed_in|%s|硬编执行|解析失败] error=%s",
                _CHAIN,
                cfg.kind,
                ex,
            )
    prefs = cfg.profile_dir / "Preferences"
    if prefs.is_file():
        try:
            data = json.loads(prefs.read_text(encoding="utf-8"))
            acct = data.get("account_info") or []
            if isinstance(acct, list) and acct:
                return True
            signed = (data.get("google") or {}).get("services") or {}
            if signed.get("signin_scoped_device_id"):
                return True
        except Exception:
            pass
    _log.warning(
        "[%s|is_browser_google_signed_in|%s|硬编执行|未登录] "
        "浏览器 Google 账号未登录，非用户态 Profile",
        _CHAIN,
        cfg.kind,
    )
    return False


def _should_dismiss_login(*, attempt: int = 0) -> bool:
    from .xhs_stateless import cookie_attempts, should_use_stateless

    return attempt >= 3 or cookie_attempts() >= 3 or should_use_stateless()


def _dismiss_xhs_login_modal(page, *, attempt: int = 0) -> bool:
    """关闭小红书「手机号登录」弹窗；第 3 次 Cookie 失败后强制点 × 跳过登录。"""
    if not _should_dismiss_login(attempt=attempt):
        return False
    _log.info(
        "[%s|dismiss_login|Agent执行|跳过] 尝试关闭手机号登录弹窗; attempt=%s",
        _CHAIN,
        attempt,
    )
    selectors = (
        ".login-container .close",
        ".login-modal .close-icon",
        ".close-wrapper",
        ".reds-mask-close",
        "[class*='login'] [class*='close']",
        "[class*='Login'] [class*='close']",
        "div.close",
        "svg.close-icon",
    )
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=3000)
                time.sleep(0.8)
                return True
        except Exception:
            continue
    try:
        if page.get_by_text("手机号登录", exact=False).first.is_visible(timeout=2000):
            page.locator(
                "xpath=//*[contains(text(),'手机号登录')]/ancestor::*[contains(@class,'login') or contains(@class,'Login')]//*[contains(@class,'close') or @aria-label='close' or @aria-label='关闭']"
            ).first.click(timeout=3000)
            time.sleep(0.8)
            return True
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        return True
    except Exception:
        return False


def _prepare_xhs_explore_page(page, *, attempt: int = 0) -> None:
    """打开 explore 并处理登录弹窗。"""
    maybe_bring_page_to_front(page)
    page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=120000)
    time.sleep(2)
    _dismiss_xhs_login_modal(page, attempt=attempt)
    if "/login" in (page.url or ""):
        _dismiss_xhs_login_modal(page, attempt=max(attempt, 3))


def _cdp_ready(port: int = CDP_PORT) -> bool:
    return find_cdp_port() is not None


def _allow_persistent_browser() -> bool:
    """是否允许 Playwright launch_persistent_context（会显示「自动测试软件控制」横幅）。"""
    return os.environ.get("SBA_XHS_ALLOW_PERSISTENT", "").strip().lower() in ("1", "true", "yes")


def favorites_playwright_fallback_enabled() -> bool:
    """CDP 不可用时是否回退 Playwright（Cookie 注入 launch 或 persistent profile）。默认开启。"""
    v = (os.environ.get("SBA_XHS_FAVORITES_PLAYWRIGHT_FALLBACK") or "1").strip().lower()
    return v not in ("0", "false", "no")


def _ensure_cdp_port(cfg: BrowserConfig, port: int = CDP_PORT) -> Optional[int]:
    """确保本机浏览器 CDP 端口可用（attach-only 模式下不启动/不杀 Chrome）。"""
    found = find_cdp_port()
    if found:
        return found
    if xhs_cdp_attach_only():
        return None
    if _start_browser_with_cdp(cfg, port):
        return find_cdp_port()
    return None


def _skip_chrome_ui() -> bool:
    return os.environ.get("SBA_XHS_SKIP_CHROME", "").strip() in ("1", "true", "yes")


def _browser_running(cfg: BrowserConfig) -> bool:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {cfg.process_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return cfg.process_name.lower() in (r.stdout or "").lower()
    except Exception:
        return False


def _close_browser(cfg: BrowserConfig) -> None:
    """仅关闭 CDP 模式 Chrome（带 --remote-debugging-port 参数），不碰日常 Chrome。"""
    if xhs_cdp_attach_only():
        _log.warning("[%s|close_browser|硬编执行|跳过] attach-only 禁止杀 Chrome", _CHAIN)
        return
    # 仅关闭 CDP 模式 Chrome 进程（命令行含 --remote-debugging-port），保护日常 Chrome
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -match 'remote-debugging-port' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass
    time.sleep(2)
    # 仅清理 CDP User Data 锁文件（不碰日常 Profile）
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (cfg.user_data_dir / lock_name).unlink(missing_ok=True)
        except Exception:
            pass


def _chrome_running() -> bool:
    return _browser_running(_browser_config_chrome())


def _edge_running() -> bool:
    edge = _browser_config_edge()
    return bool(edge and _browser_running(edge))


def _restart_browser_with_cdp(cfg: BrowserConfig, port: int = CDP_PORT) -> bool:
    """关闭本机浏览器后用同一 Profile 带 CDP 重启（收藏夹方案 A 默认禁用）。"""
    if xhs_cdp_attach_only():
        _log.warning(
            "[%s|restart_browser_cdp|%s|硬编执行|拒绝] attach-only 模式禁止杀 Chrome 重启",
            _CHAIN,
            cfg.kind,
        )
        return False
    if not is_browser_google_signed_in(cfg):
        return False
    if _browser_running(cfg):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", cfg.process_name],
                capture_output=True,
                timeout=20,
            )
            time.sleep(2.5)
        except Exception as ex:
            _log.warning(
                "[%s|restart_browser_cdp|%s|硬编执行|结束进程失败] error=%s",
                _CHAIN,
                cfg.kind,
                ex,
            )
    return _start_browser_with_cdp(cfg, port=port)


def _start_browser_with_cdp(cfg: BrowserConfig, port: int = CDP_PORT) -> bool:
    """用本机用户 Profile 启动浏览器并打开 CDP（收藏夹方案 A 默认禁用）。"""
    if xhs_cdp_attach_only():
        _log.warning(
            "[%s|start_browser_cdp|%s|硬编执行|拒绝] attach-only 模式禁止新开 Chrome",
            _CHAIN,
            cfg.kind,
        )
        return False
    if not is_browser_google_signed_in(cfg):
        _log.warning(
            "[%s|start_browser_cdp|%s|硬编执行|跳过] Google 账号未登录，不启动非用户态浏览器",
            _CHAIN,
            cfg.kind,
        )
        return False
    if _browser_running(cfg):
        return _cdp_ready(port)
    profile = cfg.user_data_dir
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / lock).unlink(missing_ok=True)
        except Exception:
            pass
    exe = str(cfg.exe)
    profile_s = str(profile)
    ps = (
        f"$p = Start-Process -FilePath '{exe}' -ArgumentList @("
        f"'--remote-debugging-port={port}',"
        f"'--user-data-dir={profile_s}',"
        f"'--profile-directory={cfg.profile}',"
        f"'--remote-allow-origins=*',"
        f"'--start-maximized'"
        f") -PassThru; Start-Sleep 3; "
        f"if ($p.HasExited) {{ 'EXITED' }} else {{ 'OK' }}"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
    for _ in range(60):
        if _cdp_ready(port):
            time.sleep(2)
            return True
        time.sleep(0.5)
    return False


def _start_user_chrome_with_cdp(port: int = CDP_PORT) -> bool:
    return _start_browser_with_cdp(_browser_config_chrome(), port=port)


def _cookies_from_context(context) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for c in context.cookies():
        domain = c.get("domain") or ""
        if any(d in domain for d in PLATFORM_DOMAINS["xiaohongshu"]):
            out[c["name"]] = c["value"]
    return out


def _xhs_user_search_urls(red_id: str) -> List[str]:
    """小红书用户搜索 URL 变体（type=user / type=51 / 无 type，与前端实际跳转对齐）。"""
    from urllib.parse import quote

    kw = quote(str(red_id or "").strip(), safe="")
    return [
        f"https://www.xiaohongshu.com/search_result?keyword={kw}&type=user",
        f"https://www.xiaohongshu.com/search_result?keyword={kw}&type=51",
        f"https://www.xiaohongshu.com/search_result?keyword={kw}",
    ]


def _search_on_page(page, red_id: str, *, attempt: int = 1) -> Tuple[List[Dict], List[str], Optional[Dict]]:
    """在**当前标签**内搜索用户；依次尝试 type=user / type=51 / 无 type。"""
    search_urls = _xhs_user_search_urls(red_id)
    search_url = search_urls[0]
    api_payloads: List[Dict[str, Any]] = []
    profile_ids: List[str] = []

    page.bring_to_front()
    cur = page.url or ""
    if red_id not in cur or "search_result" not in cur:
        navigated = False
        for candidate in search_urls:
            try:
                page.goto(candidate, wait_until="domcontentloaded", timeout=120000)
                time.sleep(2.5)
                navigated = True
                search_url = candidate
                break
            except Exception:
                continue
        if not navigated:
            page.goto(search_url, wait_until="domcontentloaded", timeout=120000)
            time.sleep(2.5)
    _dismiss_xhs_login_modal(page, attempt=attempt)

    if "/login" in (page.url or ""):
        _log.warning("[%s|search|Agent执行|登录页] attempt=%s; 尝试关闭弹窗继续", _CHAIN, attempt)
        _dismiss_xhs_login_modal(page, attempt=max(attempt, 3))
        for _ in range(3):
            time.sleep(1.5)
            if "/login" not in (page.url or ""):
                break
            _dismiss_xhs_login_modal(page, attempt=max(attempt, 3))
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass

    # URL 已带 type=user，不再点击「用户」Tab（避免 Tab 切换导致二次跳转）

    # 等待用户卡片出现在页面上（走 UI，不依赖裸 fetch 签名）
    try:
        page.wait_for_selector("a[href*='/user/profile/']", timeout=45000)
    except Exception:
        pass
    time.sleep(2)

    # 按小红书号文本定位用户卡片
    try:
        card = page.get_by_text(red_id, exact=False).first
        if card.is_visible(timeout=5000):
            link = card.locator("xpath=ancestor::a[contains(@href,'/user/profile/')]").first
            href = link.get_attribute("href")
            if href:
                m = re.search(r"/user/profile/([a-f0-9]{24})", href, re.I)
                if m:
                    profile_ids.append(m.group(1))
    except Exception:
        pass

    try:
        if not page.is_closed():
            fetched = page.evaluate(
                """async (redId) => {
                    const body = {
                        searchUserRequest: {
                            keyword: redId,
                            page: 1,
                            pageSize: 20,
                            searchId: crypto.randomUUID().replace(/-/g, ''),
                            requestId: crypto.randomUUID().replace(/-/g, ''),
                        }
                    };
                    const r = await fetch('/api/sns/web/v1/search/usersearch', {
                        method: 'POST',
                        headers: { 'content-type': 'application/json;charset=UTF-8' },
                        body: JSON.stringify(body),
                        credentials: 'include',
                    });
                    const text = await r.text();
                    try { return { status: r.status, json: JSON.parse(text) }; }
                    catch (e) { return { status: r.status, text: text.slice(0, 800) }; }
                }""",
                red_id,
            )
            if isinstance(fetched, dict) and fetched.get("json"):
                api_payloads.append(fetched["json"])
                _log.info("[%s|fetch|Agent执行|完成] status=%s", _CHAIN, fetched.get("status"))
            elif isinstance(fetched, dict):
                _log.warning("[%s|fetch|Agent执行|非JSON] %s", _CHAIN, str(fetched)[:300])
    except Exception as ex:
        _log.warning("[%s|fetch|Agent执行|失败] %s", _CHAIN, ex)

    try:
        if not page.is_closed():
            hrefs = page.eval_on_selector_all(
                "a[href*='/user/profile/']",
                "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
            )
            for h in hrefs or []:
                m = re.search(r"/user/profile/([a-f0-9]{24})", h or "", re.I)
                if m:
                    profile_ids.append(m.group(1))
    except Exception:
        pass

    state = None
    try:
        if not page.is_closed():
            state = _parse_init_state(page.content())
    except Exception:
        pass

    # 首屏未命中时，依次尝试 type=51 / 无 type（与用户浏览器实际 URL 对齐）
    if not profile_ids and not api_payloads:
        for alt_url in search_urls[1:]:
            if alt_url == search_url:
                continue
            try:
                if page.is_closed():
                    break
                page.goto(alt_url, wait_until="domcontentloaded", timeout=90000)
                time.sleep(2.0)
                _dismiss_xhs_login_modal(page, attempt=attempt)
                try:
                    page.wait_for_selector("a[href*='/user/profile/']", timeout=20000)
                except Exception:
                    pass
                hrefs = page.eval_on_selector_all(
                    "a[href*='/user/profile/']",
                    "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
                )
                for h in hrefs or []:
                    m = re.search(r"/user/profile/([a-f0-9]{24})", h or "", re.I)
                    if m:
                        profile_ids.append(m.group(1))
                if profile_ids:
                    break
                st2 = _parse_init_state(page.content())
                if st2:
                    state = st2
                    from .creator_feed_adapter import _find_user_by_red_id_in_obj

                    if _find_user_by_red_id_in_obj(st2, red_id):
                        break
            except Exception as ex:
                _log.debug("[%s|search|Agent执行|alt_url] %s", _CHAIN, ex)

    return api_payloads, profile_ids, state


def _pick_result(
    api_payloads: List[Dict], profile_ids: List[str], state: Optional[Dict], red_id: str
) -> Optional[Dict[str, Any]]:
    for payload in api_payloads:
        hit = _find_user_by_red_id_in_obj(payload, red_id)
        if hit:
            return _user_dict_to_resolved(hit, red_id, "local_chrome_usersearch")
    if state:
        hit = _find_user_by_red_id_in_obj(state, red_id)
        if hit:
            return _user_dict_to_resolved(hit, red_id, "local_chrome_init_state")
    if profile_ids:
        uid = profile_ids[0]
        return {
            "creator_id": uid,
            "display_name": red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
            "red_id": red_id,
            "source": "local_chrome_profile_link",
        }
    return None


def _open_tab_in_browser(cfg: BrowserConfig, url: str) -> None:
    """在已运行的浏览器中新开前台标签（复用当前用户配置）。"""
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", str(cfg.exe), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _log.info(
            "[%s|open_tab|硬编执行|完成] 已在 %s 新开标签; url=%s",
            _CHAIN,
            cfg.kind,
            url[:120],
        )
    except Exception as ex:
        _log.warning("[%s|open_tab|硬编执行|失败] error=%s", _CHAIN, ex)


def _open_tab_in_user_chrome(url: str) -> None:
    for cfg in _iter_browser_configs():
        if _browser_running(cfg) and is_browser_google_signed_in(cfg):
            _open_tab_in_browser(cfg, url)
            return
    _open_tab_in_browser(_browser_config_chrome(), url)


def _try_import_browser_cookies_via_copy(cfg: BrowserConfig) -> Dict[str, str]:
    """Chrome 运行时 Cookie 库被锁，复制到临时文件再读。"""
    import shutil
    import tempfile

    if not is_browser_google_signed_in(cfg):
        return {}
    src = cfg.profile_dir / "Network" / "Cookies"
    if not src.is_file():
        return {}
    try:
        import browser_cookie3

        getter = getattr(browser_cookie3, cfg.cookie3_name, None)
        if not getter:
            return {}
        tmp = Path(tempfile.mkdtemp(prefix="sba_cookies_"))
        dst = tmp / "Cookies"
        shutil.copy2(src, dst)
        out: Dict[str, str] = {}
        for domain in ("xiaohongshu.com", ".xiaohongshu.com", "www.xiaohongshu.com"):
            try:
                for c in getter(domain_name=domain, cookie_file=str(dst)):
                    if c.name and c.value:
                        out[c.name] = c.value
            except Exception:
                continue
        if out:
            _log.info(
                "[%s|cookies|硬编执行|复制读取] 从 %s Cookie 副本读取; count=%s",
                _CHAIN,
                cfg.kind,
                len(out),
            )
        return out
    except ImportError:
        return {}
    except Exception as ex:
        _log.warning("[%s|cookies|硬编执行|复制读取失败] %s; error=%s", _CHAIN, cfg.kind, ex)
        return {}


def _try_import_browser_cookies_live(cfg: BrowserConfig) -> Dict[str, str]:
    """从本机浏览器 Profile 直读 Cookie（Chrome / Edge）。"""
    if not is_browser_google_signed_in(cfg):
        return {}
    try:
        import browser_cookie3

        getter = getattr(browser_cookie3, cfg.cookie3_name, None)
        if not getter:
            return {}
        cookie_file = cfg.profile_dir / "Network" / "Cookies"
        out: Dict[str, str] = {}
        for domain in ("xiaohongshu.com", ".xiaohongshu.com", "www.xiaohongshu.com"):
            try:
                kwargs: Dict[str, Any] = {"domain_name": domain}
                if cookie_file.is_file():
                    kwargs["cookie_file"] = str(cookie_file)
                for c in getter(**kwargs):
                    if c.name and c.value:
                        out[c.name] = c.value
            except Exception:
                continue
        if out:
            _log.info(
                "[%s|cookies|硬编执行|读取] 从本机 %s Profile 读取 Cookie; count=%s",
                _CHAIN,
                cfg.kind,
                len(out),
            )
        return out
    except ImportError:
        _log.info("[%s|cookies|硬编执行|跳过] 未安装 browser-cookie3", _CHAIN)
        return {}
    except Exception as ex:
        _log.warning("[%s|cookies|硬编执行|失败] %s error=%s", _CHAIN, cfg.kind, ex)
        return {}


def _try_import_chrome_cookies_live() -> Dict[str, str]:
    for cfg in _iter_browser_configs():
        got = _try_import_browser_cookies_live(cfg)
        if got:
            return got
        if _browser_running(cfg):
            got = _try_import_browser_cookies_via_copy(cfg)
            if got:
                return got
    return {}


def _resolve_red_id_with_session(red_id: str, cookies: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """用已登录 Cookie 调 usersearch API（无需 Playwright）。"""
    if not cookies:
        return None
    from .creator_feed_adapter import _find_user_by_red_id_in_obj, _post_search_usersearch, _user_dict_to_resolved

    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.xiaohongshu.com/",
        }
    )
    for k, v in cookies.items():
        sess.cookies.set(k, v, domain=".xiaohongshu.com")
    api_data = _post_search_usersearch(sess, red_id)
    if api_data:
        hit = _find_user_by_red_id_in_obj(api_data, red_id)
        if hit:
            return _user_dict_to_resolved(hit, red_id, "chrome_profile_usersearch")
    return None


def _resolve_with_persistent_browser(cfg: BrowserConfig, red_id: str) -> Dict[str, Any]:
    """兜底：Playwright launch_persistent_context（仅 SBA_XHS_ALLOW_PERSISTENT=1 时启用）。"""
    from playwright.sync_api import sync_playwright

    if not is_browser_google_signed_in(cfg):
        raise RuntimeError(
            f"SUB_XHS_BROWSER_GUEST: {cfg.kind} 未登录 Google 账号，非用户态 Profile，拒绝启动"
        )

    port = _ensure_cdp_port(cfg)
    if port:
        _log.info(
            "[%s|resolve|Agent执行|CDP优先] 已切换 CDP attach，跳过 persistent; port=%s",
            _CHAIN,
            port,
        )
        return _resolve_with_cdp_playwright(red_id, port)

    if not _allow_persistent_browser():
        raise RuntimeError(
            f"SUB_XHS_CDP_REQUIRED: {cfg.kind} 未开启 CDP。"
            f"请关闭后带 --remote-debugging-port={CDP_PORT} 启动，"
            "或设置 SBA_XHS_ALLOW_PERSISTENT=1 允许自动化横幅模式"
        )

    profile_dir = cfg.profile_dir
    _log.warning(
        "[%s|resolve|Agent执行|persistent兜底] %s launch_persistent_context（将出现自动化横幅）",
        _CHAIN,
        cfg.kind,
    )
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            channel=cfg.playwright_channel,
            headless=False,
            args=["--start-maximized"],
            viewport=None,
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.bring_to_front()
        api_payloads, profile_ids, state = _search_on_page(page, red_id, attempt=1)
        try:
            fresh = _cookies_from_context(context)
            if fresh:
                save_cookies_if_better("xiaohongshu", fresh)
        except Exception:
            pass
        got = _pick_result(api_payloads, profile_ids, state, red_id)
        if got:
            got["source"] = f"persistent_{cfg.kind}_{got.get('source', 'search')}"
            if not _KEEP_BROWSER_OPEN:
                context.close()
            return got
        if not _KEEP_BROWSER_OPEN:
            context.close()
    raise RuntimeError(f"SUB_RED_ID_NOT_FOUND: 本机 {cfg.kind} 中未找到小红书号 {red_id}")


def _resolve_with_persistent_chrome(red_id: str) -> Dict[str, Any]:
    last_err: Optional[Exception] = None
    for cfg in _iter_browser_configs():
        try:
            return _resolve_with_persistent_browser(cfg, red_id)
        except Exception as ex:
            last_err = ex
            _log.warning("[%s|resolve|Agent执行|持久化失败] %s error=%s", _CHAIN, cfg.kind, ex)
    if last_err:
        raise last_err
    raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 未找到可用 Chrome/Edge")


def _resolve_with_cdp_playwright(red_id: str, port: int) -> Dict[str, Any]:
    """CDP attach（connect_over_cdp）— 附着本机 Edge/Chrome，优先复用已有搜索标签。"""
    from playwright.sync_api import sync_playwright

    last_err: Optional[Exception] = None
    for attempt in range(1, 3):
        page = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()

                # 优先：用户已打开的搜索页（含 keyword=red_id）
                for pg in context.pages:
                    url = pg.url or ""
                    if red_id in url and "search_result" in url and not pg.is_closed():
                        try:
                            api_payloads, profile_ids, state = _search_on_page(
                                pg, red_id, attempt=attempt
                            )
                            got = _pick_result(api_payloads, profile_ids, state, red_id)
                            if got:
                                _log.info(
                                    "[%s|resolve|Agent执行|复用标签] creator_id=%s; url=%s",
                                    _CHAIN,
                                    got["creator_id"],
                                    url[:100],
                                )
                                return got
                        except Exception as ex:
                            _log.debug("[%s|resolve|Agent执行|复用标签失败] %s", _CHAIN, ex)

                page = context.new_page()
                page.bring_to_front()

                _log.info(
                    "[%s|resolve|Agent执行|CDP attach] port=%s; red_id=%s; attempt=%s",
                    _CHAIN,
                    port,
                    red_id,
                    attempt,
                )
                api_payloads, profile_ids, state = _search_on_page(page, red_id, attempt=attempt)

                try:
                    fresh = _cookies_from_context(context)
                    if fresh:
                        save_cookies_if_better("xiaohongshu", fresh)
                except Exception:
                    pass

                got = _pick_result(api_payloads, profile_ids, state, red_id)
                if got:
                    _log.info(
                        "[%s|resolve|Agent执行|成功] creator_id=%s; mode=cdp_attach",
                        _CHAIN,
                        got["creator_id"],
                    )
                    return got
        except Exception as ex:
            last_err = ex
            _log.warning(
                "[%s|resolve|Agent执行|CDP重试] attempt=%s; error_type=%s; error=%s",
                _CHAIN,
                attempt,
                type(ex).__name__,
                ex,
            )
            time.sleep(1.5)
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    if last_err:
        from .tool_chat_resilience import extract_error_code

        code = extract_error_code(str(last_err))
        if code == "SUB_RED_ID_NOT_FOUND":
            raise last_err
        raise RuntimeError(
            f"SUB_XHS_CDP_SEARCH_FAILED: CDP 搜索小红书号 {red_id} 失败: {last_err}"
        ) from last_err
    raise RuntimeError(f"SUB_RED_ID_NOT_FOUND: 本机浏览器中未找到小红书号 {red_id}")


def _run_sync_off_asyncio_loop(fn, /, *args, **kwargs):
    """Playwright sync_api 不能在 asyncio 事件循环线程内调用，必要时切到独立线程。"""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        in_async = True
    except RuntimeError:
        in_async = False

    if not in_async:
        return fn(*args, **kwargs)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="xhs_pw_sync"
    ) as pool:
        return pool.submit(lambda: fn(*args, **kwargs)).result()


def resolve_with_cdp_playwright(red_id: str, port: int) -> Dict[str, Any]:
    """CDP attach 解析 red_id；在 asyncio 协程链路内自动线程隔离。"""
    return _run_sync_off_asyncio_loop(_resolve_with_cdp_playwright, red_id, port)


def refresh_xhs_cookies_cdp_only() -> Dict[str, Any]:
    """
    仅从正在运行的 Chrome（CDP）同步 Cookie，不杀 Chrome、不 Playwright 冷启动。
    适用于：浏览器里已登录小红书，但磁盘 Cookie 仍是访客态。
    """
    port = find_cdp_port()
    if not port:
        from .cookie_manager import diagnose_xhs_cookies

        diag = diagnose_xhs_cookies()
        return {
            "ok": False,
            "error_code": "CDP_NOT_READY",
            "error": diag.get("hint")
            or "CDP 未就绪：请给 Chrome 加 --remote-debugging-port=9223 后重启",
            "diagnosis": diag,
        }

    from .xhs_owner_chrome import probe_xhs_session_via_cdp

    live = probe_xhs_session_via_cdp()
    if live.get("logged_in"):
        saved = load_cookies("xiaohongshu") or {}
        probe = probe_xhs_cookies_logged_in(saved)
        if probe.get("logged_in"):
            return {
                "ok": True,
                "logged_in": True,
                "source": f"cdp_probe:{port}",
                "count": len(saved),
                "nickname": probe.get("nickname") or live.get("nickname") or "",
                "cdp_port": port,
            }

    fresh = extract_platform_cookies_via_cdp("xiaohongshu", navigate=False)
    if not fresh:
        fresh = extract_platform_cookies_via_cdp("xiaohongshu", navigate=True)
    if fresh:
        saved = save_cookies_if_better("xiaohongshu", fresh)
        probe2 = probe_xhs_cookies_logged_in(saved)
        if probe2.get("logged_in"):
            return {
                "ok": True,
                "logged_in": True,
                "source": f"cdp_extract:{port}",
                "count": len(saved),
                "nickname": probe2.get("nickname") or "",
                "cdp_port": port,
            }

    return {
        "ok": False,
        "error_code": "SUB_XHS_GUEST_SESSION",
        "error": (
            "CDP 已连接，但未从小红书 Tab 读到登录 Cookie。"
            "请在 Chrome 打开小红书任意已登录页面（如直播/收藏 tab=fav）后重试。"
        ),
        "cdp_port": port,
        "xhs_live": live,
    }


def ensure_xhs_cookies_synced(*, force: bool = False) -> Dict[str, Any]:
    """
    与 UI「从 Chrome 同步 Cookie」对齐：优先 CDP 附着同步，再回退 from_system。
    浏览器已登录 ≠ 磁盘 .xhs_cookies.json 已就绪，小红书工具调用前应走本函数。
    """
    existing = load_cookies("xiaohongshu") or {}
    probe = probe_xhs_cookies_logged_in(existing)
    if probe.get("logged_in") and not force:
        return {
            "ok": True,
            "logged_in": True,
            "source": "file_cache",
            "count": len(existing),
            "nickname": probe.get("nickname") or "",
        }

    cdp_result = refresh_xhs_cookies_cdp_only()
    if cdp_result.get("logged_in"):
        return cdp_result

    after = load_cookies("xiaohongshu") or {}
    probe_after = probe_xhs_cookies_logged_in(after)
    if probe_after.get("logged_in"):
        return {
            "ok": True,
            "logged_in": True,
            "source": "file_after_cdp_sync",
            "count": len(after),
            "nickname": probe_after.get("nickname") or "",
            "cdp_attempt": cdp_result,
        }

    return refresh_xhs_cookies_from_system()


def refresh_xhs_cookies_from_system() -> Dict[str, Any]:
    """从本机 Chrome/Edge 同步小红书 Cookie（优先 CDP，回退 Playwright persistent_context）。"""
    _log.info("[%s|refresh_cookies|硬编执行|开始] 同步本机浏览器小红书 Cookie", _CHAIN)

    existing = load_cookies("xiaohongshu") or {}
    probe = probe_xhs_cookies_logged_in(existing)
    if probe.get("logged_in"):
        return {
            "ok": True,
            "logged_in": True,
            "source": "file_cache",
            "count": len(existing),
            "nickname": probe.get("nickname") or "",
        }

    # 优先 CDP 取实时 Cookie
    cdp_port = find_cdp_port()
    if cdp_port:
        fresh = extract_platform_cookies_via_cdp("xiaohongshu", navigate=True)
        if fresh:
            saved = save_cookies_if_better("xiaohongshu", fresh)
            probe2 = probe_xhs_cookies_logged_in(saved)
            if probe2.get("logged_in"):
                return {
                    "ok": True, "logged_in": True,
                    "source": f"cdp:{cdp_port}",
                    "count": len(saved),
                    "nickname": probe2.get("nickname") or "",
                }

    # CDP 不可用：用 Playwright persistent_context 直取真实 Profile Cookie
    for cfg in _iter_browser_configs():
        signed = is_browser_google_signed_in(cfg)
        if not signed:
            continue

        # 先尝试 browser_cookie3 静默读
        fresh = _try_import_browser_cookies_live(cfg)
        if fresh:
            saved = save_cookies_if_better("xiaohongshu", fresh)
            probe2 = probe_xhs_cookies_logged_in(saved)
            if probe2.get("logged_in"):
                return {
                    "ok": True, "logged_in": True,
                    "source": f"browser_cookie3:{cfg.kind}",
                    "count": len(saved),
                    "nickname": probe2.get("nickname") or "",
                    "browser": cfg.kind,
                }

        # browser_cookie3 失败 → Playwright persistent_context（方案 A 禁止杀 Chrome）
        if _browser_running(cfg):
            if xhs_cdp_attach_only():
                _log.warning(
                    "[%s|refresh_cookies|硬编执行|跳过Playwright] attach-only 禁止关闭 Chrome",
                    _CHAIN,
                )
                continue
            _log.warning(
                "[%s|refresh_cookies|硬编执行|关闭Chrome] "
                "关闭 Chrome 后用 persistent_context 提取 Cookie",
                _CHAIN,
            )
            _close_browser(cfg)
            time.sleep(2)

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    str(cfg.profile_dir),
                    channel=cfg.playwright_channel,
                    headless=False,
                    args=["--start-maximized", "--remote-debugging-port=9223"],
                    viewport=None,
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
                # 用 JS 直接验证页面登录态（比 requests 探测更可靠）
                pw_state = page.evaluate("""() => {
                    const state = window.__INITIAL_STATE__ || {};
                    const user = state.user || {};
                    return {
                        loggedIn: !!user.loggedIn,
                        redId: user.redId || (user.userInfo && user.userInfo.redId) || '',
                        nickname: (user.userInfo && user.userInfo.nickname) || '',
                    };
                }""")
                # 从 context 提取 Cookie
                pw_cookies = context.cookies()
                fresh = {}
                for c in pw_cookies:
                    if c.get("name") and c.get("value"):
                        fresh[c["name"]] = c["value"]
                context.close()
                if fresh and pw_state.get("loggedIn"):
                    # 页面已确认登录态，直接强制保存 Cookie（跳过 requests 探测）
                    save_cookies("xiaohongshu", fresh)
                    _log.info(
                        "[%s|refresh_cookies|硬编执行|Playwright成功] "
                        "已通过 persistent_context 提取登录态 Cookie; count=%s; nickname=%s",
                        _CHAIN,
                        len(fresh),
                        pw_state.get("nickname") or "",
                    )
                    return {
                        "ok": True, "logged_in": True,
                        "source": f"playwright_persistent:{cfg.kind}",
                        "count": len(fresh),
                        "nickname": pw_state.get("nickname") or "",
                    }
        except Exception as ex:
            _log.warning(
                "[%s|refresh_cookies|硬编执行|Playwright失败] %s: %s",
                _CHAIN, type(ex).__name__, ex,
            )

    final_probe = probe_xhs_cookies_logged_in(load_cookies("xiaohongshu") or {})
    return {
        "ok": bool(final_probe.get("logged_in")),
        "logged_in": bool(final_probe.get("logged_in")),
        "source": "none",
        "count": len(load_cookies("xiaohongshu") or {}),
        "error": final_probe.get("error")
        or "未获取到已登录 Cookie；请先在配置的 Chrome 用户中登录本人小红书账号",
    }


def _resolve_red_id_via_local_chrome_sync(red_id: str, *, port: int = CDP_PORT) -> Dict[str, Any]:
    """本机 Chrome 解析：CDP 优先（不依赖 JSON）→ HTTP session → Playwright 持久化。"""
    from .cookie_manager import load_cookies
    from .tool_chat_resilience import extract_error_code

    red_id = (red_id or "").strip()
    if not red_id:
        raise ValueError("SUB_INVALID_URL")

    cdp_port = find_cdp_port()
    if not cdp_port:
        for cfg in _iter_browser_configs():
            if is_browser_google_signed_in(cfg):
                cdp_port = _ensure_cdp_port(cfg, port)
                if cdp_port:
                    break
    if not cdp_port or not _cdp_ready(cdp_port or port):
        if _start_owner_chrome_via_shortcut():
            for _ in range(20):
                time.sleep(2)
                cdp_port = find_cdp_port()
                if cdp_port and _cdp_ready(cdp_port):
                    break
    cdp_port = cdp_port or port

    cdp_tried = False
    cdp_last_err: Optional[Exception] = None

    if _cdp_ready(cdp_port):
        cdp_tried = True
        try:
            return _resolve_with_cdp_playwright(red_id, cdp_port)
        except ImportError as ex:
            raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 未安装 playwright") from ex
        except RuntimeError as ex:
            code = extract_error_code(str(ex))
            if code == "SUB_RED_ID_NOT_FOUND":
                raise
            cdp_last_err = ex
        except Exception as ex:
            cdp_last_err = ex

    # HTTP session（JSON Cookie 缓存，可选）
    fresh = load_cookies("xiaohongshu") or {}
    if fresh:
        got = _resolve_red_id_with_session(red_id, fresh)
        if got:
            return got
    sync_res = ensure_xhs_cookies_synced()
    fresh = load_cookies("xiaohongshu") or {}
    if fresh and sync_res.get("logged_in"):
        got = _resolve_red_id_with_session(red_id, fresh)
        if got:
            return got

    if not _skip_chrome_ui():
        try:
            return _resolve_with_persistent_chrome(red_id)
        except ImportError as ex:
            raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 未安装 playwright") from ex
        except Exception as ex:
            _log.warning("[%s|resolve|Agent执行|持久化Chrome失败] %s", _CHAIN, ex)

    if _chrome_running():
        for _su in _xhs_user_search_urls(red_id)[:2]:
            _open_tab_in_user_chrome(_su)
            break

    if not _cdp_ready(cdp_port):
        raise RuntimeError(
            f"SUB_XHS_CDP_REQUIRED: CDP 未就绪。请用「Google Chrome CDP 9223」打开已登录的小红书页面"
            f"（--remote-debugging-port={cdp_port}）。"
        )
    if cdp_tried:
        if cdp_last_err:
            code = extract_error_code(str(cdp_last_err))
            if code:
                raise RuntimeError(str(cdp_last_err)) from cdp_last_err
            raise RuntimeError(
                f"SUB_XHS_CDP_SEARCH_FAILED: CDP 已连接但未解析到小红书号 {red_id}: {cdp_last_err}"
            ) from cdp_last_err
        raise RuntimeError(
            f"SUB_RED_ID_NOT_FOUND: 本机 CDP Chrome 中未找到小红书号 {red_id}。"
            "请确认号正确，或在浏览器打开该号的搜索结果页后重试。"
        )

    raise RuntimeError(
        f"SUB_XHS_CDP_REQUIRED: 无法附着 CDP Chrome（port={cdp_port}）。"
        "请打开 CDP Chrome 并登录小红书。"
    )


def resolve_red_id_via_local_chrome(red_id: str, *, port: int = CDP_PORT) -> Dict[str, Any]:
    """本机 Chrome 解析小红书号；若在 asyncio 循环内则切到线程池执行。"""
    return _run_sync_off_asyncio_loop(_resolve_red_id_via_local_chrome_sync, red_id, port=port)


def _sync_cookies_from_browser_sync(cfg: BrowserConfig, *, port: int = CDP_PORT) -> Dict[str, str]:
    if not is_browser_google_signed_in(cfg):
        raise RuntimeError(f"SUB_XHS_BROWSER_GUEST: {cfg.kind} Google 账号未登录")
    if _browser_running(cfg) and not _cdp_ready(port):
        fresh = _try_import_browser_cookies_live(cfg)
        if fresh:
            save_cookies_if_better("xiaohongshu", fresh)
            return fresh
        raise RuntimeError(
            f"SUB_XHS_PROFILE_BUSY: 请关闭 {cfg.kind} 或启用 --remote-debugging-port={port}"
        )
    if not _cdp_ready(port):
        if xhs_cdp_attach_only():
            raise RuntimeError(
                f"SUB_OWNER_CDP_REQUIRED: 方案A需在你已打开的 Chrome 启用 --remote-debugging-port={port}"
            )
        if not _start_browser_with_cdp(cfg, port):
            raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE")
    from playwright.sync_api import sync_playwright

    cookies: Dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = pick_xhs_page(context)
        if page is None:
            if xhs_cdp_attach_only():
                raise RuntimeError(
                    "SUB_OWNER_NO_XHS_TAB: 方案A不新开标签，请先在 Chrome 打开小红书页面"
                )
            page = context.new_page()
        _prepare_xhs_explore_page(page, attempt=3)
        cookies = _cookies_from_context(context)
        if cookies:
            save_cookies_if_better("xiaohongshu", cookies)
    return cookies


def _sync_cookies_from_local_chrome_sync(*, port: int = CDP_PORT) -> Dict[str, str]:
    for cfg in _iter_browser_configs():
        if is_browser_google_signed_in(cfg):
            return _sync_cookies_from_browser_sync(cfg, port=port)
    raise RuntimeError("SUB_XHS_BROWSER_GUEST: Chrome/Edge 均未登录 Google 账号")


def _fetch_catalog_cdp(port: int, url: str, creator_id: str, *, cfg_kind: str) -> List[Any]:
    """CDP attach 打开博主主页一次，解析后关闭标签。"""
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import _parse_init_state, parse_feed_from_init_state

    page = None
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            _log.info(
                "[%s|fetch_catalog|Agent执行|CDP attach] %s; url=%s",
                _CHAIN,
                cfg_kind,
                url[:100],
            )
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            time.sleep(2.5)
            _dismiss_xhs_login_modal(page, attempt=3)
            state = _parse_init_state(page.content()) or {}
            items = parse_feed_from_init_state(
                state,
                creator_id=creator_id,
                profile_url=url,
                fetch_source=f"cdp_{cfg_kind}",
            )
            try:
                fresh = _cookies_from_context(context)
                if fresh:
                    save_cookies_if_better("xiaohongshu", fresh)
            except Exception:
                pass
            return items
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
    return []


def _cdp_eval_iife(ws_url: str, js_fn_body: str, *, timeout_sec: float = 12.0) -> Any:
    """CDP Runtime.evaluate 需立即执行的 IIFE；js_fn_body 形如 () => { ... }。"""
    expr = js_fn_body.strip()
    if not expr.startswith("(()"):
        expr = f"({expr})()"
    return cdp_tab_eval(ws_url, expr, timeout_sec=timeout_sec)


_NOTE_HREFS_JS = """() => {
    const out = {};
    const add = (href) => {
        if (!href) return;
        let full = href;
        if (full.startsWith('/')) full = 'https://www.xiaohongshu.com' + full;
        const m = full.match(/\\/(?:explore|discovery\\/item)\\/([a-f0-9]{24})/i);
        if (!m) return;
        const id = m[1];
        if (!out[id] || (full.includes('xsec_token') && !out[id].includes('xsec_token'))) {
            out[id] = full;
        }
    };
    document.querySelectorAll('a[href]').forEach(a => add(a.getAttribute('href')));
    document.querySelectorAll('[data-href],[data-url]').forEach(el => {
        add(el.getAttribute('data-href') || el.getAttribute('data-url'));
    });
    return out;
}"""


def _merge_note_link_maps(*maps: Dict[str, str]) -> Dict[str, str]:
    """合并多源链接；优先保留带 xsec_token 的完整 href。"""
    from .creator_feed_adapter import is_valid_xhs_note_id

    out: Dict[str, str] = {}
    for m in maps:
        if not isinstance(m, dict):
            continue
        for nid, url in m.items():
            if not is_valid_xhs_note_id(str(nid or "")):
                continue
            u = str(url or "").strip()
            if not u:
                continue
            if nid not in out or ("xsec_token" in u and "xsec_token" not in out.get(nid, "")):
                out[nid] = u
    return out


_DISMISS_XHS_OVERLAY_JS = """() => {
    const labels = ['关闭', 'Close', '我知道了', '知道了', '暂不', '以后再说'];
    document.querySelectorAll('button, span, i, div, a').forEach(el => {
        const t = (el.textContent || '').trim();
        const aria = (el.getAttribute('aria-label') || '').trim();
        if (labels.some(l => t === l || aria.includes(l))) {
            try { el.click(); } catch (e) {}
        }
    });
    document.querySelectorAll('.reds-mask').forEach(el => {
        try { el.remove(); } catch (e) {}
    });
    return document.querySelectorAll('section.note-item').length;
}"""

_CLICK_FIRST_FAV_NOTE_JS = """() => {
    const el = document.querySelector('section.note-item');
    if (!el) return { ok: false, total: 0 };
    const target = el.querySelector('a.cover') || el.querySelector('.cover')
        || el.querySelector('a[href]') || el;
    try { target.click(); } catch (e) {}
    return {
        ok: true,
        total: document.querySelectorAll('section.note-item').length,
        title: (el.textContent || '').trim().slice(0, 80),
    };
}"""


def _cdp_return_to_favorites_tab(ws_url: str, creator_id: str, fav_url: str) -> None:
    """点击笔记后回到收藏 Tab（history.back 优先，避免整页 goto 丢会话）。"""
    for _ in range(4):
        cur = str(cdp_tab_eval(ws_url, "location.href") or "")
        if _page_on_favorites_tab_url(cur, creator_id):
            _cdp_eval_iife(ws_url, _DISMISS_XHS_OVERLAY_JS)
            return
        cdp_tab_eval(ws_url, "history.back()")
        time.sleep(1.2)
    cur = str(cdp_tab_eval(ws_url, "location.href") or "")
    if not _page_on_favorites_tab_url(cur, creator_id) and fav_url:
        cdp_tab_eval(ws_url, f"location.assign({json.dumps(fav_url)})")
        time.sleep(2.5)
        _cdp_eval_iife(ws_url, _DISMISS_XHS_OVERLAY_JS)


def _collect_note_links_via_click_cdp_ws(
    ws_url: str,
    *,
    creator_id: str,
    fav_url: str,
    max_clicks: int = 30,
) -> List[Tuple[str, str]]:
    """
    收藏页 SSR 无 noteId 时，逐条点击首张卡片，从跳转 URL / redirectPath 解析真实 explore 链接。
    返回 [(note_id, canonical_url), ...]，保持点击顺序。
    """
    from .creator_feed_adapter import (
        extract_xhs_note_id_from_url,
        extract_xhs_note_url_from_location,
        is_valid_xhs_note_id,
    )

    _cdp_eval_iife(ws_url, _DISMISS_XHS_OVERLAY_JS)
    time.sleep(0.5)
    ordered: List[Tuple[str, str]] = []
    seen: set[str] = set()

    for attempt in range(max(1, max_clicks)):
        count = cdp_tab_eval(ws_url, "document.querySelectorAll('section.note-item').length") or 0
        if not count:
            cur = str(cdp_tab_eval(ws_url, "location.href") or "")
            if not _page_on_favorites_tab_url(cur, creator_id):
                _cdp_return_to_favorites_tab(ws_url, creator_id, fav_url)
                count = cdp_tab_eval(ws_url, "document.querySelectorAll('section.note-item').length") or 0
            if not count:
                break

        click_res = _cdp_eval_iife(ws_url, _CLICK_FIRST_FAV_NOTE_JS)
        if not (isinstance(click_res, dict) and click_res.get("ok")):
            break
        time.sleep(2.8)

        loc = str(cdp_tab_eval(ws_url, "location.href") or "")
        html = cdp_tab_get_html(ws_url)
        note_url = extract_xhs_note_url_from_location(loc, html)
        nid = extract_xhs_note_id_from_url(note_url)
        if is_valid_xhs_note_id(nid) and nid not in seen:
            seen.add(nid)
            ordered.append((nid, note_url))
            _log.info(
                "[%s|_collect_note_links_via_click_cdp_ws|Agent执行|解析] attempt=%s; note_id=%s; title=%s",
                _CHAIN,
                attempt + 1,
                nid,
                (click_res.get("title") or "")[:40],
            )

        _cdp_return_to_favorites_tab(ws_url, creator_id, fav_url)
        time.sleep(1.0)

    _log.info(
        "[%s|_collect_note_links_via_click_cdp_ws|Agent执行|完成] count=%s; with_token=%s",
        _CHAIN,
        len(ordered),
        sum(1 for _, u in ordered if "xsec_token" in u),
    )
    return ordered


def _extract_note_links_from_html(html: str) -> Dict[str, str]:
    """从页面 HTML 正则提取 explore/discovery 真实链接（CDP 无 DOM 时的兜底）。"""
    from .creator_feed_adapter import extract_xhs_note_id_from_url, is_valid_xhs_note_id

    links: Dict[str, str] = {}
    if not html:
        return links
    for m in re.finditer(
        r'(?:href|data-href|data-url)\s*=\s*["\']([^"\']*(?:explore|discovery/item)/[a-f0-9]{24}[^"\']*)["\']',
        html,
        re.I,
    ):
        full = (m.group(1) or "").strip()
        if full.startswith("/"):
            full = "https://www.xiaohongshu.com" + full
        nid = extract_xhs_note_id_from_url(full)
        if not is_valid_xhs_note_id(nid):
            continue
        if nid not in links or ("xsec_token" in full and "xsec_token" not in links.get(nid, "")):
            links[nid] = full
    return links


def _collect_note_hrefs_via_cdp_ws(ws_url: str) -> Dict[str, str]:
    """纯 CDP WebSocket 从收藏/主页 DOM 采集笔记 href。"""
    try:
        raw = _cdp_eval_iife(ws_url, _NOTE_HREFS_JS)
        return raw if isinstance(raw, dict) else {}
    except Exception as ex:
        _log.warning("[%s|scrape_favorites|CDP DOM|Agent执行|失败] error=%s", _CHAIN, ex)
        return {}


def _collect_note_hrefs_from_dom(page) -> Dict[str, str]:
    """从博主主页 DOM 采集笔记完整 href（含 xsec_token）。"""
    try:
        raw = page.evaluate(_NOTE_HREFS_JS)
        return raw if isinstance(raw, dict) else {}
    except Exception as ex:
        _log.warning("[%s|scrape_profile_links|DOM|Agent执行|失败] error=%s", _CHAIN, ex)
        return {}


def scrape_profile_note_links_via_cdp(
    profile_url: str,
    *,
    creator_id: str = "",
    min_count: int = 0,
    scroll_rounds: int = 0,
) -> Dict[str, str]:
    """CDP 滚动+点击采集博主主页笔记真实链接（含 xsec_token，与 fetch_catalog 同链路）。"""
    from .creator_feed_adapter import is_valid_xhs_note_id

    cid = (creator_id or "").strip()
    url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if not url:
        return {}

    need = max(0, int(min_count or 0))
    sr = int(scroll_rounds or 0) or catalog_scroll_rounds(need or 60)
    last_err: Optional[Exception] = None
    try:
        items = scrape_profile_feed_items(
            url,
            creator_id=cid,
            scroll_rounds=sr,
            min_count=need or 60,
        )
        links: Dict[str, str] = {}
        for it in items or []:
            nid = str(getattr(it, "note_id", "") or "").strip()
            href = str(getattr(it, "canonical_url", "") or "").strip()
            if not is_valid_xhs_note_id(nid) or not href:
                continue
            cur = links.get(nid) or ""
            if not cur or ("xsec_token" in href and "xsec_token" not in cur):
                links[nid] = href
        _log.info(
            "[%s|scrape_profile_links|Agent执行|成功] count=%s; with_token=%s; need=%s",
            _CHAIN,
            len(links),
            sum(1 for v in links.values() if "xsec_token" in v),
            need or 60,
        )
        return links
    except Exception as ex:
        last_err = ex
        _log.warning(
            "[%s|scrape_profile_links|Agent执行|feed失败] error=%s; 尝试轻量 CDP",
            _CHAIN,
            ex,
        )

    links: Dict[str, str] = {}
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import _parse_init_state, parse_feed_from_init_state

    for cfg in _iter_browser_configs():
        if not is_browser_google_signed_in(cfg):
            continue
        port = _ensure_cdp_port(cfg)
        if not port:
            continue
        page = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                _log.info(
                    "[%s|scrape_profile_links|Agent执行|CDP attach] %s; url=%s",
                    _CHAIN,
                    cfg.kind,
                    url[:100],
                )
                page.goto(url, wait_until="domcontentloaded", timeout=120000)
                time.sleep(2.5)
                _dismiss_xhs_login_modal(page, attempt=3)
                for _ in range(4):
                    links.update(_collect_note_hrefs_from_dom(page))
                    try:
                        page.evaluate("window.scrollBy(0, Math.max(800, window.innerHeight))")
                    except Exception:
                        pass
                    time.sleep(1.2)
                state = _parse_init_state(page.content()) or {}
                cid = creator_id or ""
                if state and cid:
                    for it in parse_feed_from_init_state(
                        state,
                        creator_id=cid,
                        profile_url=url,
                        fetch_source=f"cdp_scrape_{cfg.kind}",
                    ):
                        if it.canonical_url:
                            nid = it.note_id
                            if nid and (
                                nid not in links
                                or (
                                    "xsec_token" in it.canonical_url
                                    and "xsec_token" not in links.get(nid, "")
                                )
                            ):
                                links[nid] = it.canonical_url
                try:
                    fresh = _cookies_from_context(context)
                    if fresh:
                        save_cookies_if_better("xiaohongshu", fresh)
                except Exception:
                    pass
                if links:
                    _log.info(
                        "[%s|scrape_profile_links|Agent执行|成功] count=%s; with_token=%s",
                        _CHAIN,
                        len(links),
                        sum(1 for v in links.values() if "xsec_token" in v),
                    )
                    return links
        except Exception as ex:
            last_err = ex
            _log.warning(
                "[%s|scrape_profile_links|Agent执行|失败] %s; error=%s",
                _CHAIN,
                cfg.kind,
                ex,
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    if last_err:
        raise last_err
    return links


def _click_favorites_tab(page) -> bool:
    """点击个人主页「收藏」Tab。"""
    try:
        clicked = page.evaluate(
            """() => {
                const labels = ['收藏', '我的收藏', 'Collect', '收藏夹'];
                const nodes = document.querySelectorAll(
                    '[role="tab"], .tab-item, div[class*="tab"], span[class*="tab"], a[class*="tab"], button'
                );
                for (const el of nodes) {
                    const t = (el.textContent || '').trim();
                    if (!t || t.length > 12) continue;
                    if (labels.some(l => t === l || t.startsWith(l))) {
                        el.click();
                        return true;
                    }
                }
                const all = document.querySelectorAll('div, span, a');
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    if (t === '收藏' || t === '我的收藏') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        return bool(clicked)
    except Exception:
        return False


def _state_active_tab_query(state: Dict[str, Any]) -> str:
    user = state.get("user") if isinstance(state.get("user"), dict) else {}
    tab = user.get("activeTab") if isinstance(user.get("activeTab"), dict) else {}
    return str(tab.get("query") or tab.get("label") or "").strip().lower()


def _scrape_favorites_via_cdp_ws(
    ws_url: str,
    tab_url: str,
    *,
    creator_id: str,
    scroll_rounds: int = 6,
) -> Dict[str, str]:
    """纯 CDP WebSocket 采集收藏链接（不经过 Playwright）。"""
    from .creator_feed_adapter import _parse_init_state
    from .xhs_favorites_adapter import parse_favorites_from_init_state

    cid = (creator_id or "").strip()
    if "/login" in (tab_url or ""):
        raise RuntimeError("SUB_OWNER_XHS_LOGIN_REQUIRED: 收藏 Tab 在登录页")
    if not _page_on_favorites_tab_url(tab_url, cid):
        raise RuntimeError(
            f"SUB_OWNER_OPEN_FAV_TAB: 请手动打开 profile/{cid}?tab=fav；当前={tab_url[:100]}"
        )

    from .creator_feed_adapter import is_valid_xhs_note_id

    links: Dict[str, str] = {}
    for rnd in range(max(1, scroll_rounds)):
        dom_links = _collect_note_hrefs_via_cdp_ws(ws_url)
        html = cdp_tab_get_html(ws_url)
        html_links = _extract_note_links_from_html(html)
        state = _parse_init_state(html) or {}
        state_links: Dict[str, str] = {}
        items = parse_favorites_from_init_state(
            state,
            owner_creator_id=cid,
            profile_url=tab_url,
            fetch_source="cdp_ws_favorites",
        )
        for it in items:
            if is_valid_xhs_note_id(it.note_id) and it.canonical_url:
                state_links[it.note_id] = it.canonical_url
        links = _merge_note_link_maps(links, dom_links, html_links, state_links)
        if rnd + 1 < scroll_rounds:
            cdp_tab_scroll_bottom(ws_url, rounds=1, pause_sec=1.4)

    if not links:
        fav_url = tab_url if _page_on_favorites_tab_url(tab_url, cid) else (
            f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"
        )
        for nid, note_url in _collect_note_links_via_click_cdp_ws(
            ws_url, creator_id=cid, fav_url=fav_url, max_clicks=30
        ):
            links[nid] = note_url

    _log.info(
        "[%s|_scrape_favorites_via_cdp_ws|Agent执行|采集] rounds=%s; count=%s; with_token=%s",
        _CHAIN,
        scroll_rounds,
        len(links),
        sum(1 for v in links.values() if "xsec_token" in v),
    )
    return links


def _page_on_favorites_tab_url(url: str, creator_id: str) -> bool:
    cur = url or ""
    if not (creator_id and creator_id in cur):
        return False
    return any(k in cur for k in ("tab=fav", "tab=collect", "tab=favorite"))


def _page_on_favorites_tab(page, creator_id: str) -> bool:
    """URL 含 tab=fav/collect 即视为已在收藏 Tab（subTab=note 是收藏内子类，不是「笔记」主 Tab）。"""
    return _page_on_favorites_tab_url(page.url or "", creator_id)


def _scrape_favorites_on_page(
    page,
    fav_url: str,
    *,
    creator_id: str,
    cfg_kind: str,
    scroll_rounds: int = 6,
) -> Dict[str, str]:
    from .creator_feed_adapter import _parse_init_state
    from .xhs_favorites_adapter import parse_favorites_from_init_state

    links: Dict[str, str] = {}
    cur = page.url or ""
    if xhs_cdp_attach_only():
        if not _page_on_favorites_tab(page, creator_id):
            raise RuntimeError(
                f"SUB_OWNER_OPEN_FAV_TAB: 请在你已登录的 Chrome 手动打开收藏页 "
                f"profile/{creator_id}?tab=fav 后再同步；禁止自动导航或新开标签。"
            )
        assert_page_not_xhs_login(page, action="收藏 scrape")
        maybe_bring_page_to_front(page)
        time.sleep(0.8)
    else:
        already_fav = _page_on_favorites_tab(page, creator_id)
        if not already_fav:
            page.goto(fav_url, wait_until="domcontentloaded", timeout=120000)
            time.sleep(2.5)
            _dismiss_xhs_login_modal(page, attempt=3)
        else:
            maybe_bring_page_to_front(page)
            time.sleep(1.0)
    state0 = _parse_init_state(page.content()) or {}
    tab_q = _state_active_tab_query(state0)
    if not xhs_cdp_attach_only() and not _page_on_favorites_tab(page, creator_id) and tab_q in ("note", "笔记", ""):
        if _click_favorites_tab(page):
            time.sleep(2.5)
        else:
            base = fav_url.split("?")[0]
            for alt in ("?tab=collect", "?tab=fav", "?tab=favorite"):
                page.goto(f"{base}{alt}", wait_until="domcontentloaded", timeout=120000)
                time.sleep(2.0)
                _dismiss_xhs_login_modal(page, attempt=3)
                tab_q = _state_active_tab_query(_parse_init_state(page.content()) or {})
                if tab_q not in ("note", "笔记", ""):
                    break
    for _ in range(max(3, scroll_rounds)):
        links = _merge_note_link_maps(
            links,
            _collect_note_hrefs_from_dom(page),
            _extract_note_links_from_html(page.content()),
        )
        try:
            page.evaluate("window.scrollBy(0, Math.max(900, window.innerHeight))")
        except Exception:
            pass
        time.sleep(1.2)
    state = _parse_init_state(page.content()) or {}
    cid = (creator_id or "").strip()
    if state and cid:
        for it in parse_favorites_from_init_state(
            state,
            owner_creator_id=cid,
            profile_url=fav_url,
            fetch_source=f"scrape_fav_{cfg_kind}",
        ):
            if it.canonical_url:
                nid = it.note_id
                if nid and (
                    nid not in links
                    or ("xsec_token" in it.canonical_url and "xsec_token" not in links.get(nid, ""))
                ):
                    links[nid] = it.canonical_url
    return links


def scrape_favorites_note_links_via_cdp(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 6,
) -> Dict[str, str]:
    """CDP 附着已有收藏 Tab 采集链接（方案 A：纯 WebSocket，禁止 Playwright/new 标签）。"""
    assert_plan_a_owner_browser_ops(caller="scrape_favorites_note_links_via_cdp")

    cid = (creator_id or "").strip()
    base = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if not base:
        return {}

    from .xhs_owner_chrome import ensure_owner_chrome_cdp

    ensure_owner_chrome_cdp()
    port = require_cdp_port()
    tabs = cdp_list_tabs(port)
    if cdp_session_looks_like_guest_or_automation(tabs):
        raise RuntimeError(
            "SUB_OWNER_CHROME_GUEST_SESSION: 附着的不是您日常使用的 Chrome。"
            "右上角须显示配置的用户头像，不能是「登录 Chrome」或灰色访客；"
            "请关闭此窗口，在您平时的 Chrome 快捷方式后加 "
            f"--remote-debugging-port={CDP_PORT} --remote-allow-origins=* 后从任务栏打开。"
        )

    tab = cdp_pick_owner_tab(port, prefer_cid=cid)
    if tab is None:
        raise RuntimeError(
            "SUB_OWNER_NO_XHS_TAB: 请在你已打开的 Chrome 中打开小红书收藏页 "
            f"(profile/{cid}?tab=fav)，勿关闭该标签；禁止新开浏览器/标签。"
        )
    tab_url = str(tab.get("url") or "")
    if "/login" in tab_url:
        raise RuntimeError(
            "SUB_OWNER_XHS_LOGIN_REQUIRED: 当前 Tab 在登录页。"
            "请在配置的日常 Chrome 用户中登录本人小红书账号并打开收藏页。"
        )
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("SUB_OWNER_CDP_NO_WS: 无法附着收藏 Tab")

    _log.info(
        "[%s|scrape_favorites|Agent执行|CDP WebSocket] port=%s; url=%s",
        _CHAIN,
        port,
        tab_url[:120],
    )
    links = _scrape_favorites_via_cdp_ws(
        ws_url,
        tab_url,
        creator_id=cid,
        scroll_rounds=scroll_rounds,
    )
    try:
        fresh = cdp_tab_get_xhs_cookies(ws_url)
        if fresh:
            from .xhs_owner_chrome import _expected_xhs_nickname

            save_cookies_if_better("xiaohongshu", fresh, owner_nickname=_expected_xhs_nickname())
    except Exception:
        pass

    if links:
        _log.info(
            "[%s|scrape_favorites|Agent执行|成功] count=%s; with_token=%s",
            _CHAIN,
            len(links),
            sum(1 for v in links.values() if "xsec_token" in v),
        )
        return links

    raise RuntimeError(
        "SUB_FAVORITES_EMPTY: 未在你已打开的 Chrome 收藏页采集到笔记。"
        f"请确认收藏 Tab 已打开且 Chrome 已启用 --remote-debugging-port={CDP_PORT}"
    )


def _playwright_cookies_for_context() -> List[Dict[str, Any]]:
    """将磁盘/本机 Chrome Cookie 转为 Playwright add_cookies 格式。"""
    from .cookie_manager import load_cookies

    jar: Dict[str, str] = dict(load_cookies("xiaohongshu") or {})
    try:
        live = _try_import_chrome_cookies_live()
        if live:
            jar.update(live)
    except Exception:
        pass
    try:
        from .xhs_owner_chrome import refresh_owner_xhs_cookies

        ck = refresh_owner_xhs_cookies()
        if ck.get("logged_in"):
            jar.update(load_cookies("xiaohongshu") or {})
    except Exception:
        pass

    out: List[Dict[str, Any]] = []
    for name, val in jar.items():
        if not name or not val:
            continue
        out.append(
            {
                "name": name,
                "value": val,
                "domain": ".xiaohongshu.com",
                "path": "/",
            }
        )
    return out


def _resolve_xhs_cookies_for_scrape() -> Dict[str, str]:
    """收藏/订阅抓取用 Cookie：文件 → browser_cookie3 → CDP 复用 tab。"""
    cookies = load_cookies("xiaohongshu") or {}
    probe = probe_xhs_cookies_logged_in(cookies) if cookies else {"logged_in": False}
    if probe.get("logged_in"):
        return cookies

    live = _try_import_chrome_cookies_live()
    if live:
        probe_live = probe_xhs_cookies_logged_in(live)
        if probe_live.get("logged_in"):
            save_cookies_if_better("xiaohongshu", live)
            return live

    port = find_cdp_port()
    if port:
        cdp_ck = extract_platform_cookies_via_cdp("xiaohongshu", navigate=False)
        if cdp_ck:
            probe_cdp = probe_xhs_cookies_logged_in(cdp_ck)
            if probe_cdp.get("logged_in"):
                save_cookies_if_better("xiaohongshu", cdp_ck)
                return cdp_ck

    from .cookie_manager import ensure_cookies

    ensured = ensure_cookies("xiaohongshu", open_login_if_missing=False) or {}
    if ensured and probe_xhs_cookies_logged_in(ensured).get("logged_in"):
        return ensured

    # 尝试 CDP 页面探测（比 Cookie 文件更准）
    if port:
        try:
            from .xhs_owner_chrome import probe_xhs_session_via_cdp

            live = probe_xhs_session_via_cdp()
            if live.get("logged_in") and live.get("cookie_count", 0) > 0:
                ck2 = load_cookies("xiaohongshu") or {}
                if ck2 and probe_xhs_cookies_logged_in(ck2).get("logged_in"):
                    return ck2
        except Exception:
            pass

    final = ensured or cookies or {}
    if not final:
        return {}
    final_probe = probe_xhs_cookies_logged_in(final)
    if final_probe.get("logged_in"):
        return final
    # 禁止把访客 Cookie 当登录态交给订阅/收藏（评论路径可单独传 cookies 参数）
    if final_probe.get("guest"):
        _log.warning(
            "[%s|_resolve_xhs_cookies_for_scrape|Cookie|硬编执行|访客态] "
            "拒绝用于订阅/收藏; count=%s",
            _CHAIN,
            len(final),
        )
        return {}
    return final


def scrape_favorites_feed_items_via_headless_cookies(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 6,
) -> List[Any]:
    """
    与评论抓取同模式：磁盘 Cookie + headless Playwright 导航收藏页。
    不依赖 CDP 附着、不杀用户 Chrome、不要求特定 Google Profile。
    """
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import _parse_init_state, is_valid_xhs_note_id
    from .xhs_favorites_adapter import (
        FavoritesFeedItem,
        parse_favorites_from_init_state,
        parse_favorites_meta_from_init_state,
    )
    from .link_hash import url_hash as _link_uh

    cookies = _resolve_xhs_cookies_for_scrape()
    if not cookies:
        raise RuntimeError(
            "SUB_XHS_COOKIE_UNAVAILABLE: 无小红书 Cookie。"
            "请先在本机 Chrome 登录配置的小红书账号，或跑一次带 read_comments 的链接分析以写入 Cookie。"
        )
    if not probe_xhs_cookies_logged_in(cookies).get("logged_in"):
        from .cookie_manager import diagnose_xhs_cookies

        diag = diagnose_xhs_cookies()
        if diag.get("guest"):
            raise RuntimeError(
                "SUB_XHS_GUEST_SESSION: 当前 Cookie 为访客态，无法读取收藏/UP 订阅。"
                "请在本机 Chrome 登录配置的小红书账号后，在设置页重新提取 Cookie 或重启带 CDP 的 Chrome。"
                f" {diag.get('hint','')}"
            )
        raise RuntimeError(
            "SUB_OWNER_XHS_LOGIN_REQUIRED: Cookie 未处于登录态。"
            "请在本机 Chrome 登录小红书后重试（无需 CDP）。"
        )

    cid = (creator_id or "").strip()
    fav_url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if cid and not _page_on_favorites_tab_url(fav_url, cid):
        fav_url = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"

    _log.info(
        "[%s|scrape_favorites_feed_items_via_headless_cookies|Agent执行|开始] url=%s; cookies=%s",
        _CHAIN,
        fav_url[:120],
        len(cookies),
    )

    saved_attach = os.environ.get("SBA_XHS_CDP_ATTACH_ONLY")
    os.environ["SBA_XHS_CDP_ATTACH_ONLY"] = "0"
    by_note: Dict[str, Any] = {}
    html = ""
    state: Dict[str, Any] = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/144.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            context.add_cookies(
                [
                    {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
                    for k, v in cookies.items()
                ]
            )
            page = context.new_page()
            page.goto(fav_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(4)
            cur_url = page.url or ""
            if "/login" in cur_url:
                raise RuntimeError("SUB_OWNER_XHS_LOGIN_REQUIRED: Cookie 已失效，被重定向到登录页")
            login_state = page.evaluate(
                """() => ({
                    loggedIn: !!(window.__INITIAL_STATE__?.user?.loggedIn),
                    guest: !!(window.__INITIAL_STATE__?.user?.guest),
                })"""
            )
            if not login_state.get("loggedIn") or login_state.get("guest"):
                raise RuntimeError(
                    "SUB_OWNER_XHS_LOGIN_REQUIRED: Cookie 会话为访客或未登录，请重新登录小红书"
                )

            links = _scrape_favorites_on_page(
                page,
                fav_url,
                creator_id=cid,
                cfg_kind="headless_cookie",
                scroll_rounds=scroll_rounds,
            )
            html = page.content()
            state = _parse_init_state(html) or {}

            parse_favorites_meta_from_init_state(
                state,
                owner_creator_id=cid,
                profile_url=fav_url,
                fetch_source="headless_fav_meta",
            )
            for it in parse_favorites_from_init_state(
                state,
                owner_creator_id=cid,
                profile_url=fav_url,
                fetch_source="headless_fav_state",
            ):
                if is_valid_xhs_note_id(it.note_id):
                    by_note[it.note_id] = it
            for nid, note_url in links.items():
                if not is_valid_xhs_note_id(nid):
                    continue
                if nid in by_note:
                    cur = by_note[nid]
                    if note_url and "xsec_token" in note_url:
                        cur.canonical_url = note_url
                    continue
                by_note[nid] = FavoritesFeedItem(
                    platform="xiaohongshu_favorites",
                    note_id=nid,
                    canonical_url=note_url,
                    url_hash=_link_uh(note_url),
                    content_type="unknown",
                    title=f"笔记 {nid[:8]}",
                    published_at=None,
                    author_id="",
                    author_name="",
                    fetch_source="headless_fav_dom",
                    author_followers=0,
                    collected_at=None,
                )

            try:
                pw_cookies = context.cookies()
                fresh = {
                    c["name"]: c["value"]
                    for c in pw_cookies
                    if c.get("name") and c.get("value")
                }
                if fresh:
                    save_cookies_if_better("xiaohongshu", fresh)
            except Exception:
                pass
            context.close()
            browser.close()
    finally:
        if saved_attach is None:
            os.environ.pop("SBA_XHS_CDP_ATTACH_ONLY", None)
        else:
            os.environ["SBA_XHS_CDP_ATTACH_ONLY"] = saved_attach

    out = [it for it in by_note.values() if is_valid_xhs_note_id(getattr(it, "note_id", ""))]
    if not out:
        user = (state or {}).get("user") if isinstance((state or {}).get("user"), dict) else {}
        if not user.get("loggedIn") or "登录后可查看" in (html or ""):
            raise RuntimeError("SUB_OWNER_XHS_LOGIN_REQUIRED: headless 收藏页未登录")
        raise RuntimeError("SUB_FAVORITES_EMPTY: headless 未解析到收藏笔记")
    _log.info(
        "[%s|scrape_favorites_feed_items_via_headless_cookies|Agent执行|完成] notes=%s",
        _CHAIN,
        len(out),
    )
    return out


def scrape_favorites_feed_items_via_playwright(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 6,
) -> List[Any]:
    """
    Playwright 兜底：CDP 未就绪时读收藏。
    Chrome 已开 → launch + 注入 Cookie（避免 Profile 锁）；未开 → launch_persistent_context。
    """
    if not favorites_playwright_fallback_enabled() and not _allow_persistent_browser():
        raise RuntimeError(
            f"SUB_OWNER_CDP_REQUIRED: CDP 未就绪且未启用 Playwright 兜底。"
            f"请用桌面快捷方式带 --remote-debugging-port={CDP_PORT} 启动 Chrome，"
            "或设置 SBA_XHS_FAVORITES_PLAYWRIGHT_FALLBACK=1"
        )

    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import _parse_init_state, is_valid_xhs_note_id
    from .xhs_favorites_adapter import (
        FavoritesFeedItem,
        parse_favorites_from_init_state,
        parse_favorites_meta_from_init_state,
    )
    from .link_hash import url_hash as _link_uh

    cfg = _browser_config_chrome()
    if not is_browser_google_signed_in(cfg):
        raise RuntimeError("SUB_XHS_BROWSER_GUEST: Chrome 未登录配置的 Google 账号")

    cid = (creator_id or "").strip()
    fav_url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if cid and not _page_on_favorites_tab_url(fav_url, cid):
        fav_url = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"

    _log.warning(
        "[%s|scrape_favorites_feed_items_via_playwright|Agent执行|Playwright兜底] url=%s",
        _CHAIN,
        fav_url[:120],
    )

    # ── 一次浏览器会话完成全流程：登录验证 + 收藏抓取（不拆分多次会话） ──
    # 保护日常 Chrome：不杀进程、不篡改 attach_only 环境变量

    by_note: Dict[str, Any] = {}
    html = ""
    state: Dict[str, Any] = {}

    # 如果日常 Chrome 正在运行，使用独立 CDP Profile 避免锁冲突
    chrome_busy = _browser_running(cfg)
    profile_dir = str(cfg.profile_dir)
    if chrome_busy and xhs_cdp_attach_only():
        from .chrome_profile_prep import cdp_chrome_user_data_dir
        cdp_dir = cdp_chrome_user_data_dir() / "Default"
        if cdp_dir.is_dir():
            profile_dir = str(cdp_dir)
            _log.info("[%s|scrape_favorites|硬编执行|Profile切换] 日常 Chrome 运行中，使用 CDP Profile: %s", _CHAIN, profile_dir)

    try:
        with sync_playwright() as p:
            _log.info("[%s|scrape_favorites|Agent执行|PersistentProfile] 使用 Chrome Profile 保持登录态", _CHAIN)
            context = p.chromium.launch_persistent_context(
                profile_dir,
                channel=cfg.playwright_channel,
                headless=False,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                viewport=None,
                ignore_default_args=["--enable-automation"],
            )
            page = context.pages[0] if context.pages else context.new_page()

            # 先验证登录态
            page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            login_state = page.evaluate("""() => {
                var s = window.__INITIAL_STATE__ || {};
                var u = s.user || {};
                return {
                    loggedIn: !!(u.loggedIn),
                    guest: !!(u.guest || (u.userInfo && u.userInfo.guest)),
                    redId: u.redId || (u.userInfo && u.userInfo.redId) || '',
                    nickname: (u.userInfo && u.userInfo.nickname) || '',
                };
            }""")
            _log.info(
                "[%s|scrape_favorites|硬编执行|登录检测] loggedIn=%s guest=%s redId=%s",
                _CHAIN,
                login_state.get("loggedIn"),
                login_state.get("guest"),
                (login_state.get("redId") or "")[:10],
            )
            if not login_state.get("loggedIn") or login_state.get("guest"):
                raise RuntimeError(
                    f"SUB_OWNER_XHS_LOGIN_REQUIRED: Chrome Profile 中小红书为{'访客态' if login_state.get('guest') else '未登录'}。"
                    f"loggedIn={login_state.get('loggedIn')} guest={login_state.get('guest')} redId={login_state.get('redId','')[:10]}。"
                    "请在 Chrome 中登录配置的小红书账号后重试。"
                )

            # 登录态确认，导航到收藏页
            _log.info("[%s|scrape_favorites|Agent执行|导航收藏页] url=%s", _CHAIN, fav_url[:120])
            page.goto(fav_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)

            links = _scrape_favorites_on_page(
                page, fav_url, creator_id=cid,
                cfg_kind=cfg.kind,
                scroll_rounds=scroll_rounds,
            )
            html = page.content()
            state = _parse_init_state(html) or {}

            meta_items = parse_favorites_meta_from_init_state(
                state, owner_creator_id=cid, profile_url=fav_url,
                fetch_source="playwright_fav_meta",
            )
            for it in parse_favorites_from_init_state(
                state, owner_creator_id=cid, profile_url=fav_url,
                fetch_source="playwright_fav_state",
            ):
                if is_valid_xhs_note_id(it.note_id):
                    by_note[it.note_id] = it
            for nid, note_url in links.items():
                if not is_valid_xhs_note_id(nid):
                    continue
                if nid in by_note:
                    cur = by_note[nid]
                    if note_url and "xsec_token" in note_url:
                        cur.canonical_url = note_url
                    continue
                by_note[nid] = FavoritesFeedItem(
                    platform="xiaohongshu_favorites",
                    note_id=nid, canonical_url=note_url,
                    url_hash=_link_uh(note_url),
                    content_type="unknown",
                    title=f"笔记 {nid[:8]}",
                    published_at=None, author_id="", author_name="",
                    fetch_source="playwright_fav_dom",
                    author_followers=0, collected_at=None,
                )

            # 保存 Cookie 供后续使用
            try:
                pw_cookies = context.cookies()
                fresh = {}
                for c in pw_cookies:
                    if c.get("name") and c.get("value"):
                        fresh[c["name"]] = c["value"]
                if fresh:
                    save_cookies("xiaohongshu", fresh)
                    _log.info("[%s|scrape_favorites|硬编执行|Cookie保存] 已保存 %s 个 Cookie", _CHAIN, len(fresh))
            except Exception:
                pass

            context.close()
    except Exception as ex:
        _log.error(
            "[%s|scrape_favorites_feed_items_via_playwright|Agent执行|Playwright崩溃] error=%s",
            _CHAIN, ex,
        )
        raise RuntimeError(f"SUB_PLAYWRIGHT_CRASH: {ex}") from ex

    out = [it for it in by_note.values() if is_valid_xhs_note_id(getattr(it, "note_id", ""))]
    if not out:
        user = (state or {}).get("user") if isinstance((state or {}).get("user"), dict) else {}
        if not user.get("loggedIn") or "登录后可查看" in (html or ""):
            raise RuntimeError(
                "SUB_OWNER_XHS_LOGIN_REQUIRED: Playwright 收藏页未登录。"
                "请在 Chrome 登录配置的小红书账号后重试。"
            )
        raise RuntimeError("SUB_FAVORITES_EMPTY: Playwright 未解析到收藏笔记")
    _log.info(
        "[%s|scrape_favorites_feed_items_via_playwright|Agent执行|完成] notes=%s",
        _CHAIN,
        len(out),
    )
    return out


def should_prefer_cookie_favorites_fetch() -> bool:
    """磁盘 Cookie 已登录但 CDP 配置用户/收藏 Tab 未就绪时，优先 Cookie 模式（与评论抓取一致）。"""
    ck = _resolve_xhs_cookies_for_scrape()
    probe = probe_xhs_cookies_logged_in(ck) if ck else {"logged_in": False}
    if not probe.get("logged_in"):
        return False
    port = find_cdp_port()
    if not port:
        return True
    try:
        from .xhs_owner_chrome import verify_plan_a_owner_session

        return not bool(verify_plan_a_owner_session().get("ok"))
    except Exception:
        return True


_MAX_SCRAPE_ATTEMPTS = 3


def scrape_favorites_feed_items(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 6,
    prefer_cookies: bool = False,
) -> List[Any]:
    """
    收藏列表采集链：Cookie → CDP → Headless Cookie → Playwright 兜底。
    全局最多 _MAX_SCRAPE_ATTEMPTS 次尝试，超限后返回空列表（不抛异常）。
    prefer_cookies=True 时跳过 CDP，避免「Cookie 已登录但 CDP 附着访客 Chrome」导致空列表。
    """
    port = find_cdp_port()
    last_err: Optional[Exception] = None
    global_attempt = 0
    use_cookies_first = bool(prefer_cookies or should_prefer_cookie_favorites_fetch())

    if use_cookies_first and favorites_playwright_fallback_enabled():
        global_attempt += 1
        _log.info(
            "[%s|scrape_favorites|Agent执行|Cookie优先|attempt=%s/%s] CDP=%s",
            _CHAIN, global_attempt, _MAX_SCRAPE_ATTEMPTS, port,
        )
        try:
            return scrape_favorites_feed_items_via_headless_cookies(
                profile_url,
                creator_id=creator_id,
                scroll_rounds=scroll_rounds,
            )
        except Exception as ex:
            last_err = ex
            _log.warning(
                "[%s|scrape_favorites|Agent执行|Cookie模式失败|attempt=%s] error=%s",
                _CHAIN, global_attempt, ex,
            )

    if port and global_attempt < _MAX_SCRAPE_ATTEMPTS:
        for attempt in range(1, _MAX_SCRAPE_ATTEMPTS + 1):
            global_attempt += 1
            try:
                return scrape_favorites_feed_items_via_cdp(
                    profile_url,
                    creator_id=creator_id,
                    scroll_rounds=scroll_rounds,
                    _attempt=attempt,
                )
            except Exception as ex:
                last_err = ex
                msg = str(ex)
                if "LOGIN" in msg.upper() or "登录" in msg or "GUEST" in msg.upper():
                    _log.warning(
                        "[%s|scrape_favorites|硬编执行|CDP重试|attempt=%s/%s] 登录态异常: %s",
                        _CHAIN, attempt, _MAX_SCRAPE_ATTEMPTS, msg[:120],
                    )
                    if attempt < _MAX_SCRAPE_ATTEMPTS and global_attempt < _MAX_SCRAPE_ATTEMPTS:
                        time.sleep(3)
                        continue
                elif attempt < _MAX_SCRAPE_ATTEMPTS and global_attempt < _MAX_SCRAPE_ATTEMPTS:
                    time.sleep(2)
                    continue
                break

    if favorites_playwright_fallback_enabled() and global_attempt < _MAX_SCRAPE_ATTEMPTS:
        global_attempt += 1
        _log.warning(
            "[%s|scrape_favorites|Agent执行|headless回退|attempt=%s/%s] last=%s",
            _CHAIN, global_attempt, _MAX_SCRAPE_ATTEMPTS,
            (str(last_err)[:120] if last_err else "no_cdp"),
        )
        try:
            return scrape_favorites_feed_items_via_headless_cookies(
                profile_url,
                creator_id=creator_id,
                scroll_rounds=scroll_rounds,
            )
        except Exception as ex:
            last_err = ex
            _log.warning(
                "[%s|scrape_favorites|Agent执行|headless失败|attempt=%s] error=%s",
                _CHAIN, global_attempt, ex,
            )

        if not xhs_cdp_attach_only() and global_attempt < _MAX_SCRAPE_ATTEMPTS:
            global_attempt += 1
            try:
                return scrape_favorites_feed_items_via_playwright(
                    profile_url,
                    creator_id=creator_id,
                    scroll_rounds=scroll_rounds,
                )
            except Exception as ex:
                last_err = ex
                _log.warning(
                    "[%s|scrape_favorites|Agent执行|playwright失败|attempt=%s] error=%s",
                    _CHAIN, global_attempt, ex,
                )
        elif xhs_cdp_attach_only():
            _log.warning(
                "[%s|scrape_favorites|Agent执行|跳过Playwright] attach-only 禁止 persistent profile",
                _CHAIN,
            )

    if not port and global_attempt < _MAX_SCRAPE_ATTEMPTS:
        _log.warning("[%s|scrape_favorites|硬编执行|启动CDP] CDP 未就绪，用桌面快捷方式启动", _CHAIN)
        if not xhs_cdp_attach_only():
            _start_owner_chrome_via_shortcut()
            for _ in range(15):
                time.sleep(2)
                port = find_cdp_port()
                if port:
                    _log.info("[%s|scrape_favorites|硬编执行|CDP就绪] port=%s", _CHAIN, port)
                    break
        if port:
            for attempt in range(1, _MAX_SCRAPE_ATTEMPTS + 1):
                global_attempt += 1
                if global_attempt > _MAX_SCRAPE_ATTEMPTS:
                    break
                try:
                    return scrape_favorites_feed_items_via_cdp(
                        profile_url,
                        creator_id=creator_id,
                        scroll_rounds=scroll_rounds,
                        _attempt=attempt,
                    )
                except Exception as ex:
                    last_err = ex
                    msg = str(ex)
                    if "LOGIN" in msg.upper() or "登录" in msg or "GUEST" in msg.upper():
                        _log.warning(
                            "[%s|scrape_favorites|硬编执行|CDP重试|attempt=%s/%s] 登录态异常: %s",
                            _CHAIN, attempt, _MAX_SCRAPE_ATTEMPTS, msg[:120],
                        )
                        if attempt < _MAX_SCRAPE_ATTEMPTS and global_attempt < _MAX_SCRAPE_ATTEMPTS:
                            time.sleep(3)
                            continue
                    raise

    _log.error(
        "[%s|scrape_favorites|Agent执行|全部失败] global_attempt=%s/%s; last=%s",
        _CHAIN, global_attempt, _MAX_SCRAPE_ATTEMPTS,
        (str(last_err)[:200] if last_err else "未知"),
    )
    return []


def scrape_favorites_feed_items_via_cdp(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 6,
    _attempt: int = 1,
) -> List[Any]:
    """CDP 附着收藏 Tab，解析带作者信息的收藏笔记列表（供「收藏 UP」拉取）。"""
    from .creator_feed_adapter import _parse_init_state
    from .xhs_favorites_adapter import (
        FavoritesFeedItem,
        parse_favorites_from_init_state,
        parse_favorites_meta_from_init_state,
    )
    from .link_hash import url_hash as _link_uh

    assert_plan_a_owner_browser_ops(caller="scrape_favorites_feed_items_via_cdp")

    cid = (creator_id or "").strip()
    base = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if not base:
        return []

    port = find_cdp_port() or CDP_PORT

    # 先清理 Playwright 残留标签页，避免误判为自动化会话
    import requests as _cdp_req
    try:
        tabs_pre = _cdp_req.get(f"http://127.0.0.1:{port}/json/list", timeout=5).json()
        for t in (tabs_pre if isinstance(tabs_pre, list) else []):
            turl = str(t.get("url") or "")
            if turl.startswith("about:") or turl.startswith("data:"):
                try:
                    _cdp_req.get(f"http://127.0.0.1:{port}/json/close/{t['id']}", timeout=3)
                except Exception:
                    pass
    except Exception:
        pass

    # 只在 CDP 可用、Google 已登录时继续
    cfg_local = _browser_config_chrome()
    if not is_browser_google_signed_in(cfg_local):
        raise RuntimeError(
            "SUB_OWNER_CHROME_PROFILE_MISMATCH: Chrome 未登录配置的 Google 账号。"
            "请确认用桌面快捷方式「Google Chrome CDP 9223」启动的 Chrome 右上角显示配置用户头像。"
        )

    # 找或创建小红书 tab
    tab = cdp_pick_owner_tab(port, prefer_cid=cid)
    if tab is None:
        try:
            _cdp_req.put(f"http://127.0.0.1:{port}/json/new?url=https://www.xiaohongshu.com/explore", timeout=10)
            time.sleep(6)
            tab = cdp_pick_owner_tab(port, prefer_cid=cid)
        except Exception:
            pass
    if tab is None:
        raise RuntimeError("SUB_OWNER_NO_XHS_TAB: 无法找到或创建小红书标签页")
    tab_url = str(tab.get("url") or "")
    if "/login" in tab_url:
        raise RuntimeError("SUB_OWNER_XHS_LOGIN_REQUIRED: 当前 Tab 在登录页")
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("SUB_OWNER_CDP_NO_WS: 无法附着收藏 Tab")

    # 检测小红书登录态（JS 直读 __INITIAL_STATE__，比 URL 判断更准）
    login_js = "JSON.stringify({loggedIn:!!(window.__INITIAL_STATE__?.user?.loggedIn),guest:!!(window.__INITIAL_STATE__?.user?.guest||window.__INITIAL_STATE__?.user?.userInfo?.guest),redId:window.__INITIAL_STATE__?.user?.redId||window.__INITIAL_STATE__?.user?.userInfo?.redId||''})"
    login_raw = cdp_tab_eval(ws_url, login_js, timeout_sec=8)
    login_state = {}
    try:
        login_state = json.loads(str(login_raw or "{}"))
    except Exception:
        pass
    is_guest = login_state.get("guest", True)
    is_logged = login_state.get("loggedIn", False) and not is_guest

    _log.info(
        "[%s|scrape_favorites_via_cdp|硬编执行|登录检测] loggedIn=%s guest=%s redId=%s attempt=%s",
        _CHAIN,
        is_logged,
        is_guest,
        (login_state.get("redId") or "")[:10],
        _attempt,
    )

    if not is_logged:
        if _attempt <= 2:
            _log.warning(
                "[%s|scrape_favorites_via_cdp|硬编执行|重新登录] 未登录，导航到 explore 页恢复会话",
                _CHAIN,
            )
            cdp_tab_eval(ws_url, "window.location.href='https://www.xiaohongshu.com/explore'", timeout_sec=5)
            time.sleep(5)
            raise RuntimeError(
                f"SUB_OWNER_XHS_LOGIN_REQUIRED: 小红书为{'访客态' if is_guest else '未登录'}，已导航到首页请确认登录后重试"
            )
        raise RuntimeError(
            f"SUB_OWNER_XHS_LOGIN_REQUIRED: 已重试 {_attempt} 次仍为{'访客态' if is_guest else '未登录'}。"
            "请在 Chrome 中确认已登录配置的小红书账号"
        )

    # 自动导航到收藏页（用 CDP API 新开标签页，避免 JS 导航被 XHS 检测）
    if not _page_on_favorites_tab_url(tab_url, cid):
        fav_nav = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"
        _log.info(
            "[%s|scrape_favorites_via_cdp|硬编执行|导航收藏页] from=%s to=%s",
            _CHAIN,
            tab_url[:80],
            fav_nav,
        )
        # 用 CDP API 打开新标签页（保留原有登录 tab）
        try:
            r_new = _cdp_req.put(f"http://127.0.0.1:{port}/json/new?url={fav_nav}", timeout=15)
            if r_new.status_code == 200:
                time.sleep(6)
                new_tab = cdp_pick_owner_tab(port, prefer_cid=cid)
                if new_tab and _page_on_favorites_tab_url(str(new_tab.get("url") or ""), cid):
                    tab = new_tab
                    tab_url = str(tab.get("url") or "")
                    ws_url = tab.get("webSocketDebuggerUrl") or ws_url
                else:
                    # 新标签没成功，退回 JS 导航
                    cdp_tab_eval(ws_url, f"window.location.href='{fav_nav}'", timeout_sec=5)
                    time.sleep(5)
            else:
                cdp_tab_eval(ws_url, f"window.location.href='{fav_nav}'", timeout_sec=5)
                time.sleep(5)
        except Exception:
            cdp_tab_eval(ws_url, f"window.location.href='{fav_nav}'", timeout_sec=5)
            time.sleep(5)
        tab_url = fav_nav

    from .creator_feed_adapter import is_valid_xhs_note_id

    by_note: Dict[str, Any] = {}
    link_by_id: Dict[str, str] = {}
    meta_items: List[Any] = []
    fav_url = tab_url if _page_on_favorites_tab_url(tab_url, cid) else (
        f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"
    )
    for rnd in range(max(1, scroll_rounds)):
        dom_links = _collect_note_hrefs_via_cdp_ws(ws_url)
        html = cdp_tab_get_html(ws_url)
        html_links = _extract_note_links_from_html(html)
        link_by_id = _merge_note_link_maps(link_by_id, dom_links, html_links)
        state = _parse_init_state(html) or {}
        if not meta_items:
            meta_items = parse_favorites_meta_from_init_state(
                state,
                owner_creator_id=cid,
                profile_url=tab_url,
                fetch_source="cdp_ws_fav_meta",
            )
        items = parse_favorites_from_init_state(
            state,
            owner_creator_id=cid,
            profile_url=tab_url,
            fetch_source="cdp_ws_fav_up",
        )
        for it in items:
            if is_valid_xhs_note_id(it.note_id):
                by_note[it.note_id] = it
        if rnd + 1 < scroll_rounds:
            cdp_tab_scroll_bottom(ws_url, rounds=1, pause_sec=1.4)

    if not link_by_id:
        for nid, note_url in _collect_note_links_via_click_cdp_ws(
            ws_url, creator_id=cid, fav_url=fav_url, max_clicks=30
        ):
            link_by_id[nid] = note_url

    click_ordered = [(nid, link_by_id[nid]) for nid in link_by_id if nid in link_by_id]
    if meta_items and click_ordered:
        for i, (nid, note_url) in enumerate(click_ordered):
            if i >= len(meta_items):
                break
            meta = meta_items[i]
            if nid in by_note:
                cur = by_note[nid]
                if not getattr(cur, "author_id", "") and meta.author_id:
                    cur.author_id = meta.author_id
                if not getattr(cur, "author_name", "") and meta.author_name:
                    cur.author_name = meta.author_name
                if getattr(cur, "title", "").startswith("笔记 ") and meta.title:
                    cur.title = meta.title
                if note_url and "xsec_token" in note_url:
                    cur.canonical_url = note_url
                continue
            by_note[nid] = FavoritesFeedItem(
                platform="xiaohongshu_favorites",
                note_id=nid,
                canonical_url=note_url,
                url_hash=_link_uh(note_url),
                content_type=meta.content_type or "unknown",
                title=meta.title or f"笔记 {nid[:8]}",
                published_at=meta.published_at,
                author_id=meta.author_id,
                author_name=meta.author_name,
                fetch_source="cdp_ws_fav_click_meta",
                author_followers=meta.author_followers,
                collected_at=meta.collected_at,
            )
    elif link_by_id:
        for nid, note_url in link_by_id.items():
            if nid in by_note:
                cur = by_note[nid]
                if note_url and "xsec_token" in note_url and "xsec_token" not in getattr(cur, "canonical_url", ""):
                    cur.canonical_url = note_url
                continue
            by_note[nid] = FavoritesFeedItem(
                platform="xiaohongshu_favorites",
                note_id=nid,
                canonical_url=note_url,
                url_hash=_link_uh(note_url),
                content_type="unknown",
                title=f"笔记 {nid[:8]}",
                published_at=None,
                author_id="",
                author_name="",
                fetch_source="cdp_ws_fav_up_dom",
                author_followers=0,
                collected_at=None,
            )

    out = [it for it in by_note.values() if is_valid_xhs_note_id(getattr(it, "note_id", ""))]
    if not out:
        # 未登录时收藏 API 常被 rejected，给出可运维定位的报错码
        try:
            from .creator_feed_adapter import _parse_init_state

            st = _parse_init_state(cdp_tab_get_html(ws_url)) or {}
            user = st.get("user") if isinstance(st.get("user"), dict) else {}
            statuses = user.get("userNoteFetchingStatus") or []
            fav_idx = 1
            fav_rejected = (
                len(statuses) > fav_idx and str(statuses[fav_idx]).lower() == "rejected"
            )
            overlay_login = "登录后可查看" in (cdp_tab_get_html(ws_url) or "")
            if fav_rejected or not user.get("loggedIn") or overlay_login:
                raise RuntimeError(
                    "SUB_OWNER_XHS_LOGIN_REQUIRED: 收藏页未登录或 Cookie 失效"
                    f"（loggedIn={user.get('loggedIn')}; fav_status="
                    f"{statuses[fav_idx] if len(statuses) > fav_idx else 'n/a'}）。"
                    "请在 CDP Chrome 登录配置的小红书账号后刷新收藏页 tab=fav。"
                )
        except RuntimeError:
            raise
        except Exception:
            pass
        raise RuntimeError(
            "SUB_FAVORITES_EMPTY: 未在收藏页解析到笔记。"
            f"请确认 Chrome 已启用 --remote-debugging-port={CDP_PORT}"
        )
    _log.info(
        "[%s|scrape_favorites_feed_items_via_cdp|Agent执行|完成] notes=%s; with_author=%s",
        _CHAIN,
        len(out),
        sum(1 for it in out if getattr(it, "author_id", "")),
    )
    return out


def catalog_scroll_rounds(min_count: int = 0) -> int:
    """按目标条数估算主页滚动轮数（对齐收藏夹 sync 的 scroll 策略）。"""
    n = max(0, int(min_count or 0))
    if n <= 20:
        return 6
    if n <= 40:
        return 10
    if n <= 60:
        return 14
    return min(24, max(8, (n + 4) // 5))


def _merge_profile_feed_items(
    by_note: Dict[str, Any],
    items: List[Any],
    *,
    link_by_id: Optional[Dict[str, str]] = None,
    target_creator_id: str,
    profile_url: str,
) -> None:
    """合并 parse_feed 与 DOM 链接，优先保留带 xsec_token 的 URL。"""
    from .creator_feed_adapter import FeedItem, is_valid_xhs_note_id, _build_note_url
    from .link_hash import url_hash as _link_uh

    cid = (target_creator_id or "").strip()
    for it in items or []:
        nid = str(
            getattr(it, "note_id", "")
            or (it.get("note_id") if isinstance(it, dict) else "")
            or (it.get("noteId") if isinstance(it, dict) else "")
            or ""
        ).strip()
        if not is_valid_xhs_note_id(nid):
            continue
        if hasattr(it, "note_id"):
            cur = by_note.get(nid)
            url = str(getattr(it, "canonical_url", "") or "")
            if not cur or ("xsec_token" in url and "xsec_token" not in getattr(cur, "canonical_url", "")):
                by_note[nid] = it
        elif isinstance(it, dict):
            url = str(it.get("canonical_url") or _build_note_url(nid, str(it.get("xsecToken") or "")))
            by_note[nid] = FeedItem(
                platform="xiaohongshu",
                note_id=nid,
                canonical_url=url,
                url_hash=_link_uh(url),
                content_type=str(it.get("content_type") or "unknown"),
                title=str(it.get("title") or f"笔记 {nid[:8]}"),
                published_at=it.get("published_at"),
                author_id=cid,
                author_name=str(it.get("author_name") or ""),
                fetch_source=str(it.get("fetch_source") or "profile_merge"),
            )

    for nid, note_url in (link_by_id or {}).items():
        if not is_valid_xhs_note_id(nid):
            continue
        u = str(note_url or "").strip()
        if not u:
            continue
        cur = by_note.get(nid)
        if cur is not None:
            curl = str(getattr(cur, "canonical_url", "") or "")
            if "xsec_token" in u and "xsec_token" not in curl:
                cur.canonical_url = u
                cur.url_hash = _link_uh(u)
            continue
        by_note[nid] = FeedItem(
            platform="xiaohongshu",
            note_id=nid,
            canonical_url=u,
            url_hash=_link_uh(u),
            content_type="unknown",
            title=f"笔记 {nid[:8]}",
            published_at=None,
            author_id=cid,
            author_name="",
            fetch_source="profile_dom",
        )


def _scrape_profile_via_cdp_ws(
    ws_url: str,
    tab_url: str,
    *,
    target_creator_id: str,
    scroll_rounds: int = 6,
) -> List[Any]:
    """CDP WebSocket 滚动采集博主主页笔记（与收藏夹同模式）。"""
    from .creator_feed_adapter import _parse_init_state, parse_feed_from_init_state, is_valid_xhs_note_id

    cid = (target_creator_id or "").strip()
    profile_url = f"https://www.xiaohongshu.com/user/profile/{cid}"
    if "/login" in (tab_url or ""):
        raise RuntimeError("SUB_OWNER_XHS_LOGIN_REQUIRED: 主页 Tab 在登录页")

    by_note: Dict[str, Any] = {}
    link_by_id: Dict[str, str] = {}
    for rnd in range(max(1, scroll_rounds)):
        dom_links = _collect_note_hrefs_via_cdp_ws(ws_url)
        html = cdp_tab_get_html(ws_url)
        html_links = _extract_note_links_from_html(html)
        link_by_id = _merge_note_link_maps(link_by_id, dom_links, html_links)
        state = _parse_init_state(html) or {}
        items = parse_feed_from_init_state(
            state,
            creator_id=cid,
            profile_url=profile_url,
            fetch_source="cdp_ws_profile",
        )
        _merge_profile_feed_items(
            by_note,
            items,
            link_by_id=link_by_id,
            target_creator_id=cid,
            profile_url=profile_url,
        )
        if rnd + 1 < scroll_rounds:
            cdp_tab_scroll_bottom(ws_url, rounds=1, pause_sec=1.4)

    out = [it for it in by_note.values() if is_valid_xhs_note_id(getattr(it, "note_id", ""))]
    _log.info(
        "[%s|_scrape_profile_via_cdp_ws|Agent执行|采集] creator=%s; rounds=%s; count=%s; with_token=%s",
        _CHAIN,
        cid,
        scroll_rounds,
        len(out),
        sum(1 for it in out if "xsec_token" in getattr(it, "canonical_url", "")),
    )
    return out


_CLICK_PROFILE_NOTE_BY_INDEX_JS = """(idx) => {
    const items = document.querySelectorAll('section.note-item');
    const el = items[idx];
    if (!el) return { ok: false, total: items.length, idx };
    try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    const target = el.querySelector('a.cover') || el.querySelector('.cover')
        || el.querySelector('a[href]') || el;
    try { target.click(); } catch (e) {}
    return {
        ok: true,
        total: items.length,
        idx,
        title: (el.innerText || '').trim().slice(0, 80),
    };
}"""


_CLICK_PROFILE_NOTE_BY_ID_JS = """(noteId) => {
    const a = document.querySelector('a[href*="' + noteId + '"]');
    if (!a) return { ok: false, count: document.querySelectorAll('section.note-item').length };
    try { a.scrollIntoView({ block: 'center' }); } catch (e) {}
    const target = a.closest('section.note-item')
        ? (a.closest('section.note-item').querySelector('a.cover') || a)
        : a;
    try { target.click(); } catch (e) {}
    return { ok: true, href: a.getAttribute('href') || '' };
}"""


def resolve_bare_note_links_via_profile_click(
    profile_url: str,
    note_ids: List[str],
    *,
    creator_id: str = "",
) -> Dict[str, str]:
    """在博主主页按 noteId 定位卡片并点击，从跳转 URL 补全 xsec_token。"""
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import (
        extract_xhs_note_url_from_location,
        is_valid_xhs_note_id,
    )

    cid = (creator_id or "").strip()
    url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    targets = [str(n).strip() for n in note_ids if is_valid_xhs_note_id(str(n or ""))]
    if not url or not targets:
        return {}

    port = find_cdp_port()
    if not port:
        _log.warning(
            "[%s|resolve_bare_note_links_via_profile_click|CDP|Agent执行|未就绪] count=%s",
            _CHAIN,
            len(targets),
        )
        return {}

    out: Dict[str, str] = {}
    page = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            time.sleep(2.5)
            _dismiss_xhs_login_modal(page, attempt=3)

            for nid in targets:
                found = False
                for _ in range(36):
                    click_res = page.evaluate(_CLICK_PROFILE_NOTE_BY_ID_JS, nid)
                    if isinstance(click_res, dict) and click_res.get("ok"):
                        found = True
                        break
                    try:
                        page.evaluate("window.scrollBy(0, Math.max(900, window.innerHeight))")
                    except Exception:
                        pass
                    time.sleep(1.0)
                if not found:
                    _log.warning(
                        "[%s|resolve_bare_note_links_via_profile_click|note:%s|Agent执行|未找到卡片]",
                        _CHAIN,
                        nid[:8],
                    )
                    continue
                time.sleep(2.5)
                loc = page.url or ""
                note_url = extract_xhs_note_url_from_location(loc, page.content())
                if note_url and "xsec_token" in note_url:
                    out[nid] = note_url
                    _log.info(
                        "[%s|resolve_bare_note_links_via_profile_click|note:%s|Agent执行|补全token] ok=true",
                        _CHAIN,
                        nid[:8],
                    )
                else:
                    _log.warning(
                        "[%s|resolve_bare_note_links_via_profile_click|note:%s|Agent执行|点击无token] loc=%s",
                        _CHAIN,
                        nid[:8],
                        loc[:120],
                    )
                _return_to_profile_page(page, url, cid)
                time.sleep(0.8)
            page.close()
    except Exception as ex:
        _log.warning(
            "[%s|resolve_bare_note_links_via_profile_click|Agent执行|失败] error=%s",
            _CHAIN,
            ex,
        )
        if page is not None:
            try:
                page.close()
            except Exception:
                pass

    _log.info(
        "[%s|resolve_bare_note_links_via_profile_click|Agent执行|完成] requested=%s; resolved=%s",
        _CHAIN,
        len(targets),
        len(out),
    )
    return out


def _return_to_profile_page(page, profile_url: str, creator_id: str) -> None:
    """点击笔记详情后回到博主主页（history.back 优先，避免整页 reload 丢滚动位置）。"""
    cid = (creator_id or "").strip()
    for _ in range(4):
        cur = page.url or ""
        if cid and cid in cur and "/user/profile/" in cur:
            _dismiss_xhs_login_modal(page, attempt=1)
            return
        try:
            page.go_back(wait_until="domcontentloaded", timeout=60000)
        except Exception:
            break
        time.sleep(1.2)
    page.goto(profile_url, wait_until="domcontentloaded", timeout=120000)
    time.sleep(1.5)
    _dismiss_xhs_login_modal(page, attempt=1)


def _scrape_profile_on_page(
    page,
    profile_url: str,
    *,
    target_creator_id: str,
    scroll_rounds: int = 6,
    min_count: int = 0,
) -> List[Any]:
    """Playwright Page 滚动采集博主主页笔记。"""
    from .creator_feed_adapter import (
        _parse_init_state,
        extract_profile_notes_from_html,
        extract_xhs_note_url_from_location,
        extract_xhs_note_id_from_url,
        is_valid_xhs_note_id,
        parse_feed_from_init_state,
    )

    cid = (target_creator_id or "").strip()
    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{cid}"
    cur = page.url or ""
    if cid not in cur or "/user/profile/" not in cur:
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        time.sleep(3.0)
        _dismiss_xhs_login_modal(page, attempt=3)
    else:
        maybe_bring_page_to_front(page)
        time.sleep(0.8)

    by_note: Dict[str, Any] = {}
    link_by_id: Dict[str, str] = {}
    need = max(0, int(min_count or 0))

    def _ingest_html(html: str) -> None:
        state = _parse_init_state(html) or {}
        items = parse_feed_from_init_state(
            state,
            creator_id=cid,
            profile_url=url,
            fetch_source="profile_scroll",
        )
        blob_items = extract_profile_notes_from_html(html, creator_id=cid)
        _merge_profile_feed_items(
            by_note,
            items,
            link_by_id=link_by_id,
            target_creator_id=cid,
            profile_url=url,
        )
        _merge_profile_feed_items(
            by_note,
            blob_items,
            target_creator_id=cid,
            profile_url=url,
        )

    def _on_api_response(response) -> None:
        try:
            if response.status != 200:
                return
            rurl = str(response.url or "")
            if "/api/sns/" not in rurl and "edith.xiaohongshu.com" not in rurl:
                return
            body = response.text()
            if not body or "noteId" not in body:
                return
            blob_items = extract_profile_notes_from_html(body, creator_id=cid)
            if blob_items:
                _merge_profile_feed_items(
                    by_note,
                    blob_items,
                    target_creator_id=cid,
                    profile_url=url,
                )
        except Exception:
            pass

    page.on("response", _on_api_response)
    extra_rounds = max(scroll_rounds, ((need + 9) // 10) * 4) if need > 0 else scroll_rounds
    stable_rounds = 0
    last_dom = 0
    last_parsed = 0
    try:
        for rnd in range(max(1, extra_rounds)):
            dom_links = _collect_note_hrefs_from_dom(page)
            html = page.content()
            html_links = _extract_note_links_from_html(html)
            link_by_id = _merge_note_link_maps(link_by_id, dom_links, html_links)
            _ingest_html(html)
            _merge_profile_feed_items(
                by_note,
                [],
                link_by_id=link_by_id,
                target_creator_id=cid,
                profile_url=url,
            )
            dom_count = page.evaluate("document.querySelectorAll('section.note-item').length") or 0
            parsed = len(by_note)
            if need > 0 and parsed >= need:
                break
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                try:
                    page.evaluate("window.scrollBy(0, Math.max(900, window.innerHeight))")
                except Exception:
                    pass
            time.sleep(1.6)
            if dom_count <= last_dom and parsed <= last_parsed:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_dom = dom_count
            last_parsed = parsed
            if stable_rounds >= 5 and (need <= 0 or dom_count >= need or parsed >= dom_count):
                break
    finally:
        try:
            page.remove_listener("response", _on_api_response)
        except Exception:
            pass

    def _tokenless_note_count() -> int:
        return sum(
            1
            for it in by_note.values()
            if "xsec_token" not in (str(getattr(it, "canonical_url", "") or ""))
        )

    # 条数不足或存在裸 explore 链时：逐条点击 note-item，从跳转 URL 补全 xsec_token
    if need <= 0 or len(by_note) < need or _tokenless_note_count() > 0:
        load_rounds = 0
        while need > 0 and load_rounds < 24:
            dom_count = page.evaluate("document.querySelectorAll('section.note-item').length") or 0
            if dom_count >= need:
                break
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            time.sleep(1.8)
            _ingest_html(page.content())
            load_rounds += 1

        dom_count = page.evaluate("document.querySelectorAll('section.note-item').length") or 0
        max_clicks = min(max(dom_count, need, 20), 80)
        seen_ids = set(by_note.keys())
        for idx in range(max_clicks):
            if need > 0 and len(by_note) >= need and _tokenless_note_count() == 0:
                break
            click_res = page.evaluate(_CLICK_PROFILE_NOTE_BY_INDEX_JS, idx)
            if not (isinstance(click_res, dict) and click_res.get("ok")):
                break
            time.sleep(2.5)
            loc = page.url or ""
            html = page.content()
            note_url = extract_xhs_note_url_from_location(loc, html)
            nid = extract_xhs_note_id_from_url(note_url)
            if is_valid_xhs_note_id(nid) and nid not in seen_ids:
                seen_ids.add(nid)
                _merge_profile_feed_items(
                    by_note,
                    [
                        {
                            "noteId": nid,
                            "title": click_res.get("title") or f"笔记 {nid[:8]}",
                            "canonical_url": note_url,
                        }
                    ],
                    target_creator_id=cid,
                    profile_url=url,
                )
                _log.info(
                    "[%s|_scrape_profile_on_page|Agent执行|点击解析] idx=%s; note_id=%s; total=%s",
                    _CHAIN,
                    idx,
                    nid,
                    len(by_note),
                )
            elif is_valid_xhs_note_id(nid) and note_url and "xsec_token" in note_url:
                cur = by_note.get(nid)
                if cur is not None:
                    curl = str(getattr(cur, "canonical_url", "") or "")
                    if "xsec_token" not in curl:
                        cur.canonical_url = note_url
                        from .link_hash import url_hash as _link_uh

                        cur.url_hash = _link_uh(note_url)
                        _log.info(
                            "[%s|_scrape_profile_on_page|Agent执行|点击升级token] idx=%s; note_id=%s",
                            _CHAIN,
                            idx,
                            nid,
                        )
            _return_to_profile_page(page, url, cid)
            time.sleep(0.8)

    out = [it for it in by_note.values() if is_valid_xhs_note_id(getattr(it, "note_id", ""))]
    dom_final = 0
    try:
        dom_final = page.evaluate("document.querySelectorAll('section.note-item').length") or 0
    except Exception:
        pass
    _log.info(
        "[%s|_scrape_profile_on_page|Agent执行|采集] creator=%s; rounds=%s; count=%s; dom=%s; need=%s",
        _CHAIN,
        cid,
        extra_rounds,
        len(out),
        dom_final,
        need,
    )
    return out


def scrape_profile_feed_items_via_headless_cookies(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 6,
) -> List[Any]:
    """Cookie + headless Playwright 采集他人 UP 主页（与收藏夹同链路）。"""
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import is_valid_xhs_note_id

    cookies = _resolve_xhs_cookies_for_scrape()
    if not cookies:
        raise RuntimeError(
            "SUB_XHS_COOKIE_UNAVAILABLE: 无小红书 Cookie。请在本机 Chrome 登录小红书后同步 Cookie。"
        )
    if not probe_xhs_cookies_logged_in(cookies).get("logged_in"):
        raise RuntimeError(
            "SUB_OWNER_XHS_LOGIN_REQUIRED: Cookie 未处于登录态，请重新登录小红书"
        )

    cid = (creator_id or "").strip()
    url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if not url:
        return []

    _log.info(
        "[%s|scrape_profile_feed_items_via_headless_cookies|Agent执行|开始] url=%s; scroll=%s",
        _CHAIN,
        url[:120],
        scroll_rounds,
    )

    saved_attach = os.environ.get("SBA_XHS_CDP_ATTACH_ONLY")
    os.environ["SBA_XHS_CDP_ATTACH_ONLY"] = "0"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/144.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            context.add_cookies(
                [
                    {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
                    for k, v in cookies.items()
                ]
            )
            page = context.new_page()
            out = _scrape_profile_on_page(
                page,
                url,
                target_creator_id=cid,
                scroll_rounds=scroll_rounds,
                min_count=min_count or 0,
            )
            if "/login" in (page.url or ""):
                raise RuntimeError("SUB_OWNER_XHS_LOGIN_REQUIRED: Cookie 已失效，被重定向到登录页")
            try:
                fresh = _cookies_from_context(context)
                if fresh:
                    save_cookies_if_better("xiaohongshu", fresh)
            except Exception:
                pass
            context.close()
            browser.close()
    finally:
        if saved_attach is None:
            os.environ.pop("SBA_XHS_CDP_ATTACH_ONLY", None)
        else:
            os.environ["SBA_XHS_CDP_ATTACH_ONLY"] = saved_attach

    out = [it for it in out if is_valid_xhs_note_id(getattr(it, "note_id", ""))]
    if not out:
        raise RuntimeError("SUB_PROFILE_CATALOG_EMPTY: headless Cookie 模式未解析到 UP 笔记")
    return out


def scrape_profile_feed_items_via_cdp(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 6,
    min_count: int = 0,
    _attempt: int = 1,
) -> List[Any]:
    """CDP 附着 + Playwright 新标签打开他人 UP 主页并滚动采集（与收藏夹同登录态）。"""
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import is_valid_xhs_note_id

    assert_plan_a_owner_browser_ops(caller="scrape_profile_feed_items_via_cdp")

    cid = (creator_id or "").strip()
    url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if not url:
        return []

    cfg_local = _browser_config_chrome()
    if not is_browser_google_signed_in(cfg_local):
        raise RuntimeError(
            "SUB_OWNER_CHROME_PROFILE_MISMATCH: Chrome 未登录配置的 Google 账号。"
        )

    port = find_cdp_port() or CDP_PORT
    page = None
    last_err: Optional[Exception] = None
    for cfg in _iter_browser_configs():
        if not is_browser_google_signed_in(cfg):
            continue
        p = _ensure_cdp_port(cfg)
        if not p:
            continue
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{p}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                out = _scrape_profile_on_page(
                    page,
                    url,
                    target_creator_id=cid,
                    scroll_rounds=scroll_rounds,
                    min_count=min_count,
                )
                try:
                    fresh = _cookies_from_context(context)
                    if fresh:
                        save_cookies_if_better("xiaohongshu", fresh)
                except Exception:
                    pass
                page.close()
            out = [it for it in out if is_valid_xhs_note_id(getattr(it, "note_id", ""))]
            if not out:
                raise RuntimeError("SUB_PROFILE_CATALOG_EMPTY: CDP 滚动后仍未解析到 UP 笔记")
            _log.info(
                "[%s|scrape_profile_feed_items_via_cdp|Agent执行|完成] creator=%s; count=%s; attempt=%s",
                _CHAIN,
                cid,
                len(out),
                _attempt,
            )
            return out
        except Exception as ex:
            last_err = ex
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            _log.warning(
                "[%s|scrape_profile_feed_items_via_cdp|Agent执行|失败] %s; error=%s",
                _CHAIN,
                cfg.kind,
                ex,
            )
    raise RuntimeError(f"SUB_PROFILE_CATALOG_EMPTY: {last_err}") from last_err


def scrape_profile_feed_items(
    profile_url: str,
    *,
    creator_id: str = "",
    scroll_rounds: int = 0,
    min_count: int = 0,
    prefer_cookies: bool = False,
) -> List[Any]:
    """
    UP 主页笔记采集链：Cookie 优先 → CDP 滚动 → headless Cookie 兜底。
    与收藏夹订阅同一登录态与滚动策略，仅目标页为「他人主页」而非收藏 Tab。
    """
    cid = (creator_id or "").strip()
    url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    sr = int(scroll_rounds or 0) or catalog_scroll_rounds(min_count or 60)
    port = find_cdp_port()
    last_err: Optional[Exception] = None

    if port:
        for attempt in range(1, 4):
            try:
                return scrape_profile_feed_items_via_cdp(
                    url,
                    creator_id=cid,
                    scroll_rounds=sr,
                    min_count=min_count or 60,
                    _attempt=attempt,
                )
            except Exception as ex:
                last_err = ex
                msg = str(ex)
                if "LOGIN" in msg.upper() or "登录" in msg or "GUEST" in msg.upper():
                    if attempt < 3:
                        time.sleep(3)
                        continue
                break

    use_cookies_first = bool(prefer_cookies or should_prefer_cookie_favorites_fetch())
    if use_cookies_first and favorites_playwright_fallback_enabled():
        _log.info(
            "[%s|scrape_profile_feed_items|Agent执行|Cookie优先] creator=%s; scroll=%s",
            _CHAIN,
            cid,
            sr,
        )
        try:
            return scrape_profile_feed_items_via_headless_cookies(
                url, creator_id=cid, scroll_rounds=sr
            )
        except Exception as ex:
            last_err = ex
            _log.warning(
                "[%s|scrape_profile_feed_items|Agent执行|Cookie失败] error=%s; 尝试 CDP",
                _CHAIN,
                ex,
            )

    if port:
        for attempt in range(1, 4):
            try:
                return scrape_profile_feed_items_via_cdp(
                    url, creator_id=cid, scroll_rounds=sr, min_count=min_count or 60, _attempt=attempt
                )
            except Exception as ex:
                last_err = ex
                msg = str(ex)
                if "LOGIN" in msg.upper() or "登录" in msg or "GUEST" in msg.upper():
                    if attempt < 3:
                        time.sleep(3)
                        continue
                break

    if favorites_playwright_fallback_enabled():
        try:
            return scrape_profile_feed_items_via_headless_cookies(
                url, creator_id=cid, scroll_rounds=sr
            )
        except Exception as ex:
            last_err = ex

    if not port:
        _start_owner_chrome_via_shortcut()
        for _ in range(15):
            time.sleep(2)
            port = find_cdp_port()
            if port:
                break
        if port:
            try:
                return scrape_profile_feed_items_via_cdp(
                    url, creator_id=cid, scroll_rounds=sr, min_count=min_count or 60, _attempt=1
                )
            except Exception as ex:
                last_err = ex

    raise RuntimeError(f"SUB_PROFILE_CATALOG_FAILED: {last_err}") from last_err


def fetch_catalog_via_headless_cookies(
    creator_id: str,
    *,
    profile_url: str = "",
    scroll_rounds: int = 0,
    min_count: int = 0,
) -> List[Any]:
    """UP 主页笔记：磁盘 Cookie + 滚动（委托 scrape_profile_feed_items）。"""
    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    sr = int(scroll_rounds or 0) or catalog_scroll_rounds(min_count or 60)
    return scrape_profile_feed_items_via_headless_cookies(
        url,
        creator_id=creator_id,
        scroll_rounds=sr,
    )


def _fetch_catalog_via_headless_cookies_legacy(
    creator_id: str,
    *,
    profile_url: str = "",
) -> List[Any]:
    """旧版单次解析（保留供对照，勿在生产路径调用）。"""
    from playwright.sync_api import sync_playwright

    from .cookie_manager import ensure_cookies
    from .creator_feed_adapter import _parse_init_state, parse_feed_from_init_state

    cookies = _resolve_xhs_cookies_for_scrape()
    if not cookies:
        return []

    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    _log.info(
        "[%s|fetch_catalog_via_headless_cookies|Agent执行|开始] creator_id=%s",
        _CHAIN,
        creator_id,
    )
    saved_attach = os.environ.get("SBA_XHS_CDP_ATTACH_ONLY")
    os.environ["SBA_XHS_CDP_ATTACH_ONLY"] = "0"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/144.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            context.add_cookies(
                [
                    {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
                    for k, v in cookies.items()
                ]
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            for _ in range(4):
                try:
                    page.evaluate("window.scrollBy(0, Math.max(900, window.innerHeight))")
                except Exception:
                    pass
                time.sleep(1.0)
            state = _parse_init_state(page.content()) or {}
            items = parse_feed_from_init_state(
                state,
                creator_id=creator_id,
                profile_url=url,
                xsec_token="",
                fetch_source="headless_profile_cookie",
            )
            context.close()
            browser.close()
    finally:
        if saved_attach is None:
            os.environ.pop("SBA_XHS_CDP_ATTACH_ONLY", None)
        else:
            os.environ["SBA_XHS_CDP_ATTACH_ONLY"] = saved_attach

    _log.info(
        "[%s|fetch_catalog_via_headless_cookies|Agent执行|完成] count=%s",
        _CHAIN,
        len(items),
    )
    return items


def fetch_catalog_via_browser(
    creator_id: str,
    *,
    profile_url: str = "",
    min_count: int = 0,
    scroll_rounds: int = 0,
) -> List[Any]:
    """Cookie/CDP 滚动采集博主主页笔记（与收藏夹订阅同模式）。"""
    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    sr = int(scroll_rounds or 0) or catalog_scroll_rounds(min_count or 60)
    try:
        items = scrape_profile_feed_items(
            url,
            creator_id=creator_id,
            scroll_rounds=sr,
            min_count=min_count or 60,
        )
        if items:
            _log.info(
                "[%s|fetch_catalog_via_browser|Agent执行|成功] count=%s; need=%s; scroll=%s",
                _CHAIN,
                len(items),
                min_count,
                sr,
            )
            return items
    except Exception as ex:
        _log.warning(
            "[%s|fetch_catalog_via_browser|Agent执行|滚动采集失败] error=%s; 尝试单次 CDP",
            _CHAIN,
            ex,
        )
        last_err: Optional[Exception] = ex

    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    for cfg in _iter_browser_configs():
        if not is_browser_google_signed_in(cfg):
            continue
        port = _ensure_cdp_port(cfg)
        if port:
            try:
                items = _fetch_catalog_cdp(port, url, creator_id, cfg_kind=cfg.kind)
                if items:
                    _log.info(
                        "[%s|fetch_catalog_via_browser|%s|Agent执行|成功] mode=cdp; count=%s",
                        _CHAIN,
                        cfg.kind,
                        len(items),
                    )
                    return items
            except Exception as ex:
                last_err = ex
                _log.warning(
                    "[%s|fetch_catalog_via_browser|%s|Agent执行|CDP失败] error=%s",
                    _CHAIN,
                    cfg.kind,
                    ex,
                )

    if not _allow_persistent_browser():
        if last_err:
            raise last_err
        return []

    _log.warning(
        "[%s|fetch_catalog|Agent执行|persistent兜底] SBA_XHS_ALLOW_PERSISTENT=1",
        _CHAIN,
    )
    for cfg in _iter_browser_configs():
        if not is_browser_google_signed_in(cfg):
            continue
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    str(cfg.profile_dir),
                    channel=cfg.playwright_channel,
                    headless=False,
                    args=["--start-maximized"],
                    viewport=None,
                    ignore_default_args=["--enable-automation"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=120000)
                time.sleep(2.5)
                _dismiss_xhs_login_modal(page, attempt=3)
                state = _parse_init_state(page.content()) or {}
                items = parse_feed_from_init_state(
                    state,
                    creator_id=creator_id,
                    profile_url=url,
                    fetch_source=f"persistent_{cfg.kind}",
                )
                if not _KEEP_BROWSER_OPEN:
                    context.close()
                if items:
                    return items
        except Exception as ex:
            last_err = ex
    if last_err:
        raise last_err
    return []


def sync_cookies_from_local_chrome(*, port: int = CDP_PORT) -> Dict[str, str]:
    return _run_sync_off_asyncio_loop(_sync_cookies_from_local_chrome_sync, port=port)
