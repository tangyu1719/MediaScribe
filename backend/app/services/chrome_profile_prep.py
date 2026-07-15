"""Chrome Profile 启动前/后处理 — 避免「要恢复页面吗」弹窗卡住自动化。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("sba.chrome_profile_prep")
_CHAIN = "小红书收藏夹-Chrome启动预处理"

# 与桌面快捷方式一致（plan_a_open_my_chrome.ps1）
DEFAULT_CDP_PORT = int(os.environ.get("SBA_CHROME_CDP_PORT", "9223") or "9223")
EXTRA_LAUNCH_FLAGS = (
    "--disable-session-crashed-bubble",
    "--disable-infobars",
)


def default_chrome_user_data_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"


def cdp_chrome_user_data_dir() -> Path:
    """
    Chrome 136+ 仅在「非默认」User Data 目录下才开启 CDP。
    默认使用 %LOCALAPPDATA%\\SBA-Chrome-CDP（与日常 Chrome User Data 分离）。
    """
    override = (os.environ.get("SBA_CHROME_CDP_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override)
    return Path(os.environ["LOCALAPPDATA"]) / "SBA-Chrome-CDP"


def is_default_chrome_user_data_path(path: str | Path) -> bool:
    try:
        a = Path(path).resolve()
        b = default_chrome_user_data_dir().resolve()
        return a == b
    except Exception:
        return False


def bootstrap_cdp_profile_from_owner(*, force: bool = False) -> Dict[str, Any]:
    """
    首次将日常 Chrome Default Profile 复制到 CDP 专用目录（Chrome 须已完全退出）。
    复制后用户可在 CDP Chrome 中沿用已配置用户/小红书登录态（视 Chrome 加密策略而定）。
    """
    dest_ud = cdp_chrome_user_data_dir()
    dest_ud.mkdir(parents=True, exist_ok=True)
    dest_prof = dest_ud / "Default"
    src_prof = default_chrome_profile_dir()
    src_ud = default_chrome_user_data_dir()

    if dest_prof.is_dir() and any(dest_prof.iterdir()) and not force:
        return {
            "ok": True,
            "copied": False,
            "reason": "already_exists",
            "dest": str(dest_ud),
        }

    if is_chrome_process_running():
        return {
            "ok": False,
            "error_code": "CHROME_STILL_RUNNING",
            "error": "复制 Profile 前须完全退出 Chrome",
        }

    if not src_prof.is_dir():
        return {
            "ok": False,
            "error_code": "SRC_PROFILE_MISSING",
            "error": f"未找到日常 Chrome Profile: {src_prof}",
        }

    try:
        import shutil

        if dest_prof.exists():
            shutil.rmtree(dest_prof, ignore_errors=True)
        shutil.copytree(
            src_prof,
            dest_prof,
            ignore=shutil.ignore_patterns("Cache", "Code Cache", "GPUCache", "Service Worker"),
            dirs_exist_ok=False,
        )
        src_local = src_ud / "Local State"
        if src_local.is_file():
            shutil.copy2(src_local, dest_ud / "Local State")
        _log.info(
            "[%s|chrome_profile_prep.bootstrap_cdp_profile|Default|硬编执行|完成] dest=%s",
            _CHAIN,
            dest_ud,
        )
        return {"ok": True, "copied": True, "dest": str(dest_ud)}
    except Exception as ex:
        _log.warning(
            "[%s|chrome_profile_prep.bootstrap_cdp_profile|Default|硬编执行|失败] error=%s",
            _CHAIN,
            ex,
        )
        return {"ok": False, "error_code": "COPY_FAILED", "error": str(ex)}


def chrome_cdp_launch_tokens(*, port: Optional[int] = None) -> List[str]:
    port = int(port or DEFAULT_CDP_PORT)
    prof = (os.environ.get("SBA_CHROME_PROFILE") or "Default").strip() or "Default"
    return [
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={cdp_chrome_user_data_dir()}",
        f"--profile-directory={prof}",
        *EXTRA_LAUNCH_FLAGS,
    ]


def chrome_cdp_blocked_by_default_user_data() -> bool:
    """Chrome 进程带 9223 参数但端口未监听 → 典型为 Chrome 136+ 默认目录静默禁用 CDP。"""
    port = DEFAULT_CDP_PORT
    if is_cdp_ready(port):
        return False
    if not is_chrome_process_running():
        return False
    if not _chrome_main_process_has_cdp_flag(port):
        return False
    try:
        import subprocess

        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                "Where-Object { $_.CommandLine -notmatch '--type=' } | "
                "Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
        cmd = (r.stdout or "").strip()
        if not cmd:
            return False
        m = None
        import re

        m = re.search(r'--user-data-dir=(?:"([^"]+)"|([^\s]+))', cmd, re.I)
        ud = (m.group(1) or m.group(2) or "").strip() if m else ""
        return bool(ud) and is_default_chrome_user_data_path(ud)
    except Exception:
        return False


def default_chrome_profile_dir() -> Path:
    prof = (os.environ.get("SBA_CHROME_PROFILE") or "Default").strip() or "Default"
    return default_chrome_user_data_dir() / prof


def mark_chrome_profile_clean_exit(profile_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    写入 Preferences：exit_type=Normal，降低「Chrome 未正确关闭」恢复条出现概率。
    须在 Chrome 完全退出后、启动前调用。
    """
    profile_dir = profile_dir or default_chrome_profile_dir()
    prefs_path = profile_dir / "Preferences"
    if not prefs_path.is_file():
        return {"ok": False, "reason": "preferences_missing", "path": str(prefs_path)}

    try:
        raw = prefs_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as ex:
        return {"ok": False, "reason": f"read_failed:{ex}"}

    profile = data.setdefault("profile", {})
    if not isinstance(profile, dict):
        profile = {}
        data["profile"] = profile
    profile["exit_type"] = "Normal"

    # 部分版本用 session 字段记录异常退出
    session = data.get("session")
    if isinstance(session, dict):
        session["restore_on_startup"] = session.get("restore_on_startup", 1)

    try:
        prefs_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        _log.info(
            "[%s|chrome_profile_prep.mark_clean_exit|Preferences|硬编执行|完成] path=%s",
            _CHAIN,
            prefs_path,
        )
        return {"ok": True, "path": str(prefs_path)}
    except Exception as ex:
        return {"ok": False, "reason": f"write_failed:{ex}"}


