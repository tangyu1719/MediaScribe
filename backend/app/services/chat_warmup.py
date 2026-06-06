"""AI 对话运行时预热：MCP 全量工具、LangGraph 图、可选 RAG 嵌入/Milvus 探活。"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger("sba.chat_warmup")

_lock = threading.Lock()
_warming = False
_status: Dict[str, Any] = {
    "ready": False,
    "warming": False,
    "started_at": "",
    "finished_at": "",
    "elapsed_ms": 0,
    "phases": {},
    "error": "",
}
# read_comments -> (tools, meta)
_tools_cache: Dict[bool, Tuple[List[Any], Dict[str, Any]]] = {}
# session_id -> (memory_prepared, ts)
_session_mem_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_SESSION_MEM_TTL_SEC = 180.0
_RAG_EMBED_WARMUP_TIMEOUT_SEC = 45.0


def get_warmup_status() -> Dict[str, Any]:
    with _lock:
        out = dict(_status)
        out["tools_cached"] = {
            "default": bool(_tools_cache.get(False)),
            "read_comments": bool(_tools_cache.get(True)),
        }
        out["session_mem_cached"] = len(_session_mem_cache)
        if _tools_cache.get(False):
            _, meta = _tools_cache[False]
            out["tools_total"] = int(meta.get("total") or 0)
            out["mcp_count"] = int(meta.get("mcp_count") or 0)
        return out


def get_cached_tools(*, read_comments: bool = False) -> Optional[Tuple[List[Any], Dict[str, Any]]]:
    with _lock:
        row = _tools_cache.get(bool(read_comments))
        if not row:
            # 未勾选评论时，可复用默认全量缓存（评论工具在调用前再过滤）
            if not read_comments:
                return None
            row = _tools_cache.get(False)
        if not row:
            return None
        tools, meta = row
        if read_comments and not _tools_cache.get(True):
            return list(tools), dict(meta)
        return list(tools), dict(meta)


def get_cached_session_memory(session_id: str) -> Optional[Dict[str, Any]]:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with _lock:
        row = _session_mem_cache.get(sid)
        if not row:
            return None
        mem, ts = row
        if time.time() - ts > _SESSION_MEM_TTL_SEC:
            _session_mem_cache.pop(sid, None)
            return None
        return dict(mem)


def store_session_memory_cache(session_id: str, memory_prepared: Dict[str, Any]) -> None:
    sid = str(session_id or "").strip()
    if not sid or not isinstance(memory_prepared, dict):
        return
    with _lock:
        _session_mem_cache[sid] = (dict(memory_prepared), time.time())


async def refresh_or_prepare_session_memory(
    session_id: str,
    *,
    client_cur_task: Optional[Dict[str, Any]] = None,
    client_history: Optional[List] = None,
    extra_tokens: int = 32,
    lite: bool = False,
) -> Dict[str, Any]:
    """命中会话缓存时仅刷新 token 占用；阈值触发时仍走完整 prepare。lite=续接主任务时跳过 Redis 任务仓库。"""
    from .chat_context_memory import (
        context_usage,
        get_session_document,
        memory_prefs_from_doc,
        prepare_session_memory,
    )

    extra = max(32, int(extra_tokens or 0))
    cached = get_cached_session_memory(session_id)
    if cached:
        doc = get_session_document(session_id) or {}
        if client_cur_task and isinstance(client_cur_task, dict):
            doc["cur_task"] = client_cur_task
        if isinstance(client_history, list) and client_history:
            doc["main_task_history"] = client_history
        prefs = memory_prefs_from_doc(doc)
        usage = context_usage(doc, extra_tokens=extra, prefs=prefs)
        if not usage.get("should_pre_summarize") and not usage.get("should_force_archive"):
            out = {
                **cached,
                "usage": usage,
                "cur_task": doc.get("cur_task"),
                "main_task_history": doc.get("main_task_history") or [],
            }
            store_session_memory_cache(session_id, out)
            return out
    if lite and isinstance(client_cur_task, dict) and str(client_cur_task.get("task_id") or "").strip():
        doc = get_session_document(session_id) or {}
        doc["cur_task"] = client_cur_task
        if isinstance(client_history, list) and client_history:
            doc["main_task_history"] = client_history
        prefs = memory_prefs_from_doc(doc)
        usage = context_usage(doc, extra_tokens=extra, prefs=prefs)
        if not usage.get("should_pre_summarize") and not usage.get("should_force_archive"):
            tid = str(client_cur_task.get("task_id") or "")
            uq = str(client_cur_task.get("user_query") or client_cur_task.get("query_summary") or "")
            mem_meta = dict(doc.get("memory_meta") or {})
            mem_meta["mode"] = usage["mode"]
            out = {
                "usage": usage,
                "memory_meta": mem_meta,
                "memory_mode": usage["mode"],
                "task_context_block": f"- task_id: {tid}\n- user_query: {uq[:240]}",
                "task_redis": {"task_id": tid, "user_query": uq},
                "task_repo": {"task_id": tid, "user_query": uq},
                "task_group_seq": 0,
                "cur_task": client_cur_task,
                "main_task_history": client_history or doc.get("main_task_history") or [],
                "summary_text": mem_meta.get("summary_text") or "",
                "events": [],
                "force_new_session": False,
            }
            store_session_memory_cache(session_id, out)
            return out
    mem = await prepare_session_memory(
        session_id,
        client_cur_task=client_cur_task if isinstance(client_cur_task, dict) else None,
        client_history=client_history if isinstance(client_history, list) else None,
        extra_tokens=extra,
    )
    store_session_memory_cache(session_id, mem)
    return mem


async def warm_session_memory(
    session_id: str,
    *,
    client_cur_task: Optional[Dict[str, Any]] = None,
    client_history: Optional[List] = None,
    extra_tokens: int = 32,
) -> Dict[str, Any]:
    """进入对话页/切换会话时预加载会话记忆（不阻塞首条 SSE）。"""
    t0 = time.perf_counter()
    mem = await refresh_or_prepare_session_memory(
        session_id,
        client_cur_task=client_cur_task,
        client_history=client_history,
        extra_tokens=extra_tokens,
    )
    ms = int((time.perf_counter() - t0) * 1000)
    _set_phase(
        f"session_{session_id[:12]}",
        ok=True,
        elapsed_ms=ms,
        detail=f"memory_mode={mem.get('memory_mode')}; events={len(mem.get('events') or [])}",
    )
    return mem


def _set_phase(name: str, *, ok: bool, elapsed_ms: int, detail: str = "", error: str = "") -> None:
    with _lock:
        phases = dict(_status.get("phases") or {})
        phases[name] = {
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "detail": detail[:300],
            "error": error[:300],
        }
        _status["phases"] = phases


async def _warm_langgraph() -> None:
    t0 = time.perf_counter()
    try:
        from .chat_graph import get_compiled_chat_graph

        get_compiled_chat_graph()
        ms = int((time.perf_counter() - t0) * 1000)
        _set_phase("langgraph", ok=True, elapsed_ms=ms, detail="graph_compiled")
    except Exception as ex:
        ms = int((time.perf_counter() - t0) * 1000)
        _set_phase("langgraph", ok=False, elapsed_ms=ms, error=str(ex))
        raise


async def _warm_tools(*, read_comments: bool) -> None:
    t0 = time.perf_counter()
    from .chat_tool_registry import load_all_chat_tools

    tools, meta = await load_all_chat_tools(read_comments=read_comments)
    ms = int((time.perf_counter() - t0) * 1000)
    with _lock:
        _tools_cache[bool(read_comments)] = (tools, meta)
    phase_name = "tools_mcp_read_comments" if read_comments else "tools_mcp"
    _set_phase(
        phase_name,
        ok=True,
        elapsed_ms=ms,
        detail=f"total={meta.get('total')}; mcp={meta.get('mcp_count')}; err={meta.get('mcp_error') or 'none'}",
    )


async def _warm_rag_optional() -> None:
    """后台加载嵌入模型 / Milvus 探活（失败不阻断对话）。"""
    t0 = time.perf_counter()
    try:
        from .milvus_rag_query import fetch_milvus_rag_snapshot

        st = await asyncio.to_thread(fetch_milvus_rag_snapshot, force=False)
        ms = int((time.perf_counter() - t0) * 1000)
        ok = bool(st and st.get("ok"))
        _set_phase(
            "rag_milvus",
            ok=ok,
            elapsed_ms=ms,
            detail=f"chunks={int((st or {}).get('total_chunks') or 0)}" if st else "no_snapshot",
            error="" if ok else "milvus_not_ready",
        )
    except Exception as ex:
        ms = int((time.perf_counter() - t0) * 1000)
        _set_phase("rag_milvus", ok=False, elapsed_ms=ms, error=str(ex)[:200])

    t1 = time.perf_counter()
    try:
        from .kb_rag import get_kb_manager

        await asyncio.wait_for(
            asyncio.to_thread(get_kb_manager),
            timeout=_RAG_EMBED_WARMUP_TIMEOUT_SEC,
        )
        ms = int((time.perf_counter() - t1) * 1000)
        _set_phase("rag_embedder", ok=True, elapsed_ms=ms, detail="kb_manager_init")
    except asyncio.TimeoutError:
        ms = int((time.perf_counter() - t1) * 1000)
        _set_phase(
            "rag_embedder",
            ok=False,
            elapsed_ms=ms,
            error=f"embedder_warmup_timeout_{int(_RAG_EMBED_WARMUP_TIMEOUT_SEC)}s",
        )
    except Exception as ex:
        ms = int((time.perf_counter() - t1) * 1000)
        _set_phase("rag_embedder", ok=False, elapsed_ms=ms, error=str(ex)[:200])


def _warmup_cache_satisfied(*, read_comments: bool) -> bool:
    if not _tools_cache.get(False):
        return False
    if read_comments and not _tools_cache.get(True):
        return False
    return True


async def run_chat_warmup(
    *,
    read_comments: bool = False,
    include_rag: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """预热 MCP 工具与 LangGraph；可选 RAG 嵌入/Milvus（失败可降级）。"""
    global _warming
    with _lock:
        if _warming and not force:
            return get_warmup_status()
        rag_ok = bool(_status.get("rag_embed_ready"))
        if (
            _status.get("ready")
            and not force
            and _warmup_cache_satisfied(read_comments=read_comments)
            and (not include_rag or rag_ok)
        ):
            return get_warmup_status()
        _warming = True
        _status["warming"] = True
        _status["ready"] = False
        _status["error"] = ""
        _status["started_at"] = datetime.now().isoformat(timespec="seconds")
        _status["phases"] = {}

    t_all = time.perf_counter()
    err_msg = ""
    try:
        tasks: List[Any] = [
            _warm_langgraph(),
            _warm_tools(read_comments=False),
        ]
        if read_comments:
            tasks.append(_warm_tools(read_comments=True))
        if include_rag:
            tasks.append(_warm_rag_optional())
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                raise r
    except Exception as ex:
        err_msg = str(ex)[:500]
        _LOG.warning(
            "[AI问答-预热|chat_warmup.run_chat_warmup|runtime|硬编执行|部分失败] "
            "warmup_partial; error_message=%s",
            err_msg,
        )
    finally:
        elapsed = int((time.perf_counter() - t_all) * 1000)
        with _lock:
            _warming = False
            _status["warming"] = False
            _status["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _status["elapsed_ms"] = elapsed
            # ready=工具+MCP 已缓存；RAG 嵌入在 phases.rag_embedder 单独标记
            _status["ready"] = bool(_tools_cache.get(False))
            _status["rag_embed_ready"] = bool(
                ((_status.get("phases") or {}).get("rag_embedder") or {}).get("ok")
            )
            _status["error"] = err_msg
            cached_row = _tools_cache.get(False) or ([], {})
            tools_total = int((cached_row[1] or {}).get("total") or 0)
            mcp_count = int((cached_row[1] or {}).get("mcp_count") or 0)
        _LOG.info(
            "[AI问答-预热|chat_warmup.run_chat_warmup|runtime|硬编执行|完成] "
            "ready=%s; elapsed_ms=%s; tools=%s; mcp=%s",
            _status.get("ready"),
            elapsed,
            tools_total,
            mcp_count,
        )
    return get_warmup_status()


async def wait_for_chat_warmup(
    *,
    read_comments: bool = False,
    include_rag: bool = True,
    force: bool = False,
    timeout_sec: float = 120.0,
) -> Dict[str, Any]:
    """阻塞直到运行时预热完成（或超时）。"""
    deadline = time.perf_counter() + max(5.0, float(timeout_sec or 120.0))
    while time.perf_counter() < deadline:
        st = get_warmup_status()
        if st.get("ready") and _warmup_cache_satisfied(read_comments=read_comments):
            return st
        if not st.get("warming"):
            await run_chat_warmup(
                read_comments=read_comments,
                include_rag=include_rag,
                force=force,
            )
            st = get_warmup_status()
            if st.get("ready") and _warmup_cache_satisfied(read_comments=read_comments):
                return st
        await asyncio.sleep(0.35)
    return get_warmup_status()


def schedule_chat_warmup_on_startup() -> None:
    """FastAPI startup：后台预热 MCP + LangGraph（不阻塞 HTTP 监听）。"""

    def _run() -> None:
        try:
            asyncio.run(run_chat_warmup(read_comments=False, include_rag=True))
        except Exception as ex:
            _LOG.warning(
                "[AI问答-预热|chat_warmup.schedule_chat_warmup_on_startup|startup|硬编执行|失败] "
                "error_message=%s",
                str(ex)[:300],
            )

    threading.Thread(target=_run, name="chat-warmup", daemon=True).start()
