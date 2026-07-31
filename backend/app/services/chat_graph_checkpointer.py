"""Persistent LangGraph checkpoints, isolated by session and turn namespace."""
from __future__ import annotations

import os
import sqlite3
import asyncio
from pathlib import Path
from threading import RLock
from typing import Any, AsyncIterator, Dict, Optional, Sequence

from langgraph.checkpoint.sqlite import SqliteSaver


class _AsyncCompatibleSqliteSaver(SqliteSaver):
    """Expose SqliteSaver's synchronized operations to LangGraph async runs."""

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config,
        *,
        filter=None,
        before=None,
        limit=None,
    ) -> AsyncIterator[Any]:
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            yield row

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

_lock = RLock()
_checkpointer: Optional[_AsyncCompatibleSqliteSaver] = None
_connection: Optional[sqlite3.Connection] = None
_active_db_path = ""
_TURN_SEPARATOR = "::sba-turn::"


def checkpoint_thread_id(session_id: str, checkpoint_ns: str = "") -> str:
    """Map the public session/turn pair to LangGraph's top-level thread id.

    LangGraph 1.x reserves ``checkpoint_ns`` for subgraphs and clears it for a
    top-level graph, so turn isolation must live in ``thread_id``.
    """
    sid = (session_id or "default").strip() or "default"
    turn = str(checkpoint_ns or "").strip()
    return f"{sid}{_TURN_SEPARATOR}{turn}" if turn else sid


def _split_checkpoint_thread_id(value: str) -> tuple[str, str]:
    raw = str(value or "")
    if _TURN_SEPARATOR not in raw:
        return raw, ""
    return tuple(raw.split(_TURN_SEPARATOR, 1))  # type: ignore[return-value]


def checkpoint_db_path() -> Path:
    configured = str(os.environ.get("SBA_CHAT_CHECKPOINT_DB") or "").strip()
    path = Path(configured).resolve() if configured else Path(__file__).resolve().parents[2] / "data" / "chat_graph" / "checkpoints.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _close_current() -> None:
    global _checkpointer, _connection, _active_db_path
    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass
    _checkpointer = None
    _connection = None
    _active_db_path = ""


def get_session_checkpointer(session_id: str) -> _AsyncCompatibleSqliteSaver:
    """Return the process-wide saver; LangGraph isolates rows by thread_id/ns."""
    del session_id  # isolation is provided by RunnableConfig, not saver instances
    global _checkpointer, _connection, _active_db_path
    path = str(checkpoint_db_path())
    with _lock:
        if _checkpointer is not None and _active_db_path == path:
            return _checkpointer
        _close_current()
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        saver = _AsyncCompatibleSqliteSaver(conn)
        saver.setup()
        _connection = conn
        _checkpointer = saver
        _active_db_path = path
        return saver


def clear_session_checkpointer(session_id: str) -> None:
    """Explicitly delete all checkpoint namespaces for one chat session."""
    sid = (session_id or "default").strip() or "default"
    with _lock:
        saver = get_session_checkpointer(sid)
        physical_ids = {sid}
        for item in saver.list(None):
            configurable = (item.config or {}).get("configurable") or {}
            physical = str(configurable.get("thread_id") or "")
            logical, _turn = _split_checkpoint_thread_id(physical)
            if logical == sid:
                physical_ids.add(physical)
        for physical in physical_ids:
            saver.delete_thread(physical)


def list_session_checkpoints(
    session_id: str,
    *,
    checkpoint_ns: str = "",
    limit: int = 100,
) -> list[Dict[str, Any]]:
    """Return compact, secret-free node snapshots for audit/UI rendering."""
    sid = (session_id or "default").strip() or "default"
    cfg: Optional[Dict[str, Any]] = None
    if checkpoint_ns:
        cfg = {
            "configurable": {
                "thread_id": checkpoint_thread_id(sid, checkpoint_ns),
                "checkpoint_ns": "",
            }
        }
    saver = get_session_checkpointer(sid)
    rows: list[Dict[str, Any]] = []
    wanted = max(1, min(int(limit or 100), 500))
    for item in saver.list(cfg):
        configurable = (item.config or {}).get("configurable") or {}
        physical_thread_id = str(configurable.get("thread_id") or "")
        logical_session_id, logical_turn_id = _split_checkpoint_thread_id(physical_thread_id)
        if logical_session_id != sid:
            continue
        checkpoint = item.checkpoint if isinstance(item.checkpoint, dict) else {}
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        values = checkpoint.get("channel_values") if isinstance(checkpoint.get("channel_values"), dict) else {}
        writes = metadata.get("writes") if isinstance(metadata.get("writes"), dict) else {}
        node_name = next(iter(writes.keys()), "") if writes else ""
        if not node_name:
            versions_seen = checkpoint.get("versions_seen")
            if isinstance(versions_seen, dict):
                node_candidates = [str(name) for name in versions_seen if not str(name).startswith("__")]
                if node_candidates:
                    node_name = node_candidates[-1]
        rows.append(
            {
                "checkpoint_id": configurable.get("checkpoint_id"),
                "checkpoint_ns": logical_turn_id,
                "thread_id": sid,
                "checkpoint_thread_id": physical_thread_id,
                "created_at": checkpoint.get("ts") or "",
                "step": metadata.get("step"),
                "source": metadata.get("source") or "",
                "node": node_name,
                "orchestration_phase": values.get("orchestration_phase") or "",
                "graph_route": values.get("graph_route") or "",
                "task_id": values.get("task_id") or "",
                "tool_wait_checkpoint": {
                    k: v
                    for k, v in (values.get("tool_wait_checkpoint") or {}).items()
                    if k in {
                        "status", "tool_name", "pipeline_task_ids", "started_at",
                        "resumed_at", "trace_id", "task_id",
                    }
                }
                if isinstance(values.get("tool_wait_checkpoint"), dict)
                else {},
                "parent_checkpoint_id": (
                    ((item.parent_config or {}).get("configurable") or {}).get("checkpoint_id")
                    if item.parent_config
                    else None
                ),
            }
        )
        if len(rows) >= wanted:
            break
    return rows


def reset_checkpointer_for_tests() -> None:
    """Close cached handles so tests can switch SBA_CHAT_CHECKPOINT_DB safely."""
    with _lock:
        _close_current()
