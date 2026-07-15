"""SSE 思考/结果分片流式回归 — 对齐 HaiChiAgent token 流式 UX。"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("CHAT_GRAPH_AUTO_HITL", "1")
os.environ.setdefault("CHAT_USE_LANGGRAPH", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.main import app
from tests.sse_regress_lib import (
    assert_answer_streaming,
    assert_required_events,
    assert_step_think_multi_delta,
    assert_task_completed,
    iter_sse_from_response,
)


@pytest.fixture(scope="module")
def client():
    c = TestClient(app)
    r = c.post(
        "/api/auth/login",
        json={"identifier": "admin", "credential": "admin", "login_type": "password"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return c, {"Authorization": f"Bearer {token}"}


def test_sse_think_and_answer_streaming(client):
    c, headers = client
    body = {
        "session_id": "sse_think_stream_regress",
        "message": "你好，用一句话介绍你能做什么",
        "web_search": False,
        "rag_prefetch": False,
        "deep_think": False,
    }
    with c.stream("POST", "/api/chat/stream", json=body, headers=headers, timeout=120.0) as resp:
        assert resp.status_code == 200, resp.text
        events = iter_sse_from_response(resp)
    assert_required_events(events)
    assert_step_think_multi_delta(events, min_deltas=2)
    assert_answer_streaming(events)
    assert_task_completed(events)
