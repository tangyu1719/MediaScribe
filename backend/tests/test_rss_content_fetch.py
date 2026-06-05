"""RSS 全文抓取单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import rss_content_fetch as rcf  # noqa: E402


def test_plain_from_html_strips_tags() -> None:
    html = "<html><body><article><h1>标题</h1><p>正文段落一。</p><p>正文段落二。</p></article></body></html>"
    text = rcf._plain_from_html(html)
    assert "标题" in text
    assert "正文段落一" in text
    assert "<p>" not in text


def test_fetch_uses_summary_fallback_when_short() -> None:
    orig = rcf._fetch_generic_article_text
    rcf._fetch_generic_article_text = lambda *_a, **_k: "短"  # type: ignore[assignment]
    try:
        out = rcf.fetch_article_full_text(
            "https://example.com/post-1",
            feed_summary="这是 Feed 里较长的摘要内容，用于在网页抓取失败或过短时作为沉淀输入。" * 3,
            feed_title="Demo",
            min_chars=80,
        )
        assert out["ok"] is True
        assert "Feed" in out["text"] or "摘要" in out["text"]
    finally:
        rcf._fetch_generic_article_text = orig  # type: ignore[assignment]


if __name__ == "__main__":
    test_plain_from_html_strips_tags()
    test_fetch_uses_summary_fallback_when_short()
    print("ok: rss_content_fetch tests passed")
