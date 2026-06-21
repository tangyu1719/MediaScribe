"""Ollama 小模型 Pipeline LLM：意图预处理专用 + 并发限流。

Ollama 单模型默认同模型请求会排队；高并发需：
1. 本模块 Semaphore 限制同时 in-flight 请求数；
2. 服务端设置 OLLAMA_NUM_PARALLEL（与 OLLAMA_PIPELINE_CONCURRENCY 对齐）；
3. 超时快速降级至网关大模型（agent_pipeline.PIPELINE_GATEWAY_LLM_FALLBACK）。
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

_log = logging.getLogger("sba.pipeline_llm")

_PIPELINE_SEM: Optional[threading.Semaphore] = None
_PIPELINE_SEM_SIZE = 0


@dataclass(frozen=True)
class PipelineNode:
    id: str
    name: str
    provider: str
    base_url: str
    api_key: str
    model: str
    priority: int = 1
    weight: int = 100
    status: str = "active"


def pipeline_settings() -> Dict[str, Any]:
    return {
        "ollama_base_url": (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1").strip(),
        "ollama_model": (os.environ.get("OLLAMA_MODEL") or "qwen2:0.5b").strip(),
        "pipeline_gateway_llm_fallback": (os.environ.get("PIPELINE_GATEWAY_LLM_FALLBACK", "true").strip().lower() not in ("0", "false", "no")),
        "pipeline_concurrency": max(1, min(32, int(os.environ.get("OLLAMA_PIPELINE_CONCURRENCY", "4") or "4"))),
        "pipeline_timeout_sec": max(1.0, min(30.0, float(os.environ.get("OLLAMA_PIPELINE_TIMEOUT_SEC", "3") or "3"))),
    }


def _ensure_semaphore() -> threading.Semaphore:
    global _PIPELINE_SEM, _PIPELINE_SEM_SIZE
    size = pipeline_settings()["pipeline_concurrency"]
    if _PIPELINE_SEM is None or _PIPELINE_SEM_SIZE != size:
        _PIPELINE_SEM = threading.Semaphore(size)
        _PIPELINE_SEM_SIZE = size
    return _PIPELINE_SEM


def get_pipeline_llm() -> Optional[PipelineNode]:
    cfg = pipeline_settings()
    base = cfg["ollama_base_url"]
    model = cfg["ollama_model"]
    if not base or not model:
        return None
    return PipelineNode(
        id="ollama_pipeline",
        name="Ollama Pipeline",
        provider="openai_compatible",
        base_url=base,
        api_key="ollama",
        model=model,
    )


def normalize_openai_base_url(base_url: str) -> str:
    u = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    return u


def openai_chat_url(base_url: str) -> str:
    return f"{normalize_openai_base_url(base_url)}/chat/completions"


class PipelineConcurrencyGuard:
    """上下文管理器：占用一个 pipeline 并发槽。"""

    def __enter__(self) -> "PipelineConcurrencyGuard":
        self._sem = _ensure_semaphore()
        self._sem.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self._sem.release()


def probe_ollama_health() -> Dict[str, Any]:
    """探测 Ollama /api/tags（供 platform_health 调用）。"""
    import time

    import httpx

    cfg = pipeline_settings()
    base = cfg["ollama_base_url"]
    model = cfg["ollama_model"]
    t0 = time.perf_counter()
    if not base:
        return {
            "id": "ollama",
            "label": "Ollama 预处理",
            "status": "warn",
            "error": "未配置 OLLAMA_BASE_URL",
            "latency_ms": 0,
            "detail": {"hint": "意图预处理将使用 API 网关大模型", "settings_href": "/#ops"},
        }
    tags_url = base.replace("/v1", "").rstrip("/") + "/api/tags"
    try:
        resp = httpx.get(tags_url, timeout=5.0)
        ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 200:
            models = [m.get("name", "?") for m in resp.json().get("models", [])]
            model_ok = any(model in m or m.startswith(model.split(":")[0]) for m in models) if models else False
            return {
                "id": "ollama",
                "label": "Ollama 预处理",
                "status": "ok" if model_ok or not models else "warn",
                "latency_ms": ms,
                "detail": {
                    "url": base,
                    "model": model,
                    "installed": models,
                    "concurrency": cfg["pipeline_concurrency"],
                    "model_installed": model_ok,
                },
            }
        return {
            "id": "ollama",
            "label": "Ollama 预处理",
            "status": "warn",
            "latency_ms": ms,
            "error": f"HTTP {resp.status_code}",
            "detail": {"hint": "意图预处理将使用 API 网关大模型"},
        }
    except Exception as exc:
        return {
            "id": "ollama",
            "label": "Ollama 预处理",
            "status": "warn",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "error": str(exc)[:120],
            "detail": {"hint": "意图预处理将使用 API 网关大模型"},
        }
