"""订阅链接选取：连续性 + HASH 去重。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from app.services.subscription_link_order import select_items_for_subscription_sync


class _Item:
    def __init__(self, note_id, url_hash, url, published_at, platform="xiaohongshu"):
        self.note_id = note_id
        self.url_hash = url_hash
        self.canonical_url = url
        self.published_at = published_at
        self.platform = platform
        self.title = note_id
        self.content_type = "normal"


def test_select_from_newest_when_no_anchor():
    items = [
        _Item("n3", "h3", "https://x.test/3", "2026-06-03T10:00:00"),
        _Item("n2", "h2", "https://x.test/2", "2026-06-02T10:00:00"),
        _Item("n1", "h1", "https://x.test/1", "2026-06-01T10:00:00"),
    ]
    with patch("app.services.subscription_link_order.check_already_imported", return_value=(False, "")):
        with patch("app.services.subscription_link_order.probe_link_accessible", return_value=(True, "")):
            picked, _, _ = select_items_for_subscription_sync(
                items,
                platform="xiaohongshu",
                seen_url_hashes=set(),
                seen_note_ids=set(),
                anchor_published_at=None,
                limit=2,
            )
    assert len(picked) == 2
    assert picked[0].note_id == "n3"


def test_invalid_link_blocks_continuity():
    items = [
        _Item("n2", "h2", "https://x.test/2", "2026-06-02T10:00:00"),
        _Item("n1", "h1", "https://x.test/bad", "2026-06-01T10:00:00"),
    ]
    anchor = datetime(2026, 5, 31, 10, 0, 0)

    def _probe(url):
        if "bad" in url:
            return False, "http_404"
        return True, ""

    with patch("app.services.subscription_link_order.check_already_imported", return_value=(False, "")):
        with patch("app.services.subscription_link_order.probe_link_accessible", side_effect=_probe):
            picked, _, reason = select_items_for_subscription_sync(
                items,
                platform="xiaohongshu",
                seen_url_hashes=set(),
                seen_note_ids=set(),
                anchor_published_at=anchor,
                limit=5,
            )
    assert not picked
    assert "link_invalid" in reason
