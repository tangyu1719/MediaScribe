"""RSS 阅读器单元测试（无 pytest fixture，避免沙箱 temp 权限问题）。"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

_RR_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "rss_reader.py"
_spec = importlib.util.spec_from_file_location("rss_reader_under_test", _RR_PATH)
assert _spec and _spec.loader
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

_SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Demo Blog</title>
    <link>https://example.com</link>
    <item>
      <title>Hello RSS</title>
      <link>https://example.com/post-1</link>
      <description>&lt;p&gt;First &amp; test&lt;/p&gt;</description>
      <pubDate>Mon, 01 Jun 2026 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

_SAMPLE_OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>test</title></head>
  <body>
    <outline text="Demo" type="rss" xmlUrl="https://example.com/feed.xml"/>
    <outline text="Demo2" type="rss" xmlUrl="https://example.org/atom.xml"/>
  </body>
</opml>
"""


class _StoreCtx:
    def __init__(self) -> None:
        self.store_dir = Path(__file__).resolve().parent / "_tmp_rss_test"
        self.store_file = self.store_dir / "store.json"
        self._orig_dir = rr._DATA_DIR
        self._orig_file = rr._STORE_FILE

    def __enter__(self) -> Path:
        if self.store_dir.exists():
            shutil.rmtree(self.store_dir, ignore_errors=True)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        rr._DATA_DIR = self.store_dir
        rr._STORE_FILE = self.store_file
        return self.store_file

    def __exit__(self, *_args) -> None:
        rr._DATA_DIR = self._orig_dir
        rr._STORE_FILE = self._orig_file
        shutil.rmtree(self.store_dir, ignore_errors=True)


def test_add_feed_rejects_invalid_url() -> None:
    with _StoreCtx():
        try:
            rr.add_feed("u1", "not-a-url")
            raise AssertionError("expected ValueError")
        except ValueError as ex:
            assert "订阅地址" in str(ex)


def test_parse_and_store_items() -> None:
    import feedparser

    with _StoreCtx():
        orig_parse = feedparser.parse
        feedparser.parse = lambda _url: orig_parse(_SAMPLE_FEED)  # type: ignore[assignment]
        try:
            feed = rr.add_feed("u1", "https://example.com/feed.xml")
            assert feed["title"] == "Demo Blog"
            items = rr.list_items("u1")
            assert len(items) == 1
            assert items[0]["title"] == "Hello RSS"
            assert items[0]["read"] is False
            assert items[0]["starred"] is False
        finally:
            feedparser.parse = orig_parse  # type: ignore[assignment]


def test_read_and_star() -> None:
    import feedparser

    with _StoreCtx():
        orig_parse = feedparser.parse
        feedparser.parse = lambda _url: orig_parse(_SAMPLE_FEED)  # type: ignore[assignment]
        try:
            rr.add_feed("u1", "https://example.com/feed.xml")
            item_id = rr.list_items("u1")[0]["id"]
            rr.set_item_read("u1", item_id, True)
            rr.set_item_starred("u1", item_id, True)
            row = rr.list_items("u1")[0]
            assert row["read"] is True
            assert row["starred"] is True
            stats = rr.rss_stats("u1")
            assert stats["unread_count"] == 0
            assert stats["starred_count"] == 1
        finally:
            feedparser.parse = orig_parse  # type: ignore[assignment]


def test_opml_import_export() -> None:
    with _StoreCtx():
        orig_sync = rr.sync_feed
        rr.sync_feed = lambda uid, fid: {"id": fid, "title": "Demo", "url": "https://example.com/feed.xml"}  # type: ignore[method-assign]
        try:
            result = rr.import_opml("u1", _SAMPLE_OPML)
            assert result["added"] == 2
            xml = rr.export_opml("u1")
            assert "xmlUrl" in xml
            assert "example.com/feed.xml" in xml
        finally:
            rr.sync_feed = orig_sync  # type: ignore[method-assign]


def test_chat_context_block() -> None:
    import feedparser

    with _StoreCtx():
        orig_parse = feedparser.parse
        feedparser.parse = lambda _url: orig_parse(_SAMPLE_FEED)  # type: ignore[assignment]
        try:
            rr.add_feed("u1", "https://example.com/feed.xml")
            block = rr.build_chat_context_block("u1", limit=5)
            assert "Hello RSS" in block
            assert "RSS 订阅" in block
        finally:
            feedparser.parse = orig_parse  # type: ignore[assignment]


if __name__ == "__main__":
    test_add_feed_rejects_invalid_url()
    test_parse_and_store_items()
    test_read_and_star()
    test_opml_import_export()
    test_chat_context_block()
    print("ok: rss_reader tests passed")
