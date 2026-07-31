from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.parametrize(
    "prompt",
    [
        "分析最近几个视频的实际内容并整理报告",
        "请按需处理视频音频与画面",
        "实际读取视频，必要时用 FFmpeg、Whisper 和 OCR",
        "提取近三条视频的语音和字幕",
    ],
)
def test_xhs_deep_media_intent_accepts_natural_phrasings(prompt):
    from app.services.chat_tool_registry import wants_xhs_deep_media

    assert wants_xhs_deep_media(prompt) is True


def test_xhs_deep_media_current_negation_wins():
    from app.services.ai_chat import _hydrate_xhs_user_search_args
    from app.services.chat_tool_registry import wants_xhs_deep_media

    hydrated = _hydrate_xhs_user_search_args(
        {"red_id": "haiyun862"},
        current_message="这次不要下载视频，也不用 Whisper，只看公开标题",
        task_user_query="读取最近视频实际内容并使用 FFmpeg、Whisper 和 OCR",
    )

    assert "读取最近视频实际内容" not in hydrated["user_prompt"]
    assert wants_xhs_deep_media(hydrated["user_prompt"]) is False


def test_xhs_tool_args_inherit_parent_media_goal_and_account():
    from app.services.ai_chat import _hydrate_xhs_user_search_args
    from app.services.chat_tool_registry import wants_xhs_deep_media

    hydrated = _hydrate_xhs_user_search_args(
        {},
        current_message="继续执行刚才的主任务",
        task_user_query=(
            "小红书号 haiyun862，分析最近几个视频的实际内容；"
            "必要时使用 FFmpeg、Whisper 和 OCR"
        ),
    )

    assert hydrated["red_id"] == "haiyun862"
    assert wants_xhs_deep_media(hydrated["user_prompt"]) is True


def test_specialized_xhs_request_skips_fixed_generic_web_prefetch():
    from app.services.ai_chat import _should_skip_fixed_web_for_specialized_xhs

    assert _should_skip_fixed_web_for_specialized_xhs(
        "继续执行刚才的任务",
        "小红书号 haiyun862，分析最近视频实际内容",
    ) is True
    assert _should_skip_fixed_web_for_specialized_xhs(
        "小红书上搜索 SFT 微调资料",
        "",
    ) is True
    assert _should_skip_fixed_web_for_specialized_xhs(
        "搜索 LangGraph checkpoint 官方资料",
        "",
    ) is False


def test_xhs_failed_media_wait_is_still_terminal_profile_evidence():
    from app.services.ai_chat import (
        _is_terminal_xhs_profile_result,
        _xhs_profile_used_deep_media,
    )

    wait_result = {
        "ok": False,
        "hint": "流水线失败，请检查错误字段",
        "pipelines": [{"task_id": "pipe-1", "status": "failed"}],
        "submission_result": {
            "ok": True,
            "profile_run_id": "profile-1",
            "selected_notes": [{"note_id": "note-1", "title": "公开视频"}],
            "pipeline_task_ids": ["pipe-1"],
            "resource_mode": "deep_media_async",
        },
    }

    assert _is_terminal_xhs_profile_result(wait_result) is True
    assert _xhs_profile_used_deep_media(wait_result) is True


def test_xhs_failed_media_wait_accepts_json_string_submission_result():
    """StructuredTool 返回 JSON 字符串时也必须恢复总结，不能误转 local_file_read。"""
    from app.services.ai_chat import (
        _is_terminal_xhs_profile_result,
        _xhs_profile_used_deep_media,
    )

    submission = json.dumps(
        {
            "ok": True,
            "profile_run_id": "profile-json-1",
            "selected_notes": [{"note_id": "note-1", "title": "公开视频"}],
            "pipeline_task_ids": ["pipe-failed-1"],
            "resource_mode": "deep_media_async",
        },
        ensure_ascii=False,
    )
    wait_result = {
        "ok": False,
        "hint": "流水线失败，请检查错误字段",
        "pipelines": [{"task_id": "pipe-failed-1", "status": "failed"}],
        "submission_result": submission,
    }

    assert _is_terminal_xhs_profile_result(wait_result) is True
    assert _xhs_profile_used_deep_media(wait_result) is True