def cdp_auto_restart_enabled() -> bool:
    """仅非 attach-only 且显式 SBA_XHS_CDP_AUTO_RESTART=1 时允许冷启动；主路径禁止。"""
    from .xhs_local_browser import xhs_cdp_attach_only

    if xhs_cdp_attach_only():
        return False
    v = (os.environ.get("SBA_XHS_CDP_AUTO_RESTART") or "").strip().lower()
    return v in ("1", "true", "yes")


def _clear_chrome_singleton_locks() -> None:
    ud = default_chrome_user_data_dir()
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (ud / name).unlink(missing_ok=True)
        except Exception:
            pass


def kill_all_chrome_processes(*, wait_sec: float = 30.0) -> bool:
    """仅结束 CDP 模式 Chrome 进程（含 --remote-debugging-port 参数），保护日常 Chrome。"""
    if not is_chrome_process_running():
        return True
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -match 'remote-debugging-port' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True,
            timeout=45,
        )
    except Exception as ex:
        _log.warning(
            "[%s|chrome_profile_prep.kill_all_chrome|chrome.exe|硬编执行|taskkill] error=%s",
            _CHAIN,
            ex,
        )
    deadline = time.time() + wait_sec
    while is_chrome_process_running() and time.time() < deadline:
        time.sleep(0.5)
    gone = not is_chrome_process_running()
    _log.info(
        "[%s|chrome_profile_prep.kill_all_chrome|chrome.exe|硬编执行|完成] ok=%s",
        _CHAIN,
        gone,
    )
    return gone


def _split_equals_flag(token: str) -> List[str]:
    """把 --key=value（value 可含空格）拆成 Chrome 需要的 [key, value]。"""
    raw = (token or "").strip()
    if not raw.startswith("--") or "=" not in raw:
        return [raw] if raw else []
    key, val = raw.split("=", 1)
    if key in ("--user-data-dir", "--profile-directory") and val:
        return [key, val]
    return [raw]


def _normalize_chrome_launch_argv(tokens: List[str]) -> List[str]:
    """确保 user-data-dir / profile-directory 以独立 argv 传递（Windows 路径含空格）。"""
    out: List[str] = []
    for t in tokens:
        if t.startswith("--user-data-dir=") or t.startswith("--profile-directory="):
            out.extend(_split_equals_flag(t))
        else:
            out.append(t)
    return out


