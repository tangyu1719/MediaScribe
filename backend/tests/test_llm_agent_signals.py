"""LLM Agent ok/reject 信号解析。"""
from __future__ import annotations

from app.services.llm_agent_signals import (
    LLM_INPUT_REJECTED,
    LLM_INPUT_TOO_SHORT,
    parse_agent_status,
)
from app.services.pipeline_output_quality import LLMInputRejectedError


def test_parse_agent_status_ok_legacy():
    status, payload = parse_agent_status({"title": "t", "summary": "s"})
    assert status == "ok"
    assert payload.get("title") == "t"


def test_parse_agent_status_reject_too_short():
    status, payload = parse_agent_status(
        {"status": "reject", "reject_code": "INPUT_TOO_SHORT", "reject_reason": "仅3字无法摘要"},
    )
    assert status == "reject"
    assert payload["reject_code"] == LLM_INPUT_TOO_SHORT
    assert "3字" in payload["reject_reason"]


def test_parse_agent_status_reject_default_code():
    status, payload = parse_agent_status({"status": "reject", "reject_reason": "空壳"})
    assert status == "reject"
    assert payload["reject_code"] == LLM_INPUT_REJECTED


def test_llm_input_rejected_error_fields():
    err = LLMInputRejectedError(LLM_INPUT_TOO_SHORT, "输入过短", reject_reason="仅平台名")
    assert err.error_code == LLM_INPUT_TOO_SHORT
    assert err.reject_reason == "仅平台名"
