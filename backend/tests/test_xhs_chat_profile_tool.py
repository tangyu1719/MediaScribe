"""xhs_user_search 应走五阶段画像，不得将 profile URL 提交 link_pipeline。"""
from __future__ import annotations

import json
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


def test_xhs_user_search_source_no_profile_pipeline_enqueue():
    import inspect

    from app.services import chat_tool_registry

    src = inspect.getsource(chat_tool_registry.build_internal_chat_tools)
    # 旧错误路径：profile URL → reuse_or_enqueue_task
    assert "reuse_or_enqueue_task(plat, profile_url" not in src