def _verify_chrome_owner_profile_attached() -> None:
    """冷启动后校验：必须是已配置的 Default Profile，禁止空白/访客态冒充用户态。"""
    from .xhs_local_browser import _browser_config_chrome, is_browser_google_signed_in

    cfg = _browser_config_chrome()
    if not is_browser_google_signed_in(cfg):
        raise RuntimeError(
            "SUB_OWNER_CHROME_PROFILE_MISMATCH: Chrome 未附着到已配置的 Default Profile。"
            "常见原因：--user-data-dir 路径含空格被错误拆分。"
            f"期望 user_data_dir={cfg.user_data_dir}; profile={cfg.profile}"
        )
    expected_gaia = (os.environ.get("SBA_CHROME_EXPECTED_GAIA") or "").strip()
    local_state = cfg.user_data_dir / "Local State"
    gaia = ""
    if local_state.is_file():
        try:
            data = json.loads(local_state.read_text(encoding="utf-8"))
            prof = ((data.get("profile") or {}).get("info_cache") or {}).get(cfg.profile) or {}
            gaia = str(prof.get("gaia_name") or prof.get("user_name") or "")
        except Exception:
            pass
    if expected_gaia and gaia and expected_gaia not in gaia:
        raise RuntimeError(
            f"SUB_OWNER_CHROME_PROFILE_MISMATCH: 当前 Profile gaia={gaia}，期望含「{expected_gaia}」"
        )
    _log.info(
        "[%s|chrome_profile_prep._verify_chrome_owner_profile_attached|Profile|硬编执行|通过] gaia=%s; profile=%s",
        _CHAIN,
        gaia or "ok",
        cfg.profile,
    )


def _launch_chrome_exe(exe: str, tokens: List[str], *, initial_url: str = "") -> Dict[str, Any]:
    """
    冷启动 Chrome（Windows Start-Process + 单参数 user-data-dir，与实测可用命令一致）。
    禁止 remote-debugging-address；禁止 about:blank 冒充用户态。
    """
    launch_tokens = [t for t in tokens if not str(t).lower().startswith("--remote-debugging-address=")]
    has_cdp = any("remote-debugging-port" in str(t) for t in launch_tokens)
    ud = cdp_chrome_user_data_dir() if has_cdp else default_chrome_user_data_dir()
    prof = (os.environ.get("SBA_CHROME_PROFILE") or "Default").strip() or "Default"
    if has_cdp:
        bootstrap_cdp_profile_from_owner()
    if not any(str(t).startswith("--user-data-dir") for t in launch_tokens):
        launch_tokens.append(f"--user-data-dir={ud}")
    if not any(str(t).startswith("--profile-directory") for t in launch_tokens):
        launch_tokens.append(f"--profile-directory={prof}")

    start_url = (initial_url or "").strip()
    if not start_url:
        cid = (os.environ.get("XHS_FAVORITES_CREATOR_ID") or "").strip()
        if cid:
            start_url = f"https://www.xiaohongshu.com/user/profile/{cid}?tab=fav&subTab=note"
    if start_url and not any(str(t).startswith("http") for t in launch_tokens):
        launch_tokens.append(start_url)

    if sys.platform == "win32":
        ps_tokens = ",".join(json.dumps(t) for t in launch_tokens)
        ps = f"Start-Process -FilePath {json.dumps(exe)} -ArgumentList @({ps_tokens})"
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except subprocess.CalledProcessError as ex:
            return {
                "ok": False,
                "error_code": "CHROME_START_PROCESS_FAILED",
                "error": (ex.stderr or ex.stdout or str(ex)).strip(),
                "exe": exe,
            }
        method = "powershell_start_process"
    else:
        argv = [exe, *launch_tokens]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as ex:
            return {"ok": False, "error_code": "CHROME_EXE_LAUNCH_FAILED", "error": str(ex), "exe": exe}
        method = "popen"

    _log.info(
        "[%s|chrome_profile_prep._launch_chrome_exe|chrome.exe|硬编执行|启动] method=%s; args=%s",
        _CHAIN,
        method,
        launch_tokens,
    )
    return {"ok": True, "method": method, "exe": exe, "args": launch_tokens}


