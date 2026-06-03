"""LangGraph 运行时上下文：SSE 发射、配置、工具目录。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from langchain_core.runnables import RunnableConfig


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@dataclass
class ChatGraphRuntime:
    """注入 configurable，供各节点共享。"""

    session_id: str
    trace_id: str
    message: str
    model: Optional[str] = None
    agent_id: Optional[str] = None
    agent_profile: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = None
    rag_prefetch: bool = False
    web_search: bool = False
    read_comments: bool = False
    deep_think: bool = False
    chat_max_tool_rounds: int = 15
    chat_tool_timeout_sec: float = 60.0
    chat_tool_max_retry: int = 3
    chat_distinct_tool_fail_limit: int = 3
    orch_pipeline_nodes: Dict[str, bool] = field(default_factory=dict)
    cfg: Dict[str, Any] = field(default_factory=dict)
    provider: str = "ark"
    api_key: str = ""
    base_url: str = ""
    model_resolved: str = ""
    system_prompt: str = ""
    link_ctx: Dict[str, Any] = field(default_factory=dict)
    tools_meta: Dict[str, Any] = field(default_factory=dict)
    chat_lc_tools: List[Any] = field(default_factory=list)
    _pending_sse: List[str] = field(default_factory=list)
    _live_sse_sink: Optional[Callable[[str], None]] = None
    last_hitl_event: Optional[Dict[str, Any]] = None

    def set_live_sse_sink(self, sink: Optional[Callable[[str], None]]) -> None:
        """Runner 注册：节点内 emit 时即时刷 SSE，避免 LangGraph 单节点阻塞导致 UI 假卡住。"""
        self._live_sse_sink = sink

    def emit(self, event: str, data: Dict[str, Any]) -> None:
        payload = dict(data)
        payload.setdefault("trace_id", self.trace_id)
        payload.setdefault("session_id", self.session_id)
        if event == "hitl_required":
            self.last_hitl_event = {
                "hitl_kind": payload.get("hitl_kind") or "",
                "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
                "task_id": payload.get("task_id") or "",
                "parent_status": payload.get("parent_status") or "",
            }
        line = _sse(event, payload)
        self._pending_sse.append(line)
        sink = self._live_sse_sink
        if sink is not None:
            try:
                sink(line)
            except Exception:
                pass

    def drain_sse(self) -> List[str]:
        out = list(self._pending_sse)
        self._pending_sse.clear()
        return out

    def snapshot_config(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_resolved,
            "rag_prefetch": self.rag_prefetch,
            "web_search": self.web_search,
            "read_comments": self.read_comments,
            "deep_think": self.deep_think,
            "chat_max_tool_rounds": self.chat_max_tool_rounds,
            "chat_tool_timeout_sec": self.chat_tool_timeout_sec,
            "chat_tool_max_retry": self.chat_tool_max_retry,
            "chat_distinct_tool_fail_limit": self.chat_distinct_tool_fail_limit,
            "orch_pipeline_nodes": dict(self.orch_pipeline_nodes or {}),
        }


def _lookup_runtime_registry(runtime_key: str) -> Optional[ChatGraphRuntime]:
    key = str(runtime_key or "").strip()
    if not key:
        return None
    try:
        from .chat_graph_runner import _RUNTIME_REGISTRY

        rt = _RUNTIME_REGISTRY.get(key)
        return rt if isinstance(rt, ChatGraphRuntime) else None
    except Exception:
        return None


def restore_runtime_from_state(state: Dict[str, Any]) -> Optional[ChatGraphRuntime]:
    """从 checkpoint 中的 runtime_config 快照重建进程内 runtime（reload / 多 worker 兜底）。"""
    if not isinstance(state, dict):
        return None
    session_id = str(state.get("session_id") or state.get("runtime_key") or "").strip()
    if not session_id:
        return None
    existing = _lookup_runtime_registry(session_id)
    if existing is not None:
        return existing

    rc = state.get("runtime_config") if isinstance(state.get("runtime_config"), dict) else {}
    trace_id = str(state.get("trace_id") or "").strip() or session_id
    message = str(state.get("message") or "").strip()
    try:
        from . import ai_chat
        from .orch_pipeline_config import merge_orch_pipeline_nodes

        cfg = ai_chat.load_chat_llm_config()
        merged_orch = merge_orch_pipeline_nodes(rc.get("orch_pipeline_nodes"), cfg)
        creds = ai_chat.resolve_chat_api_credentials(cfg)
        runtime = ChatGraphRuntime(
            session_id=session_id,
            trace_id=trace_id,
            message=message,
            rag_prefetch=bool(rc.get("rag_prefetch")),
            web_search=bool(rc.get("web_search")),
            read_comments=bool(rc.get("read_comments")),
            deep_think=bool(rc.get("deep_think")),
            chat_max_tool_rounds=max(1, int(rc.get("chat_max_tool_rounds") or cfg.get("chat_max_tool_rounds", 15) or 15)),
            chat_tool_timeout_sec=float(rc.get("chat_tool_timeout_sec") or cfg.get("chat_tool_timeout_sec", 60) or 60),
            chat_tool_max_retry=max(1, int(rc.get("chat_tool_max_retry") or cfg.get("chat_tool_max_retry", 3) or 3)),
            chat_distinct_tool_fail_limit=max(
                1,
                int(rc.get("chat_distinct_tool_fail_limit") or cfg.get("chat_distinct_tool_fail_limit", 3) or 3),
            ),
            orch_pipeline_nodes=merged_orch,
            cfg=cfg,
            provider=creds["provider"],
            api_key=creds["api_key"],
            base_url=creds["base_url"],
            model_resolved=str(rc.get("model") or creds.get("model") or "").strip(),
            system_prompt="",
            link_ctx=state.get("link_ctx") if isinstance(state.get("link_ctx"), dict) else {},
            tools_meta=state.get("tools_meta") if isinstance(state.get("tools_meta"), dict) else {},
        )
        from .chat_graph_runner import _RUNTIME_REGISTRY

        _RUNTIME_REGISTRY[session_id] = runtime
        return runtime
    except Exception:
        return None


def get_runtime_from_config(
    config: Optional[Union[RunnableConfig, Dict[str, Any]]],
) -> ChatGraphRuntime:
    if config is None:
        raise ValueError("缺少 LangGraph configurable.runtime")

    configurable: Dict[str, Any] = {}
    if isinstance(config, dict):
        configurable = dict(config.get("configurable") or {})
    else:
        configurable = dict(getattr(config, "configurable", {}) or {})
        if not configurable:
            try:
                configurable = dict(config.get("configurable") or {})  # type: ignore[attr-defined]
            except Exception:
                configurable = {}

    runtime = configurable.get("runtime")
    if isinstance(runtime, ChatGraphRuntime):
        return runtime

    runtime_key = str(configurable.get("runtime_key") or configurable.get("session_id") or "").strip()
    restored = _lookup_runtime_registry(runtime_key)
    if restored is not None:
        return restored

    raise ValueError("configurable.runtime 未注入")
