"""WebReplay CDP 服务单元测试（不依赖真实 Chrome）。"""
from __future__ import annotations

from app.services.webreplay_cdp import _is_usable_tab_url, media_file_path
from app.services.webreplay_inject_bundle import (
    RECORDER_INIT_JS,
    REPLAY_ONE_STEP_JS,
    SELECTOR_RUNTIME_JS,
)


def test_is_usable_tab_url():
    assert _is_usable_tab_url("https://example.com/admin") is True
    assert _is_usable_tab_url("chrome://extensions") is False
    assert _is_usable_tab_url("devtools://devtools/") is False
    assert _is_usable_tab_url("data:text/html,hello") is False
    assert _is_usable_tab_url("about:blank") is False


def test_media_file_path_rejects_traversal():
    assert media_file_path("u1", "sess", "../etc/passwd") is None
    assert media_file_path("u1", "sess", "sub/x.png") is None


def test_inject_bundle_contains_core_hooks():
    assert "isTrusted" in RECORDER_INIT_JS
    assert "snapshotElement" in SELECTOR_RUNTIME_JS
    assert "resolveElement" in REPLAY_ONE_STEP_JS
    assert "click" in RECORDER_INIT_JS