@pytest.mark.asyncio
async def test_xhs_profile_explicit_video_request_enqueues_hybrid_media_without_token_leak():
    from app.services.chat_tool_registry import build_internal_chat_tools

    result = {
        "ok": True,
        "profile_run_id": "profile-1",
        "display_name": "海云日记",
        "creator_id": "creator-1",
        "profile_md_path": "profile.md",
        "deep_ok_count": 3,
        "selected_notes": [
            {
                "note_id": "0123456789abcdef01234567",
                "title": "公开视频",
                "canonical_url": "https://www.xiaohongshu.com/explore/0123456789abcdef01234567",
                "content_type": "video",
            }
        ],
        "_selected_access_notes": [
            {
                "note_id": "0123456789abcdef01234567",
                "title": "公开视频",
                "content_type": "video",
                "access_url": (
                    "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
                    "?xsec_token=private-token&xsec_source=pc_user"
                ),
            }
        ],
    }
    with (
        patch(
            "app.services.creator_profile_runner.run_xhs_chat_profile",
            new_callable=AsyncMock,
            return_value=result,
        ),
        patch(
            "app.services.task_manager.reuse_or_enqueue_task",
            return_value=("pipe-1", False, None),
        ) as enqueue_mock,
        patch(
            "app.services.pipeline_scheduler.request_video_pipeline_async",
            new_callable=AsyncMock,
        ) as schedule_mock,
    ):
        tool = next(t for t in build_internal_chat_tools() if t.name == "xhs_user_search")
        raw = await tool.ainvoke(
            {"red_id": "haiyun862", "user_prompt": "分析最近视频内容并整理报告"}
        )
        data = json.loads(raw)

    assert data["async"] is True
    assert data["pipeline_task_ids"] == ["pipe-1"]
    assert data["resource_mode"] == "deep_media_async"
    assert "private-token" not in raw
    assert "xsec_token" not in raw
    options = enqueue_mock.call_args.kwargs["pipeline_options"]
    assert options["video_transcript_mode"] == "hybrid"
    assert options["media_processing"] is True
    assert enqueue_mock.call_args.kwargs["pipeline_route"] == "xhs_chat_deep_media"
    schedule_mock.assert_awaited_once_with("pipe-1")


def test_async_wait_extracts_all_xhs_profile_pipeline_ids():
    from app.services.react_async_wait import extract_async_pipeline_ids

    raw = {
        "ok": True,
        "async": True,
        "pipeline_task_ids": ["pipe-1", "pipe-2", "pipe-1", ""],
    }
    assert extract_async_pipeline_ids("xhs_user_search", raw) == ["pipe-1", "pipe-2"]


def test_xhs_content_tool_is_registered_separately_from_profile_tool():
    from app.services.chat_tool_registry import build_internal_chat_tools

    names = {tool.name for tool in build_internal_chat_tools()}
    assert "xhs_user_search" in names
    assert "xhs_content_search" in names


def test_xhs_interaction_count_normalizes_visible_units():
    from app.services.xhs_local_browser import _xhs_interaction_count

    assert _xhs_interaction_count("1.2万") == 12000
    assert _xhs_interaction_count("3k") == 3000
    assert _xhs_interaction_count("86") == 86


def test_model_limit_fallback_keeps_five_xhs_evidence_links_and_directions():
    from app.services.ai_chat import _build_xhs_content_search_fallback

    titles = [
        "5分钟看懂 SFT 监督微调",
        "SFT 到底在学习什么？Loss 原理",
        "从零上手 LoRA 微调项目与部署",
        "SFT 训练经验与效果评测",
        "SFT 数据格式与幻觉知识点",
    ]
    raw = {
        "ok": True,
        "results": [
            {
                "rank": index,
                "note_id": f"{index:024x}",
                "title": title,
                "author": f"作者{index}",
                "interaction_text": str(index * 100),
                "engagement_count": index * 100,
                "canonical_url": f"https://www.xiaohongshu.com/explore/{index:024x}",
            }
            for index, title in enumerate(titles, start=1)
        ],
    }

    report = _build_xhs_content_search_fallback(raw, "筛选五篇 SFT 微调资料")

    assert report.count("https://www.xiaohongshu.com/explore/") == 5
    for direction in ("入门介绍", "深度原理", "训练框架", "实践经验", "关键知识点"):
        assert direction in report
    assert "429 配额限制" in report
