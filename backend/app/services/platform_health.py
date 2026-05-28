"""平台启动健康检查：模型网关 / RAG / MCP / 本地工具目录。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from .config import load_config
from .milvus_health import check_milvus
from .vector_connection import get_connection_state, probe_connection

_log = logging.getLogger("sba.platform_health")

T = TypeVar("T")

_HEALTH_CACHE: Dict[str, Any] = {
    "ready": False,
    "checked_at": 0.0,
    "items": [],
    "summary": {"ok": 0, "warn": 0, "error": 0},
}
_HEALTH_LOCK = asyncio.Lock()
_BACKOFF_SEC = (0.4, 0.8, 1.6)
_MCP_PROBE_TIMEOUT_SEC = 8.0


async def _retry_async(
    label: str,
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
) -> Dict[str, Any]:
    last_err = ""
    for attempt in range(1, retries + 1):
        t0 = time.perf_counter()
        try:
            result = await fn()
            ms = int((time.perf_counter() - t0) * 1000)
            return {
                "id": label,
                "status": "ok",
                "attempt": attempt,
                "latency_ms": ms,
                "detail": result if isinstance(result, dict) else {"result": result},
                "error": "",
            }
        except Exception as ex:
            last_err = str(ex)
            _log.warning(
                "[平台健康检查|platform_health._retry_async|%s|硬编执行|重试] "
                "attempt=%s; error_type=%s; error_message=%s",
                label,
                attempt,
                type(ex).__name__,
                last_err[:200],
            )
            if attempt < retries:
                await asyncio.sleep(_BACKOFF_SEC[min(attempt - 1, len(_BACKOFF_SEC) - 1)])
    return {
        "id": label,
        "status": "error",
        "attempt": retries,
        "latency_ms": 0,
        "detail": {},
        "error": last_err[:500],
    }


_AUTO_MODEL_TOKENS = frozenset(
    {"auto", "default", "节点池", "auto (节点池)", "auto(节点池)"}
)


def _is_auto_model_name(model: str) -> bool:
    m = (model or "").strip().lower()
    if not m:
        return True
    if m in _AUTO_MODEL_TOKENS:
        return True
    return m.startswith("auto") and "节点" in m


def _check_model_gateway(cfg: Dict[str, Any], *, chat_model: str = "") -> Dict[str, Any]:
    model = (chat_model or cfg.get("ai_chat_model") or "").strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    route_mode = (cfg.get("gateway_route_mode") or "").strip()
    is_auto = _is_auto_model_name(model)
    chosen = model
    gw_err = ""
    if is_auto:
        try:
            from .pipeline_logging import resolve_gateway_models

            routes = resolve_gateway_models(cfg, agent_name="ai_chat", task_type="chat")
            chosen = routes.get("primary_endpoint") or routes.get("gateway_chosen") or ""
            gw_err = routes.get("gateway_error") or ""
            if not chosen and gw_err:
                raise RuntimeError(gw_err)
        except Exception as ex:
            raise RuntimeError(f"AUTO 路由失败: {ex}") from ex
    if not chosen:
        raise RuntimeError("未配置 ai_chat_model 且 AUTO 未选出节点")
    return {
        "provider": provider,
        "route_mode": route_mode,
        "model": chosen,
        "auto_selected": is_auto,
    }


def _check_rag_vector() -> Dict[str, Any]:
    st = get_connection_state()
    host = st.get("params", {}).get("host") or st.get("host") or "127.0.0.1"
    port = st.get("params", {}).get("port") or st.get("port") or "19530"
    if str(host).lower() in ("local", "embedded", "memory"):
        return {"skipped": True, "reason": "本地向量模式，跳过 Milvus 探测"}
    milvus = check_milvus(host=host, port=port)
    if not milvus.get("milvus_ok"):
        raise RuntimeError(milvus.get("error") or "Milvus 不可达")
    return milvus


def _check_kb_connection() -> Dict[str, Any]:
    """轻量知识库探测：读 file_records，不拉全量 Milvus 快照（避免健康检查卡 60s+）。"""
    from .kb_rag import agent_kb_dir, load_merged_file_records

    records = load_merged_file_records()
    rec_path = agent_kb_dir() / "file_records.json"
    if not rec_path.is_file() and not records:
        raise RuntimeError("知识库索引不存在，请先在 RAG 页导入文档")
    return {
        "files": len(records),
        "chunks": sum(int(r.get("chunk_count") or 0) for r in records),
        "storage_backend": "file_records",
        "file_records_path": str(rec_path),
    }


async def _probe_one_mcp(alias: str) -> Dict[str, Any]:
    from .mcp_langchain import probe_mcp_server_health

    try:
        return await asyncio.wait_for(
            probe_mcp_server_health(alias),
            timeout=_MCP_PROBE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as ex:
        raise RuntimeError(f"MCP {alias} 探测超时({_MCP_PROBE_TIMEOUT_SEC}s)") from ex


async def _check_mcp_servers() -> Dict[str, Any]:
    from .mcp_langchain import load_mcp_server_dict

    servers = load_mcp_server_dict()
    if not servers:
        return {"servers": [], "message": "未配置 MCP 服务", "failed_count": 0}

    async def _one(alias: str) -> Dict[str, Any]:
        async def _run(a: str = alias) -> Dict[str, Any]:
            return await _probe_one_mcp(a)

        row = await _retry_async(f"mcp:{alias}", _run, retries=2)
        return {
            "alias": alias,
            "status": row["status"],
            "error": row.get("error") or "",
            "latency_ms": row.get("latency_ms", 0),
            "config_path": "mcp_servers.json",
        }

    results = list(await asyncio.gather(*[_one(a) for a in servers]))
    failed = [r for r in results if r["status"] != "ok"]
    if failed and len(failed) == len(results):
        raise RuntimeError(f"全部 MCP 不可用: {failed[0].get('error', '')[:120]}")
    return {"servers": results, "failed_count": len(failed)}


def _check_local_tools() -> Dict[str, Any]:
    from .builtin_tools import list_builtin_tools
    from .skill_registry import list_skills

    builtins = list_builtin_tools() or []
    skills = list_skills() or []
    return {
        "builtin_count": len(builtins),
        "skill_count": len(skills),
        "note": "内置与 SKILL 为本地目录，启动即加载",
    }


async def run_platform_health_check(
    *, force: bool = False, chat_model: str = ""
) -> Dict[str, Any]:
    """全量健康检查（启动时与手动刷新）。"""
    async with _HEALTH_LOCK:
        now = time.time()
        if not force and _HEALTH_CACHE.get("ready") and now - float(_HEALTH_CACHE.get("checked_at") or 0) < 30:
            return dict(_HEALTH_CACHE)

        cfg = load_config()
        items: List[Dict[str, Any]] = []

        chat_m = (chat_model or "").strip()

        async def _model():
            return await asyncio.to_thread(_check_model_gateway, cfg, chat_model=chat_m)

        items.append(
            {
                **await _retry_async("ai_model", _model, retries=3),
                "label": "AI 模型 / 网关",
                "category": "model",
                "settings_href": "/#ops",
            }
        )

        async def _rag_vec():
            return await asyncio.to_thread(_check_rag_vector)

        rag_row = await _retry_async("rag_vector", _rag_vec, retries=3)
        rag_row["label"] = "RAG 向量库 (Milvus)"
        rag_row["category"] = "rag"
        rag_row["settings_href"] = "/#rag"
        items.append(rag_row)

        async def _kb():
            return await asyncio.to_thread(_check_kb_connection)

        kb_row = await _retry_async("rag_kb", _kb, retries=3)
        kb_row["label"] = "知识库连接"
        kb_row["category"] = "rag"
        kb_row["settings_href"] = "/#rag"
        items.append(kb_row)

        mcp_row = await _retry_async("mcp", _check_mcp_servers, retries=1)
        mcp_row["label"] = "MCP 连接"
        mcp_row["category"] = "mcp"
        mcp_row["settings_href"] = "/#orch"
        failed_mcp = int((mcp_row.get("detail") or {}).get("failed_count") or 0)
        if mcp_row.get("status") == "ok" and failed_mcp > 0:
            mcp_row["status"] = "warn"
        items.append(mcp_row)

        async def _tools():
            return await asyncio.to_thread(_check_local_tools)

        tools_row = await _retry_async("tools_local", _tools, retries=1)
        tools_row["label"] = "工具 / SKILL（本地）"
        tools_row["category"] = "tools"
        tools_row["settings_href"] = "/#orch"
        items.append(tools_row)

        summary = {"ok": 0, "warn": 0, "error": 0}
        for it in items:
            st = it.get("status") or "error"
            if st == "ok":
                summary["ok"] += 1
            elif st == "warn":
                summary["warn"] += 1
            else:
                summary["error"] += 1

        payload = {
            "ready": True,
            "checked_at": now,
            "items": items,
            "summary": summary,
            "all_ok": summary["error"] == 0,
        }
        _HEALTH_CACHE.clear()
        _HEALTH_CACHE.update(payload)
        _log.info(
            "[平台健康检查|platform_health.run_platform_health_check|platform|硬编执行|完成] "
            "ok=%s; warn=%s; error=%s",
            summary["ok"],
            summary["warn"],
            summary["error"],
        )
        return dict(payload)


def get_platform_health_snapshot() -> Dict[str, Any]:
    return dict(_HEALTH_CACHE) if _HEALTH_CACHE.get("ready") else {
        "ready": False,
        "items": [],
        "summary": {"ok": 0, "warn": 0, "error": 0},
        "all_ok": False,
    }


async def schedule_startup_health_check() -> None:
    """应用启动后后台跑一轮健康检查（由 FastAPI startup 调用）。"""
    try:
        await run_platform_health_check(force=True)
    except Exception as ex:
        _log.warning(
            "[平台健康检查|platform_health.schedule_startup_health_check|startup|硬编执行|失败] "
            "error_type=%s; error_message=%s",
            type(ex).__name__,
            ex,
        )
