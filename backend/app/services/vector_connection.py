"""Vector / Milvus connection lifecycle helpers.

This module provides a small dynamic connection model for the UI:
- disconnected / connecting / connected / degraded / failed
- current connection params
- retryable health probe
- connection history and timestamps
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional

from .milvus_health import check_milvus


@dataclass
class VectorConnectionState:
    status: str = "disconnected"  # disconnected | connecting | connected | degraded | failed
    host: str = "127.0.0.1"
    port: str = "19530"
    error: str = ""
    version: str = ""
    latency_ms: int = 0
    last_checked_at: float = 0.0
    retry_count: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)


_STATE = VectorConnectionState(
    host=os.environ.get("MILVUS_HOST", "127.0.0.1"),
    port=os.environ.get("MILVUS_PORT", "19530"),
)


def _sync_params(host: Optional[str] = None, port: Optional[str] = None, **extra: Any) -> None:
    if host:
        _STATE.host = str(host)
    if port:
        _STATE.port = str(port)
    if extra:
        _STATE.params.update(extra)


def get_connection_state() -> Dict[str, Any]:
    data = asdict(_STATE)
    data["connected"] = _STATE.status in {"connected", "degraded"}
    data["params"] = {"host": _STATE.host, "port": _STATE.port, **(_STATE.params or {})}
    return data


def set_connection_params(host: Optional[str] = None, port: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    _sync_params(host, port, **extra)
    return get_connection_state()


def mark_connecting() -> Dict[str, Any]:
    _STATE.status = "connecting"
    _STATE.error = ""
    _STATE.last_checked_at = time.time()
    return get_connection_state()


def probe_connection(host: Optional[str] = None, port: Optional[str] = None) -> Dict[str, Any]:
    if host or port:
        _sync_params(host, port)
    mark_connecting()
    result = check_milvus(_STATE.host, _STATE.port)
    _STATE.last_checked_at = time.time()
    _STATE.latency_ms = int(result.get("latency_ms") or 0)
    _STATE.version = str(result.get("version") or "")
    _STATE.error = str(result.get("error") or "")
    _STATE.status = "connected" if result.get("milvus_ok") else "failed"
    if result.get("milvus_ok"):
        _STATE.last_success_at = _STATE.last_checked_at
        _STATE.retry_count = 0
    else:
        _STATE.last_failure_at = _STATE.last_checked_at
        _STATE.retry_count += 1
    return get_connection_state() | {"probe": result}


def retry_connection() -> Dict[str, Any]:
    return probe_connection()


def refresh_connection_state() -> Dict[str, Any]:
    result = check_milvus(_STATE.host, _STATE.port)
    _STATE.last_checked_at = time.time()
    _STATE.latency_ms = int(result.get("latency_ms") or 0)
    _STATE.version = str(result.get("version") or "")
    _STATE.error = str(result.get("error") or "")
    _STATE.status = "connected" if result.get("milvus_ok") else "disconnected"
    return get_connection_state() | {"probe": result}
