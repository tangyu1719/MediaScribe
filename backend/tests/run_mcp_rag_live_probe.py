#!/usr/bin/env py -3
"""对运行中后端发起真实 MCP 检索 SSE 探测（含 HITL resume），结果写入 reports/。"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports"
QUERY = "搜索知识库中关于MCP技术相关的文档进行总结反馈"
BASE = "http://127.0.0.1:8000"


def _parse_blocks(raw_buf: str) -> Tuple[List[Tuple[str, dict]], str]:
    events: List[Tuple[str, dict]] = []
    while "\n\n" in raw_buf:
        block, raw_buf = raw_buf.split("\n\n", 1)
        ev = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if data:
            try:
                events.append((ev, json.loads(data)))
            except json.JSONDecodeError:
                pass
    return events, raw_buf


def _collect_sse(
    client: httpx.Client,
    path: str,
    body: dict,
    headers: dict,
    *,
    max_idle_rounds: int = 1,
) -> List[Tuple[str, dict]]:
    out: List[Tuple[str, dict]] = []
    raw_buf = ""
    with client.stream("POST", f"{BASE}{path}", json=body, headers=headers) as resp:
        if resp.status_code != 200:
            err_body = resp.read().decode("utf-8", errors="replace")[:800]
            out.append(("stream_error", {"message": f"HTTP {resp.status_code}: {err_body}"}))
            return out
        for chunk in resp.iter_text():
            if not chunk:
                continue
            raw_buf += chunk
            batch, raw_buf = _parse_blocks(raw_buf)
            out.extend(batch)
            if any(e in ("task_completed", "stream_error") for e, _ in out):
                return out
    if raw_buf.strip():
        batch, _ = _parse_blocks(raw_buf + "\n\n")
        out.extend(batch)
    return out


def main() -> int:
    sid = "probe_mcp_" + uuid.uuid4().hex[:10]
    body = {
        "session_id": sid,
        "message": QUERY,
        "rag_prefetch": True,
        "web_search": False,
        "orch_pipeline_nodes": {
            "intent_enhance": False,
            "rewrite_confirm": False,
            "rag_filter_confirm": True,
            "rag_decision": True,
        },
    }
    events: List[Tuple[str, dict]] = []

    with httpx.Client(timeout=httpx.Timeout(900.0, connect=15.0)) as client:
        login = client.post(
            f"{BASE}/api/auth/login",
            json={"identifier": "admin", "credential": "admin", "login_type": "password"},
        )
        if login.status_code != 200:
            print("login failed", login.status_code, login.text[:300])
            return 1
        token = login.json().get("access_token") or ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            events.extend(_collect_sse(client, "/api/chat/stream", body, headers))
        except httpx.ReadTimeout as ex:
            events.append(("stream_error", {"message": f"stream_timeout: {ex}"}))

        for _ in range(4):
            if any(e == "task_completed" for e, _ in events):
                break
            if any(e == "stream_error" for e, _ in events):
                break
            if not any(e == "graph_interrupt" for e, _ in events):
                break
            resume_body = {
                "session_id": sid,
                "thread_id": sid,
                "message": QUERY,
                "rag_prefetch": True,
                "web_search": False,
                "hitl": {"action": "confirm"},
                "orch_pipeline_nodes": body["orch_pipeline_nodes"],
            }
            try:
                events.extend(
                    _collect_sse(client, "/api/chat/graph/resume", resume_body, headers)
                )
            except httpx.ReadTimeout as ex:
                events.append(("stream_error", {"message": f"resume_timeout: {ex}"}))
                break

    names = [e for e, _ in events]
    rag_steps = [
        d
        for e, d in events
        if e == "thought_step_end" and str(d.get("phase") or "").lower() == "rag_decision"
    ]
    slice_ev = [d for e, d in events if e == "rag_prefetch_slices"]
    errors = [d for e, d in events if e == "stream_error"]

    report: Dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": sid,
        "query": QUERY,
        "event_count": len(events),
        "event_names": sorted(set(names)),
        "has_stream_error": bool(errors),
        "stream_errors": errors[:3],
        "rag_decision_step_count": len(rag_steps),
        "rag_prefetch_slices_events": len(slice_ev),
        "slice_count_in_event": (slice_ev[0].get("slice_count") if slice_ev else 0),
        "sample_slice_titles": [
            (slice_ev[0].get("slices") or [{}])[0].get("title") if slice_ev else None
        ],
        "sub_indices": sorted(
            {
                int(d.get("sub_index") or 0)
                for e, d in events
                if e == "thought_step_end" and d.get("sub_index") is not None
            }
        ),
        "phases": sorted(
            {
                str(d.get("phase") or "")
                for e, d in events
                if e == "thought_step_end" and d.get("phase")
            }
        ),
        "has_answer_delta": "answer_delta" in names,
        "has_task_completed": "task_completed" in names,
    }
    if rag_steps:
        try:
            out_j = json.loads(rag_steps[-1].get("output_text") or "{}")
            report["rag_slices_in_output"] = len(out_j.get("rag_slices") or [])
        except Exception:
            report["rag_slices_in_output"] = 0

    REPORTS.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS / f"mcp_rag_live_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("written:", out_path)

    if errors:
        return 2
    if not rag_steps:
        return 3
    if not report.get("has_task_completed") and not report.get("has_answer_delta"):
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
