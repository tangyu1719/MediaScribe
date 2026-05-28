"""AI 问答 SSE 回归共用：解析 event-stream、校验事件序列与 JSON schema。"""
from __future__ import annotations

import json
from typing import Any, Iterable, List, Tuple

REQUIRED_EVENTS = CORE_EVENTS = [
    "stream_open",
    "thinking_start",
    "thought_step_start",
    "thought_step_end",
    "answer_start",
    "answer_delta",
    "answer_end",
    "task_completed",
]

SSE_PROBE_MESSAGE = "测试SSE是否完整输出，请按步骤展示思考、回答和任务状态。"


def parse_sse_blocks(raw: str) -> List[Tuple[str, dict]]:
    """将 SSE 文本解析为 (event_name, data_dict) 列表。"""
    events: List[Tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        ev = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if not data:
            continue
        try:
            events.append((ev, json.loads(data)))
        except json.JSONDecodeError:
            pass
    return events


def iter_sse_from_response(resp) -> List[Tuple[str, dict]]:
    """从 TestClient stream 响应收集事件，见到 task_completed 后停止。"""
    events: List[Tuple[str, dict]] = []
    buf = ""
    for chunk in resp.iter_bytes():
        if not chunk:
            continue
        buf += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            for ev, data in parse_sse_blocks(block + "\n\n"):
                events.append((ev, data))
            if any(e == "task_completed" for e, _ in events):
                return events
    if buf.strip():
        events.extend(parse_sse_blocks(buf))
    return events


def assert_required_events(events: List[Tuple[str, dict]]) -> None:
    names = [ev for ev, _ in events]
    missing = [x for x in CORE_EVENTS if x not in names]
    assert not missing, f"缺少 SSE 事件: {missing}"
    completed = next((d for ev, d in events if ev == "task_completed"), {}) or {}
    if completed.get("persist_main_task"):
        assert "task_created" in names, "复杂任务须含 task_created"
        assert "answer_preface" in names, "复杂任务须含 answer_preface"
    else:
        assert "task_created" not in names, "简单对话不得创建主任务"
        assert not any(
            ev == "thought_step_end" and "答案组织" in str(d.get("step_name", ""))
            for ev, d in events
        ), "不得出现独立的「答案组织」步骤"


def assert_simple_greeting_flow(events: List[Tuple[str, dict]]) -> None:
    """简单问候：意图识别 + LLM，步骤组序号递增。"""
    ends = [d for ev, d in events if ev == "thought_step_end"]
    phases = [str(d.get("phase") or "") for d in ends]
    assert "intent" in phases, "须有意图识别步骤"
    assert "llm" in phases, "须有 LLM 步骤"
    assert "reason" not in phases, "不得有 reason/答案组织"
    indices = sorted(
        int(d["sub_index"])
        for d in ends
        if d.get("sub_index") is not None and str(d.get("sub_index")).isdigit()
    )
    if len(indices) >= 2:
        assert indices == sorted(set(indices)), f"步骤组序号应递增: {indices}"


def assert_llm_step_after_answer(events: List[Tuple[str, dict]]) -> None:
    names = [ev for ev, _ in events]
    llm_idxs = [
        i
        for i, (ev, d) in enumerate(events)
        if ev == "thought_step_start" and "LLM" in str(d.get("step_name", ""))
    ]
    ans_idx = next((i for i, ev in enumerate(names) if ev == "answer_start"), -1)
    if llm_idxs and ans_idx >= 0:
        assert min(llm_idxs) >= ans_idx, "LLM thought_step_start 不得出现在 answer_start 之前"


def assert_thought_step_json_schema(events: List[Tuple[str, dict]], min_steps: int = 1) -> int:
    """thought_step_end 的 output_text 须为统一 tool schema JSON。"""
    json_steps = 0
    for ev, d in events:
        if ev != "thought_step_end":
            continue
        raw = d.get("output_text") or ""
        if not raw:
            continue
        obj = json.loads(raw) if isinstance(raw, str) else raw
        assert "tool_call" in obj, f"缺少 tool_call: {obj!r}"
        assert "schema_version" in obj, f"缺少 schema_version: {obj!r}"
        json_steps += 1
    assert json_steps >= min_steps, "未收到带 JSON schema 的 thought_step_end"
    return json_steps


def assert_task_completed(events: List[Tuple[str, dict]]) -> dict:
    completed = next((d for ev, d in events if ev == "task_completed"), None)
    assert completed is not None, "缺少 task_completed"
    assert completed.get("status") is not None, "task_completed 无 status"
    return completed
