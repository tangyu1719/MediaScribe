"""会话落盘瘦身：thinking/span 不得撑爆 JSON 与 token 估算。"""
from __future__ import annotations

import json

from app.services.chat_context_memory import (
    context_usage,
    estimate_messages_tokens,
    guard_session_payload_for_persist,
    normalize_session_document_for_storage,
    session_document_has_full_orchestration_io,
    slim_message_for_storage,
    session_doc_byte_size,
)


def test_slim_strips_orchestration_io():
    bloated = {
        "role": "assistant",
        "content": "回答正文",
        "thinking": [
            {
                "step_id": "s1",
                "step_name": "意图识别",
                "status": "done",
                "input_text": "x" * 5000,
                "output_text": "y" * 8000,
                "think_text": "z" * 3000,
                "result_brief": "识别为复杂任务",
            }
        ],
        "span": {"task_id": "task_abc", "trace_id": "trace_1", "search_results": [{"a": 1}] * 50},
    }
    slim = slim_message_for_storage(bloated)
    raw = json.dumps(slim, ensure_ascii=False)
    assert len(raw) < len(json.dumps(bloated, ensure_ascii=False)) // 4
    assert "input_text" not in raw
    assert "output_text" not in raw
    th0 = (slim.get("thinking") or [None])[0] or {}
    assert th0.get("what") or th0.get("result")
    assert "status" not in raw or "what" in raw
    assert slim["span"].get("task_id") == "task_abc"
    assert "search_results" not in slim.get("span", {})


def test_token_estimate_includes_thinking_not_only_content():
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "ok",
            "thinking": [{"step_name": "a", "output_text": "b" * 4000}],
        },
    ]
    tok = estimate_messages_tokens(msgs)
    assert tok > len("hi") // 2 + len("ok") // 2


def test_guard_caps_message_count():
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(100)]
    slim, _, _ = guard_session_payload_for_persist(msgs, None, [])
    assert len(slim) <= 40


def test_context_usage_triggers_compress_on_bloated_doc():
    doc = {
        "messages": [
            {
                "role": "assistant",
                "content": "x",
                "thinking": [{"output_text": "z" * 20000, "step_name": "s"}],
            }
        ]
        * 20,
        "memory_meta": {},
        "prefs": {"chatPrefs": {"contextMaxTokens": 128000, "contextWarnPct": 80}},
    }
    usage = context_usage(doc)
    assert usage["should_pre_summarize"] is True


def test_normalize_strips_orchestration_io():
    doc = {
        "messages": [
            {
                "role": "assistant",
                "content": "ok",
                "thinking": [{"step_name": "s", "input_text": "a" * 3000, "result_brief": "done"}],
                "span": {"task_id": "task_x", "search_results": [{"n": 1}]},
            }
        ],
        "cur_task": {"task_id": "task_x", "steps": [{"output_text": "y" * 5000}]},
    }
    assert session_document_has_full_orchestration_io(doc) is True
    normalized, changed = normalize_session_document_for_storage(doc)
    assert changed is True
    assert session_document_has_full_orchestration_io(normalized) is False
    raw = json.dumps(normalized, ensure_ascii=False)
    assert "input_text" not in raw
    assert "search_results" not in raw
    assert normalized["messages"][0]["span"]["task_id"] == "task_x"
