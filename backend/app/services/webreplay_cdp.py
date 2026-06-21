"""WebReplay CDP 路径：附着用户 Chrome，DOM 事件录制 + 步进/帧截图 + 脚本重放。"""
from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cookie_manager import find_cdp_port
from .webreplay_inject_bundle import (
    RECORDER_INIT_JS,
    RECORDER_POLL_JS,
    RECORDER_STOP_JS,
    REPLAY_ONE_STEP_JS,
)
from .xhs_local_browser import cdp_list_tabs, maybe_bring_page_to_front

_log = logging.getLogger("sba.webreplay_cdp")
_CHAIN = "浏览器自动化-CDP录制重放"
_MEDIA_ROOT = Path(__file__).resolve().parent / "data" / "webreplay" / "media"
_SESSION_LOCK = threading.Lock()
_SESSIONS: Dict[str, "CdpRecordSession"] = {}

# 录制期间帧截图间隔（轻量「录屏」时间轴，非 CV 重放）
FRAME_CAPTURE_INTERVAL_SEC = 2.0
STEP_DELAY_MIN_MS = 300
STEP_DELAY_MAX_MS = 1500


@dataclass
class CdpRecordSession:
    session_id: str
    user_id: str
    name: str
    tab_url: str
    cdp_port: int
    started_at: float
    steps: List[dict[str, Any]] = field(default_factory=list)
    frames: List[dict[str, Any]] = field(default_factory=list)
    last_step_count: int = 0
    stop_event: threading.Event = field(default_factory=threading.Event)
    frame_thread: Optional[threading.Thread] = None
    error: Optional[str] = None

    def media_dir(self) -> Path:
        d = _MEDIA_ROOT / self.user_id / self.session_id
        d.mkdir(parents=True, exist_ok=True)
        return d


def _is_usable_tab_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    if u.startswith("chrome://") or u.startswith("devtools://") or u.startswith("edge://"):
        return False
    if u.startswith("data:") or u in ("about:blank", "chrome://newtab/"):
        return False
    return True


def cdp_status(*, port: Optional[int] = None) -> dict[str, Any]:
    port = port or find_cdp_port()
    if not port:
        return {"ok": False, "connected": False, "error": "未检测到 CDP 端口，请用 --remote-debugging-port 启动 Chrome"}
    tabs_raw = cdp_list_tabs(port)
    tabs = []
    for t in tabs_raw:
        url = str(t.get("url") or "")
        if not _is_usable_tab_url(url):
            continue
        tabs.append(
            {
                "url": url,
                "title": str(t.get("title") or ""),
                "webSocketDebuggerUrl": t.get("webSocketDebuggerUrl"),
            }
        )
    return {
        "ok": True,
        "connected": True,
        "port": port,
        "tabCount": len(tabs),
        "tabs": tabs[:30],
    }


def _connect_playwright(port: int):
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    return p, browser


def _pick_page(browser, tab_url_hint: str = ""):
    hint = (tab_url_hint or "").strip()
    candidates = []
    for ctx in browser.contexts:
        for page in ctx.pages:
            url = page.url or ""
            if not _is_usable_tab_url(url):
                continue
            candidates.append(page)
            if hint and hint in url:
                return page
    if not candidates:
        return None
    return candidates[0]


def _inject_recorder_all_frames(page, session_id: str) -> None:
    for frame in page.frames:
        try:
            frame.evaluate(RECORDER_INIT_JS, session_id)
        except Exception as ex:
            _log.debug("[%s|webreplay_cdp._inject_recorder|frame|硬编执行|注入] skip; error_message=%s", _CHAIN, ex)


def _poll_recorder_steps(page) -> dict[str, Any]:
    merged: list[dict[str, Any]] = []
    frame_url = page.url or ""
    for frame in page.frames:
        try:
            row = frame.evaluate(RECORDER_POLL_JS)
            if not isinstance(row, dict):
                continue
            steps = row.get("steps") or []
            if isinstance(steps, list):
                for s in steps:
                    if isinstance(s, dict):
                        merged.append(s)
            if row.get("frameUrl"):
                frame_url = str(row["frameUrl"])
        except Exception:
            continue
    merged.sort(key=lambda x: float(x.get("recordedAt") or 0))
    return {"steps": merged, "count": len(merged), "frameUrl": frame_url, "active": True}


def _save_step_screenshot(page, media_dir: Path, index: int) -> Optional[str]:
    try:
        path = media_dir / f"step_{index:04d}.png"
        page.screenshot(path=str(path), full_page=False)
        return path.name
    except Exception as ex:
        _log.warning(
            "[%s|webreplay_cdp._save_step_screenshot|step_%s|硬编执行|截图] failed; error_message=%s",
            _CHAIN,
            index,
            ex,
        )
        return None


