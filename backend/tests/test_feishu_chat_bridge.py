"""飞书 Agent 桥接单元测试。"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def map_path(monkeypatch):
    root = Path(__file__).resolve().parents[1] / ".pytest_tmp" / "feishu_bridge"
    root.mkdir(parents=True, exist_ok=True)
    p = root / "feishu_im_sessions.json"
    monkeypatch.setattr("app.services.feishu_chat_bridge._MAP_PATH", p)
    monkeypatch.setattr("app.services.feishu_chat_bridge._ROOT", root)
    return p


def test_build_feishu_session_title():
    from app.services.feishu_chat_bridge import build_feishu_session_title

    assert build_feishu_session_title("你好，帮我查一下知识库") == "飞书聊天-你好，帮我查一下知识库"
    long = "这是一段很长的问题" * 5
    title = build_feishu_session_title(long)
    assert title.startswith("飞书聊天-")
    assert "…" in title


def test_parse_sse_block_answer():
    from app.services.feishu_chat_bridge import parse_sse_block

    block = 'event: answer_delta\ndata: {"content":"你好"}\n\n'
    ev, data = parse_sse_block(block.strip())
    assert ev == "answer_delta"
    assert data.get("content") == "你好"


@pytest.mark.asyncio
async def test_aggregate_chat_stream_to_text():
    from app.services.feishu_chat_bridge import aggregate_chat_stream_to_text

    async def _fake():
        yield 'event: answer_delta\ndata: {"content":"A"}\n\n'
        yield 'event: answer_end\ndata: {"full_text":"AB"}\n\n'

    text, meta = await aggregate_chat_stream_to_text(_fake())
    assert text == "AB"
    assert meta.get("errors") == []


def test_resolve_or_create_session_new(map_path):
    import uuid

    from app.services.feishu_chat_bridge import get_feishu_session_mapping, resolve_or_create_session

    chat_id = f"oc_test_chat_{uuid.uuid4().hex[:8]}"
    sid, title, is_new = resolve_or_create_session(chat_id, "第一条消息")
    assert is_new is True
    assert sid.startswith("sess_")
    assert title == "飞书聊天-第一条消息"
    mapping = get_feishu_session_mapping(chat_id)
    assert mapping["active_session_id"] == sid


@pytest.mark.asyncio
async def test_create_handoff_session_transfers_summary(map_path, monkeypatch):
    from app.services.chat_session_store import persist_session
    from app.services.feishu_chat_bridge import create_handoff_session, get_feishu_session_mapping

    chat_dir = Path(__file__).resolve().parents[1] / ".pytest_tmp" / "feishu_bridge" / "chat_sessions"
    chat_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.chat_session_store._CHAT_DIR", chat_dir)
    monkeypatch.setattr("app.services.chat_session_store._INDEX_PATH", chat_dir.parent / "index.json")

    old_sid = "sess_old_handoff_test"

    persist_session(
        old_sid,
        {"id": old_sid, "title": "飞书聊天-旧会话", "status": "archived"},
        [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ],
        memory_meta={"summary_text": "先前会话摘要内容", "mode": "summary"},
        mark_dirty=False,
    )

    new_sid = await create_handoff_session(
        old_sid,
        chat_id="oc_handoff",
        title="飞书聊天-旧会话",
    )
    assert new_sid != old_sid
    from app.services.chat_session_store import get_session_document

    new_doc = get_session_document(new_sid) or {}
    assert new_doc.get("memory_meta", {}).get("summary_text") == "先前会话摘要内容"
    assert len(new_doc.get("messages") or []) >= 2
    assert get_feishu_session_mapping("oc_handoff")["active_session_id"] == new_sid
