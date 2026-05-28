"""LinkAnalyzer 轻量路径与分阶段埋点（mock 网络，不测真实 OCR）。"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_AGENT = _BACKEND.parent / "src" / "agent"
if not (_AGENT / "link_analyzer.py").is_file():
    _AGENT = _BACKEND.parents[1] / "src" / "agent"
if str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))

_SAMPLE_HTML = """
<html><head><title>测试笔记 - 小红书</title>
<meta name="og:image" content="https://sns-img.xhscdn.com/1.jpg"/>
</head><body>
<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"n1":{"note":{"type":"normal","desc":"正文测试","imageList":[{"urlDefault":"https://sns-img.xhscdn.com/1.jpg"}]}}}}}};</script>
</body></html>
"""


def test_analyze_link_fast_path_skips_ocr(monkeypatch):
    from link_analyzer import LinkAnalyzer

    class _Resp:
        status_code = 200
        text = _SAMPLE_HTML

    monkeypatch.setattr(
        "link_analyzer.requests.get",
        lambda *a, **k: _Resp(),
    )
    monkeypatch.setattr(
        LinkAnalyzer,
        "download_image",
        lambda self, url: pytest.fail("不应在 include_image_ocr=False 时下载图片"),
    )

    res = LinkAnalyzer().analyze_link("https://www.xiaohongshu.com/explore/abc", include_image_ocr=False)
    assert res.get("type") == "xiaohongshu"
    assert len(res.get("image_links") or []) >= 1
    assert res.get("image_analysis") == []
    timing = res.get("_timing") or {}
    assert timing.get("include_image_ocr") is False
    assert "http_fetch" in (timing.get("phases_ms") or {})
    assert "ocr_all_images" not in (timing.get("phases_ms") or {})


def test_profile_total_ms():
    from link_analyzer_profile import LinkAnalyzerProfile

    p = LinkAnalyzerProfile("http://x", include_image_ocr=False)
    with p.phase("a"):
        pass
    with p.phase("b"):
        pass
    assert p.total_ms >= 0
    d = p.to_dict()
    assert "phases_ms" in d and "a" in d["phases_ms"]
