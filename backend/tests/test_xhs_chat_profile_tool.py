"""xhs_user_search 应走五阶段画像，不得将 profile URL 提交 link_pipeline。"""
from __future__ import annotations

import json
import inspect
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_xhs_user_search_uses_chat_profile_not_link_pipeline():
    from app.services.chat_tool_registry import build_internal_chat_tools

    mock_result = {
        "ok": True,
        "profile_run_id": "chat_profile_abc",
        "status": "completed",
        "red_id": "981032418",
        "creator_id": "5d8abd1c0000000001003e35",
        "display_name": "产品老焦",
        "profile_md_path": "/tmp/chat_profiles/981032418/profile.md",
        "profile_summary": "测试画像摘要",
        "deep_ok_count": 3,
        "selected_notes": [{"note_id": "n1", "title": "t1"}],
    }

    with patch(
        "app.services.creator_profile_runner.run_xhs_chat_profile",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as run_mock:
        tools = build_internal_chat_tools()
        tool = next(t for t in tools if t.name == "xhs_user_search")
        raw = await tool.ainvoke({"red_id": "981032418", "user_prompt": "人物画像"})
        data = json.loads(raw)

    assert data["ok"] is True
    assert data["async"] is False
    assert "profile_summary" in data
    run_mock.assert_awaited_once()
    assert run_mock.await_args.kwargs["red_id"] == "981032418"


@pytest.mark.asyncio
async def test_xhs_user_search_accepts_alphanumeric_account():
    from app.services.chat_tool_registry import build_internal_chat_tools

    mock_result = {
        "ok": True,
        "profile_run_id": "chat_profile_haiyun",
        "status": "completed",
        "display_name": "海云日记",
        "creator_id": "creator-haiyun",
        "profile_md_path": "output/haiyun.md",
        "profile_summary": "recent videos",
        "deep_ok_count": 3,
    }
    with patch(
        "app.services.creator_profile_runner.run_xhs_chat_profile",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as run_mock:
        tool = next(t for t in build_internal_chat_tools() if t.name == "xhs_user_search")
        data = json.loads(
            await tool.ainvoke({"red_id": "haiyun862", "user_prompt": "分析最近视频"})
        )

    assert data["ok"] is True
    assert run_mock.await_args.kwargs["red_id"] == "haiyun862"


def test_xhs_alphanumeric_account_routes_without_generic_web_search():
    from app.services.agent_pipeline import should_skip_pipeline_llm
    from app.services.link_doc_routing import analyze_link_doc_intent, extract_xhs_account_id

    query = "小红书号：haiyun862，分析最近几个视频并整理报告。"
    assert extract_xhs_account_id(query) == "haiyun862"
    assert should_skip_pipeline_llm(query) is True
    assert analyze_link_doc_intent(query)["skip_web_search"] is True


def test_xhs_tool_name_is_not_misread_as_account_id():
    from app.services.link_doc_routing import extract_xhs_account_id

    assert extract_xhs_account_id("重新调用 xhs_user_search 并等待工具完成") is None
    assert extract_xhs_account_id("用 xhs id haiyun862 继续分析") == "haiyun862"


def test_xhs_user_search_source_no_profile_pipeline_enqueue():
    from app.services import chat_tool_registry

    src = inspect.getsource(chat_tool_registry.build_internal_chat_tools)
    # 旧错误路径：profile URL → reuse_or_enqueue_task
    assert "reuse_or_enqueue_task(plat, profile_url" not in src


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["video", "graphic"])
async def test_profile_note_sampling_is_lightweight_for_all_xhs_types(content_type: str):
    from app.services import creator_profile_article as article

    link = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=test"
    note = {
        "note_id": "0123456789abcdef01234567",
        "title": "可验证的页面标题",
        "content_type": content_type,
        "published_at": "2026-07-20T12:00:00",
        "pipeline_url": link,
        "canonical_url": link,
        "link_source": "profile_click",
        "link_resolved": True,
    }
    page = {
        "title": note["title"],
        "type": "xiaohongshu_video" if content_type == "video" else "xiaohongshu",
        "text_content": "这是网页直接可见的正文证据。" * 20,
    }

    with (
        patch.object(article, "create_task", return_value="profile_light_task") as create_mock,
        patch.object(article, "update_task") as update_mock,
        patch.object(article, "_run_in_profile_pool", new_callable=AsyncMock, return_value=page) as run_mock,
        patch.object(article, "_write_lightweight_note_md", return_value="output/profile_light_task.md"),
    ):
        result = await article.run_article_only_for_note(note=note, timeout_sec=5)

    assert result["ok"] is True
    assert result["media_processing"] is False
    assert result["heavy_services_used"] == []
    assert result["evidence_level"] == "page_text"
    assert "网页直接可见" in result["article"]
    assert "xsec_token" not in result["canonical_url"]
    run_mock.assert_awaited_once_with(link)
    assert "xsec_token" not in create_mock.call_args.args[1]
    options = create_mock.call_args.kwargs["pipeline_options"]
    assert options["media_processing"] is False
    assert options["skip_media_download"] is True
    assert options["skip_whisper"] is True
    assert "whisper_pool" not in options
    assert update_mock.call_args.kwargs["pipeline_route"] == "creator_profile_light"


def test_profile_note_sampling_has_no_heavy_pipeline_callsite():
    from app.services import creator_profile_article as article

    source = inspect.getsource(article)
    assert "process_video_pipeline" not in source
    assert "from .video_pipeline" not in source
    assert '"whisper_pool"' not in source


def test_profile_note_log_masks_xsec_token():
    from app.services.creator_profile_article import _safe_log_link

    masked = _safe_log_link(
        "https://www.xiaohongshu.com/explore/abc?xsec_token=private-value&xsec_source=pc_user"
    )
    assert "private-value" not in masked
    assert "xsec_token=***" in masked


def test_successful_xhs_profile_is_terminal_for_react_tools():
    from app.services.ai_chat import _is_terminal_xhs_profile_result

    assert _is_terminal_xhs_profile_result(
        {
            "ok": True,
            "profile_run_id": "chat_profile_1",
            "selected_notes": [{"note_id": "n1"}],
        }
    ) is True
    assert _is_terminal_xhs_profile_result(
        {"ok": False, "profile_run_id": "chat_profile_2", "selected_notes": []}
    ) is False


def test_ai_chat_stops_tool_loop_after_xhs_profile_delivery():
    from app.services import ai_chat

    source = inspect.getsource(ai_chat.chat_stream_v2)
    assert "force_terminal_xhs_answer" in source
    assert "_is_terminal_xhs_profile_result(raw_out)" in source
    assert "submission_result" in source
    assert "禁止再次提交同一批" in source
    assert "本轮已按用户要求完成视频下载、Whisper 与画面 OCR" in source


@pytest.mark.asyncio
async def test_link_pipeline_refuses_creator_profile_lightweight_task_reuse():
    from app.services.chat_tool_registry import build_internal_chat_tools

    public_url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
    profile_task = {
        "task_id": "profile_light_task",
        "link": public_url,
        "pipeline_options": {"source": "creator_profile", "media_processing": False},
    }
    tool = next(t for t in build_internal_chat_tools() if t.name == "link_pipeline_start")

    with patch("app.services.task_manager.list_tasks", return_value=[profile_task]):
        data = json.loads(
            await tool.ainvoke(
                {
                    "link": public_url + "?xsec_token=private",
                    "platform": "小红书",
                }
            )
        )

    assert data["ok"] is False
    assert data["skipped"] is True
    assert data["error_code"] == "PROFILE_LIGHTWEIGHT_NO_MEDIA"
    assert "FFmpeg/Whisper" in data["error"]


def test_profile_metadata_only_result_is_explicit_and_traceable():
    from app.services.creator_profile_article import _render_lightweight_note_md

    link = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=test"
    markdown, body, evidence = _render_lightweight_note_md(
        note={
            "note_id": "0123456789abcdef01234567",
            "title": "仅有标题的视频笔记",
            "content_type": "video",
            "published_at": "2026-07-20",
        },
        link=link,
        page={"error": "page text unavailable"},
    )

    assert body == ""
    assert evidence == "catalog_metadata"
    assert "未下载音视频" in markdown
    assert "未提取到足够正文" in markdown
    assert link in markdown


def test_profile_selection_falls_back_when_llm_ids_do_not_exist_in_catalog():
    from app.services import creator_profile_llm as profile_llm

    catalog = [
        {
            "note_id": f"{idx:024x}",
            "title": f"笔记 {idx}",
            "content_type": "video",
            "published_at": f"2026-07-{10 + idx:02d}T12:00:00",
            "canonical_url": f"https://www.xiaohongshu.com/explore/{idx:024x}",
        }
        for idx in range(1, 6)
    ]
    fake = {
        "ok": True,
        "model": "test-model",
        "content": (
            '```json\n{"selected_note_ids": '
            '["bad-1", "bad-2", "bad-3", "bad-4", "bad-5"]}\n```'
        ),
    }

    with patch.object(profile_llm, "invoke_profile_llm", return_value=fake):
        result = profile_llm.build_note_selection(
            display_name="海云日记",
            light_profile={},
            catalog=catalog,
            min_pick=3,
            max_pick=5,
        )

    valid_ids = {row["note_id"] for row in catalog}
    assert result["ok"] is True
    assert result["fallback"] is True
    assert result["invalid_selected_count"] == 5
    assert 3 <= len(result["selected_note_ids"]) <= 5
    assert set(result["selected_note_ids"]).issubset(valid_ids)


def test_headless_profile_catalog_accepts_and_forwards_min_count():
    from app.services import xhs_local_browser

    assert "min_count" in inspect.signature(
        xhs_local_browser.scrape_profile_feed_items_via_headless_cookies
    ).parameters

    with patch.object(
        xhs_local_browser,
        "scrape_profile_feed_items_via_headless_cookies",
        return_value=["note"],
    ) as scrape_mock:
        result = xhs_local_browser.fetch_catalog_via_headless_cookies(
            "creator-1",
            profile_url="https://www.xiaohongshu.com/user/profile/creator-1",
            min_count=37,
        )

    assert result == ["note"]
    assert scrape_mock.call_args.kwargs["min_count"] == 37


def test_profile_catalog_reuses_matching_logged_in_page():
    from app.services.xhs_local_browser import _pick_existing_profile_page

    class Page:
        def __init__(self, url: str, closed: bool = False):
            self.url = url
            self._closed = closed

        def is_closed(self) -> bool:
            return self._closed

    matching = Page(
        "https://www.xiaohongshu.com/user/profile/646f817e000000001c02a018"
        "?xsec_token=private&xsec_source=pc_search"
    )
    context = type(
        "Context",
        (),
        {
            "pages": [
                Page("https://www.xiaohongshu.com/explore"),
                Page("https://www.xiaohongshu.com/login"),
                matching,
            ]
        },
    )()

    assert _pick_existing_profile_page(context, "646f817e000000001c02a018") is matching


def test_profile_catalog_recovers_access_url_from_open_search_page():
    from app.services.xhs_local_browser import _profile_url_from_open_pages

    class Match:
        def __init__(self, href: str):
            self.first = self
            self.href = href

        def count(self) -> int:
            return 1

        def get_attribute(self, name: str) -> str:
            assert name == "href"
            return self.href

    class SearchPage:
        url = "https://www.xiaohongshu.com/search_result?keyword=haiyun862&type=user"

        def is_closed(self) -> bool:
            return False

        def locator(self, selector: str) -> Match:
            assert "646f817e000000001c02a018" in selector
            return Match(
                "/user/profile/646f817e000000001c02a018"
                "?xsec_token=private&xsec_source=pc_search"
            )

    context = type("Context", (), {"pages": [SearchPage()]})()
    recovered = _profile_url_from_open_pages(context, "646f817e000000001c02a018")

    assert recovered.startswith(
        "https://www.xiaohongshu.com/user/profile/646f817e000000001c02a018?"
    )
    assert "xsec_token=private" in recovered


def test_chat_profile_keeps_access_url_internal_and_returns_public_url():
    from app.services import creator_profile_runner

    source = inspect.getsource(creator_profile_runner.run_xhs_chat_profile)
    assert 'resolved.get("_access_profile_url")' in source
    assert "profile_url = f\"https://www.xiaohongshu.com/user/profile/{creator_id}\"" in source
    assert "profile_url=catalog_profile_url" in source
    assert '"profile_url": profile_url' in source


def test_chat_profile_media_access_notes_are_internal_and_public_notes_are_sanitized():
    from app.services.creator_profile_runner import (
        _enrich_selected_notes,
        _selected_access_notes_for_media,
    )

    private_url = (
        "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        "?xsec_token=private-value&xsec_source=pc_user"
    )
    selected = [{
        "note_id": "0123456789abcdef01234567",
        "title": "video",
        "content_type": "video",
        "canonical_url": private_url,
        "pipeline_url": private_url,
    }]

    access = _selected_access_notes_for_media(selected)
    public = _enrich_selected_notes(selected, [])

    assert "xsec_token=private-value" in access[0]["access_url"]
    assert "xsec_token" not in public[0]["canonical_url"]
    assert "xsec_token" not in public[0]["pipeline_url"]


def test_catalog_does_not_upgrade_every_note_access_token():
    from app.services import xhs_local_browser

    source = inspect.getsource(xhs_local_browser._scrape_profile_on_page)
    assert "_tokenless_note_count" not in source
    assert "Catalog only needs metadata" in source


def test_selected_note_log_masks_access_token():
    from app.services.creator_feed_adapter import _safe_log_url

    masked = _safe_log_url(
        "https://www.xiaohongshu.com/explore/abc"
        "?xsec_token=private-value&xsec_source=pc_user"
    )
    assert "private-value" not in masked
    assert "xsec_token=***" in masked


def test_profile_display_name_is_recovered_from_catalog_author():
    from types import SimpleNamespace

    from app.services.creator_profile_runner import _display_name_from_catalog

    name = _display_name_from_catalog(
        [SimpleNamespace(author_name="海云日记")],
        "haiyun862",
        "haiyun862",
    )

    assert name == "海云日记"
