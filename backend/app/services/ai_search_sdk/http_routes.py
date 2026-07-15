"""SearchBox SDK — HTTP 路由（从 main.py 抽离，保持薄路由）。"""
from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import HTTPException, Request

from .facade import SearchBoxSDK
from .ollama_config import (
    ai_search_ollama_settings,
    apply_ai_search_ollama_config,
    get_ai_search_ollama_node,
    probe_ai_search_ollama_health,
)
from .service import get_search_box_sdk
from .types import SearchQuery


def _sdk() -> SearchBoxSDK:
    return get_search_box_sdk()


def list_indices() -> Dict[str, Any]:
    sdk = _sdk()
    return {
        "ok": True,
        "indices": sdk.list_indices(),
        "providers": sdk.list_indices(),
        "ollama": ai_search_ollama_settings(),
    }


def ollama_config_get() -> Dict[str, Any]:
    cfg = ai_search_ollama_settings()
    return {"ok": True, "configured": bool(get_ai_search_ollama_node()), **cfg}


async def ollama_config_put(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}
    cfg = apply_ai_search_ollama_config(body if isinstance(body, dict) else {})
    return {"ok": True, "configured": bool(get_ai_search_ollama_node()), **cfg}


def ollama_health() -> Dict[str, Any]:
    return {"ok": True, **probe_ai_search_ollama_health()}


async def search_body(body: Dict[str, Any], *, es_format: bool = False) -> Dict[str, Any]:
    q = SearchQuery.from_body(body if isinstance(body, dict) else {})
    if not (q.q or "").strip():
        raise HTTPException(400, "缺少 q / query")
    result = _sdk().execute(q)
    payload = result.to_es_response() if es_format else result.to_dict()
    return {"ok": True, **payload}


async def search(request: Request, *, es_format: bool = False) -> Dict[str, Any]:
    body = await request.json()
    return await search_body(body if isinstance(body, dict) else {}, es_format=es_format)


async def suggest(request: Request) -> Dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    q = str(body.get("q") or body.get("query") or "").strip()
    if not q:
        raise HTTPException(400, "缺少 q / query")
    indices = body.get("indices") or body.get("providers")
    if isinstance(indices, list):
        indices = [str(x).strip() for x in indices if str(x).strip()]
    else:
        indices = None
    size = max(1, min(int(body.get("size") or body.get("limit") or 8), 20))
    result = _sdk().suggest(q, indices=indices, size=size)
    return {"ok": True, **result.to_dict()}


async def facets(request: Request) -> Dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    q = str(body.get("q") or body.get("query") or "").strip()
    if not q:
        raise HTTPException(400, "缺少 q / query")
    indices = body.get("indices") or body.get("providers")
    if isinstance(indices, list):
        indices = [str(x).strip() for x in indices if str(x).strip()]
    else:
        indices = None
    aggs = _sdk().facets(q, indices=indices)
    return {"ok": True, "q": q, "aggregations": aggs}


async def index_disable(index_id: str) -> Dict[str, Any]:
    ok = _sdk().disable_index(index_id)
    if not ok:
        raise HTTPException(404, f"索引不存在: {index_id}")
    return {"ok": True, "index_id": index_id, "enabled": False}


async def index_enable(index_id: str) -> Dict[str, Any]:
    ok = _sdk().enable_index(index_id)
    if not ok:
        raise HTTPException(404, f"索引不存在: {index_id}")
    return {"ok": True, "index_id": index_id, "enabled": True}
