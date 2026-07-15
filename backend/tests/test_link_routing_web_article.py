"""平台识别与 OCR/reject 门禁回归。"""
from __future__ import annotations

from app.services.link_doc_routing import (
    is_web_article_link,
    platform_from_url,
    resolve_task_platform,
)
from app.services.llm_reject_guard import looks_like_llm_reject_text, safe_plain_text_fallback
from app.services.pipeline_output_quality import (
    XHS_OCR_ALL_FAILED,
    assess_assembled_source_text,
    assess_xhs_extractor_result,
    effective_xhs_body_chars,
)


def test_platform_weixin():
    url = "https://mp.weixin.qq.com/s/3WM-rIQsKp5RmGvgI6gz7g"
    assert platform_from_url(url) == "微信公众号"
    assert resolve_task_platform(url) == "微信公众号"
    assert resolve_task_platform(url, "微信") == "微信公众号"
    assert is_web_article_link(url)


def test_restart_failed_weixin_migrates_from_video_route():
    """复用失败卡片时，旧 video/yt-dlp 路由应迁移到 web_article。"""
    from app.services import task_manager as tm

    url = "https://mp.weixin.qq.com/s/3WM-rIQsKp5RmGvgI6gz7g"
    tid = "testwxmigrate01"
    tm._task_store[tid] = {
        "task_id": tid,
        "status": "failed",
        "platform": "微信",
        "link": url,
        "pipeline_route": "video",
        "pipeline_stages": {"download": {"status": "failed", "label": "下载视频"}},
        "failed_stage": "download",
        "resume_from": "download",
        "logs": [],
    }
    try:
        tm.restart_existing_task(tid, platform="微信", link=url)
        row = tm.get_task(tid)
        assert row["status"] == "pending"
        assert row["pipeline_route"] == "web_article"
        assert not row.get("resume_from")
        assert "fetch_fulltext" in (row.get("pipeline_stages") or {})
    finally:
        tm._task_store.pop(tid, None)


def test_platform_unknown_not_xhs():
    assert platform_from_url("https://example.com/article") == "通用网页"
    assert resolve_task_platform("https://example.com/x") == "通用网页"


def test_ocr_gate_fails_when_shell_body_and_no_ocr():
    payload = {
        "type": "xiaohongshu",
        "text_content": "创作中心业务合作发现直播发布通知 拿到 offer 收到字节面试",
        "image_links": ["http://img/1.jpg", "http://img/2.jpg"],
        "image_analysis": [],
    }
    gate = assess_xhs_extractor_result(payload, after_ocr=True)
    assert not gate.ok
    assert gate.error_code == XHS_OCR_ALL_FAILED


def test_assemble_gate_rejects_ocr_placeholders():
    raw = (
        "# 标题\n## 正文\n拿到 offer\n\n## 图片OCR\n"
        "[图片1]\n来源：http://a\n（OCR未识别到文本）\n\n"
        "[图片2]\n来源：http://b\n（OCR未识别到文本）"
    )
    gate = assess_assembled_source_text(raw)
    assert not gate.ok
    assert gate.error_code == XHS_OCR_ALL_FAILED


def test_reject_json_guard():
    reject = '{"status":"reject","reject_code":"INPUT_TOO_SHORT","reject_reason":"过短"}'
    assert looks_like_llm_reject_text(reject)
    out = safe_plain_text_fallback(reject, fallback_fn=lambda r: r or "fallback")
    assert out == "fallback"


def test_effective_body_strips_shell():
    body = "创作中心业务合作 拿到 offer 收到字节面试 沪ICP备13030189号"
    assert effective_xhs_body_chars(body) >= 10
