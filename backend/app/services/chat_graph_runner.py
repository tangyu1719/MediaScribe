"""LangGraph 运行器：SSE 刷出、HITL 中断恢复、执行段 handoff。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from langgraph.types import Command, RunnableConfig

from . import ai_chat
from .chat_graph import get_compiled_chat_graph
from .chat_graph_checkpointer import clear_session_checkpointer, get_session_checkpointer
from .chat_graph_runtime import ChatGraphRuntime

_RUNTIME_REGISTRY: Dict[str, ChatGraphRuntime] = {}
from .chat_graph_state import PHASE_IDLE

_LOG = logging.getLogger(__name__)


def _new_trace() -> str:
    return "trace_" + uuid.uuid4().hex[:12]


def _sync_span_context(session_id: str, trace_id: str, state: Dict[str, Any]) -> None:
    """将 LangGraph 状态中的 task_id 同步到 ContextVar，供 rag_search 等工具写 SPAN。"""
    from .span_orchestration import set_active_span_context

    filt = state.get("rag_metadata_filter")
    set_active_span_context(
        session_id=session_id,
        task_id=str(state.get("task_id") or ""),
        trace_id=trace_id,
        rag_metadata_filter=filt if isinstance(filt, dict) else None,
    )


def langgraph_enabled() -> bool:
    return os.environ.get("CHAT_USE_LANGGRAPH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _auto_hitl_enabled() -> bool:
    """pytest 或显式 CHAT_GRAPH_AUTO_HITL=1 时自动确认 HITL，便于 SSE 回归。"""
    v = os.environ.get("CHAT_GRAPH_AUTO_HITL", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _sync_final_state_from_snap(final_state: Dict[str, Any], snap: Any) -> None:
    if snap is None:
        return
    values = getattr(snap, "values", None)
    if isinstance(values, dict):
        final_state.update(values)


def _graph_config(session_id: str, runtime: ChatGraphRuntime) -> RunnableConfig:
    _RUNTIME_REGISTRY[session_id] = runtime
    cfg: RunnableConfig = {
        "configurable": {
            "thread_id": session_id,
            "session_id": session_id,
            "runtime_key": session_id,
            "runtime": runtime,
            "runtime_config": runtime.snapshot_config(),
        },
        "metadata": {
            "session_id": session_id,
            "trace_id": getattr(runtime, "trace_id", "") or "",
        },
        "tags": ["sba", "chat_orchestration"],
    }
    try:
        from app.eval.tracing import build_run_callbacks

        cbs = build_run_callbacks(
            session_id=session_id,
            trace_id=str(getattr(runtime, "trace_id", "") or ""),
            user_id=str(getattr(runtime, "user_id", "") or "") or None,
        )
        if cbs:
            cfg["callbacks"] = cbs
    except Exception as ex:
        _LOG.debug(
            "[Eval-追踪|chat_graph_runner._graph_config|build_run_callbacks|硬编执行|跳过] error=%s",
            ex,
        )
    return cfg


def _yield_sse_batches(state_update: Dict[str, Any]) -> List[str]:
    events = state_update.get("sse_events") if isinstance(state_update, dict) else None
    if not events:
        return []
    return list(events)


async def _iter_graph_astream_with_live_sse(
    graph: Any,
    input_state: Any,
    *,
    config: RunnableConfig,
    runtime: ChatGraphRuntime,
    final_state: Dict[str, Any],
    session_id: str,
    trace_id: str,
) -> AsyncIterator[str]:
    """
    LangGraph astream + runtime 实时 emit。
    节点执行期间 pipeline_progress / orchestration_node_start 等可立即到达前端。
    """
    live_q: asyncio.Queue[str] = asyncio.Queue()
    chunk_q: asyncio.Queue[Any] = asyncio.Queue()
    worker_err: List[BaseException] = []

    def _live_sink(line: str) -> None:
        try:
            live_q.put_nowait(line)
        except Exception:
            pass

    runtime.set_live_sse_sink(_live_sink)

    async def _worker() -> None:
        try:
            async for chunk in graph.astream(input_state, config=config, stream_mode="updates"):
                await chunk_q.put(chunk)
        except BaseException as ex:
            worker_err.append(ex)
        finally:
            await chunk_q.put(None)

    worker = asyncio.create_task(_worker())
    try:
        while True:
            while not live_q.empty():
                yield live_q.get_nowait()
            if worker.done() and chunk_q.empty() and live_q.empty():
                break
            try:
                chunk = await asyncio.wait_for(chunk_q.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            if chunk is None:
                break
            merged = _merge_node_updates(chunk)
            if merged:
                final_state.update(merged)
                if final_state.get("task_id"):
                    _sync_span_context(session_id, trace_id, final_state)
            # live_sink 已实时刷出节点 emit；sse_events 仅审计落库，勿 replay
            while not live_q.empty():
                yield live_q.get_nowait()
        if worker_err:
            raise worker_err[0]
    finally:
        runtime.set_live_sse_sink(None)
        if not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass


def _merge_node_updates(chunk: Any) -> Dict[str, Any]:
    """astream updates 模式：{node_name: partial_state}."""
    merged: Dict[str, Any] = {}
    if not isinstance(chunk, dict):
        return merged
    for _node, upd in chunk.items():
        if isinstance(upd, dict):
            for k, v in upd.items():
                if k == "sse_events" and k in merged:
                    merged[k] = list(merged.get(k) or []) + list(v or [])
                else:
                    merged[k] = v
    return merged


async def _prepare_runtime(
    *,
    message: str,
    session_id: str,
    trace_id: str,
    model: Optional[str],
    agent_id: Optional[str],
    agent_profile: Optional[Dict[str, Any]],
    user_id: Optional[str],
    rag_prefetch: bool,
    web_search: bool,
    read_comments: bool,
    deep_think: bool,
    chat_max_tool_rounds: Optional[int],
    chat_tool_timeout_sec: Optional[float],
    chat_tool_max_retry: Optional[int],
    chat_distinct_tool_fail_limit: Optional[int],
    orch_pipeline_nodes: Optional[Dict[str, Any]] = None,
    tools_cache_only: bool = False,
) -> ChatGraphRuntime:
    cfg = ai_chat.load_chat_llm_config()
    from .orch_pipeline_config import merge_orch_pipeline_nodes

    merged_orch = merge_orch_pipeline_nodes(orch_pipeline_nodes, cfg)

    if chat_max_tool_rounds is not None:
        cfg["chat_max_tool_rounds"] = chat_max_tool_rounds
    if chat_tool_timeout_sec is not None:
        cfg["chat_tool_timeout_sec"] = chat_tool_timeout_sec
    if chat_tool_max_retry is not None:
        cfg["chat_tool_max_retry"] = chat_tool_max_retry
    if chat_distinct_tool_fail_limit is not None:
        cfg["chat_distinct_tool_fail_limit"] = chat_distinct_tool_fail_limit

    from .link_doc_routing import analyze_link_doc_intent
    from .chat_tool_registry import load_orchestration_tools_catalog

    link_ctx = analyze_link_doc_intent(message, read_comments=read_comments)
    chat_lc_tools: List[Any] = []
    tools_meta: Dict[str, Any] = {}
    try:
        from .chat_warmup import get_cached_tools

        cached = get_cached_tools(read_comments=read_comments)
        if cached:
            chat_lc_tools, tools_meta = cached
            tools_meta = dict(tools_meta)
            tools_meta.setdefault("discovery_stage", "full")
            tools_meta["mcp_pending"] = False
            tools_meta["warmup_cache"] = True
        elif tools_cache_only:
            chat_lc_tools, tools_meta = [], {
                "total": 0,
                "tools": [],
                "mcp_pending": True,
                "warmup_cache": False,
                "discovery_stage": "deferred",
            }
        else:
            chat_lc_tools, tools_meta = load_orchestration_tools_catalog(read_comments=read_comments)
    except Exception as ex:
        chat_lc_tools, tools_meta = [], {"total": 0, "tools": [], "mcp_error": str(ex), "mcp_pending": True}

    creds = ai_chat.resolve_chat_api_credentials(cfg)
    provider = creds["provider"]
    api_key = creds["api_key"]
    base_url = creds["base_url"]
    model_resolved = (model or "").strip() or creds["model"]
    system_prompt = ai_chat.assemble_chat_system_prompt(
        cfg, agent_id, agent_profile=agent_profile, user_id=user_id
    )

    return ChatGraphRuntime(
        session_id=session_id,
        trace_id=trace_id,
        message=message,
        model=model,
        agent_id=agent_id,
        agent_profile=agent_profile,
        user_id=user_id,
        rag_prefetch=rag_prefetch,
        web_search=web_search,
        read_comments=read_comments,
        deep_think=deep_think,
        chat_max_tool_rounds=max(1, int(cfg.get("chat_max_tool_rounds", 15) or 15)),
        chat_tool_timeout_sec=float(cfg.get("chat_tool_timeout_sec", 60) or 60),
        chat_tool_max_retry=max(1, int(cfg.get("chat_tool_max_retry", 3) or 3)),
        chat_distinct_tool_fail_limit=max(
            1, int(cfg.get("chat_distinct_tool_fail_limit", 3) or 3)
        ),
        cfg=cfg,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model_resolved=model_resolved,
        system_prompt=system_prompt,
        link_ctx=link_ctx,
        tools_meta=tools_meta,
        chat_lc_tools=chat_lc_tools,
        orch_pipeline_nodes=merged_orch,
    )


def _initial_state(
    *,
    message: str,
    session_id: str,
    trace_id: str,
    runtime: ChatGraphRuntime,
) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "trace_id": trace_id,
        "message": message,
        "orchestration_phase": PHASE_IDLE,
        "rag_prefetch": runtime.rag_prefetch,
        "react_memory": [],
        "failed_tool_names": [],
        "react_round": 0,
        "react_max_rounds": runtime.chat_max_tool_rounds,
        "distinct_tool_fail_limit": runtime.chat_distinct_tool_fail_limit,
        "tool_round": 0,
        "link_ctx": runtime.link_ctx,
        "tools_meta": runtime.tools_meta,
        "sse_events": [],
        "graph_route": "",
        "runtime_config": runtime.snapshot_config(),
        "runtime_key": session_id,
    }


async def _memory_for_handoff(
    session_id: str,
    final_state: Dict[str, Any],
) -> Dict[str, Any]:
    """执行段 handoff 用会话记忆；resume 路径须单独加载（无 stream_langgraph_chat 的 mem 变量）。"""
    mem = final_state.get("memory_prepared")
    if isinstance(mem, dict) and mem:
        return mem
    from .chat_context_memory import prepare_session_memory

    cur = final_state.get("client_cur_task")
    hist = final_state.get("client_main_task_history")
    try:
        return await prepare_session_memory(
            session_id,
            client_cur_task=cur if isinstance(cur, dict) else None,
            client_history=hist if isinstance(hist, list) else None,
        )
    except Exception as ex:
        _LOG.warning(
            "[AI问答-LangGraph|chat_graph_runner._memory_for_handoff|session:%s|硬编执行|降级] "
            "prepare_session_memory_failed; error_message=%s",
            session_id,
            str(ex)[:200],
        )
        return {}


def _bootstrap_from_graph_state(state: Dict[str, Any], runtime: ChatGraphRuntime) -> Dict[str, Any]:
    """供 ai_chat 执行段使用的快照。"""
    return {
        "trace_id": state.get("trace_id") or runtime.trace_id,
        "session_id": state.get("session_id") or runtime.session_id,
        "message": state.get("message") or runtime.message,
        "task_id": state.get("task_id"),
        "use_main_task": bool(state.get("use_main_task")),
        "intent_task_kind": state.get("task_kind") or ("main" if state.get("use_main_task") else "simple"),
        "framework": state.get("framework") or "react",
        "intent_rewrite_snapshot": state.get("intent_rewrite_snapshot") or {},
        "slot_snapshot": state.get("slot_snapshot") or {},
        "enhancement_snapshot": state.get("enhancement_snapshot") or {},
        "rewritten_query": state.get("rewritten_query")
        or (state.get("intent_rewrite_snapshot") or {}).get("rewritten_query")
        or state.get("message"),
        "query_summary": state.get("query_summary") or "",
        "react_memory": state.get("react_memory") or [],
        "plan_steps": state.get("plan_steps") or [],
        "needs_rag": bool(state.get("needs_rag")),
        "rag_prefetch": runtime.rag_prefetch,
        "link_ctx": runtime.link_ctx,
        "tools_meta": runtime.tools_meta,
        "chat_lc_tools": runtime.chat_lc_tools,
        "provider": runtime.provider,
        "api_key": runtime.api_key,
        "base_url": runtime.base_url,
        "model_resolved": runtime.model_resolved,
        "system_prompt": runtime.system_prompt,
        "cfg": runtime.cfg,
        "read_comments": runtime.read_comments,
        "web_search": runtime.web_search,
        "deep_think": runtime.deep_think,
        "chat_max_tool_rounds": runtime.chat_max_tool_rounds,
        "chat_tool_timeout_sec": runtime.chat_tool_timeout_sec,
        "chat_tool_max_retry": runtime.chat_tool_max_retry,
        "chat_distinct_tool_fail_limit": runtime.chat_distinct_tool_fail_limit,
        "orchestration_phase": state.get("orchestration_phase"),
        "group_seq": int(state.get("group_seq") or 0),
        "continue_main_task": bool(state.get("continue_main_task")),
        "rag_context_block": state.get("rag_context_block") or "",
        "rag_slices": state.get("rag_slices") if isinstance(state.get("rag_slices"), list) else [],
        "rag_citation_instruction": state.get("rag_citation_instruction") or "",
        "rag_prefetch_done": bool(state.get("rag_prefetch_done")),
        "needs_rag": bool(state.get("needs_rag")),
    }


async def _handoff_load_execution_tools(
    runtime: ChatGraphRuntime,
    *,
    trace_id: str,
    task_id: str,
    read_comments: bool,
) -> tuple[Any, Any]:
    """
    执行段 handoff：绑定 LangChain 工具（含 MCP list_tools）。
    说明：MCP 发现是运行时基础设施，不是 LangGraph 编排节点（标准图为 agent↔tools 循环）。
    """
    from .chat_tool_registry import ensure_execution_tools

    # 进入对话页 / 启动预热已完成 MCP 发现时，直接复用缓存，禁止重复 list_tools
    if (
        runtime.tools_meta.get("warmup_cache")
        and runtime.tools_meta.get("discovery_stage") == "full"
        and not runtime.tools_meta.get("mcp_pending")
        and runtime.chat_lc_tools
    ):
        meta = dict(runtime.tools_meta)
        meta["handoff_from"] = "warmup_cache"
        runtime.emit(
            "tools_discovered",
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "total": meta.get("total", 0),
                "builtin_count": meta.get("builtin_count", 0),
                "mcp_count": meta.get("mcp_count", 0),
                "skill_count": meta.get("skill_count", 0),
                "read_comments": read_comments,
                "discovery_stage": "full",
                "mcp_pending": False,
                "warmup_cache": True,
                "stage": "execution",
                "silent": True,
                "hint": "复用页面预热缓存，跳过 MCP list_tools",
            },
        )
        return runtime.chat_lc_tools, meta

    runtime.emit(
        "tools_discovered",
        {
            "trace_id": trace_id,
            "task_id": task_id,
            "stage": "execution",
            "discovery_stage": "loading_mcp",
            "silent": True,
            "hint": "执行段绑定工具（非编排步骤）",
        },
    )
    tools_full, meta_full = await ensure_execution_tools(
        runtime.chat_lc_tools,
        runtime.tools_meta,
        read_comments=read_comments,
    )
    runtime.chat_lc_tools = tools_full
    runtime.tools_meta = meta_full
    runtime.emit(
        "tools_discovered",
        {
            "trace_id": trace_id,
            "task_id": task_id,
            "total": meta_full.get("total", 0),
            "builtin_count": meta_full.get("builtin_count", 0),
            "mcp_count": meta_full.get("mcp_count", 0),
            "skill_count": meta_full.get("skill_count", 0),
            "read_comments": read_comments,
            "tools": meta_full.get("tools", [])[:80],
            "mcp_error": meta_full.get("mcp_error") or "",
            "discovery_stage": "full",
            "mcp_pending": False,
            "stage": "execution",
            "silent": True,
        },
    )
    return tools_full, meta_full


async def _stream_simple_answer_events(
    runtime: ChatGraphRuntime,
    *,
    message: str,
    session_id: str,
    trace_id: str,
    memory_prepared: Optional[Dict[str, Any]] = None,
    client_cur_task: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[str]:
    """简单任务直答：在 runner 侧流式刷 SSE，避免 LangGraph 单节点阻塞导致长时间卡在「正在编排」。"""
    from .chat_tool_registry import format_tools_catalog_markdown, is_tools_inventory_query

    def _flush() -> List[str]:
        return list(runtime.drain_sse())

    runtime.emit("answer_generating", {"task_id": "", "label": "简单任务直答", "ephemeral": True})
    for line in _flush():
        yield line
    runtime.emit("answer_start", {"task_id": "", "ephemeral": True, "stream_mode": "token"})
    for line in _flush():
        yield line

    full = ""
    tools_inventory = is_tools_inventory_query(message)
    if tools_inventory:
        full = format_tools_catalog_markdown(runtime.tools_meta)
        for pos in range(0, len(full), 48):
            runtime.emit(
                "answer_delta",
                {"task_id": "", "content": full[pos : pos + 48], "kind": "body", "stream_mode": "token"},
            )
            for line in _flush():
                yield line
    elif runtime.api_key and runtime.model_resolved:
        try:
            from .chat_context_memory import build_agent_llm_messages

            mem = memory_prepared if isinstance(memory_prepared, dict) else {}
            llm_messages = build_agent_llm_messages(
                session_id=session_id,
                user_message=message,
                system_prompt=runtime.system_prompt,
                memory_prepared=mem,
                max_recent_turns=10,
            )
            deadline = time.perf_counter() + 95.0
            timed_out = False
            async for piece in ai_chat._async_iter_llm_token_stream(
                provider=runtime.provider,
                base_url=runtime.base_url,
                api_key=runtime.api_key,
                model=runtime.model_resolved,
                messages=llm_messages,
                temperature=0.35,
                max_tokens=1200,
                timeout=90.0,
            ):
                if time.perf_counter() > deadline:
                    timed_out = True
                    break
                full += piece
                runtime.emit(
                    "answer_delta",
                    {"task_id": "", "content": piece, "kind": "body", "stream_mode": "token"},
                )
                for line in _flush():
                    yield line
            if timed_out and not full.strip():
                full = "[LLM 请求超时] 简单任务直答未在 95 秒内返回，请检查网关或稍后重试。"
                runtime.emit("answer_delta", {"task_id": "", "content": full, "kind": "body"})
                for line in _flush():
                    yield line
            elif timed_out and full.strip():
                runtime.emit(
                    "answer_delta",
                    {"task_id": "", "content": "\n\n[提示：后续生成已因超时截断]", "kind": "body"},
                )
                for line in _flush():
                    yield line
        except Exception as ex:
            full = f"[LLM 调用失败] {ex}"
            runtime.emit("answer_delta", {"task_id": "", "content": full, "kind": "body"})
            for line in _flush():
                yield line

    if not full:
        if not runtime.api_key or not runtime.model_resolved:
            diag = ai_chat.chat_llm_config_diagnostics()
            cfg_hint = (
                f"配置路径: {diag.get('config_path')} (存在={diag.get('config_exists')})。"
                f"请用 start_backend.bat 启动并确认环境变量 SBA_AGENT_CONFIG 指向 src/agent/config.json；"
                f"修改配置后需重启后端。"
            )
            full = (
                "未配置 LLM（volcengine_api_key / ai_chat_model）。"
                + cfg_hint
            )
        else:
            full = "你好，我是 SuperBizAgent 对话助手。请描述你的具体任务（如链接文档化、知识库检索等）。"
        runtime.emit("answer_delta", {"task_id": "", "content": full, "kind": "body"})
        for line in _flush():
            yield line

    runtime.emit(
        "answer_end",
        {
            "task_id": "",
            "token_usage": {"prompt": max(1, len(message) // 4), "completion": len(full) // 4},
            "ephemeral": True,
        },
    )
    for line in _flush():
        yield line
    runtime.emit(
        "task_completed",
        {
            "task_id": "",
            "status": "ephemeral",
            "persist_main_task": False,
            "task_kind": "simple",
            "ephemeral": True,
            "user_resolved_allowed": False,
        },
    )
    for line in _flush():
        yield line
    ai_chat._append_session_messages(session_id, message, full, ephemeral=True)


async def stream_langgraph_chat(
    message: str,
    session_id: str = "default",
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_profile: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    rag_prefetch: bool = False,
    web_search: bool = False,
    read_comments: bool = False,
    deep_think: bool = False,
    chat_max_tool_rounds: Optional[int] = None,
    chat_tool_timeout_sec: Optional[float] = None,
    chat_tool_max_retry: Optional[int] = None,
    chat_distinct_tool_fail_limit: Optional[int] = None,
    orch_pipeline_nodes: Optional[Dict[str, Any]] = None,
    client_cur_task: Optional[Dict[str, Any]] = None,
    client_main_task_history: Optional[List] = None,
    memory_prepared: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[str]:
    """完整对话：LangGraph 编排 + 执行段 handoff。"""
    if session_id not in ai_chat._sessions:
        ai_chat.ensure_session(session_id, message[:30] or "新对话")
    elif message and (ai_chat._sessions[session_id].get("title") or "") in ("", "新对话"):
        ai_chat._sessions[session_id]["title"] = message[:40]
        from datetime import datetime as _dt

        ai_chat._sessions[session_id]["updated_at"] = _dt.now().isoformat(timespec="seconds")

    trace_id = _new_trace()
    mem = memory_prepared or {}
    from .chat_context_memory import (
        hydrate_client_task_context,
        peek_continue_main_intent,
        resolve_task_group_seq,
        _resolve_continue_task_id,
    )

    _cur_early, _hist_early = hydrate_client_task_context(
        session_id,
        client_cur_task=client_cur_task if isinstance(client_cur_task, dict) else mem.get("cur_task"),
        client_main_task_history=(
            client_main_task_history
            if isinstance(client_main_task_history, list) and client_main_task_history
            else mem.get("main_task_history")
        ),
    )
    _tid_early = str((_cur_early or {}).get("task_id") or "").strip()
    _continue_early = peek_continue_main_intent(
        message, cur_task=_cur_early, main_task_history=_hist_early
    )
    if _continue_early and not _tid_early:
        _tid_early = str(_resolve_continue_task_id(_cur_early, _hist_early) or "").strip()
    if _continue_early and _tid_early:
        yield (
            "event: task_created\n"
            + f"data: {json.dumps({'trace_id': trace_id, 'task_id': _tid_early, 'session_id': session_id, 'user_query': str((client_cur_task or mem.get('cur_task') or {}).get('user_query') or message or '')[:200], 'query_summary': str((client_cur_task or mem.get('cur_task') or {}).get('query_summary') or message or '')[:120], 'status': 'executing', 'task_kind': 'main', 'persist_main_task': True, 'stage': '延续主任务', 'progress': 4, 'task_action': 'continue', 'preserve_task_identity': True}, ensure_ascii=False)}\n\n"
        )
        yield (
            "event: pipeline_progress\n"
            + f"data: {json.dumps({'trace_id': trace_id, 'task_id': _tid_early, 'stage': '延续主任务', 'progress': 4, 'detail': '检测到主任务续接，展开编排面板'}, ensure_ascii=False)}\n\n"
        )
        yield (
            "event: orchestration_node_start\n"
            + f"data: {json.dumps({'trace_id': trace_id, 'task_id': _tid_early, 'step_id': 'step_early', 'step_name': '延续主任务', 'phase': 'execute_prep', 'progress_hint': '正在执行：延续主任务', 'sub_index': int(mem.get('task_group_seq') or 0)}, ensure_ascii=False)}\n\n"
        )

    ctx_appendix = ""
    try:
        from .chat_context_memory import build_context_system_appendix

        ctx_appendix = build_context_system_appendix(
            memory_mode=str(mem.get("memory_mode") or "short"),
            summary_text=str(mem.get("summary_text") or ""),
            task_context_block=str(mem.get("task_context_block") or ""),
        )
    except Exception:
        ctx_appendix = str(mem.get("task_context_block") or "")

    runtime = await _prepare_runtime(
        message=message,
        session_id=session_id,
        trace_id=trace_id,
        model=model,
        agent_id=agent_id,
        agent_profile=agent_profile,
        user_id=user_id,
        rag_prefetch=rag_prefetch,
        web_search=web_search,
        read_comments=read_comments,
        deep_think=deep_think,
        chat_max_tool_rounds=chat_max_tool_rounds,
        chat_tool_timeout_sec=chat_tool_timeout_sec,
        chat_tool_max_retry=chat_tool_max_retry,
        chat_distinct_tool_fail_limit=chat_distinct_tool_fail_limit,
        orch_pipeline_nodes=orch_pipeline_nodes,
        tools_cache_only=bool(_continue_early and _tid_early),
    )
    if ctx_appendix:
        runtime.system_prompt = (runtime.system_prompt or "").rstrip() + "\n\n---\n\n" + ctx_appendix

    for ev in mem.get("events") or []:
        if isinstance(ev, dict):
            runtime.emit("context_memory", ev)

    _LOG.info(
        "[AI问答-LangGraph|chat_graph_runner.stream_langgraph_chat|session:%s|硬编执行|启动] "
        "langgraph=1; auto_hitl=%s; message_len=%s",
        session_id,
        _auto_hitl_enabled(),
        len(message or ""),
    )
    runtime.emit(
        "stream_open",
        {
            "session_id": session_id,
            "stage": "编排启动",
            "progress": 1,
            "orchestration_engine": "langgraph",
            "expected_orchestration_phases": [
                "intent",
                "rewrite",
                "slot",
                "decompose",
                "enhance",
                "rag_decision",
                "execute_prep",
            ],
            "tools_catalog_total": runtime.tools_meta.get("total", 0),
            "tools_discovery_stage": runtime.tools_meta.get("discovery_stage") or "builtin_only",
        },
    )
    runtime.emit("thinking_start", {"task_id": "", "ephemeral": True})
    for line in runtime.drain_sse():
        yield line

    initial = _initial_state(message=message, session_id=session_id, trace_id=trace_id, runtime=runtime)
    from .chat_context_memory import (
        hydrate_client_task_context,
        peek_continue_main_intent,
        resolve_task_affiliation,
        resolve_task_group_seq,
    )

    _cur, _hist = hydrate_client_task_context(
        session_id,
        client_cur_task=client_cur_task if isinstance(client_cur_task, dict) else mem.get("cur_task"),
        client_main_task_history=(
            client_main_task_history
            if isinstance(client_main_task_history, list) and client_main_task_history
            else mem.get("main_task_history")
        ),
    )
    _tid = str((_cur or {}).get("task_id") or "").strip()
    _continue = peek_continue_main_intent(message, cur_task=_cur, main_task_history=_hist)
    if _continue and not _tid:
        from .chat_context_memory import _resolve_continue_task_id

        _tid = str(_resolve_continue_task_id(_cur, _hist) or "").strip()
        if _tid and isinstance(_cur, dict):
            _cur = {**_cur, "task_id": _tid}
    if _continue and _tid:
        initial["group_seq"] = int(mem.get("task_group_seq") or resolve_task_group_seq(_tid))
    else:
        initial["group_seq"] = 0
    initial["step_idx"] = 0
    initial["orch_chain"] = []
    if isinstance(client_cur_task, dict):
        initial["client_cur_task"] = client_cur_task
    elif isinstance(mem.get("cur_task"), dict):
        initial["client_cur_task"] = mem["cur_task"]
    if mem:
        initial["memory_prepared"] = mem
    if mem.get("memory_mode"):
        initial["memory_mode"] = mem.get("memory_mode")
    if mem.get("task_context_block"):
        initial["react_context_block"] = mem.get("task_context_block")
    if isinstance(_hist, list) and _hist:
        initial["client_main_task_history"] = _hist

    final_state: Dict[str, Any] = dict(initial)
    interrupted = False
    snap = None
    used_fast_continue = bool(_continue and _tid)

    from .span_orchestration import clear_active_span_context

    _sync_span_context(session_id, trace_id, final_state)

    if used_fast_continue:
        t_fast = time.perf_counter()
        live_q: asyncio.Queue[str] = asyncio.Queue()
        fast_result: List[Dict[str, Any]] = []
        fast_err: List[BaseException] = []

        def _live_sink(line: str) -> None:
            try:
                live_q.put_nowait(line)
            except Exception:
                pass

        runtime.set_live_sse_sink(_live_sink)

        async def _run_fast() -> None:
            from .chat_graph_nodes import fast_continue_main_to_handoff

            try:
                fast_result.append(
                    await fast_continue_main_to_handoff(
                        initial,
                        runtime=runtime,
                        message=message,
                        session_id=session_id,
                        trace_id=trace_id,
                        task_id=_tid,
                        cur_task=_cur if isinstance(_cur, dict) else None,
                        main_hist=_hist if isinstance(_hist, list) else None,
                    )
                )
            except BaseException as ex:
                fast_err.append(ex)

        fast_task = asyncio.create_task(_run_fast())
        try:
            while not fast_task.done() or not live_q.empty():
                while not live_q.empty():
                    yield live_q.get_nowait()
                if fast_task.done():
                    break
                await asyncio.sleep(0.03)
        finally:
            runtime.set_live_sse_sink(None)
        if fast_err:
            raise fast_err[0]
        fast_upd = fast_result[0] if fast_result else {}
        final_state.update(fast_upd)
        _sync_span_context(session_id, trace_id, final_state)
        # live_q 已实时刷出编排 SSE；sse_events 仅落库，勿 replay 避免「知识库检索」等步骤双份
        for line in runtime.drain_sse():
            yield line
        _LOG.info(
            "[AI问答-LangGraph|chat_graph_runner.stream_langgraph_chat|session:%s|硬编执行|快径] "
            "continue_main_fast; cost_ms=%s; task_id=%s",
            session_id,
            int((time.perf_counter() - t_fast) * 1000),
            _tid[:16],
        )
    else:
        yield (
            "event: pipeline_progress\n"
            + f"data: {json.dumps({'trace_id': trace_id, 'stage': '意图识别', 'progress': 5, 'detail': 'LangGraph 进入首节点'}, ensure_ascii=False)}\n\n"
        )
        pre_step_id = "step_" + uuid.uuid4().hex[:12]
        runtime.emit(
            "orchestration_node_start",
            {
                "task_id": _tid or "",
                "step_id": pre_step_id,
                "step_name": "意图识别",
                "phase": "intent",
                "progress_hint": "正在执行：意图识别",
            },
        )
        runtime.emit(
            "thought_step_start",
            {
                "trace_id": trace_id,
                "task_id": _tid or "",
                "step_id": pre_step_id,
                "step_name": "意图识别",
                "phase": "intent",
                "node_kind": "orchestration",
                "step_lane": "orchestration",
                "status": "running",
            },
        )
        for line in runtime.drain_sse():
            yield line
        # 新用户消息走全新编排：清掉同 thread 上未 resume 的 HITL 中断，避免 checkpoint 缺 runtime_key
        clear_session_checkpointer(session_id)
        checkpointer = get_session_checkpointer(session_id)
        graph = get_compiled_chat_graph(checkpointer=checkpointer)
        config = _graph_config(session_id, runtime)

        try:
            async for line in _iter_graph_astream_with_live_sse(
                graph,
                initial,
                config=config,
                runtime=runtime,
                final_state=final_state,
                session_id=session_id,
                trace_id=trace_id,
            ):
                yield line

            snap = graph.get_state(config)
            _sync_final_state_from_snap(final_state, snap)
            interrupted = _hitl_pending(snap)

            if interrupted and _auto_hitl_enabled():
                for _attempt in range(8):
                    if not _hitl_pending(snap):
                        break
                    _LOG.info(
                        "[AI问答-LangGraph|chat_graph_runner.stream_langgraph_chat|session:%s|硬编执行|HITL] "
                        "auto_resume; hitl_kind=%s; attempt=%s",
                        session_id,
                        _infer_hitl_kind(snap),
                        _attempt + 1,
                    )
                    config = _graph_config(session_id, runtime)
                    async for chunk in graph.astream(
                        Command(resume={"action": "confirm"}),
                        config=config,
                        stream_mode="updates",
                    ):
                        merged = _merge_node_updates(chunk)
                        if merged:
                            final_state.update(merged)
                        for line in _yield_sse_batches(merged):
                            yield line
                    snap = graph.get_state(config)
                    _sync_final_state_from_snap(final_state, snap)
                interrupted = _hitl_pending(snap)
        except Exception as ex:
            from langgraph.errors import GraphInterrupt

            if isinstance(ex, GraphInterrupt):
                interrupted = True
                try:
                    snap = graph.get_state(config)
                    _sync_final_state_from_snap(final_state, snap)
                except Exception:
                    pass
            else:
                from .chat_error_handler import stream_user_error_sse

                async for line in stream_user_error_sse(
                    ex,
                    session_id=session_id,
                    trace_id=trace_id,
                    task_id=str(final_state.get("task_id") or ""),
                    stage="LangGraph 编排",
                    user_message=message,
                ):
                    yield line
                clear_active_span_context()
                return

    if not used_fast_continue and (
        interrupted or final_state.get("graph_route") in ("paused",) or _hitl_pending(snap)
    ):
        payload = _build_graph_interrupt_payload(
            session_id=session_id,
            trace_id=trace_id,
            final_state=final_state,
            snap=snap,
            runtime=runtime,
        )
        yield f"event: graph_interrupt\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        clear_active_span_context()
        return

    route = str(final_state.get("graph_route") or "")
    _aff_guard = resolve_task_affiliation(message, cur_task=_cur, main_task_history=_hist)
    _must_continue = bool(_aff_guard) or peek_continue_main_intent(
        message, cur_task=_cur, main_task_history=_hist
    )
    if _must_continue and route in ("handoff_simple", "simple"):
        route = "handoff_execute"
        final_state["graph_route"] = "continue_execute"
        final_state["task_kind"] = "main"
        final_state["continue_main_task"] = True
        final_state["use_main_task"] = True
        if _aff_guard and _aff_guard.get("task_id"):
            final_state["task_id"] = str(_aff_guard["task_id"])
        _LOG.warning(
            "[AI问答-LangGraph|chat_graph_runner.stream_langgraph_chat|session:%s|硬编执行|纠偏] "
            "simple_route_blocked; reason=%s; task_id=%s",
            session_id,
            (_aff_guard or {}).get("reason") or "continue_main",
            final_state.get("task_id") or "",
        )
    if route == "handoff_simple" or (
        final_state.get("task_kind") == "simple"
        and not final_state.get("execution_done")
        and route not in ("handoff_execute", "done")
        and not _must_continue
    ):
        async for line in _stream_simple_answer_events(
            runtime,
            message=message,
            session_id=session_id,
            trace_id=trace_id,
            memory_prepared=mem,
            client_cur_task=client_cur_task,
        ):
            yield line
        clear_active_span_context()
        return

    if route == "handoff_execute" or final_state.get("orchestration_phase") in (
        "react_running",
        "plan_execute_running",
    ):
        yield (
            "event: pipeline_progress\n"
            + f"data: {json.dumps({'trace_id': trace_id, 'stage': '绑定执行工具', 'progress': 68, 'detail': 'MCP 已在启动健康检查中预连；正在绑定执行段工具'}, ensure_ascii=False)}\n\n"
        )
        tools_full, meta_full = await _handoff_load_execution_tools(
            runtime,
            trace_id=trace_id,
            task_id=str(final_state.get("task_id") or ""),
            read_comments=read_comments,
        )
        for line in runtime.drain_sse():
            yield line
        final_state["tools_meta"] = meta_full
        handoff_mem = await _memory_for_handoff(session_id, final_state)
        bootstrap = _bootstrap_from_graph_state(final_state, runtime)
        bootstrap["memory_prepared"] = handoff_mem
        _sync_span_context(session_id, trace_id, final_state)
        try:
            async for ev in ai_chat.chat_stream_v2(
                message,
                session_id,
                model=model,
                agent_id=agent_id,
                agent_profile=agent_profile,
                user_id=user_id,
                rag_prefetch=rag_prefetch,
                web_search=web_search,
                read_comments=read_comments,
                deep_think=deep_think,
                chat_max_tool_rounds=chat_max_tool_rounds,
                chat_tool_timeout_sec=chat_tool_timeout_sec,
                chat_tool_max_retry=chat_tool_max_retry,
                chat_distinct_tool_fail_limit=chat_distinct_tool_fail_limit,
                graph_execution_boot=bootstrap,
                _langgraph_orchestration_done=True,
                memory_prepared=handoff_mem,
            ):
                yield ev
        except Exception as ex:
            from .chat_error_handler import stream_user_error_sse

            async for line in stream_user_error_sse(
                ex,
                session_id=session_id,
                trace_id=trace_id,
                task_id=str(final_state.get("task_id") or ""),
                stage="任务执行",
                user_message=message,
            ):
                yield line
        clear_active_span_context()
        return

    if final_state.get("execution_done") or route == "done":
        for line in runtime.drain_sse():
            yield line
        clear_active_span_context()
        return

    clear_active_span_context()
    _LOG.warning(
        "[AI问答-LangGraph|chat_graph_runner|session:%s|硬编执行|未完成] "
        "unexpected_terminal; route=%s; phase=%s",
        session_id,
        route,
        final_state.get("orchestration_phase"),
    )


def _hitl_pending(snap: Any) -> bool:
    if snap is None:
        return False
    nxt = getattr(snap, "next", None) or ()
    return bool(nxt)


def _infer_hitl_kind(snap: Any) -> str:
    if snap is None:
        return ""
    for t in getattr(snap, "tasks", None) or ():
        name = getattr(t, "name", "") or ""
        if "rewrite_confirm" in name:
            return "rewrite_confirm"
        if "slot_confirm" in name:
            return "slot_confirm"
        if "rag_filter_confirm" in name:
            return "rag_filter_confirm"
        if "rag_decision" in name:
            return "rag_confirm"
    return "unknown"


def _hitl_default_message(kind: str) -> str:
    return {
        "rewrite_confirm": "请确认改写后的任务表述，或修改后点击「确认继续」。",
        "slot_confirm": "请确认业务领域与检索意图后继续编排。",
        "rag_confirm": "请确认或调整知识库检索词后继续。",
        "rag_filter_confirm": "请确认知识库元数据筛选条件（空=不筛）后继续检索。",
        "tool_exception": "工具执行需人工选择后继续。",
        "paused": "编排已暂停，确认后可恢复。",
    }.get(kind or "", "编排等待人工确认，请选择操作后继续。")


def _build_graph_interrupt_payload(
    *,
    session_id: str,
    trace_id: str,
    final_state: Dict[str, Any],
    snap: Any,
    runtime: Optional[ChatGraphRuntime] = None,
) -> Dict[str, Any]:
    """统一 graph_interrupt 载荷，供前端 HITL 面板渲染与 resume。"""
    kind = str(final_state.get("hitl_kind") or "").strip()
    if not kind and runtime and runtime.last_hitl_event:
        kind = str(runtime.last_hitl_event.get("hitl_kind") or "").strip()
    if not kind:
        kind = _infer_hitl_kind(snap)

    hitl_payload: Dict[str, Any] = {}
    if runtime and isinstance(runtime.last_hitl_event, dict):
        hitl_payload = dict(runtime.last_hitl_event.get("payload") or {})

    values = getattr(snap, "values", None) if snap is not None else None
    if isinstance(values, dict):
        if kind == "rewrite_confirm" and not hitl_payload.get("rewrite_snapshot"):
            hitl_payload["rewrite_snapshot"] = values.get("intent_rewrite_snapshot") or {}
            hitl_payload.setdefault("kind", "rewrite_confirm")
        if kind == "slot_confirm" and not hitl_payload.get("slot_snapshot"):
            hitl_payload["slot_snapshot"] = values.get("slot_snapshot") or {}
            hitl_payload.setdefault("kind", "slot_confirm")
        if kind == "rag_filter_confirm":
            hitl_payload.setdefault("kind", "rag_filter_confirm")
            if not hitl_payload.get("filter_form") and isinstance(values.get("rag_metadata_filter"), dict):
                hitl_payload["filter_form"] = values.get("rag_metadata_filter")
        if kind == "rag_confirm":
            hitl_payload.setdefault("kind", "rag_confirm")
            if not hitl_payload.get("keywords"):
                enh = values.get("enhancement_snapshot") or {}
                kws = enh.get("search_keyword_queries") if isinstance(enh, dict) else []
                if isinstance(kws, list):
                    hitl_payload["keywords"] = kws
            if not hitl_payload.get("query"):
                slot = values.get("slot_snapshot") or {}
                hitl_payload["query"] = (
                    (slot.get("rewritten_query") if isinstance(slot, dict) else None)
                    or values.get("rewritten_query")
                    or values.get("message")
                    or ""
                )

    msg = str(hitl_payload.get("message") or _hitl_default_message(kind))
    return {
        "session_id": session_id,
        "trace_id": trace_id,
        "thread_id": session_id,
        "hitl_kind": kind,
        "hitl_payload": hitl_payload,
        "message": msg,
        "orchestration_phase": final_state.get("orchestration_phase"),
        "task_id": final_state.get("task_id") or (runtime.last_hitl_event or {}).get("task_id") if runtime else "",
        "state_preview": {
            "task_id": final_state.get("task_id"),
            "rewrite_snapshot": final_state.get("intent_rewrite_snapshot"),
            "slot_snapshot": final_state.get("slot_snapshot"),
            "enhancement_snapshot": final_state.get("enhancement_snapshot"),
        },
    }


async def stream_langgraph_resume(
    session_id: str,
    hitl_payload: Dict[str, Any],
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_profile: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    rag_prefetch: bool = False,
    web_search: bool = False,
    read_comments: bool = False,
    deep_think: bool = False,
    chat_max_tool_rounds: Optional[int] = None,
    chat_tool_timeout_sec: Optional[float] = None,
    chat_tool_max_retry: Optional[int] = None,
    chat_distinct_tool_fail_limit: Optional[int] = None,
    orch_pipeline_nodes: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[str]:
    """HITL resume：Command(resume=...) 继续编排或进入执行段。"""
    trace_id = _new_trace()
    message = str(hitl_payload.get("message") or "").strip()
    if not message:
        message = "(HITL resume)"

    runtime = await _prepare_runtime(
        message=message,
        session_id=session_id,
        trace_id=trace_id,
        model=model,
        agent_id=agent_id,
        agent_profile=agent_profile,
        user_id=user_id,
        rag_prefetch=rag_prefetch,
        web_search=web_search,
        read_comments=read_comments,
        deep_think=deep_think,
        chat_max_tool_rounds=chat_max_tool_rounds,
        chat_tool_timeout_sec=chat_tool_timeout_sec,
        chat_tool_max_retry=chat_tool_max_retry,
        chat_distinct_tool_fail_limit=chat_distinct_tool_fail_limit,
        orch_pipeline_nodes=orch_pipeline_nodes,
    )

    checkpointer = get_session_checkpointer(session_id)
    graph = get_compiled_chat_graph(checkpointer=checkpointer)
    config = _graph_config(session_id, runtime)

    resume_val = hitl_payload.get("hitl") if isinstance(hitl_payload.get("hitl"), dict) else hitl_payload
    final_state: Dict[str, Any] = {}
    interrupted = False
    snap = None

    from .span_orchestration import clear_active_span_context
    from .chat_graph_runtime import restore_runtime_from_state

    _sync_span_context(session_id, trace_id, final_state)

    try:
        snap_pre = graph.get_state(config)
        pre_vals = getattr(snap_pre, "values", None) if snap_pre else None
        if isinstance(pre_vals, dict):
            restore_runtime_from_state(pre_vals)
            _sync_final_state_from_snap(final_state, snap_pre)
        async for chunk in graph.astream(
            Command(resume=resume_val),
            config=config,
            stream_mode="updates",
        ):
            merged = _merge_node_updates(chunk)
            if merged:
                final_state.update(merged)
                if final_state.get("task_id"):
                    _sync_span_context(session_id, trace_id, final_state)
            for line in _yield_sse_batches(merged):
                yield line
        snap = graph.get_state(config)
        if snap and (getattr(snap, "next", None) or getattr(snap, "tasks", None)):
            interrupted = True
    except Exception as ex:
        from langgraph.errors import GraphInterrupt

        if isinstance(ex, GraphInterrupt):
            interrupted = True
            try:
                snap = graph.get_state(config)
                _sync_final_state_from_snap(final_state, snap)
            except Exception:
                pass
        else:
            from .chat_error_handler import stream_user_error_sse

            async for line in stream_user_error_sse(
                ex,
                session_id=session_id,
                trace_id=trace_id or runtime.trace_id,
                task_id=str(final_state.get("task_id") or ""),
                stage="HITL 恢复",
                user_message=message,
            ):
                yield line
            clear_active_span_context()
            return

    if interrupted or _hitl_pending(snap):
        payload = _build_graph_interrupt_payload(
            session_id=session_id,
            trace_id=trace_id or runtime.trace_id,
            final_state=final_state,
            snap=snap,
            runtime=runtime,
        )
        yield f"event: graph_interrupt\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        clear_active_span_context()
        return

    route = str(final_state.get("graph_route") or "")
    if route == "handoff_execute":
        mem = await _memory_for_handoff(session_id, final_state)
        tools_full, meta_full = await _handoff_load_execution_tools(
            runtime,
            trace_id=trace_id,
            task_id=str(final_state.get("task_id") or ""),
            read_comments=read_comments,
        )
        for line in runtime.drain_sse():
            yield line
        final_state["tools_meta"] = meta_full
        bootstrap = _bootstrap_from_graph_state(final_state, runtime)
        bootstrap["memory_prepared"] = mem
        _sync_span_context(session_id, trace_id, final_state)
        async for ev in ai_chat.chat_stream_v2(
            message,
            session_id,
            model=model,
            agent_id=agent_id,
            agent_profile=agent_profile,
            user_id=user_id,
            rag_prefetch=rag_prefetch,
            web_search=web_search,
            read_comments=read_comments,
            deep_think=deep_think,
            chat_max_tool_rounds=chat_max_tool_rounds,
            chat_tool_timeout_sec=chat_tool_timeout_sec,
            chat_tool_max_retry=chat_tool_max_retry,
            chat_distinct_tool_fail_limit=chat_distinct_tool_fail_limit,
            graph_execution_boot=bootstrap,
            _langgraph_orchestration_done=True,
            memory_prepared=mem,
        ):
            yield ev
        clear_active_span_context()
        return