def ensure_chrome_cdp_ready(
    *,
    force_restart: bool = False,
    wait_cdp_sec: float = 90.0,
) -> int:
    """
    稳定 CDP 就绪：必要时杀旧 Chrome → 清锁 → 按桌面快捷方式参数冷启动 → 等端口。
    方案 A（SBA_XHS_CDP_ATTACH_ONLY=1）下禁止调用；仅供显式离线脚本。
    """
    from .xhs_local_browser import xhs_cdp_attach_only

    if xhs_cdp_attach_only():
        raise RuntimeError(
            "SUB_PLAN_A_NO_AUTO_LAUNCH: 方案 A 禁止 Agent 杀进程/冷启动 Chrome。"
            "请手动双击桌面 Google Chrome.lnk 启动。"
        )
    info = build_launch_args_from_desktop_shortcut()
    if not info.get("ok"):
        raise RuntimeError(info.get("error") or "无法读取桌面 Chrome 快捷方式")
    port = int(info.get("cdp_port") or DEFAULT_CDP_PORT)

    if is_cdp_ready(port):
        _log.info(
            "[%s|chrome_profile_prep.ensure_chrome_cdp_ready|CDP|硬编执行|已就绪] port=%s; skip_restart=%s",
            _CHAIN,
            port,
            force_restart,
        )
        dismiss_chrome_restore_prompt(port)
        if force_restart:
            _verify_chrome_owner_profile_attached()
        return port

    broken = is_chrome_process_running() and _chrome_main_process_has_cdp_flag(port) and not is_cdp_ready(port)
    need_kill = force_restart or is_chrome_process_running()
    if need_kill:
        _log.warning(
            "[%s|chrome_profile_prep.ensure_chrome_cdp_ready|CDP|硬编执行|冷启动] "
            "force=%s; broken=%s; port=%s",
            _CHAIN,
            force_restart,
            broken,
            port,
        )
        if not kill_all_chrome_processes():
            raise RuntimeError("无法结束旧 Chrome 进程，CDP 冷启动失败")
        _clear_chrome_singleton_locks()
        time.sleep(1.5)

    mark_chrome_profile_clean_exit()
    exe = str(info["exe"])
    tokens: List[str] = list(info.get("args") or [])

    run = _launch_chrome_exe(exe, tokens)
    if not run.get("ok"):
        raise RuntimeError(run.get("error") or "Chrome 冷启动失败")

    if not _wait_cdp_ready(port, wait_cdp_sec):
        raise RuntimeError(
            f"Chrome 已启动但 {wait_cdp_sec}s 内 CDP {port} 未就绪；"
            "请确认桌面快捷方式含 --remote-debugging-port 且未使用 --remote-debugging-address"
        )
    _verify_chrome_owner_profile_attached()
    dismiss_chrome_restore_prompt(port)
    if need_kill:
        from .chrome_cdp_open import warm_xhs_owner_session_via_cdp

        warm_xhs_owner_session_via_cdp(
            port=port,
            settle_sec=float(os.environ.get("SBA_XHS_CDP_WARM_SEC", "14")),
        )
    _log.info(
        "[%s|chrome_profile_prep.ensure_chrome_cdp_ready|CDP|硬编执行|完成] port=%s; method=%s",
        _CHAIN,
        port,
        run.get("method"),
    )
    return port


def build_launch_args_from_desktop_shortcut() -> Dict[str, Any]:
    """读取桌面 Google Chrome.lnk，返回 exe + 参数（与 plan_a_open_my_chrome.ps1 一致）。"""
    lnk = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Google Chrome.lnk"
    if not lnk.is_file():
        return {"ok": False, "error": "shortcut_missing", "path": str(lnk)}

    try:
        import subprocess

        args_line = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{str(lnk).replace(chr(39), chr(39)+chr(39))}'); "
                "$s.TargetPath; '---'; $s.Arguments",
            ],
            text=True,
            timeout=10,
        )
        parts = args_line.strip().split("\n---\n", 1)
        exe = parts[0].strip()
        shortcut_args = parts[1].strip() if len(parts) > 1 else ""
    except Exception as ex:
        return {"ok": False, "error": str(ex)}

    port = DEFAULT_CDP_PORT
    import re

    m = re.search(r"remote-debugging-port=(\d+)", shortcut_args, re.I)
    if m:
        port = int(m.group(1))

    tokens: List[str] = []
    if shortcut_args:
        tokens.extend(shortcut_args.split())
    # Chrome 149+ Windows：remote-debugging-address=127.0.0.1 可能导致端口不 bind（DevToolsActivePort 缺失）
    tokens = [t for t in tokens if not t.lower().startswith("--remote-debugging-address=")]
    for flag in EXTRA_LAUNCH_FLAGS:
        key = flag.split("=")[0]
        if not any(t.startswith(key) for t in tokens):
            tokens.append(flag)

    return {
        "ok": True,
        "exe": exe,
        "args": tokens,
        "arg_line": " ".join(tokens),
        "cdp_port": port,
        "shortcut": str(lnk),
    }


