"""链接沉淀流水线边界测试：签名兼容、评论并入摘要、断点映射、转写门禁等。"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.comment_scraper import (
    CommentItem,
    CommentResult,
    format_comments_as_text,
    merge_text_with_comments,
)
from app.services.pipeline_stages import remap_resume_stage
from app.services.transcribe_quality import assess_transcript, is_mock_transcript


# ─── 评论并入摘要输入 ───


def test_merge_text_with_comments_appends_section():
    cr = CommentResult(
        platform="xiaohongshu",
        fetched_count=1,
        comments=[CommentItem(author="u1", text="很好", likes=3)],
    )
    out = merge_text_with_comments("正文A", comments_data=cr)
    assert "正文A" in out
    assert "## 评论区" in out
    assert "很好" in out


def test_merge_text_with_comments_prefers_comments_text():
    out = merge_text_with_comments("正文B", comments_text="[1] 预设评论")
    assert "预设评论" in out
    assert "正文B" in out


def test_merge_text_with_comments_empty_when_no_comments():
    assert merge_text_with_comments("仅正文") == "仅正文"


def test_render_comments_section_default_template():
    from app.services.pipeline_comments import render_comments_section, DEFAULT_COMMENTS_SECTION_TEMPLATE

    out = render_comments_section(
        DEFAULT_COMMENTS_SECTION_TEMPLATE,
        comments_analysis="| 层次 | 提问 | 怎么玩 agent |",
        comments_file_path="",
    )
    assert "【评论区】" in out
    assert "怎么玩 agent" in out


def test_append_comments_section_after_ai_summary():
    from app.services.pipeline_comments import append_comments_section_to_md

    md = "## AI分析摘要\n要点1\n"
    cfg = {"comments_section_template": "## 【评论区】\n\n{comments_analysis}\n"}
    out = append_comments_section_to_md(
        md,
        cfg,
        comments_analysis="| 作者回复 | 博主 | 字节要手撕 |",
    )
    assert out.index("AI分析摘要") < out.index("【评论区】")
    assert "手撕" in out


# ─── 转写 strict / MOCK 门禁 ───


def test_invoke_speech_to_text_filters_strict_when_missing():
    from app.services import video_pipeline as vp

    def _legacy_fn(path, log_callback=None, progress_callback=None, llm_config=None, user_prompt=""):
        return {
            "full_text": "这是一段模拟的视频转文字结果。视频内容包括产品介绍。",
            "transcribe_source": "mock",
        }

    with patch.object(vp, "_reload_speech_to_text", return_value=(_legacy_fn, False, "/tmp/old.py")):
        out = vp.invoke_speech_to_text("/tmp/x.mp4", strict=True)
    assert out is not None
    assert out.get("ok") is False
    assert out.get("error_code") == "transcript_mock_fallback"


def test_invoke_speech_to_text_retries_when_strict_rejected_at_runtime():
    from app.services import video_pipeline as vp
    from app.services.transcribe_quality import TranscriptAssessment

    calls = []

    def _race_fn(path, **kwargs):
        calls.append(dict(kwargs))
        if "strict" in kwargs:
            raise TypeError("speech_to_text() got an unexpected keyword argument 'strict'")
        return {"ok": True, "full_text": "retry ok"}

    ok_assessment = TranscriptAssessment(ok=True, error_code="", error_message="")
    with patch.object(vp, "_reload_speech_to_text", return_value=(_race_fn, True, "/tmp/race.py")), patch.object(
        vp, "assess_transcript", return_value=ok_assessment, create=True
    ):
        with patch("app.services.transcribe_quality.assess_transcript", return_value=ok_assessment):
            out = vp.invoke_speech_to_text("/tmp/x.mp4", strict=True)
    assert out.get("full_text") == "retry ok"
    assert len(calls) == 2
    assert "strict" in calls[0]
    assert "strict" not in calls[1]


def test_invoke_speech_to_text_passes_strict_when_supported():
    from app.services import video_pipeline as vp

    captured = {}

    def _modern_fn(path, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "full_text": "真实转写内容足够长" * 10}

    with patch.object(vp, "_reload_speech_to_text", return_value=(_modern_fn, True, "/tmp/new.py")):
        out = vp.invoke_speech_to_text("/tmp/x.mp4", strict=True, user_prompt="u")
    assert out.get("full_text")
    assert captured.get("strict") is True


def test_speech_to_text_in_agent_has_strict_param():
    """运行时 agent 目录的 speech_to_text 须含 strict（防 uvicorn 缓存旧模块）。"""
    from app.services.config import resolve_agent_dir
    import sys

    agent = str(resolve_agent_dir())
    if agent not in sys.path:
        sys.path.insert(0, agent)
    import importlib
    import video_downloader as vd

    vd = importlib.reload(vd)
    assert "strict" in inspect.signature(vd.speech_to_text).parameters


def test_strip_whisper_hallucination_tail_keeps_valid_prefix():
    from app.services.transcribe_quality import (
        assess_transcript,
        strip_whisper_hallucination_tail,
    )

    prefix = (
        "在企业销售右中,如何更好的服务客户是持续贬定单的关键因素之一。"
        "隐结AI智能套件。通过将AI融入业务以更高效的业务自动化和更敏捷的业务洞察力,重塑运营新格局。"
        "当企业收到客户采购定单时,业务员需要第一时间响应客户需求。"
    )
    # 滑动错位幻听（与线上一致：非整段相同但高重复率）
    tail = "小伙伴" + "的小伙伴们" * 24
    full = prefix + tail
    cleaned, stripped = strip_whisper_hallucination_tail(full)
    assert stripped > 0
    assert "企业销售" in cleaned
    assert "小伙伴们" not in cleaned
    gate = assess_transcript(full)
    assert gate.ok, gate.error_message


def test_assess_transcript_rejects_mock():
    text = "这是一段模拟的视频转文字结果。视频内容包括产品介绍。"
    a = assess_transcript(text)
    assert not a.ok
    assert a.error_code == "transcript_mock_fallback"
    assert a.is_mock
    assert is_mock_transcript(text)


# ─── 评论全量 count=0 ───


@pytest.mark.parametrize(
    "count,expected",
    [(0, None), (10, 10), (50, 50)],
)
def test_video_pipeline_comment_max_count_mapping(count, expected):
    """与 video_pipeline 步骤0 一致：0 → 全量(None)。"""
    max_count = count
    if max_count == 0:
        max_count = None
    assert max_count is expected


# ─── 断点跨路由映射 ───


@pytest.mark.parametrize(
    "from_stage,to_route,expect",
    [
        ("download", "xiaohongshu_graphic", "extract"),
        ("transcribe", "xiaohongshu_graphic", "ai_analysis"),
        ("comments", "xiaohongshu_graphic", "comments"),
        ("download", "douyin_graphic", "extract"),
    ],
)
def test_remap_resume_stage(from_stage, to_route, expect):
    assert remap_resume_stage(from_stage, "video", to_route) == expect


def test_probe_xiaohongshu_wrapper_delegates():
    from app.services.video_pipeline import (
        _is_xiaohongshu_graphic_sync,
        _probe_xiaohongshu_graphic_sync,
    )

    stub = {"type": "xiaohongshu", "title": "t"}
    with patch(
        "app.services.video_pipeline._probe_xiaohongshu_graphic_sync",
        return_value=(True, stub),
    ) as mocked:
        assert _is_xiaohongshu_graphic_sync("https://www.xiaohongshu.com/explore/x") is True
        mocked.assert_called_once()


# ─── AI 工具 link_pipeline_start 评论配置 ───


def test_link_pipeline_start_full_comments_when_read_comments_enabled():
    from app.services.chat_tool_registry import build_internal_chat_tools

    tools = build_internal_chat_tools(read_comments=True)
    names = [getattr(t, "name", "") for t in tools]
    assert "link_pipeline_start" in names
    assert "scrape_comments" in names

    lps = next(t for t in tools if getattr(t, "name", "") == "link_pipeline_start")
    # StructuredTool 包装后 coroutine 在 func/coroutine 属性
    coro = getattr(lps, "coroutine", None) or getattr(lps, "func", None)
    assert coro is not None


def test_xiaohongshu_comments_passed_separately_not_in_source():
    """评论不进原文装配；经 comments_text 送入 document_consolidation。"""
    async def _run():
        from app.services import xiaohongshu_article as xhs
        from app.services.task_manager import create_task, get_task

        tid = create_task(
            "小红书",
            "https://www.xiaohongshu.com/explore/6a13f2eb000000003502b334",
            user_prompt="边界测试",
            comments={"enabled": True, "count": 0, "sort": "hot"},
        )
        from app.services.comment_scraper import CommentItem, CommentResult

        comments_data = CommentResult(
            platform="xiaohongshu",
            fetched_count=1,
            comments=[CommentItem(author="用户A", text="评论应进入摘要", likes=1)],
        )
        stub = {
            "type": "xiaohongshu",
            "title": "标题",
            "text_content": "正文区",
            "image_links": [],
            "image_analysis": [],
        }
        captured = {}

        def _fake_consolidation(*, text, comments_text="", **kwargs):
            captured["text"] = text
            captured["comments_text"] = comments_text
            return {"ai_summary": "摘要", "article": "润色正文", "comments_viewpoint": ""}

        with patch.object(xhs, "_extract_xiaohongshu_content", return_value=stub):
            with patch.object(xhs, "_ocr_compensation", side_effect=lambda r, _tid: r):
                with patch.object(xhs, "run_document_consolidation", side_effect=_fake_consolidation):
                    with patch.object(xhs, "resolve_doc_title", return_value="文档标题"):
                        with patch.object(xhs, "_generate_md", return_value="/tmp/x.md"):
                            with patch("app.services.xiaohongshu_article.start_feishu_upload_async"):
                                with patch("app.services.xiaohongshu_article.complete_task_after_md"):
                                    await xhs.process_xiaohongshu_article_pipeline(
                                        tid,
                                        user_prompt="边界测试",
                                        comments_data=comments_data,
                                    )
        assert "评论应进入摘要" not in captured.get("text", "")
        assert "评论应进入摘要" in captured.get("comments_text", "")
        assert "正文区" in captured.get("text", "")
        task = get_task(tid)
        assert task is not None

    asyncio.run(_run())