def _frame_capture_loop(session: CdpRecordSession) -> None:
    port = session.cdp_port
    seq = 0
    while not session.stop_event.wait(FRAME_CAPTURE_INTERVAL_SEC):
        try:
            p, browser = _connect_playwright(port)
            try:
                page = _pick_page(browser, session.tab_url)
                if not page:
                    continue
                media_dir = session.media_dir()
                fname = f"frame_{seq:05d}.png"
                page.screenshot(path=str(media_dir / fname), full_page=False)
                session.frames.append({"index": seq, "file": fname, "at": time.time()})
                seq += 1
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
                p.stop()
        except Exception as ex:
            _log.debug("[%s|webreplay_cdp._frame_capture_loop|session|硬编执行|帧截图] skip; error_message=%s", _CHAIN, ex)


def start_cdp_recording(
    user_id: str,
    *,
    name: str,
    tab_url_hint: str = "",
    port: Optional[int] = None,
) -> dict[str, Any]:
    name = (name or "").strip() or f"cdp-{int(time.time())}"
    port = port or find_cdp_port()
    if not port:
        return {"ok": False, "error": "未检测到 CDP 端口"}

    p, browser = _connect_playwright(port)
    try:
        page = _pick_page(browser, tab_url_hint)
        if not page:
            return {"ok": False, "error": "未找到可录制的普通网页标签，请先在 Chrome 打开目标后台页"}
        maybe_bring_page_to_front(page)
        session_id = str(uuid.uuid4())
        _inject_recorder_all_frames(page, session_id)
        tab_url = page.url or ""
        sess = CdpRecordSession(
            session_id=session_id,
            user_id=user_id,
            name=name,
            tab_url=tab_url,
            cdp_port=port,
            started_at=time.time(),
        )
        sess.media_dir()
        th = threading.Thread(target=_frame_capture_loop, args=(sess,), daemon=True, name=f"wr-frame-{session_id[:8]}")
        sess.frame_thread = th
        with _SESSION_LOCK:
            for k, v in list(_SESSIONS.items()):
                if v.user_id == user_id and not v.stop_event.is_set():
                    v.stop_event.set()
                    _SESSIONS.pop(k, None)
            _SESSIONS[session_id] = sess
        th.start()
        _log.info(
            "[%s|webreplay_cdp.start_cdp_recording|session:%s|Agent执行|开始] 已注入录制器; user_id=%s; tab_url=%s",
            _CHAIN,
            session_id,
            user_id,
            tab_url[:120],
        )
        return {
            "ok": True,
            "sessionId": session_id,
            "name": name,
            "tabUrl": tab_url,
            "port": port,
            "message": "请在 Chrome 中操作目标页面，完成后在本站点击「完成录制」",
        }
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


def get_cdp_recording_status(user_id: str, session_id: str) -> dict[str, Any]:
    with _SESSION_LOCK:
        sess = _SESSIONS.get(session_id)
    if not sess or sess.user_id != user_id:
        return {"ok": False, "error": "录制会话不存在或已结束"}
    if sess.error:
        return {"ok": False, "error": sess.error, "sessionId": session_id}

    try:
        p, browser = _connect_playwright(sess.cdp_port)
        try:
            page = _pick_page(browser, sess.tab_url)
            if not page:
                return {"ok": False, "error": "目标标签页已关闭", "sessionId": session_id}
            polled = _poll_recorder_steps(page)
            steps = polled.get("steps") or []
            new_count = len(steps)
            if new_count > sess.last_step_count:
                media_dir = sess.media_dir()
                for i in range(sess.last_step_count, new_count):
                    shot = _save_step_screenshot(page, media_dir, i)
                    if shot and i < len(steps):
                        steps[i]["screenshot"] = shot
                sess.steps = steps
                sess.last_step_count = new_count
                if polled.get("frameUrl"):
                    sess.tab_url = str(polled["frameUrl"])
            return {
                "ok": True,
                "sessionId": session_id,
                "running": not sess.stop_event.is_set(),
                "stepCount": sess.last_step_count,
                "frameCount": len(sess.frames),
                "tabUrl": sess.tab_url,
                "name": sess.name,
            }
        finally:
            try:
                browser.close()
            except Exception:
                pass
            p.stop()
    except Exception as ex:
        _log.warning(
            "[%s|webreplay_cdp.get_cdp_recording_status|session:%s|Agent执行|轮询] failed; error_message=%s",
            _CHAIN,
            session_id,
            ex,
        )
        return {"ok": False, "error": str(ex), "sessionId": session_id}


