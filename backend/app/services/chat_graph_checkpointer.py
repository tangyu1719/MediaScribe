"""LangGraph checkpoint：按 session 隔离，支持 HITL interrupt 恢复。"""
from __future__ import annotations

from threading import Lock
from typing import Dict

from langgraph.checkpoint.memory import MemorySaver

_lock = Lock()
_checkpointers: Dict[str, MemorySaver] = {}


def get_session_checkpointer(session_id: str) -> MemorySaver:
    sid = (session_id or "default").strip() or "default"
    with _lock:
        if sid not in _checkpointers:
            _checkpointers[sid] = MemorySaver()
        return _checkpointers[sid]


def clear_session_checkpointer(session_id: str) -> None:
    sid = (session_id or "default").strip() or "default"
    with _lock:
        _checkpointers.pop(sid, None)
