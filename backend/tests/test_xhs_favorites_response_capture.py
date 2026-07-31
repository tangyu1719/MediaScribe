from __future__ import annotations

import json

from app.services.xhs_favorites_adapter import (
    favorites_catalog_metrics,
    parse_favorites_from_response_capture,
)
from app.services.xhs_local_browser import (
    _XHS_FAVORITES_CAPTURE_INSTALL_JS,
    _merge_favorite_items_by_note,
    favorites_response_capture_enabled,
)


def _note(note_id: str, *, token: str, title: str, author_id: str) -> dict:
    return {
        "note_id": note_id,
        "xsec_token": token,
        "display_title": title,
        "type": "video",
        "user": {
            "user_id": author_id,
            "nickname": f"作者-{author_id[:4]}",
        },
        "interact_info": {"liked_count": 12, "comment_count": 3},
        "cover": {"url_default": "https://example.invalid/cover.jpg"},
    }


def test_collect_page_capture_parses_and_deduplicates_signed_notes():
    note_a = "0123456789abcdef01234567"
    note_b = "fedcba9876543210fedcba98"
    capture = {
        "installed": True,
        "pages": [
            {
                "transport": "xhr",
                "path": "/api/sns/web/v2/note/collect/page",
                "items": [
                    _note(note_a, token="token-a", title="第一篇", author_id=note_b),
                    _note(note_b, token="token-b", title="第二篇", author_id=note_a),
                ],
            },
            {
                "transport": "fetch",
                "path": "/api/sns/web/v2/note/collect/page",
                "items": [
                    _note(note_a, token="", title="第一篇", author_id=note_b),
                ],
            },
        ],
    }

    items = parse_favorites_from_response_capture(
        capture,
        owner_creator_id="owner",
        profile_url="https://www.xiaohongshu.com/user/profile/owner?tab=fav",
    )

    assert {item.note_id for item in items} == {note_a, note_b}
    assert all("xsec_token=" in item.canonical_url for item in items)
    assert all(item.author_id for item in items)
    metrics = favorites_catalog_metrics(items)
    assert metrics["count"] == 2
    assert metrics["note_id_rate"] == 1.0
    assert metrics["xsec_token_rate"] == 1.0
    assert "token-a" not in json.dumps(metrics, ensure_ascii=False)


def test_capture_source_wins_when_it_is_at_least_as_complete_as_dom():
    note_id = "0123456789abcdef01234567"
    author_id = "fedcba9876543210fedcba98"
    dom = parse_favorites_from_response_capture(
        {
            "pages": [
                {
                    "transport": "xhr",
                    "items": [_note(note_id, token="", title="", author_id="")],
                }
            ]
        },
        owner_creator_id="owner",
        profile_url="https://www.xiaohongshu.com/user/profile/owner?tab=fav",
        fetch_source="dom_fixture",
    )[0]
    captured = parse_favorites_from_response_capture(
        {
            "pages": [
                {
                    "transport": "xhr",
                    "items": [
                        _note(
                            note_id,
                            token="signed-token",
                            title="响应中的完整标题",
                            author_id=author_id,
                        )
                    ],
                }
            ]
        },
        owner_creator_id="owner",
        profile_url="https://www.xiaohongshu.com/user/profile/owner?tab=fav",
    )[0]
    by_note = {note_id: dom}

    _merge_favorite_items_by_note(by_note, [captured], prefer_incoming=True)

    merged = by_note[note_id]
    assert merged.fetch_source == "collect_page_xhr"
    assert merged.title == "响应中的完整标题"
    assert merged.author_id == author_id
    assert "xsec_token=signed-token" in merged.canonical_url


def test_page_hook_captures_xhr_and_fetch_without_exporting_request_secrets():
    assert "/api/sns/web/v2/note/collect/page" in _XHS_FAVORITES_CAPTURE_INSTALL_JS
    assert "XMLHttpRequest.prototype.open" in _XHS_FAVORITES_CAPTURE_INSTALL_JS
    assert "window.fetch" in _XHS_FAVORITES_CAPTURE_INSTALL_JS
    assert "requestHeaders" not in _XHS_FAVORITES_CAPTURE_INSTALL_JS
    assert "X-S-Common" not in _XHS_FAVORITES_CAPTURE_INSTALL_JS
    assert "document.cookie" not in _XHS_FAVORITES_CAPTURE_INSTALL_JS


def test_response_capture_can_be_disabled_for_same_parameter_baseline(monkeypatch):
    monkeypatch.setenv("SBA_XHS_FAVORITES_RESPONSE_CAPTURE", "0")
    assert favorites_response_capture_enabled() is False
    monkeypatch.setenv("SBA_XHS_FAVORITES_RESPONSE_CAPTURE", "1")
    assert favorites_response_capture_enabled() is True
