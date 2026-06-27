"""小红书号解析策略：CDP 优先、错误码规范。"""
from __future__ import annotations

import pytest

from app.services.tool_chat_resilience import extract_error_code, plan_tool_retry
from app.services.xhs_red_id_resolve import (
    cdp_port_ready,
    normalize_resolve_failure,
    orchestrate_resolve_xhs_red_id,
    resolve_via_cdp_if_ready,
    xhs_error_code,
)


def test_xhs_error_code_extract():
    assert xhs_error_code("SUB_XHS_CDP_SEARCH_FAILED: foo") == "SUB_XHS_CDP_SEARCH_FAILED"


def test_normalize_cdp_failure_not_cookie():
    err = normalize_resolve_failure("981032418", ValueError("timeout"), phase="cdp")
    assert "SUB_XHS_CDP_SEARCH_FAILED" in str(err)
    assert "SUB_XHS_COOKIE_UNAVAILABLE" not in str(err)


def test_normalize_not_found():
    err = normalize_resolve_failure("981032418", ValueError("x"), phase="local_chrome")
    assert "SUB_RED_ID_NOT_FOUND" in str(err)


def test_plan_retry_cdp_required():
    _, hook = plan_tool_retry(
        tool_name="xhs_user_search",
        tool_args={"red_id": "981032418"},
        error_message="SUB_XHS_CDP_REQUIRED: port 9223",
        attempt=0,
    )
    assert hook == "probe_cdp"


def test_orchestrate_direct_hex():
    out = orchestrate_resolve_xhs_red_id(
        "5d8abd1c0000000001003e35",
        post_search_usersearch=lambda *a, **k: None,
        find_user_by_red_id_in_obj=lambda *a, **k: None,
        user_dict_to_resolved=lambda *a, **k: {},
        parse_init_state=lambda *a, **k: None,
        is_suspicious_xhs_creator_id=lambda *a, **k: False,
        resolve_red_id_via_local_chrome=lambda *a, **k: {},
        resolve_red_id_stateless=lambda *a, **k: {},
        should_use_stateless=lambda: False,
        record_cookie_attempt=lambda **k: None,
    )
    assert out["creator_id"] == "5d8abd1c0000000001003e35"
    assert out["source"] == "direct_hex_id"


def test_orchestrate_cdp_first(monkeypatch):
    monkeypatch.setattr(
        "app.services.xhs_red_id_resolve.resolve_via_cdp_if_ready",
        lambda red_id: {
            "creator_id": "abc123",
            "profile_url": "https://www.xiaohongshu.com/user/profile/abc123",
            "red_id": red_id,
            "source": "cdp_mock",
        },
    )
    http_called = {"v": False}

    def _http(*a, **k):
        http_called["v"] = True
        return None

    out = orchestrate_resolve_xhs_red_id(
        "981032418",
        post_search_usersearch=lambda *a, **k: None,
        find_user_by_red_id_in_obj=lambda *a, **k: None,
        user_dict_to_resolved=lambda *a, **k: {},
        parse_init_state=lambda *a, **k: None,
        is_suspicious_xhs_creator_id=lambda *a, **k: False,
        resolve_red_id_via_local_chrome=lambda *a, **k: {},
        resolve_red_id_stateless=lambda *a, **k: {},
        should_use_stateless=lambda: False,
        record_cookie_attempt=lambda **k: None,
    )
    assert out["source"] == "cdp_mock"
    assert http_called["v"] is False


def test_resolve_via_cdp_skips_when_not_ready(monkeypatch):
    monkeypatch.setattr("app.services.xhs_red_id_resolve.cdp_port_ready", lambda: (None, False))
    assert resolve_via_cdp_if_ready("981032418") is None


def test_run_sync_off_asyncio_loop_uses_worker_thread():
    import asyncio
    import threading

    from app.services.xhs_local_browser import _run_sync_off_asyncio_loop

    seen: dict = {}

    def _fn():
        seen["worker"] = threading.current_thread().name
        return "ok"

    async def _run():
        seen["caller"] = threading.current_thread().name
        return _run_sync_off_asyncio_loop(_fn)

    assert asyncio.run(_run()) == "ok"
    assert seen["worker"] != seen["caller"]
    assert "xhs_pw_sync" in seen["worker"]


def test_resolve_via_cdp_runs_playwright_off_main_async_thread(monkeypatch):
    import asyncio
    import threading

    monkeypatch.setattr("app.services.xhs_red_id_resolve.cdp_port_ready", lambda: (9223, True))
    seen: dict = {}

    def _mock_cdp(red_id, port):
        seen["thread"] = threading.current_thread().name
        return {
            "creator_id": "abc123",
            "profile_url": "https://www.xiaohongshu.com/user/profile/abc123",
            "red_id": red_id,
            "source": "cdp_mock",
        }

    monkeypatch.setattr(
        "app.services.xhs_local_browser._resolve_with_cdp_playwright",
        _mock_cdp,
    )

    async def _run():
        seen["async_thread"] = threading.current_thread().name
        return resolve_via_cdp_if_ready("981032418")

    out = asyncio.run(_run())
    assert out["source"] == "cdp_mock"
    assert seen["thread"] != seen["async_thread"]
    assert "xhs_pw_sync" in seen["thread"]
