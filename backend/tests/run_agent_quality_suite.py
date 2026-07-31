#!/usr/bin/env py -3
"""真实 Agent 质量回归：普通多轮、复杂工具任务与可审计 checkpoint。"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

BASE = os.environ.get("SBA_TEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MODEL = os.environ.get("LLM_MODEL_QA", "ep-20260714145952-gzmvz")
ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "backend" / "data" / "agent_eval"

NORMAL_NODES = {
    "query_rewrite": False,
    "rewrite_confirm": False,
    "slot_fill": False,
    "task_decompose": False,
    "intent_enhance": False,
    "rag_filter_confirm": False,
    "rag_filter_confirm_hitl": False,
    "rag_decision": False,
}

BASELINE_TURNS = [
    "请制定一份把本项目链接分析能力接入 Agent 的测试方案，包含目标、步骤和验收标准，先给初版。",
    "继续当前任务，把上面的验收标准量化，并补充失败后的恢复步骤。",
    "沿用刚才的约束，整合成最终版；不要另起任务，并明确列出回归测试顺序。",
]

COMPLEX_CASES = {
    "haiyun": "小红书号 haiyun862，分析这个人的最近几个视频信息并整理成报告。",
    "sft": (
        "请在小红书搜索 SFT 微调方面较权威的内容，综合点赞量和内容质量筛选约 5 篇；"
        "覆盖入门介绍、深度原理、训练框架、实践经验和知识点，并给出选择依据。"
    ),
}


def _parse_sse(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    buf = ""
    for chunk in response.iter_text():
        if not chunk:
            continue
        buf += chunk.replace("\r\n", "\n")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event_name = ""
            data_lines: list[str] = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not data_lines:
                continue
            try:
                events.append((event_name, json.loads("\n".join(data_lines))))
            except json.JSONDecodeError:
                continue
    return events


def _stream(
    client: httpx.Client,
    headers: dict[str, str],
    path: str,
    payload: dict[str, Any],
) -> tuple[list[tuple[str, dict[str, Any]]], float]:
    started = time.perf_counter()
    stop_heartbeat = threading.Event()
    session_id = str(payload.get("session_id") or payload.get("thread_id") or "unknown")

    def _emit_heartbeat() -> None:
        while not stop_heartbeat.wait(10.0):
            elapsed = round(time.perf_counter() - started, 1)
            print(
                f"AGENT_EVAL_HEARTBEAT session={session_id} path={path} elapsed_sec={elapsed}",
                flush=True,
            )

    heartbeat = threading.Thread(target=_emit_heartbeat, daemon=True)
    heartbeat.start()
    print(f"AGENT_EVAL_START session={session_id} path={path}", flush=True)
    try:
        with client.stream("POST", BASE + path, headers=headers, json=payload) as response:
            response.raise_for_status()
            events = _parse_sse(response)
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=1.0)
        elapsed = round(time.perf_counter() - started, 1)
        print(
            f"AGENT_EVAL_END session={session_id} path={path} elapsed_sec={elapsed}",
            flush=True,
        )
    return events, round(time.perf_counter() - started, 3)


def _answer(events: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(
        str(data.get("content") or data.get("delta") or "")
        for event, data in events
        if event == "answer_delta"
    ).strip()


def _answer_is_error_fallback(answer: str) -> bool:
    normalized = (answer or "").casefold()
    markers = (
        "setlimitexceeded",
        "mcp \u5de5\u5177\u94fe\u65f6\u9047\u5230\u95ee\u9898",
        "\u7cfb\u7edf\u672a\u80fd\u5b8c\u6210\u672c\u6b21\u8bf7\u6c42",
        "\u53c2\u8003\u7f16\u53f7\uff08\u67e5\u65e5\u5fd7\u7528\uff09",
    )
    return any(marker in normalized for marker in markers)


def _answer_is_usable(answer: str, *, min_chars: int = 120) -> bool:
    cleaned = (answer or "").strip()
    if len(cleaned) < min_chars or _answer_is_error_fallback(cleaned):
        return False
    unfinished_markers = (
        "我再搜索一下",
        "我将继续搜索",
        "接下来我会搜索",
        "稍后为你整理",
    )
    return not any(marker in cleaned for marker in unfinished_markers)


def _tool_result_dict(event: dict[str, Any]) -> dict[str, Any]:
    raw: Any = event.get("output_text") or ""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    nested: Any = raw.get("tool_result")
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except json.JSONDecodeError:
            nested = None
    return nested if isinstance(nested, dict) else raw


def _complex_goal_checks(
    case_id: str,
    answer: str,
    tool_events: list[dict[str, Any]],
) -> dict[str, bool]:
    """业务达成校验：防止“合规地说无法完成”被误判为用例通过。"""
    negative = (
        "无法完成",
        "无法获取",
        "未检索到",
        "未找到",
        "cdp 仍未就绪",
        "cdp 浏览器自动化不可用",
        "请启动 cdp chrome",
    )
    answer_reaches_goal = not any(marker in (answer or "").casefold() for marker in negative)

    completed_outputs = [
        str(event.get("output_text") or "")
        for event in tool_events
        if event.get("event") == "thought_step_end"
        and str(event.get("status") or "") == "completed"
    ]
    if case_id == "haiyun":
        xhs_profile_ok = False
        for event in tool_events:
            if event.get("event") != "thought_step_end":
                continue
            if "xhs_user_search" not in str(event.get("step_name") or ""):
                continue
            result = _tool_result_dict(event)
            selected = result.get("selected_notes")
            if (
                result.get("ok")
                and result.get("profile_run_id")
                and isinstance(selected, list)
                and len(selected) >= 3
            ):
                xhs_profile_ok = True
                break
        identity_present = "海云日记" in answer or "haiyun862" in answer.casefold()
        return {
            "domain_goal_reached": xhs_profile_ok
            and identity_present,
            "xhs_profile_result_present": xhs_profile_ok,
        }

    if case_id == "sft":
        joined = "\n".join(completed_outputs)
        xhs_links = set(
            re.findall(r"https?://(?:www\.)?xiaohongshu\.com/[^\s)\]\"']+", answer)
        )
        xhs_tool_evidence = "xiaohongshu" in joined.casefold() or "小红书" in joined
        coverage_terms = ("入门", "原理", "框架", "实践", "知识点")
        return {
            "domain_goal_reached": answer_reaches_goal and len(xhs_links) >= 3,
            "xhs_search_evidence_present": xhs_tool_evidence,
            "direction_coverage": sum(term in answer for term in coverage_terms) >= 3,
        }
    return {"domain_goal_reached": answer_reaches_goal}


def _last(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    return next((data for event, data in reversed(events) if event == name), {})


def _login(client: httpx.Client) -> dict[str, str]:
    response = client.post(
        BASE + "/api/auth/login",
        json={"identifier": "admin", "credential": "admin", "login_type": "password"},
    )
    response.raise_for_status()
    token = str(response.json().get("access_token") or "")
    if not token:
        raise RuntimeError("登录响应缺少 access_token")
    return {"Authorization": f"Bearer {token}"}


def _resume_interrupts(
    client: httpx.Client,
    headers: dict[str, str],
    session_id: str,
    message: str,
    initial: list[tuple[str, dict[str, Any]]],
    nodes: dict[str, bool],
) -> list[tuple[str, dict[str, Any]]]:
    events = list(initial)
    for _ in range(8):
        if any(name == "task_completed" for name, _data in events):
            break
        interrupt = _last(events, "graph_interrupt")
        if not interrupt:
            break
        checkpoint_ns = str(interrupt.get("checkpoint_ns") or interrupt.get("trace_id") or "")
        resume, _duration = _stream(
            client,
            headers,
            "/api/chat/graph/resume",
            {
                "session_id": session_id,
                "thread_id": session_id,
                "checkpoint_ns": checkpoint_ns,
                "message": message,
                "model": MODEL,
                "orch_pipeline_nodes": nodes,
                "hitl": {"action": "confirm", "checkpoint_ns": checkpoint_ns},
            },
        )
        events.extend(resume)
    return events


def _history_row(created: dict[str, Any], completed: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": created.get("task_id") or completed.get("task_id"),
        "user_query": created.get("user_query") or "",
        "query_summary": created.get("query_summary") or "",
        "task_summary": created.get("task_summary") or created.get("query_summary") or "",
        "status": completed.get("status") or created.get("status") or "resolved",
        "task_kind": created.get("task_kind") or "main",
        "result_status": completed.get("status") or "resolved",
    }


def run_baseline(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    session_id = "agent_baseline_" + uuid.uuid4().hex[:10]
    history: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    expected_task_id = ""

    for index, message in enumerate(BASELINE_TURNS, 1):
        payload = {
            "session_id": session_id,
            "message": message,
            "model": MODEL,
            "rag_prefetch": False,
            "web_search": False,
            "read_comments": False,
            "deep_think": False,
            "main_task_history": history,
            "cur_task": None,
            "orch_pipeline_nodes": NORMAL_NODES,
            "chat_max_tool_rounds": 1,
            "chat_tool_timeout_sec": 45,
            "chat_tool_max_retry": 1,
            "chat_distinct_tool_fail_limit": 2,
        }
        events, duration = _stream(client, headers, "/api/chat/stream", payload)
        events = _resume_interrupts(client, headers, session_id, message, events, NORMAL_NODES)
        names = [name for name, _data in events]
        created = _last(events, "task_created")
        completed = _last(events, "task_completed")
        intent = _last(events, "intent_resolved")
        answer = _answer(events)
        task_id = str(completed.get("task_id") or created.get("task_id") or intent.get("task_id") or "")
        summary = str(
            created.get("task_summary")
            or created.get("query_summary")
            or intent.get("task_summary")
            or intent.get("query_summary")
            or ""
        ).strip()

        errors = [data for name, data in events if name in ("stream_error", "error")]
        checks = {
            "task_completed": "task_completed" in names,
            "intent_visible": "intent_resolved" in names or any(
                name == "thought_step_end" and str(data.get("phase") or "") == "intent"
                for name, data in events
            ),
            "answer_nonempty": bool(answer),
            "answer_usable": _answer_is_usable(answer),
            "no_stream_error": not errors,
            "summary_nonempty": bool(summary),
            "query_rewrite_disabled": not any(
                name == "thought_step_end" and str(data.get("phase") or "") == "rewrite"
                for name, data in events
            ),
        }
        if index == 1:
            expected_task_id = task_id
            checks["main_task_created"] = bool(task_id)
        else:
            checks["task_id_continued"] = bool(expected_task_id and task_id == expected_task_id)

        if created and completed:
            row = _history_row(created, completed)
            existing = next((item for item in history if item.get("task_id") == row.get("task_id")), None)
            if existing:
                existing.update(row)
            else:
                history.append(row)

        turns.append(
            {
                "turn": index,
                "message": message,
                "duration_sec": duration,
                "task_id": task_id,
                "summary": summary,
                "answer_length": len(answer),
                "answer_preview": answer[:500],
                "event_counts": dict(Counter(names)),
                "checks": checks,
                "passed": all(checks.values()),
                "errors": errors[:3],
            }
        )

    checkpoints = client.get(
        BASE + f"/api/chat/checkpoints/{session_id}",
        headers=headers,
        params={"limit": 500},
    )
    checkpoints.raise_for_status()
    checkpoint_rows = checkpoints.json().get("checkpoints") or []
    checkpoint_turns = sorted({str(row.get("checkpoint_ns") or "") for row in checkpoint_rows if row.get("checkpoint_ns")})
    checkpoint_nodes = sorted({str(row.get("node") or "") for row in checkpoint_rows if row.get("node")})
    checkpoint_checks = {
        "three_turn_namespaces": len(checkpoint_turns) >= 3,
        "node_snapshots_present": len(checkpoint_rows) >= 6,
        "intent_node_present": "intent_recognition" in checkpoint_nodes,
    }
    return {
        "suite": "baseline",
        "session_id": session_id,
        "model": MODEL,
        "nodes": NORMAL_NODES,
        "turns": turns,
        "checkpoint_count": len(checkpoint_rows),
        "checkpoint_turns": checkpoint_turns,
        "checkpoint_nodes": checkpoint_nodes,
        "checkpoint_checks": checkpoint_checks,
        "passed": all(turn["passed"] for turn in turns) and all(checkpoint_checks.values()),
    }


def _affiliation_task(task_id: str, query: str, status: str = "resolved") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "user_query": query,
        "query_summary": query[:120],
        "task_summary": query[:120],
        "status": status,
        "result_status": status,
        "task_kind": "main",
    }


def run_affiliation(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    """Real-SSE branch matrix for task ownership; it is intentionally not turn-count based."""
    link_task = _affiliation_task(
        "task_aaa111aaa111",
        "把本项目链接分析能力接入 Agent，并形成测试方案与量化验收标准",
        "resolved",
    )
    recalled_task = _affiliation_task(
        "task_bbb222bbb222",
        "调研 Kubernetes Operator 调谐循环与故障恢复边界",
        "resolved",
    )
    current_task = _affiliation_task(
        "task_ccc333ccc333",
        "搜索小红书 SFT 微调资料并整理学习框架",
        "executing",
    )
    filler_history = [
        _affiliation_task(
            f"task_{index:012x}",
            f"第 {index} 个互不相关的历史主题：内部组件 {index} 的运行说明",
            "resolved",
        )
        for index in range(1, 23)
    ]
    full_history = [link_task, recalled_task, *filler_history, current_task]
    cases = [
        {
            "case": "same_topic_continue",
            "message": "继续当前的 SFT 微调资料任务，把评测框架补成表格",
            "cur_task": current_task,
            "history": full_history,
            "expected_action": "continue_main",
            "expected_task_id": current_task["task_id"],
        },
        {
            "case": "generic_progress_uses_pointer",
            "message": "现在执行到哪了？把当前进度和下一步告诉我",
            "cur_task": current_task,
            "history": full_history,
            "expected_action": "continue_main",
            "expected_task_id": current_task["task_id"],
        },
        {
            "case": "unrelated_goal_creates_new_main",
            "message": "设计 Redis Sentinel 高可用部署方案，并给出故障切换检查表",
            "cur_task": current_task,
            "history": full_history,
            "expected_action": "new_main",
            "expected_task_id": "",
        },
        {
            "case": "meta_question_stays_simple",
            "message": "你是谁，你能做什么？",
            "cur_task": current_task,
            "history": full_history,
            "expected_action": "simple",
            "expected_task_id": "",
        },
        {
            "case": "recall_early_history_after_many_tasks",
            "message": "继续之前的 Kubernetes Operator 调研，补充调谐循环的幂等性边界",
            "cur_task": current_task,
            "history": full_history,
            "expected_action": "continue_main",
            "expected_task_id": recalled_task["task_id"],
        },
        {
            "case": "modify_completed_early_deliverable",
            "message": "继续之前的链接分析接入测试方案，把量化验收标准改成表格",
            "cur_task": current_task,
            "history": full_history,
            "expected_action": "continue_main",
            "expected_task_id": link_task["task_id"],
        },
        {
            "case": "explicit_task_id_beats_pointer",
            "message": f"恢复 {link_task['task_id']}，继续修改验收表格",
            "cur_task": current_task,
            "history": full_history,
            "expected_action": "continue_main",
            "expected_task_id": link_task["task_id"],
        },
    ]

    rows: list[dict[str, Any]] = []
    for spec in cases:
        session_id = f"agent_aff_{spec['case']}_" + uuid.uuid4().hex[:8]
        events, duration = _stream(
            client,
            headers,
            "/api/chat/stream",
            {
                "session_id": session_id,
                "message": spec["message"],
                "model": MODEL,
                "rag_prefetch": False,
                "web_search": False,
                "read_comments": False,
                "deep_think": False,
                "cur_task": spec["cur_task"],
                "main_task_history": spec["history"],
                "orch_pipeline_nodes": NORMAL_NODES,
                "chat_max_tool_rounds": 1,
                "chat_tool_timeout_sec": 45,
                "chat_tool_max_retry": 0,
                "chat_distinct_tool_fail_limit": 1,
            },
        )
        intent = _last(events, "intent_resolved")
        action = str(intent.get("task_action") or "")
        task_id = str(intent.get("task_id") or "")
        expected_task_id = str(spec["expected_task_id"] or "")
        checks = {
            "intent_event_present": bool(intent),
            "task_action_matches": action == spec["expected_action"],
        }
        if spec["expected_action"] == "new_main":
            checks["new_task_id_created"] = bool(task_id and task_id != current_task["task_id"])
        elif spec["expected_action"] == "simple":
            checks["simple_has_no_main_binding"] = not task_id and intent.get("is_simple") is True
        else:
            checks["task_id_matches"] = task_id == expected_task_id
        rows.append(
            {
                "case": spec["case"],
                "session_id": session_id,
                "message": spec["message"],
                "history_size": len(spec["history"]),
                "duration_sec": duration,
                "expected_action": spec["expected_action"],
                "expected_task_id": expected_task_id,
                "actual_action": action,
                "actual_task_id": task_id,
                "intent_reason": intent.get("intent_reason") or "",
                "event_counts": dict(Counter(name for name, _data in events)),
                "stream_errors": [data for name, data in events if name in ("stream_error", "error")][:3],
                "checks": checks,
                "passed": all(checks.values()),
            }
        )
    return {
        "suite": "affiliation",
        "model": MODEL,
        "definition": "Branch-matrix validation across current pointer, full history, completed tasks, explicit IDs and simple meta intent.",
        "cases": rows,
        "passed": all(row["passed"] for row in rows),
    }


def run_complex(client: httpx.Client, headers: dict[str, str], case_ids: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    nodes = dict(NORMAL_NODES)
    for case_id in case_ids:
        message = COMPLEX_CASES[case_id]
        session_id = f"agent_{case_id}_" + uuid.uuid4().hex[:10]
        events, duration = _stream(
            client,
            headers,
            "/api/chat/stream",
            {
                "session_id": session_id,
                "message": message,
                "model": MODEL,
                "rag_prefetch": False,
                "web_search": True,
                "read_comments": False,
                "deep_think": True,
                "orch_pipeline_nodes": nodes,
                "chat_max_tool_rounds": 12,
                # 人物画像包含目录拉取、两次 LLM 和 3-5 篇轻量网页采样；
                # 90s 会把有进展的真实链路误判为工具超时。
                "chat_tool_timeout_sec": 300,
                "chat_tool_max_retry": 2,
                "chat_distinct_tool_fail_limit": 3,
            },
        )
        events = _resume_interrupts(client, headers, session_id, message, events, nodes)
        names = [name for name, _data in events]
        tool_events = [
            {"event": name, **data}
            for name, data in events
            if name in ("tool_call_start", "tool_call_end", "tool_call_failed")
            or (
                name in ("thought_step_start", "thought_step_end")
                and (
                    str(data.get("node_kind") or "") == "tool_call"
                    or str(data.get("phase") or "") in ("tool", "web", "rag")
                )
            )
        ]
        errors = [data for name, data in events if name in ("stream_error", "error")]
        answer = _answer(events)
        checks = {
            "task_completed": "task_completed" in names,
            "answer_nonempty": bool(answer),
            "answer_usable": _answer_is_usable(answer),
            "tool_attempt_visible": bool(tool_events),
            "failure_is_evidenced": not errors or all(
                data.get("error_code") or data.get("message") or data.get("error") for data in errors
            ),
            **_complex_goal_checks(case_id, answer, tool_events),
        }
        rows.append(
            {
                "case": case_id,
                "session_id": session_id,
                "query": message,
                "duration_sec": duration,
                "answer_length": len(answer),
                "answer_preview": answer[:1200],
                "event_counts": dict(Counter(names)),
                "tool_events": tool_events,
                "errors": errors[:5],
                "checks": checks,
                "passed": all(checks.values()),
            }
        )
    return {"suite": "complex", "model": MODEL, "cases": rows, "passed": all(row["passed"] for row in rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("baseline", "affiliation", "complex"), default="baseline")
    parser.add_argument("--cases", default="haiyun,sft")
    args = parser.parse_args()

    with httpx.Client(timeout=httpx.Timeout(900.0, connect=15.0)) as client:
        headers = _login(client)
        if args.suite == "baseline":
            result = run_baseline(client, headers)
        elif args.suite == "affiliation":
            result = run_affiliation(client, headers)
        else:
            requested = [item.strip() for item in args.cases.split(",") if item.strip()]
            unknown = [item for item in requested if item not in COMPLEX_CASES]
            if unknown:
                raise SystemExit(f"未知复杂用例: {unknown}")
            result = run_complex(client, headers, requested)

    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{args.suite}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "suite": args.suite, "report": str(report_path)}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
