"""Chrome Cookie 管理器：自动提取、验证、触发登录弹窗。"""
from __future__ import annotations

import json
import logging
import os as _os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

_log = logging.getLogger("sba.cookie_manager")

_COOKIE_DIR = Path(__file__).resolve().parents[2] / "output"

PLATFORM_COOKIE_FILES = {
    "xiaohongshu": _COOKIE_DIR / ".xhs_cookies.json",
    "douyin": _COOKIE_DIR / ".dy_cookies.json",
    "bilibili": _COOKIE_DIR / ".bili_cookies.json",
}

PLATFORM_LOGIN_URLS = {
    "xiaohongshu": "https://www.xiaohongshu.com/login",
    "douyin": "https://www.douyin.com/?is_login=1",
    "bilibili": "https://passport.bilibili.com/login",
}

PLATFORM_DOMAINS = {
    "xiaohongshu": [".xiaohongshu.com", "www.xiaohongshu.com", "edith.xiaohongshu.com"],
    "douyin": [".douyin.com", "www.douyin.com"],
    "bilibili": [".bilibili.com", "www.bilibili.com", "api.bilibili.com"],
}

CDP_PORT = 9223


# ═══════════════════════════════════════════════════════════════════
#  Chrome CDP 启动
# ═══════════════════════════════════════════════════════════════════