def _cdp_send_escape(ws_url: str) -> None:
    import websocket as _ws

    ws = _ws.create_connection(ws_url, timeout=8)
    try:
        mid = 1
        for etype in ("keyDown", "keyUp"):
            ws.send(
                json.dumps(
                    {
                        "id": mid,
                        "method": "Input.dispatchKeyEvent",
                        "params": {
                            "type": etype,
                            "key": "Escape",
                            "code": "Escape",
                            "windowsVirtualKeyCode": 27,
                            "nativeVirtualKeyCode": 27,
                        },
                    }
                )
            )
            mid += 1
        time.sleep(0.2)
    finally:
        try:
            ws.close()
        except Exception:
            pass


def dismiss_chrome_restore_prompt(port: Optional[int] = None) -> Dict[str, Any]:
    """
    CDP 附着后尝试关闭「要恢复页面吗 / Chrome 未正确关闭」提示。
    优先 Escape；启动时应配合 --disable-session-crashed-bubble。
    """
    from .cookie_manager import find_cdp_port
    from .xhs_local_browser import cdp_list_tabs, cdp_tab_eval

    port = port or find_cdp_port() or DEFAULT_CDP_PORT
    tabs = cdp_list_tabs(port)
    if not tabs:
        return {"ok": False, "reason": "no_cdp_tabs", "port": port}

    dismissed = 0
    for tab in tabs:
        url = str(tab.get("url") or "")
        if url.startswith("chrome://") or url.startswith("devtools://") or url.startswith("data:"):
            continue
        ws = tab.get("webSocketDebuggerUrl")
        if not ws:
            continue
        try:
            _cdp_send_escape(ws)
            # 页面内 infobar 少见；部分扩展/内嵌条可点关闭
            hit = cdp_tab_eval(
                ws,
                """(() => {
                  const kws = ['要恢复页面', '未正确关闭', 'Restore pages', 'restore'];
                  for (const b of document.querySelectorAll('button,[role=button]')) {
                    const t = (b.textContent || '').trim();
                    const label = (b.getAttribute('aria-label') || '').trim();
                    if (label === 'Close' || t === '×' || t === 'X') {
                      const box = b.closest('div');
                      if (box && kws.some(k => (box.textContent||'').includes(k))) { b.click(); return 'close'; }
                    }
                  }
                  return '';
                })()""",
            )
            if hit:
                dismissed += 1
        except Exception as ex:
            _log.debug("dismiss tab skip: %s", ex)

    _log.info(
        "[%s|chrome_profile_prep.dismiss_restore_prompt|CDP|硬编执行|完成] port=%s; dismissed=%s",
        _CHAIN,
        port,
        dismissed,
    )
    return {"ok": True, "port": port, "dismissed": dismissed, "tab_count": len(tabs)}


def _desktop_chrome_shortcut_path() -> Path:
    return Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Google Chrome.lnk"


def is_cdp_ready(port: Optional[int] = None) -> bool:
    port = port or DEFAULT_CDP_PORT
    try:
        import requests

        return requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).status_code == 200
    except Exception:
        return False


def is_chrome_process_running() -> bool:
    import subprocess

    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return "chrome.exe" in (r.stdout or "").lower()
    except Exception:
        return False


def _chrome_main_process_has_cdp_flag(port: Optional[int] = None) -> bool:
    """主进程命令行是否含 remote-debugging-port（含指定端口或任意端口）。"""
    port = port or DEFAULT_CDP_PORT
    try:
        import subprocess

        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                "Where-Object { $_.CommandLine -notmatch '--type=' } | "
                "ForEach-Object { $_.CommandLine }",
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
        for line in (r.stdout or "").splitlines():
            cl = line.strip()
            if not cl:
                continue
            if "remote-debugging-port" in cl:
                if f"remote-debugging-port={port}" in cl or f"remote-debugging-port={port} " in cl:
                    return True
                if f"remote-debugging-port={port}\"" in cl:
                    return True
        return False
    except Exception:
        return False


def run_desktop_chrome_shortcut() -> Dict[str, Any]:
    """
    读取桌面 lnk 参数冷启动 Chrome（与快捷方式一致，不显式传默认 user-data-dir）。
    """
    info = build_launch_args_from_desktop_shortcut()
    lnk = _desktop_chrome_shortcut_path()
    if not info.get("ok"):
        return info

    exe = str(info["exe"])
    tokens: List[str] = list(info.get("args") or [])

    run = _launch_chrome_exe(exe, tokens)
    if not run.get("ok"):
        return run

    return {
        "ok": True,
        "method": run.get("method"),
        "exe": exe,
        "args": run.get("args") or tokens,
        "shortcut": str(lnk) if lnk.is_file() else None,
    }


