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
    page.bring_to_front()
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


def _ensure_cdp_port(cfg: BrowserConfig, port: int = CDP_PORT) -> Optional[int]:
    """确保本机浏览器 CDP 端口可用（不杀进程，仅附加调试端口）。"""
    found = find_cdp_port()
    if found:
        return found
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


def _chrome_running() -> bool:
    return _browser_running(_browser_config_chrome())


def _edge_running() -> bool:
    edge = _browser_config_edge()
    return bool(edge and _browser_running(edge))


def _start_browser_with_cdp(cfg: BrowserConfig, port: int = CDP_PORT) -> bool:
    """用本机用户 Profile 启动浏览器并打开 CDP（须已登录 Google 账号）。"""
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


def _search_on_page(page, red_id: str, *, attempt: int = 1) -> Tuple[List[Dict], List[str], Optional[Dict]]:
    """在**当前标签**内搜索用户；直接打开 type=user 搜索页，不再先跳 explore（避免页面来回闪）。"""
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={red_id}&type=user"
    api_payloads: List[Dict[str, Any]] = []
    profile_ids: List[str] = []

    page.bring_to_front()
    cur = page.url or ""
    if search_url not in cur and red_id not in cur:
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
    """CDP attach（connect_over_cdp）— 附着本机 Edge/Chrome，单次导航，无自动化横幅。"""
    from playwright.sync_api import sync_playwright

    last_err: Optional[Exception] = None
    for attempt in range(1, 3):
        page = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
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
        raise last_err
    raise RuntimeError(f"SUB_RED_ID_NOT_FOUND: 本机浏览器中未找到小红书号 {red_id}")


def refresh_xhs_cookies_from_system() -> Dict[str, Any]:
    """从本机 Chrome/Edge 同步小红书 Cookie（须浏览器 Google 账号已登录）。"""
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

    browser_status: List[str] = []
    for cfg in _iter_browser_configs():
        signed = is_browser_google_signed_in(cfg)
        browser_status.append(f"{cfg.kind}:google={'yes' if signed else 'no'}")
        if not signed:
            continue

        fresh = _try_import_browser_cookies_live(cfg)
        if fresh:
            saved = save_cookies_if_better("xiaohongshu", fresh)
            probe = probe_xhs_cookies_logged_in(saved)
            if probe.get("logged_in"):
                return {
                    "ok": True,
                    "logged_in": True,
                    "source": f"browser_cookie3:{cfg.kind}",
                    "count": len(saved),
                    "nickname": probe.get("nickname") or "",
                    "browser": cfg.kind,
                }

        if not find_cdp_port() and _browser_running(cfg):
            if _start_browser_with_cdp(cfg):
                fresh = extract_platform_cookies_via_cdp("xiaohongshu", navigate=True)
                if fresh:
                    saved = save_cookies_if_better("xiaohongshu", fresh)
                    probe = probe_xhs_cookies_logged_in(saved)
                    if probe.get("logged_in"):
                        return {
                            "ok": True,
                            "logged_in": True,
                            "source": f"cdp_restart:{cfg.kind}",
                            "count": len(saved),
                            "nickname": probe.get("nickname") or "",
                            "browser": cfg.kind,
                        }

    cdp_port = find_cdp_port()
    if cdp_port:
        fresh = extract_platform_cookies_via_cdp("xiaohongshu", navigate=True)
        if fresh:
            saved = save_cookies_if_better("xiaohongshu", fresh)
            probe = probe_xhs_cookies_logged_in(saved)
            if probe.get("logged_in"):
                return {
                    "ok": True,
                    "logged_in": True,
                    "source": f"cdp:{cdp_port}",
                    "count": len(saved),
                    "nickname": probe.get("nickname") or "",
                }

    if not _skip_chrome_ui():
        for cfg in _iter_browser_configs():
            if not is_browser_google_signed_in(cfg):
                continue
            try:
                cookies = _sync_cookies_from_browser_sync(cfg)
                if cookies:
                    saved = save_cookies_if_better("xiaohongshu", cookies)
                    probe = probe_xhs_cookies_logged_in(saved)
                    if probe.get("logged_in"):
                        return {
                            "ok": True,
                            "logged_in": True,
                            "source": f"cdp_playwright_sync:{cfg.kind}",
                            "count": len(saved),
                            "nickname": probe.get("nickname") or "",
                            "browser": cfg.kind,
                        }
            except Exception as ex:
                _log.warning(
                    "[%s|refresh_cookies|硬编执行|CDP同步失败] %s error=%s",
                    _CHAIN,
                    cfg.kind,
                    ex,
                )

    probe = probe_xhs_cookies_logged_in(load_cookies("xiaohongshu") or {})
    return {
        "ok": bool(probe.get("logged_in")),
        "logged_in": bool(probe.get("logged_in")),
        "source": "none",
        "count": len(load_cookies("xiaohongshu") or {}),
        "browsers": browser_status,
        "error": probe.get("error")
        or "未获取到已登录 Cookie；请先在 Chrome/Edge 登录 Google 账号并登录小红书",
    }


