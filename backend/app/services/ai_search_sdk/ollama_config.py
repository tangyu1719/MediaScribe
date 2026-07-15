"""AI 辅助搜索 — Ollama / OpenAI 兼容接口配置（复用 pipeline 环境变量 + 搜索专用项）。"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from ..pipeline_llm import (
    PipelineNode,
    get_pipeline_llm,
    normalize_openai_base_url,
    openai_chat_url,
    pipeline_settings,
    probe_ollama_health,
)

_log = logging.getLogger("sba.ai_search_sdk.ollama_config")


def _env_bool(key: str, default: bool = True) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def ai_search_ollama_settings() -> Dict[str, Any]:
    """读取 AI 搜索 Ollama 配置（与 .env / OPS 面板 pipeline-config 对齐）。"""
    pipeline = pipeline_settings()
    timeout_raw = (os.environ.get("OLLAMA_AI_SEARCH_TIMEOUT_SEC") or "").strip()
    if timeout_raw:
        timeout_sec = max(3.0, min(60.0, float(timeout_raw)))
    else:
        # 搜索 JSON 扩展比意图预处理略慢，默认 12s（pipeline 默认 3s 易超时）
        timeout_sec = 12.0
    gateway_fb_env = (os.environ.get("OLLAMA_AI_SEARCH_GATEWAY_FALLBACK") or "").strip()
    if gateway_fb_env:
        gateway_fallback = _env_bool("OLLAMA_AI_SEARCH_GATEWAY_FALLBACK", True)
    else:
        gateway_fallback = bool(pipeline.get("pipeline_gateway_llm_fallback"))
    return {
        "enabled": _env_bool("OLLAMA_AI_SEARCH_ENABLED", True),
        "ollama_base_url": pipeline["ollama_base_url"],
        "ollama_model": pipeline["ollama_model"],
        "timeout_sec": timeout_sec,
        "gateway_fallback": gateway_fallback,
        "concurrency": pipeline["pipeline_concurrency"],
        "openai_chat_url": openai_chat_url(pipeline["ollama_base_url"]),
        "tags_url": normalize_openai_base_url(pipeline["ollama_base_url"]).replace("/v1", "") + "/api/tags",
    }


def get_ai_search_ollama_node() -> Optional[PipelineNode]:
    """AI 搜索专用 Ollama 节点（与意图预处理共用 base_url/model，独立 node_id 便于日志区分）。"""
    cfg = ai_search_ollama_settings()
    if not cfg["enabled"]:
        return None
    base = cfg["ollama_base_url"]
    model = cfg["ollama_model"]
    if not base or not model:
        return None
    return PipelineNode(
        id="ollama_ai_search",
        name="Ollama AI Search",
        provider="openai_compatible",
        base_url=base,
        api_key="ollama",
        model=model,
    )


def resolve_ai_search_llm_nodes() -> List[PipelineNode]:
    """Ollama 优先；可选网关降级。"""
    cfg = ai_search_ollama_settings()
    nodes: List[PipelineNode] = []
    ollama = get_ai_search_ollama_node()
    if ollama:
        nodes.append(ollama)
    if not cfg["gateway_fallback"]:
        return nodes
    try:
        from ..ai_chat import load_chat_llm_config, resolve_chat_api_credentials

        llm_cfg = load_chat_llm_config()
        creds = resolve_chat_api_credentials(llm_cfg)
        route = (llm_cfg.get("gateway_task_type_route") or {}).get("qa") or creds.get("model") or ""
        if creds.get("api_key") and route:
            nodes.append(
                PipelineNode(
                    id="gateway_ai_search",
                    name="Gateway AI Search",
                    provider=creds.get("provider") or "ark",
                    base_url=creds.get("base_url") or "",
                    api_key=creds.get("api_key") or "",
                    model=route,
                )
            )
    except Exception as exc:
        _log.warning(
            "[AI辅助搜索-Ollama配置|ollama_config|resolve_nodes|硬编执行|跳过] "
            "error_type=%s; error_message=%s",
            type(exc).__name__,
            str(exc)[:120],
        )
    return nodes


def llm_timeout_for_node(node: PipelineNode) -> float:
    cfg = ai_search_ollama_settings()
    if node.id in ("ollama_ai_search", "ollama_pipeline"):
        return float(cfg["timeout_sec"])
    return 12.0


def probe_ai_search_ollama_health() -> Dict[str, Any]:
    """探测 Ollama 是否可用于 AI 搜索。"""
    cfg = ai_search_ollama_settings()
    base_health = probe_ollama_health()
    t0 = time.perf_counter()
    chat_probe: Dict[str, Any] = {"ok": False, "latency_ms": 0, "error": ""}
    node = get_ai_search_ollama_node()
    if node and base_health.get("status") == "ok":
        try:
            resp = httpx.post(
                openai_chat_url(node.base_url),
                json={
                    "model": node.model,
                    "messages": [{"role": "user", "content": '仅输出 JSON：{"ping":"ok"}'}],
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 32,
                },
                headers={"Authorization": f"Bearer {node.api_key}", "Content-Type": "application/json"},
                timeout=min(float(cfg["timeout_sec"]), 20.0),
            )
            chat_probe["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            chat_probe["ok"] = resp.status_code == 200
            if resp.status_code != 200:
                chat_probe["error"] = (resp.text or "")[:160]
        except Exception as exc:
            chat_probe["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            chat_probe["error"] = str(exc)[:160]
    status = "ok"
    if not cfg["enabled"]:
        status = "disabled"
    elif base_health.get("status") != "ok":
        status = "warn"
    elif not chat_probe.get("ok"):
        status = "warn"
    return {
        "status": status,
        "enabled": cfg["enabled"],
        "ollama_base_url": cfg["ollama_base_url"],
        "ollama_model": cfg["ollama_model"],
        "timeout_sec": cfg["timeout_sec"],
        "gateway_fallback": cfg["gateway_fallback"],
        "openai_chat_url": cfg["openai_chat_url"],
        "tags_health": base_health,
        "chat_probe": chat_probe,
        "node_id": node.id if node else "",
    }


def apply_ai_search_ollama_config(body: Dict[str, Any]) -> Dict[str, Any]:
    """运行时更新 Ollama 配置（写入进程环境变量，与 pipeline-config 一致）。"""
    if "enabled" in body:
        os.environ["OLLAMA_AI_SEARCH_ENABLED"] = "1" if body.get("enabled") else "0"
    if "ollama_model" in body:
        model = str(body.get("ollama_model") or "").strip()
        if model:
            os.environ["OLLAMA_MODEL"] = model
    if "ollama_base_url" in body:
        url = str(body.get("ollama_base_url") or "").strip()
        if url:
            os.environ["OLLAMA_BASE_URL"] = url
    if "timeout_sec" in body:
        os.environ["OLLAMA_AI_SEARCH_TIMEOUT_SEC"] = str(
            max(3.0, min(60.0, float(body.get("timeout_sec") or 12)))
        )
    if "gateway_fallback" in body:
        os.environ["OLLAMA_AI_SEARCH_GATEWAY_FALLBACK"] = (
            "1" if body.get("gateway_fallback") else "0"
        )
    if "pipeline_concurrency" in body:
        os.environ["OLLAMA_PIPELINE_CONCURRENCY"] = str(
            max(1, min(32, int(body.get("pipeline_concurrency") or 4)))
        )
    return ai_search_ollama_settings()