def _wait_cdp_ready(port: int, wait_sec: float) -> bool:
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        time.sleep(1.0)
        if is_cdp_ready(port):
            return True
    return False


def launch_chrome_from_desktop_shortcut(*, wait_cdp_sec: float = 45.0) -> Dict[str, Any]:
    """
    用桌面 Google Chrome.lnk 启动 Chrome（Agent 代执行，等价双击；不 taskkill）。
    Chrome 已在跑且 CDP 未就绪时返回 CHROME_RUNNING_NO_CDP / CHROME_CDP_BROKEN。
    """
    info = build_launch_args_from_desktop_shortcut()
    if not info.get("ok"):
        return info

    port = int(info.get("cdp_port") or DEFAULT_CDP_PORT)
    if is_cdp_ready(port):
        return {"ok": True, "action": "cdp_already_ready", "cdp_port": port, "shortcut": info.get("shortcut")}

    if is_chrome_process_running():
        has_cdp_flag = _chrome_main_process_has_cdp_flag(port)
        code = "CHROME_CDP_BROKEN" if has_cdp_flag else "CHROME_RUNNING_NO_CDP"
        msg = (
            f"Chrome 已在运行但 CDP {port} 未就绪（主进程含调试参数={has_cdp_flag}）。"
            + (
                " Chrome 149 禁止在默认 User Data 开 CDP；请完全退出后用「Google Chrome CDP 9223」快捷方式"
                f"（目录 {cdp_chrome_user_data_dir()}）重启。"
                if chrome_cdp_blocked_by_default_user_data()
                else " 请完全退出 Chrome 后双击 CDP 快捷方式重启。"
            )
        )
        return {
            "ok": False,
            "error_code": code,
            "error": msg,
            "cdp_port": port,
            "shortcut": info.get("shortcut"),
        }

    mark_chrome_profile_clean_exit()
    run = run_desktop_chrome_shortcut()
    if not run.get("ok"):
        return run

    if _wait_cdp_ready(port, wait_cdp_sec):
        dismiss_chrome_restore_prompt(port)
        return {
            "ok": True,
            "action": "launched_via_shortcut",
            "cdp_port": port,
            "shortcut": run.get("shortcut"),
            "exe": info.get("exe"),
            "args": info.get("args"),
        }

    return {
        "ok": False,
        "error_code": "CDP_BIND_TIMEOUT",
        "error": f"已执行桌面快捷方式但 {wait_cdp_sec}s 内 CDP {port} 未就绪",
        "shortcut": run.get("shortcut"),
        "exe": info.get("exe"),
        "args": info.get("args"),
    }


def wait_and_launch_chrome_from_desktop_shortcut(
    *,
    wait_exit_sec: float = 600.0,
    wait_cdp_sec: float = 45.0,
    poll_sec: float = 2.0,
) -> Dict[str, Any]:
    """
    等待用户完全退出 Chrome（不 taskkill），随后执行桌面快捷方式并等 CDP。
    供 Agent 无人值守：用户关浏览器后自动拉起带 9223 的已配置 Profile。
    """
    info = build_launch_args_from_desktop_shortcut()
    if not info.get("ok"):
        return info

    port = int(info.get("cdp_port") or DEFAULT_CDP_PORT)
    if is_cdp_ready(port):
        return {"ok": True, "action": "cdp_already_ready", "cdp_port": port}

    deadline = time.time() + wait_exit_sec
    while is_chrome_process_running() and time.time() < deadline:
        time.sleep(poll_sec)

    if is_chrome_process_running():
        return {
            "ok": False,
            "error_code": "CHROME_EXIT_TIMEOUT",
            "error": f"等待 {wait_exit_sec}s 后 Chrome 仍在运行，无法以 CDP 参数冷启动",
            "cdp_port": port,
        }

    return launch_chrome_from_desktop_shortcut(wait_cdp_sec=wait_cdp_sec)


def prepare_and_note_launch() -> Dict[str, Any]:
    """启动 Chrome 前：标记 clean exit + 返回与桌面快捷方式一致的启动参数。"""
    clean = mark_chrome_profile_clean_exit()
    launch = build_launch_args_from_desktop_shortcut()
    return {"clean_exit": clean, "launch": launch}