def _resolve_red_id_via_local_chrome_sync(red_id: str, *, port: int = CDP_PORT) -> Dict[str, Any]:
    """本机 Chrome 解析：CDP / Cookie 同步 / Playwright 持久化配置。"""
    red_id = (red_id or "").strip()
    if not red_id:
        raise ValueError("SUB_INVALID_URL")

    refresh_xhs_cookies_from_system()
    fresh = load_cookies("xiaohongshu") or {}
    if fresh:
        got = _resolve_red_id_with_session(red_id, fresh)
        if got:
            return got

    cdp_port = find_cdp_port()
    if not cdp_port:
        for cfg in _iter_browser_configs():
            if is_browser_google_signed_in(cfg):
                cdp_port = _ensure_cdp_port(cfg, port)
                if cdp_port:
                    break
    cdp_port = cdp_port or port
    if _cdp_ready(cdp_port):
        try:
            return _resolve_with_cdp_playwright(red_id, cdp_port)
        except ImportError as ex:
            raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 未安装 playwright") from ex

    if not _skip_chrome_ui():
        try:
            return _resolve_with_persistent_chrome(red_id)
        except ImportError as ex:
            raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 未安装 playwright") from ex
        except Exception as ex:
            _log.warning("[%s|resolve|Agent执行|持久化Chrome失败] %s", _CHAIN, ex)

    if _chrome_running():
        search_url = f"https://www.xiaohongshu.com/search_result?keyword={red_id}&type=user"
        _open_tab_in_user_chrome(search_url)

    raise RuntimeError(
        "SUB_XHS_COOKIE_UNAVAILABLE: 本机 Chrome Cookie 读取失败。"
        "请在本机 Chrome 登录小红书，或设置 Chrome 启动参数 --remote-debugging-port=9223"
    )


def resolve_red_id_via_local_chrome(red_id: str, *, port: int = CDP_PORT) -> Dict[str, Any]:
    """本机 Chrome 解析小红书号；若在 asyncio 循环内则切到线程池执行。"""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        in_async = True
    except RuntimeError:
        in_async = False

    if not in_async:
        return _resolve_red_id_via_local_chrome_sync(red_id, port=port)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_resolve_red_id_via_local_chrome_sync, red_id, port=port).result()


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
    if not _cdp_ready(port) and not _start_browser_with_cdp(cfg, port):
        raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE")
    from playwright.sync_api import sync_playwright

    cookies: Dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
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


