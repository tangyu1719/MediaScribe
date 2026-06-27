"""小红书号 → profile 解析策略：CDP 优先（不依赖 JSON Cookie），HTTP 缓存次之，错误码规范。"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from .tool_chat_resilience import extract_error_code

_log = logging.getLogger("sba.xhs_red_id_resolve")
_CHAIN = "社媒订阅-小红书号解析"


def xhs_error_code(message: str) -> str:
    return extract_error_code(str(message or ""))


def cdp_port_ready() -> Tuple[Optional[int], bool]:
    from .cookie_manager import find_cdp_port
    from .xhs_local_browser import CDP_PORT, _cdp_ready

    port = find_cdp_port()
    if port and _cdp_ready(port):
        return port, True
    return port, False


def normalize_resolve_failure(
    red_id: str,
    exc: BaseException,
    *,
    phase: str = "",
) -> RuntimeError:
    """将底层异常规范为 SUB_* 错误码，禁止 CDP 失败冒充 Cookie 不可用。"""
    raw = str(exc or "").strip()
    code = xhs_error_code(raw)
    if code:
        return RuntimeError(raw) if isinstance(exc, RuntimeError) else RuntimeError(f"{code}: {raw}")

    low = raw.lower()
    if "playwright" in low and "install" in low:
        return RuntimeError(f"SUB_XHS_BROWSER_UNAVAILABLE: 未安装 playwright（{phase}）: {raw}")

    if phase == "cdp":
        return RuntimeError(
            f"SUB_XHS_CDP_SEARCH_FAILED: CDP 已连接但未解析到小红书号 {red_id}: {raw}"
        )
    if phase == "http":
        return RuntimeError(
            f"SUB_FETCH_AUTH_FAILED: HTTP 通道需要已登录 Cookie 或 CDP 浏览器: {raw}"
        )
    return RuntimeError(
        f"SUB_RED_ID_NOT_FOUND: 未找到小红书号 {red_id} 对应的用户（{phase or 'resolve'}: {raw}）。"
        "请确认号正确；或在 App 打开博主主页复制 profile 链接（含 24 位 user_id）。"
    )


def resolve_via_cdp_if_ready(red_id: str) -> Optional[Dict[str, Any]]:
    """
    CDP 端口就绪时在浏览器内解析 red_id。
    未就绪返回 None；未找到抛 SUB_RED_ID_NOT_FOUND / SUB_XHS_CDP_SEARCH_FAILED。
    """
    port, ready = cdp_port_ready()
    if not ready or not port:
        return None

    from .xhs_local_browser import resolve_with_cdp_playwright

    try:
        got = resolve_with_cdp_playwright(red_id, port)
        _log.info(
            "[%s|xhs_red_id_resolve.resolve_via_cdp_if_ready|%s|Agent执行|成功] "
            "creator_id=%s; port=%s",
            _CHAIN,
            red_id,
            got.get("creator_id"),
            port,
        )
        return got
    except RuntimeError as ex:
        code = xhs_error_code(str(ex))
        if code in ("SUB_RED_ID_NOT_FOUND", "SUB_XHS_CDP_SEARCH_FAILED", "SUB_XHS_BROWSER_UNAVAILABLE"):
            raise
        raise normalize_resolve_failure(red_id, ex, phase="cdp") from ex
    except Exception as ex:
        raise normalize_resolve_failure(red_id, ex, phase="cdp") from ex


def resolve_via_http(
    red_id: str,
    *,
    post_search_usersearch,
    find_user_by_red_id_in_obj,
    user_dict_to_resolved,
    parse_init_state,
    is_suspicious_xhs_creator_id,
) -> Optional[Dict[str, Any]]:
    """
    HTTP + JSON Cookie 缓存通道（加速项，非 CDP 前置条件）。
    无可用登录 Cookie 时返回 None，不抛错。
    """
    import requests

    from .cookie_manager import load_cookies, probe_xhs_cookies_logged_in
    from .xhs_local_browser import _xhs_user_search_urls, ensure_xhs_cookies_synced
    from .xhs_session import probe_xhs_session, require_xhs_logged_in

    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    cookies = load_cookies("xiaohongshu") or {}
    probe_file = probe_xhs_cookies_logged_in(cookies) if cookies else {"logged_in": False}

    if not probe_file.get("logged_in"):
        ensure_xhs_cookies_synced()
        cookies = load_cookies("xiaohongshu") or {}
        probe_file = probe_xhs_cookies_logged_in(cookies) if cookies else {"logged_in": False}

    if not cookies or not probe_file.get("logged_in"):
        _log.info(
            "[%s|xhs_red_id_resolve.resolve_via_http|%s|硬编执行|跳过] "
            "无已登录 JSON Cookie，HTTP 通道跳过（可走 CDP）",
            _CHAIN,
            red_id,
        )
        return None

    for k, v in cookies.items():
        sess.cookies.set(k, v, domain=".xiaohongshu.com")
    probe = probe_xhs_session(sess)
    if probe.get("guest") or not probe.get("logged_in"):
        return None

    try:
        require_xhs_logged_in(sess)
    except RuntimeError as ex:
        _log.info(
            "[%s|xhs_red_id_resolve.resolve_via_http|%s|硬编执行|跳过] session 未登录: %s",
            _CHAIN,
            red_id,
            ex,
        )
        return None

    api_data = post_search_usersearch(sess, red_id)
    if api_data:
        hit = find_user_by_red_id_in_obj(api_data, red_id)
        if hit:
            return user_dict_to_resolved(hit, red_id, "usersearch_api")

    for search_url in _xhs_user_search_urls(red_id):
        r = sess.get(search_url, timeout=30)
        if r.status_code != 200:
            continue
        state = parse_init_state(r.text)
        if not state:
            continue
        hit = find_user_by_red_id_in_obj(state, red_id)
        if hit:
            return user_dict_to_resolved(hit, red_id, "search_init_state")

        blob = json.dumps(state, ensure_ascii=False)
        m = re.search(
            rf'"redId"\s*:\s*"{re.escape(red_id)}".{{0,1200}}?"id"\s*:\s*"([a-f0-9]{{24}})"',
            blob,
            re.S,
        )
        if not m:
            m = re.search(
                rf'"id"\s*:\s*"([a-f0-9]{{24}})".{{0,1200}}?"redId"\s*:\s*"{re.escape(red_id)}"',
                blob,
                re.S,
            )
        if m:
            uid = m.group(1)
            if is_suspicious_xhs_creator_id(uid):
                continue
            return {
                "creator_id": uid,
                "display_name": red_id,
                "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
                "red_id": red_id,
                "source": "search_init_state_regex",
            }
    return None


def orchestrate_resolve_xhs_red_id(
    red_id: str,
    *,
    display_name: str = "",
    post_search_usersearch,
    find_user_by_red_id_in_obj,
    user_dict_to_resolved,
    parse_init_state,
    is_suspicious_xhs_creator_id,
    resolve_red_id_via_local_chrome,
    resolve_red_id_stateless,
    should_use_stateless,
    record_cookie_attempt,
) -> Dict[str, Any]:
    """CDP 优先 → HTTP 缓存 → 本机 Chrome 全量兜底 → 无状态。"""
    red_id = (red_id or "").strip()
    if not red_id:
        raise ValueError("SUB_INVALID_URL")

    if re.fullmatch(r"[a-f0-9]{24}", red_id, re.I):
        return {
            "creator_id": red_id,
            "display_name": red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{red_id}",
            "red_id": red_id,
            "source": "direct_hex_id",
        }

    override = (os.environ.get("TEST_XHS_CREATOR_ID") or "").strip()
    if override and re.fullmatch(r"[a-f0-9]{24}", override, re.I):
        return {
            "creator_id": override,
            "display_name": red_id,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{override}",
            "red_id": red_id,
            "source": "env_test_creator_id",
        }

    if should_use_stateless():
        return resolve_red_id_stateless(red_id, display_name=display_name)

    cdp_err: Optional[BaseException] = None

    # 0) CDP 未就绪时自动拉起独立 SBA-Chrome-CDP（不杀日常 Chrome）
    port, ready = cdp_port_ready()
    if not ready:
        try:
            from .chrome_profile_prep import ensure_sba_cdp_chrome_running

            ensure_sba_cdp_chrome_running(wait_sec=30.0)
        except Exception as ex:
            _log.warning(
                "[%s|xhs_red_id_resolve.orchestrate|CDP|硬编执行|自动启动失败] error=%s",
                _CHAIN,
                str(ex)[:200],
            )

    # 1) CDP 优先：浏览器 Tab 有登录态即可，不要求 JSON
    try:
        got = resolve_via_cdp_if_ready(red_id)
        if got:
            record_cookie_attempt(ok=True)
            return got
    except RuntimeError:
        raise
    except Exception as ex:
        cdp_err = ex
        raise normalize_resolve_failure(red_id, ex, phase="cdp") from ex

    # 2) HTTP + JSON 缓存（可选加速）
    http_got = resolve_via_http(
        red_id,
        post_search_usersearch=post_search_usersearch,
        find_user_by_red_id_in_obj=find_user_by_red_id_in_obj,
        user_dict_to_resolved=user_dict_to_resolved,
        parse_init_state=parse_init_state,
        is_suspicious_xhs_creator_id=is_suspicious_xhs_creator_id,
    )
    if http_got:
        record_cookie_attempt(ok=True)
        return http_got

    # 3) 本机 Chrome 全量兜底（含 CDP / HTTP session / persistent）
    try:
        got = resolve_red_id_via_local_chrome(red_id)
        record_cookie_attempt(ok=True)
        return got
    except RuntimeError as ex:
        code = xhs_error_code(str(ex))
        if code in ("SUB_RED_ID_NOT_FOUND", "SUB_XHS_CDP_SEARCH_FAILED", "SUB_XHS_CDP_REQUIRED"):
            raise
        if should_use_stateless():
            return resolve_red_id_stateless(red_id, display_name=display_name)
        raise normalize_resolve_failure(red_id, ex, phase="local_chrome") from ex
    except Exception as ex:
        record_cookie_attempt(ok=False)
        if should_use_stateless():
            return resolve_red_id_stateless(red_id, display_name=display_name)
        if cdp_err:
            raise normalize_resolve_failure(red_id, cdp_err, phase="cdp") from ex
        raise normalize_resolve_failure(red_id, ex, phase="local_chrome") from ex

    # unreachable
    raise RuntimeError(f"SUB_RED_ID_NOT_FOUND: 未找到小红书号 {red_id}")