def _kill_chrome():
    """强制关闭所有 Chrome 进程。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe"],
            capture_output=True, timeout=10,
        )
        time.sleep(2)
    except Exception:
        pass


def _remove_locks():
    """删除 Chrome Profile 锁文件。"""
    profile = Path(_os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
    for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        try:
            (profile / lock).unlink(missing_ok=True)
        except Exception:
            pass


def _start_chrome_cdp() -> bool:
    """启动 Chrome 并开启 CDP 监听（使用 PowerShell 确保稳定启动）。"""
    _kill_chrome()
    time.sleep(2)
    _remove_locks()

    profile = Path(_os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"

    ps_script = f"""
Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
Start-Sleep 3
Remove-Item '{profile}\\SingletonLock' -Force -ErrorAction SilentlyContinue
Remove-Item '{profile}\\SingletonCookie' -Force -ErrorAction SilentlyContinue
Remove-Item '{profile}\\SingletonSocket' -Force -ErrorAction SilentlyContinue
$p = Start-Process 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' -ArgumentList '--remote-debugging-port={CDP_PORT}', ('--user-data-dir={profile}'), '--profile-directory=Default', '--remote-allow-origins=*' -PassThru
Start-Sleep 10
if ($p.HasExited) {{ Write-Output ('EXITED:' + $p.ExitCode) }} else {{ Write-Output ('PID:' + $p.Id) }}
"""

    try:
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        _log.info("Chrome 启动结果: %s", output[:100])

        if "EXITED" in output:
            _log.error("Chrome 异常退出")
            return False
    except Exception as e:
        _log.error("启动 Chrome 失败: %s", e)
        return False

    # 等待 CDP 就绪
    for _ in range(10):
        time.sleep(1.5)
        try:
            import requests as _req
            r = _req.get(
                f"http://127.0.0.1:{CDP_PORT}/json/version",
                timeout=3,
            )
            if r.status_code == 200:
                _log.info("Chrome CDP 就绪")
                return True
        except Exception:
            pass
    return False


def _restart_chrome_normal():
    """关闭 CDP Chrome 并重启正常模式。"""
    _kill_chrome()
    time.sleep(2)
    _remove_locks()
    try:
        subprocess.Popen(
            ["chrome.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Cookie 提取
# ═══════════════════════════════════════════════════════════════════

def _extract_cookies_for_platform(platform: str, navigate: bool = True) -> Dict[str, str]:
    """通过 CDP 提取指定平台的 Cookie。"""
    import requests
    import websocket as _ws

    try:
        r = requests.get(
            f"http://127.0.0.1:{CDP_PORT}/json",
            timeout=5,
        )
        tabs = r.json()
    except Exception as e:
        _log.error("获取 CDP tabs 失败: %s", e)
        return {}

    # 找到或创建目标平台的 tab
    ws_url = None
    for t in tabs:
        url = t.get("url", "")
        domain = PLATFORM_DOMAINS[platform][0].lstrip(".")
        if domain in url:
            ws_url = t["webSocketDebuggerUrl"]
            _log.info("复用现有 tab: %s", url[:80])
            break

    if not ws_url and navigate:
        login_url = PLATFORM_LOGIN_URLS.get(platform, f"https://www.{PLATFORM_DOMAINS[platform][0].lstrip('.')}")
        try:
            r2 = requests.put(
                f"http://127.0.0.1:{CDP_PORT}/json/new?url={login_url}",
                timeout=10,
            )
            new_tab = r2.json()
            ws_url = new_tab["webSocketDebuggerUrl"]
            time.sleep(5)  # 等待页面加载
        except Exception as e:
            _log.error("创建新 tab 失败: %s", e)
            return {}

    if not ws_url:
        _log.error("无法获取 WebSocket URL")
        return {}

    # WebSocket 连接获取 Cookie
    cookies = {}
    try:
        ws = _ws.create_connection(ws_url, timeout=15)
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        time.sleep(0.5)

        urls = [f"https://{d}" for d in PLATFORM_DOMAINS[platform]]
        ws.send(json.dumps({
            "id": 2,
            "method": "Network.getCookies",
            "params": {"urls": urls},
        }))

        time.sleep(2)
        responses = []
        ws.settimeout(0.8)
        while True:
            try:
                responses.append(ws.recv())
            except Exception:
                break
        ws.close()

        for raw in responses:
            msg = json.loads(raw)
            for c in msg.get("result", {}).get("cookies", []):
                name = c.get("name", "")
                value = c.get("value", "")
                domain = c.get("domain", "")
                if any(d in domain for d in PLATFORM_DOMAINS[platform]):
                    cookies[name] = value
    except Exception as e:
        _log.error("WebSocket 提取 Cookie 失败: %s", e)

    return cookies


# ═══════════════════════════════════════════════════════════════════
#  公开 API
# ═══════════════════════════════════════════════════════════════════

def load_cookies(platform: str) -> Dict[str, str]:
    """加载持久化的 Cookie。"""
    # 先检查环境变量
    env_keys = {
        "xiaohongshu": "SBA_XHS_COOKIE",
        "douyin": "SBA_DY_COOKIE",
        "bilibili": "SBA_BILI_COOKIE",
    }
    env_key = env_keys.get(platform, "")
    if env_key:
        raw = (_os.environ.get(env_key) or "").strip()
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                cookies = {}
                for part in raw.split(";"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        cookies[k.strip()] = v.strip()
                if cookies:
                    return cookies

    # 从文件加载
    cookie_file = PLATFORM_COOKIE_FILES.get(platform)
    if cookie_file and cookie_file.exists():
        try:
            return json.loads(cookie_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cookies(platform: str, cookies: Dict[str, str]):
    """持久化 Cookie。"""
    cookie_file = PLATFORM_COOKIE_FILES.get(platform)
    if cookie_file:
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _log.info("保存 %s Cookie: %s 个, -> %s", platform, len(cookies), cookie_file)


def extract_and_save_all_cookies() -> Dict[str, Dict[str, str]]:
    """从 Chrome 提取所有平台的 Cookie 并持久化。

    步骤：
    1. 关闭 Chrome
    2. 清理锁文件
    3. 以 CDP 模式重启
    4. 依次提取各平台 Cookie
    5. 恢复正常 Chrome

    Returns:
        {"xiaohongshu": {...}, "douyin": {...}}
    """
    if not _start_chrome_cdp():
        _log.error("无法启动 Chrome CDP")
        return {}

    all_cookies: Dict[str, Dict[str, str]] = {}
    try:
        for platform in ["xiaohongshu", "douyin"]:
            _log.info("提取 %s Cookie...", platform)
            cookies = _extract_cookies_for_platform(platform)
            if cookies:
                save_cookies(platform, cookies)
                all_cookies[platform] = cookies
                _log.info("  -> 提取到 %s 个", len(cookies))
            else:
                _log.warning("  -> 未提取到 %s Cookie（可能未登录）", platform)
    finally:
        _restart_chrome_normal()

    return all_cookies


def ensure_cookies(platform: str, open_login_if_missing: bool = False) -> Dict[str, str]:
    """确保某平台的 Cookie 可用。

    1. 从文件/环境变量加载
    2. 如果为空，从 Chrome 自动提取
    3. 如果仍为空且 open_login_if_missing=True，弹出登录页面
    """
    cookies = load_cookies(platform)
    if cookies:
        return cookies

    _log.info("%s Cookie 缺失，尝试从 Chrome 提取...", platform)

    # 自动提取
    if not _start_chrome_cdp():
        _log.error("无法启动 Chrome CDP 自动提取")
        if open_login_if_missing:
            _open_login_popup(platform)
        return {}

    try:
        cookies = _extract_cookies_for_platform(platform)

        if cookies:
            save_cookies(platform, cookies)
            _restart_chrome_normal()
            return cookies
    finally:
        _restart_chrome_normal()

    # 仍然为空 → 弹登录窗
    if open_login_if_missing:
        _open_login_popup(platform)
        # 再次尝试提取
        cookies = load_cookies(platform)
        if not cookies:
            _log.warning("登录弹窗已打开，请登录后重新运行")

    return cookies


def _open_login_popup(platform: str):
    """打开目标平台的登录页面。"""
    login_url = PLATFORM_LOGIN_URLS.get(platform, "")
    if not login_url:
        _log.warning("未知平台: %s", platform)
        return

    _log.info("打开 %s 登录页面: %s", platform, login_url)
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", "chrome.exe", login_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        try:
            subprocess.Popen(["chrome.exe", login_url])
        except Exception as e:
            _log.error("无法打开浏览器: %s", e)