def stop_cdp_recording(user_id: str, session_id: str) -> dict[str, Any]:
    with _SESSION_LOCK:
        sess = _SESSIONS.get(session_id)
    if not sess or sess.user_id != user_id:
        return {"ok": False, "error": "录制会话不存在或已结束"}

    sess.stop_event.set()
    steps: list[dict[str, Any]] = []
    target_url = sess.tab_url
    try:
        p, browser = _connect_playwright(sess.cdp_port)
        try:
            page = _pick_page(browser, sess.tab_url)
            if page:
                maybe_bring_page_to_front(page)
                for frame in page.frames:
                    try:
                        row = frame.evaluate(RECORDER_STOP_JS)
                        if isinstance(row, dict) and row.get("steps"):
                            steps.extend(row["steps"])
                            if row.get("frameUrl"):
                                target_url = str(row["frameUrl"])
                    except Exception:
                        continue
                steps.sort(key=lambda x: float(x.get("recordedAt") or 0))
                media_dir = sess.media_dir()
                for i, st in enumerate(steps):
                    if not st.get("screenshot"):
                        shot = _save_step_screenshot(page, media_dir, i)
                        if shot:
                            st["screenshot"] = shot
        finally:
            try:
                browser.close()
            except Exception:
                pass
            p.stop()
    except Exception as ex:
        _log.warning(
            "[%s|webreplay_cdp.stop_cdp_recording|session:%s|Agent执行|停止] partial; error_message=%s",
            _CHAIN,
            session_id,
            ex,
        )
        steps = sess.steps or steps

    with _SESSION_LOCK:
        _SESSIONS.pop(session_id, None)

    clean_steps = []
    for st in steps:
        if not isinstance(st, dict):
            continue
        row = {k: v for k, v in st.items() if k != "sensitiveWarning"}
        if st.get("sensitiveWarning"):
            row["meta"] = {"sensitiveWarning": st["sensitiveWarning"]}
        clean_steps.append(row)

    script = {
        "id": str(uuid.uuid4()),
        "name": sess.name,
        "targetUrl": target_url,
        "steps": clean_steps,
        "createdAt": time.time(),
        "updatedAt": time.time(),
        "source": "cdp-dom-recorder",
        "recordSessionId": session_id,
        "frameTimeline": sess.frames[-120:],
    }
    _log.info(
        "[%s|webreplay_cdp.stop_cdp_recording|session:%s|Agent执行|完成] steps=%s; frames=%s",
        _CHAIN,
        session_id,
        len(clean_steps),
        len(sess.frames),
    )
    return {
        "ok": True,
        "sessionId": session_id,
        "script": script,
        "stepCount": len(clean_steps),
        "frameCount": len(sess.frames),
    }


def run_cdp_replay(
    script: dict[str, Any],
    *,
    tab_url_hint: str = "",
    port: Optional[int] = None,
) -> dict[str, Any]:
    steps = script.get("steps") or []
    if not steps:
        return {"ok": False, "error": "脚本无步骤"}
    port = port or find_cdp_port()
    if not port:
        return {"ok": False, "error": "未检测到 CDP 端口"}

    target = str(script.get("targetUrl") or tab_url_hint or "")
    started = time.time()
    last_done = -1
    try:
        p, browser = _connect_playwright(port)
        try:
            page = _pick_page(browser, tab_url_hint or target)
            if not page:
                return {"ok": False, "error": "未找到可重放的标签页"}
            maybe_bring_page_to_front(page)
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                frame = page.main_frame
                try:
                    frame.evaluate(REPLAY_ONE_STEP_JS, step)
                    last_done = i
                except Exception as ex:
                    return {
                        "ok": False,
                        "status": "failed",
                        "failedAtStep": i,
                        "error": str(ex),
                        "doneSteps": last_done + 1,
                        "totalSteps": len(steps),
                        "elapsedMs": int((time.time() - started) * 1000),
                    }
                delay = STEP_DELAY_MIN_MS + random.random() * (STEP_DELAY_MAX_MS - STEP_DELAY_MIN_MS)
                time.sleep(delay / 1000.0)
            return {
                "ok": True,
                "status": "success",
                "doneSteps": len(steps),
                "totalSteps": len(steps),
                "elapsedMs": int((time.time() - started) * 1000),
            }
        finally:
            try:
                browser.close()
            except Exception:
                pass
            p.stop()
    except Exception as ex:
        return {
            "ok": False,
            "status": "failed",
            "error": str(ex),
            "doneSteps": last_done + 1,
            "totalSteps": len(steps),
            "elapsedMs": int((time.time() - started) * 1000),
        }


def media_file_path(user_id: str, session_id: str, filename: str) -> Optional[Path]:
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return None
    path = _MEDIA_ROOT / user_id / session_id / filename
    if path.is_file():
        return path
    return None