def _collect_note_hrefs_from_dom(page) -> Dict[str, str]:
    """从博主主页 DOM 采集笔记完整 href（含 xsec_token）。"""
    try:
        raw = page.evaluate(
            """() => {
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
        )
        return raw if isinstance(raw, dict) else {}
    except Exception as ex:
        _log.warning("[%s|scrape_profile_links|DOM|Agent执行|失败] error=%s", _CHAIN, ex)
        return {}


def scrape_profile_note_links_via_cdp(
    profile_url: str,
    *,
    creator_id: str = "",
) -> Dict[str, str]:
    """CDP 打开博主主页，采集所选笔记在页面上可见的真实链接。"""
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import _parse_init_state, parse_feed_from_init_state

    url = profile_url or (f"https://www.xiaohongshu.com/user/profile/{creator_id}" if creator_id else "")
    if not url:
        return {}

    links: Dict[str, str] = {}
    last_err: Optional[Exception] = None

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
                const labels = ['收藏', '我的收藏', 'Collect'];
                const nodes = document.querySelectorAll(
                    '[role="tab"], .tab-item, div[class*="tab"], span[class*="tab"], a[class*="tab"]'
                );
                for (const el of nodes) {
                    const t = (el.textContent || '').trim();
                    if (labels.some(l => t === l || t.startsWith(l))) {
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
    page.goto(fav_url, wait_until="domcontentloaded", timeout=120000)
    time.sleep(2.5)
    _dismiss_xhs_login_modal(page, attempt=3)
    if "tab=fav" not in fav_url and "tab=collect" not in fav_url:
        if _click_favorites_tab(page):
            time.sleep(2.0)
    for _ in range(max(3, scroll_rounds)):
        links.update(_collect_note_hrefs_from_dom(page))
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
    """CDP 打开个人主页收藏 Tab，采集收藏笔记真实链接（含 xsec_token）。"""
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import _parse_init_state
    from .xhs_favorites_adapter import parse_favorites_from_init_state

    cid = (creator_id or "").strip()
    base = profile_url or (f"https://www.xiaohongshu.com/user/profile/{cid}" if cid else "")
    if not base:
        return {}

    fav_urls = [
        f"{base.rstrip('/')}?tab=fav",
        f"{base.rstrip('/')}?tab=collect",
        f"{base.rstrip('/')}?tab=favorite",
        base,
    ]

    links: Dict[str, str] = {}
    last_err: Optional[Exception] = None

    from .cookie_manager import find_cdp_port
    from .xhs_owner_chrome import ensure_owner_chrome_cdp, iter_owner_chrome_configs

    ensure_owner_chrome_cdp()

    for cfg in iter_owner_chrome_configs():
        port = find_cdp_port()
        if not port:
            continue
        page = None
        for fav_url in fav_urls:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.new_page()
                    _log.info(
                        "[%s|scrape_favorites|Agent执行|CDP attach] %s; url=%s",
                        _CHAIN,
                        cfg.kind,
                        fav_url[:120],
                    )
                    links = _scrape_favorites_on_page(
                        page,
                        fav_url,
                        creator_id=cid,
                        cfg_kind=cfg.kind,
                        scroll_rounds=scroll_rounds,
                    )
                    try:
                        fresh = _cookies_from_context(context)
                        if fresh:
                            from .xhs_owner_chrome import _expected_xhs_nickname

                            save_cookies_if_better(
                                "xiaohongshu", fresh, owner_nickname=_expected_xhs_nickname()
                            )
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
            except Exception as ex:
                last_err = ex
                _log.warning(
                    "[%s|scrape_favorites|Agent执行|失败] %s; url=%s; error=%s",
                    _CHAIN,
                    cfg.kind,
                    fav_url[:80],
                    ex,
                )
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
                    page = None

    if last_err and not links:
        raise last_err
    if not links:
        raise RuntimeError(
            "SUB_FAVORITES_EMPTY: 未在 Chrome（有光/三点、水）收藏页采集到笔记。"
            "请确认已打开个人主页收藏 Tab，且 Chrome 已启用 --remote-debugging-port=9223"
        )
    return links


def fetch_catalog_via_browser(
    creator_id: str,
    *,
    profile_url: str = "",
) -> List[Any]:
    """CDP attach 打开博主主页解析笔记（requests 无列表时兜底；默认不用 persistent）。"""
    from playwright.sync_api import sync_playwright

    from .creator_feed_adapter import _parse_init_state, parse_feed_from_init_state

    url = profile_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
    last_err: Optional[Exception] = None

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
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        in_async = True
    except RuntimeError:
        in_async = False

    if not in_async:
        return _sync_cookies_from_local_chrome_sync(port=port)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_sync_cookies_from_local_chrome_sync, port=port).result()
