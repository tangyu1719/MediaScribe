"""Eval 评测运维层 API 测试。"""
from __future__ import annotations

import pytest

from app.eval.references import list_references, load_reference  # noqa: E402
from app.eval.trajectory_eval import evaluate_trajectory, messages_from_span_steps  # noqa: E402


def test_eval_status_extended(api_client, admin_headers):
    r = api_client.get("/api/eval/status", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    data = body.get("data") or {}
    assert "eval_enabled" in data
    assert "packages" in data
    assert isinstance(data.get("packages"), dict)


def test_eval_overview(api_client, admin_headers):
    r = api_client.get("/api/eval/overview", headers=admin_headers)
    assert r.status_code == 200
    data = r.json().get("data") or {}
    assert "capabilities" in data
    assert "tracing" in data


def test_eval_references(api_client, admin_headers):
    r = api_client.get("/api/eval/references", headers=admin_headers)
    assert r.status_code == 200
    refs = r.json().get("data", {}).get("references") or []
    assert any(x.get("id") == "tool_then_answer" for x in refs)


def test_load_reference_fixture():
    loaded = load_reference("tool_then_answer")
    assert loaded.get("ok") is True
    assert len(loaded.get("reference_outputs") or []) >= 1


def test_messages_from_span_steps():
    steps = [
        {
            "step_id": "call_demo",
            "step_type": "tool_call",
            "step_name": "kb_search",
            "input_payload": {"tool": "kb_search", "query": "test"},
            "output_payload": {"summary": "ok"},
        },
        {
            "step_type": "llm_call",
            "output_summary": "回答用户",
        },
    ]
    msgs = messages_from_span_steps(steps)
    assert len(msgs) >= 2
    assert msgs[0].get("tool_calls")


def test_trajectory_strict_api(api_client, admin_headers):
    outputs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "kb_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "assistant", "content": "结果"},
    ]
    reference = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "kb_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "assistant", "content": "期望"},
    ]
    r = api_client.post(
        "/api/eval/trajectory/strict",
        headers=admin_headers,
        json={"outputs": outputs, "reference_outputs": reference},
    )
    assert r.status_code == 200
    body = r.json()
    if body.get("skipped"):
        pytest.skip("agentevals 未安装")
    assert body.get("ok") is True
    assert body.get("score") is True


def test_trajectory_run_modes(api_client, admin_headers):
    outputs = [{"role": "assistant", "content": "a"}]
    ref = [{"role": "assistant", "content": "b"}]
    r = api_client.post(
        "/api/eval/trajectory/run",
        headers=admin_headers,
        json={"outputs": outputs, "reference_outputs": ref, "mode": "unordered"},
    )
    assert r.status_code == 200
    body = r.json()
    if body.get("skipped"):
        pytest.skip("agentevals 未安装")
    assert "score" in body


def test_trajectory_from_span_missing_task(api_client, admin_headers):
    r = api_client.post(
        "/api/eval/trajectory/from-span/nonexistent_task_xyz",
        headers=admin_headers,
        json={},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False or body.get("eval_skipped")


def test_eval_unit_strict_match():
    outputs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ],
        }
    ]
    ref = list(outputs)
    result = evaluate_trajectory(outputs, ref, mode="strict")
    if result.get("skipped"):
        pytest.skip("agentevals 未安装")
    assert result.get("score") is True


def test_eval_list_references_module():
    refs = list_references()
    assert len(refs) >= 1
