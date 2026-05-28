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
    for _ in range(40):
        if _cdp_ready(port):
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
    page.goto(search_url, wait_until="domcontentloaded", timeout=120000)
    time.sleep(2)

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


def resolve_red_id_via_local_chrome(red_id: str, *, port: int = CDP_PORT) -> Dict[str, Any]:
    red_id = (red_id or "").strip()
    if not red_id:
        raise ValueError("SUB_INVALID_URL")

    if _chrome_running() and not _cdp_ready(port):
        search_url = f"https://www.xiaohongshu.com/search_result?keyword={red_id}&type=user"
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(_chrome_exe()), search_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log.info("[%s|resolve|Agent执行|已打开] 已在您正在使用的 Chrome 中打开搜索页", _CHAIN)
        except Exception:
            pass
        raise RuntimeError(
            "SUB_XHS_PROFILE_BUSY: 已在您的 Chrome 中打开搜索页，但无法自动读取结果。"
            "请任选其一：① 关闭所有 Chrome 窗口后立刻重试（脚本会用您的用户配置自动完成搜索）；"
            "② 给 Chrome 快捷方式加上 --remote-debugging-port=9223 后用它启动 Chrome，再重试。"
        )

    if not _cdp_ready(port) and not _start_user_chrome_with_cdp(port):
        raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 无法启动本机 Chrome（用户配置）")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as ex:
        raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE: 未安装 playwright") from ex

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.bring_to_front()

        _log.info("[%s|resolve|Agent执行|开始] 在本机 Chrome 中搜索小红书号 %s", _CHAIN, red_id)
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
            return got

    raise RuntimeError(f"SUB_RED_ID_NOT_FOUND: 本机浏览器中未找到小红书号 {red_id}")


def sync_cookies_from_local_chrome(*, port: int = CDP_PORT) -> Dict[str, str]:
    if _chrome_running() and not _cdp_ready(port):
        raise RuntimeError("SUB_XHS_PROFILE_BUSY: 请先关闭 Chrome 或启用 --remote-debugging-port=9223")
    if not _cdp_ready(port) and not _start_user_chrome_with_cdp(port):
        raise RuntimeError("SUB_XHS_BROWSER_UNAVAILABLE")
    from playwright.sync_api import sync_playwright

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
