"""本机 Chrome 用户配置：可见窗口 + 页面内搜索（不杀正在使用的 Chrome）。"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .cookie_manager import PLATFORM_DOMAINS, save_cookies
from .creator_feed_adapter import _find_user_by_red_id_in_obj, _parse_init_state, _user_dict_to_resolved

_log = logging.getLogger("sba.xhs_local_browser")

_CHAIN = "社媒订阅-本机Chrome解析"
CDP_PORT = int(os.environ.get("SBA_CHROME_CDP_PORT", "9223"))
_KEEP_BROWSER_OPEN = os.environ.get("SBA_XHS_KEEP_BROWSER", "1") != "0"


def _chrome_user_data_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"


def _chrome_profile() -> str:
    return (os.environ.get("SBA_CHROME_PROFILE") or "Default").strip() or "Default"


def _chrome_exe() -> Path:
    for p in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ):
        if p.is_file():
            return p
    raise FileNotFoundError("未找到 chrome.exe")


def _cdp_ready(port: int = CDP_PORT) -> bool:
    try:
        return requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).status_code == 200
    except Exception:
        return False


def _chrome_running() -> bool:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "chrome.exe" in (r.stdout or "").lower()
    except Exception:
        return False


def _start_user_chrome_with_cdp(port: int = CDP_PORT) -> bool:
    """用本机 Default 用户配置启动 Chrome 并打开 CDP（仅当当前没有 Chrome 在跑）。"""
    if _chrome_running():
        return _cdp_ready(port)
    profile = _chrome_user_data_dir()
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / lock).unlink(missing_ok=True)
        except Exception:
            pass
    chrome = str(_chrome_exe())
    profile_s = str(profile)
    ps = (
        f"$p = Start-Process -FilePath '{chrome}' -ArgumentList @("
        f"'--remote-debugging-port={port}',"
        f"'--user-data-dir={profile_s}',"
        f"'--profile-directory={_chrome_profile()}',"
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


def _cookies_from_context(context) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for c in context.cookies():
        domain = c.get("domain") or ""
        if any(d in domain for d in PLATFORM_DOMAINS["xiaohongshu"]):
            out[c["name"]] = c["value"]
    return out


def _search_on_page(page, red_id: str) -> Tuple[List[Dict], List[str], Optional[Dict]]:
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={red_id}&type=user"
    api_payloads: List[Dict[str, Any]] = []
    profile_ids: List[str] = []

    page.bring_to_front()
    try:
        page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=120000)
        time.sleep(3)
    except Exception as ex:
        _log.warning("[%s|search|硬编执行|explore失败] error=%s", _CHAIN, ex)

    page.bring_to_front()
    page.goto(search_url, wait_until="domcontentloaded", timeout=120000)
    time.sleep(3)

    if "/login" in (page.url or ""):
        _log.warning("[%s|search|Agent执行|未登录] 当前为登录页，等待最多 90s", _CHAIN)
        for _ in range(30):
            time.sleep(3)
            if "/login" not in (page.url or ""):
                break
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass

    for sel in ("text=用户", "[role=tab]:has-text('用户')"):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=3000):
                loc.click()
                time.sleep(2)
                break
        except Exception:
            pass

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


def _open_tab_in_user_chrome(url: str) -> None:
    """在已运行的 Chrome 中新开前台标签（Windows start，复用当前用户配置）。"""
    try:
        chrome = str(_chrome_exe())
        subprocess.Popen(
            ["cmd", "/c", "start", "", chrome, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _log.info("[%s|open_tab|硬编执行|完成] 已在当前 Chrome 新开标签; url=%s", _CHAIN, url[:120])
    except Exception as ex:
        _log.warning("[%s|open_tab|硬编执行|失败] error=%s", _CHAIN, ex)


def _try_import_chrome_cookies_live() -> Dict[str, str]:
    """Chrome 已运行时，从本机 Default Profile 读取 Cookie（不杀进程、不另起后台窗口）。"""
    try:
        import browser_cookie3

        out: Dict[str, str] = {}
        for domain in ("xiaohongshu.com", ".xiaohongshu.com", "www.xiaohongshu.com"):
            try:
                for c in browser_cookie3.chrome(domain_name=domain):
                    if c.name and c.value:
                        out[c.name] = c.value
            except Exception:
                continue
        if out:
            _log.info(
                "[%s|cookies|硬编执行|读取] 从本机 Chrome Profile 读取 Cookie; count=%s",
                _CHAIN,
                len(out),
            )
        return out
    except ImportError:
        _log.info("[%s|cookies|硬编执行|跳过] 未安装 browser-cookie3，跳过 Profile 直读", _CHAIN)
        return {}
    except Exception as ex:
        _log.warning("[%s|cookies|硬编执行|失败] error=%s", _CHAIN, ex)
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


def _resolve_with_persistent_chrome(red_id: str) -> Dict[str, Any]:
    """用本机 Chrome Default 配置启动可见窗口（无需 CDP 端口）。"""
    from playwright.sync_api import sync_playwright

    profile_root = _chrome_user_data_dir()
    if _chrome_running():
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True,
                timeout=15,
            )
            time.sleep(2)
        except Exception:
            pass

    profile_dir = profile_root / _chrome_profile()
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chrome",
            headless=False,
            args=["--start-maximized"],
            viewport=None,
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.bring_to_front()
        _log.info("[%s|resolve|Agent执行|开始] 持久化 Chrome 配置搜索小红书号 %s", _CHAIN, red_id)
        try:
            page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=120000)
            time.sleep(3)
            fresh = _cookies_from_context(context)
            if fresh:
                save_cookies("xiaohongshu", fresh)
                got = _resolve_red_id_with_session(red_id, fresh)
                if got:
                    _log.info("[%s|resolve|Agent执行|成功] creator_id=%s; source=cookie_api", _CHAIN, got["creator_id"])
                    if not _KEEP_BROWSER_OPEN:
                        context.close()
                    return got
        except Exception as ex:
            _log.warning("[%s|resolve|Agent执行|Cookie API 失败] error=%s", _CHAIN, ex)

        api_payloads, profile_ids, state = _search_on_page(page, red_id)
        try:
            fresh = _cookies_from_context(context)
            if fresh:
                save_cookies("xiaohongshu", fresh)
        except Exception:
            pass
        got = _pick_result(api_payloads, profile_ids, state, red_id)
        if got:
            _log.info("[%s|resolve|Agent执行|成功] creator_id=%s", _CHAIN, got["creator_id"])
            if not _KEEP_BROWSER_OPEN:
                context.close()
            return got
        if not _KEEP_BROWSER_OPEN:
            context.close()
    raise RuntimeError(f"SUB_RED_ID_NOT_FOUND: 本机浏览器中未找到小红书号 {red_id}")


def _resolve_with_cdp_playwright(red_id: str, port: int) -> Dict[str, Any]:
    """CDP 连接本机 Chrome，在新标签页内完成搜索（前台可见）。"""
    from playwright.sync_api import sync_playwright

    last_err: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                time.sleep(1.5 if attempt == 1 else 3)
                page = context.new_page()
                page.bring_to_front()

                _log.info(
                    "[%s|resolve|Agent执行|开始] CDP 新标签搜索小红书号 %s; attempt=%s",
                    _CHAIN,
                    red_id,
                    attempt,
                )
                api_payloads, profile_ids, state = _search_on_page(page, red_id)

                try:
                    fresh = _cookies_from_context(context)
                    if fresh:
                        save_cookies("xiaohongshu", fresh)
                except Exception:
                    pass

                got = _pick_result(api_payloads, profile_ids, state, red_id)
                if got:
                    _log.info(
                        "[%s|resolve|Agent执行|成功] creator_id=%s; attempt=%s",
                        _CHAIN,
                        got["creator_id"],
                        attempt,
                    )
                    return got
        except Exception as ex:
            last_err = ex
            _log.warning(
                "[%s|resolve|Agent执行|重试] attempt=%s; error_type=%s; error=%s",
                _CHAIN,
                attempt,
                type(ex).__name__,
                ex,
            )
            time.sleep(2)

    if last_err:
        raise last_err
    raise RuntimeError(f"SUB_RED_ID_NOT_FOUND: 本机浏览器中未找到小红书号 {red_id}")


def _resolve_red_id_via_local_chrome_sync(red_id: str, *, port: int = CDP_PORT) -> Dict[str, Any]:
    """单次轻量尝试：CDP 已开则自动化；否则只读 Cookie 或新开标签，不杀 Chrome。"""
    red_id = (red_id or "").strip()
    if not red_id:
        raise ValueError("SUB_INVALID_URL")

    if _cdp_ready(port):
        try:
            return _resolve_with_cdp_playwright(red_id, port)
        except ImportError as ex:
            raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 未安装 playwright") from ex

    fresh = _try_import_chrome_cookies_live()
    if fresh:
        save_cookies("xiaohongshu", fresh)
        got = _resolve_red_id_with_session(red_id, fresh)
        if got:
            return got

    if _chrome_running():
        search_url = f"https://www.xiaohongshu.com/search_result?keyword={red_id}&type=user"
        _open_tab_in_user_chrome(search_url)

    raise RuntimeError("SUB_XHS_COOKIE_UNAVAILABLE: 本机 Chrome Cookie 读取失败（单次尝试）")


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


def _sync_cookies_from_local_chrome_sync(*, port: int = CDP_PORT) -> Dict[str, str]:
    if _chrome_running() and not _cdp_ready(port):
        fresh = _try_import_chrome_cookies_live()
        if fresh:
            save_cookies("xiaohongshu", fresh)
            return fresh
        raise RuntimeError("SUB_XHS_PROFILE_BUSY: 请先关闭 Chrome 或启用 --remote-debugging-port=9223")
    if not _cdp_ready(port) and not _start_user_chrome_with_cdp(port):
        raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE")
    from playwright.sync_api import sync_playwright

    cookies: Dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=90000)
        time.sleep(2)
        cookies = _cookies_from_context(context)
        if cookies:
            save_cookies("xiaohongshu", cookies)
    return cookies


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
