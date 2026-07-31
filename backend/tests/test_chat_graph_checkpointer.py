"""LangGraph 节点快照的持久化、隔离与显式清理回归。"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import END, START, StateGraph

from app.services.chat_graph_checkpointer import (
    checkpoint_thread_id,
    clear_session_checkpointer,
    get_session_checkpointer,
    list_session_checkpoints,
    reset_checkpointer_for_tests,
)
from app.services.chat_graph_runner import persist_tool_execution_checkpoint


class _State(TypedDict, total=False):
    value: str
    orchestration_phase: str
    graph_route: str
    task_id: str


def _graph():
    builder = StateGraph(_State)
    builder.add_node(
        "intent_node",
        lambda state: {
            "value": state.get("value", "") + "-intent",
            "orchestration_phase": "intent",
            "graph_route": "plan",
        },
    )
    builder.add_node(
        "plan_node",
        lambda state: {
            "value": state["value"] + "-plan",
            "orchestration_phase": "plan",
            "graph_route": "done",
        },
    )
    builder.add_edge(START, "intent_node")
    builder.add_edge("intent_node", "plan_node")
    builder.add_edge("plan_node", END)
    return builder.compile(checkpointer=get_session_checkpointer("unused"))


def _cfg(session_id: str, checkpoint_ns: str):
    return {
        "configurable": {
            "thread_id": checkpoint_thread_id(session_id, checkpoint_ns),
            "checkpoint_ns": "",
        }
    }


def test_node_checkpoints_persist_and_namespaces_are_isolated(tmp_path, monkeypatch):
    db = tmp_path / "checkpoints.sqlite3"
    monkeypatch.setenv("SBA_CHAT_CHECKPOINT_DB", str(db))
    reset_checkpointer_for_tests()
    try:
        graph = _graph()
        asyncio.run(graph.ainvoke({"value": "one", "task_id": "task-one"}, _cfg("session-a", "trace-one")))
        asyncio.run(graph.ainvoke({"value": "two", "task_id": "task-two"}, _cfg("session-a", "trace-two")))

        first = list_session_checkpoints("session-a", checkpoint_ns="trace-one")
        second = list_session_checkpoints("session-a", checkpoint_ns="trace-two")
        assert len(first) >= 4
        assert len(second) >= 4
        assert {row["checkpoint_ns"] for row in first} == {"trace-one"}
        assert {row["checkpoint_ns"] for row in second} == {"trace-two"}
        assert {row["task_id"] for row in first if row["task_id"]} == {"task-one"}
        assert {row["task_id"] for row in second if row["task_id"]} == {"task-two"}
        assert {row["node"] for row in first} >= {"intent_node", "plan_node"}

        reset_checkpointer_for_tests()
        reopened = list_session_checkpoints("session-a", checkpoint_ns="trace-one")
        assert len(reopened) == len(first)
        assert db.is_file()

        clear_session_checkpointer("session-a")
        assert list_session_checkpoints("session-a") == []
    finally:
        reset_checkpointer_for_tests()


def test_explicit_clear_does_not_delete_other_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("SBA_CHAT_CHECKPOINT_DB", str(tmp_path / "isolated.sqlite3"))
    reset_checkpointer_for_tests()
    try:
        graph = _graph()
        asyncio.run(graph.ainvoke({"value": "a"}, _cfg("session-a", "turn")))
        asyncio.run(graph.ainvoke({"value": "b"}, _cfg("session-b", "turn")))
        clear_session_checkpointer("session-a")
        assert list_session_checkpoints("session-a") == []
        assert list_session_checkpoints("session-b")
    finally:
        reset_checkpointer_for_tests()


def test_long_tool_wait_and_resume_are_checkpointed(tmp_path, monkeypatch):
    monkeypatch.setenv("SBA_CHAT_CHECKPOINT_DB", str(tmp_path / "tool-wait.sqlite3"))
    reset_checkpointer_for_tests()
    try:
        wait_ok = asyncio.run(
            persist_tool_execution_checkpoint(
                session_id="session-tool",
                checkpoint_ns="trace-tool",
                task_id="task-tool",
                trace_id="trace-tool",
                tool_name="link_pipeline_start",
                pipeline_task_ids=["pipe-1"],
                status="tool_calling",
            )
        )
        resume_ok = asyncio.run(
            persist_tool_execution_checkpoint(
                session_id="session-tool",
                checkpoint_ns="trace-tool",
                task_id="task-tool",
                trace_id="trace-tool",
                tool_name="link_pipeline_start",
                pipeline_task_ids=["pipe-1"],
                status="resumed",
            )
        )
        assert wait_ok and resume_ok
        rows = list_session_checkpoints("session-tool", checkpoint_ns="trace-tool")
        statuses = {
            (row.get("tool_wait_checkpoint") or {}).get("status") for row in rows
        }
        assert {"tool_calling", "resumed"}.issubset(statuses)
        assert any(row.get("orchestration_phase") == "waiting_observation" for row in rows)
    finally:
        reset_checkpointer_for_tests()


def test_concurrent_tool_checkpoints_are_isolated_by_turn_namespace(tmp_path, monkeypatch):
    """同一会话并发工具不能互相覆盖 task、trace 或恢复状态。"""
    monkeypatch.setenv("SBA_CHAT_CHECKPOINT_DB", str(tmp_path / "concurrent.sqlite3"))
    reset_checkpointer_for_tests()
    try:
        async def _write_pair(namespace: str, task_id: str, pipeline_id: str):
            await asyncio.gather(
                persist_tool_execution_checkpoint(
                    session_id="session-concurrent",
                    checkpoint_ns=namespace,
                    task_id=task_id,
                    trace_id=namespace,
                    tool_name="xhs_user_search",
                    pipeline_task_ids=[pipeline_id],
                    status="tool_calling",
                ),
                persist_tool_execution_checkpoint(
                    session_id="session-concurrent",
                    checkpoint_ns=namespace,
                    task_id=task_id,
                    trace_id=namespace,
                    tool_name="xhs_user_search",
                    pipeline_task_ids=[pipeline_id],
                    status="resumed",
                ),
            )

        async def _run_concurrent_pairs():
            await asyncio.gather(
                _write_pair("trace-a", "task-a", "pipe-a"),
                _write_pair("trace-b", "task-b", "pipe-b"),
            )

        asyncio.run(_run_concurrent_pairs())

        rows_a = list_session_checkpoints("session-concurrent", checkpoint_ns="trace-a")
        rows_b = list_session_checkpoints("session-concurrent", checkpoint_ns="trace-b")
        assert rows_a and rows_b
        assert {row["task_id"] for row in rows_a if row["task_id"]} == {"task-a"}
        assert {row["task_id"] for row in rows_b if row["task_id"]} == {"task-b"}
        assert {row["checkpoint_ns"] for row in rows_a} == {"trace-a"}
        assert {row["checkpoint_ns"] for row in rows_b} == {"trace-b"}
    finally:
        reset_checkpointer_for_tests()
