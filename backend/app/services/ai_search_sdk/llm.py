"""AI 辅助搜索 — OpenAI 兼容 LLM 查询扩展 / 重排（真实调用）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field, field_validator

from ..pipeline_llm import PipelineConcurrencyGuard, PipelineNode, openai_chat_url
from ..structured_json import GREEDY_DECODE_PARAMS, loads_json, validate_model
from .ollama_config import (
    ai_search_ollama_settings,
    llm_timeout_for_node,
    resolve_ai_search_llm_nodes,
)
from .types import SearchHit

_log = logging.getLogger("sba.ai_search_sdk.llm")


class AiSearchExpandOutput(BaseModel):
    expanded_terms: List[str] = Field(default_factory=list)
    intent_hint: str = ""

    @field_validator("expanded_terms")
    @classmethod
    def _trim_terms(cls, v: List[str]) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for item in v or []:
            t = str(item or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t[:48])
        return out[:8]


def build_ai_search_expand_prompt(query: str, *, domain_hint: str = "") -> str:
    hint = (domain_hint or "通用").strip()
    return (
        "你是 SuperBizAgent 的搜索查询扩展模块。"
        "用户输入关键词后，你需要输出 JSON，帮助系统在工具、链接、任务、技能等数据源中检索。"
        '字段：{"expanded_terms":["..."],"intent_hint":"..."}；'
        "expanded_terms 为 1～6 个检索词或同义表达；"
        "intent_hint 为一句话说明用户可能在找什么。"
        "仅输出 JSON，temperature=0。"
        f"\n领域提示：{hint}"
        f"\n用户关键词：{(query or '')[:240]}"
    )


def _invoke_openai_chat(node: PipelineNode, prompt: str, *, max_tokens: int = 320) -> Optional[str]:
    greedy = GREEDY_DECODE_PARAMS
    timeout = llm_timeout_for_node(node)
    with PipelineConcurrencyGuard():
        resp = httpx.post(
            openai_chat_url(node.base_url),
            json={
                "model": node.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": greedy["temperature"],
                "top_p": greedy["top_p"],
                "max_tokens": max_tokens,
            },
            headers={
                "Authorization": f"Bearer {node.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    if resp.status_code != 200:
        _log.warning(
            "[AI辅助搜索-LLM调用|ai_search_sdk.llm|invoke_openai_chat|Agent执行|失败] "
            "node=%s; status=%s; error_message=%s",
            node.id,
            resp.status_code,
            (resp.text or "")[:160],
        )
        return None
    body = resp.json()
    return str(body["choices"][0]["message"]["content"] or "")


def expand_query_llm(query: str, *, domain_hint: str = "") -> Dict[str, Any]:
    """调用 OpenAI 兼容接口扩展检索词；Ollama 优先，失败可降级网关。"""
    q = (query or "").strip()
    if not q:
        return {"expanded_terms": [], "intent_hint": "", "llm_powered": False, "node_id": ""}
    cfg = ai_search_ollama_settings()
    if not cfg["enabled"]:
        return {
            "expanded_terms": [],
            "intent_hint": "",
            "llm_powered": False,
            "node_id": "",
            "skipped": "ollama_disabled",
        }
    prompt = build_ai_search_expand_prompt(q, domain_hint=domain_hint)
    for node in resolve_ai_search_llm_nodes():
        try:
            raw = _invoke_openai_chat(node, prompt)
            if not raw:
                continue
            data = loads_json(raw, kind="object")
            validated = validate_model(data, AiSearchExpandOutput) if isinstance(data, dict) else None
            if not validated:
                continue
            return {
                "expanded_terms": validated.expanded_terms,
                "intent_hint": validated.intent_hint,
                "llm_powered": True,
                "node_id": node.id,
            }
        except Exception as exc:
            _log.warning(
                "[AI辅助搜索-查询扩展|ai_search_sdk.llm|expand_query_llm|Agent执行|跳过] "
                "node=%s; error_type=%s; error_message=%s",
                node.id,
                type(exc).__name__,
                str(exc)[:120],
            )
    return {"expanded_terms": [], "intent_hint": "", "llm_powered": False, "node_id": ""}


def rerank_hits_llm(query: str, hits: List[SearchHit], *, limit: int = 10) -> List[SearchHit]:
    """可选 LLM 重排：对候选列表按相关度重新排序。"""
    q = (query or "").strip()
    if not q or not hits:
        return hits
    preview = []
    for idx, hit in enumerate(hits[: min(len(hits), 20)]):
        preview.append(
            {
                "idx": idx,
                "id": hit.id,
                "title": hit.title,
                "provider_id": hit.provider_id,
                "category": hit.category,
                "description": (hit.description or "")[:120],
            }
        )
    prompt = (
        "你是 SuperBizAgent 搜索重排模块。"
        "根据用户关键词，对候选条目按相关度从高到低排序。"
        '仅输出 JSON：{"ordered_idx":[0,2,1,...]}，ordered_idx 为候选 idx 排列。'
        f"\n用户关键词：{q[:240]}"
        f"\n候选：{preview}"
    )
    for node in resolve_ai_search_llm_nodes():
        try:
            raw = _invoke_openai_chat(node, prompt, max_tokens=180)
            if not raw:
                continue
            data = loads_json(raw, kind="object")
            if not isinstance(data, dict):
                continue
            ordered = data.get("ordered_idx")
            if not isinstance(ordered, list):
                continue
            reordered: List[SearchHit] = []
            seen: set[int] = set()
            for raw_idx in ordered:
                try:
                    idx = int(raw_idx)
                except (TypeError, ValueError):
                    continue
                if idx < 0 or idx >= len(hits) or idx in seen:
                    continue
                seen.add(idx)
                reordered.append(hits[idx])
            for idx, hit in enumerate(hits):
                if idx in seen:
                    continue
                reordered.append(hit)
            return reordered[: max(1, int(limit or 10))]
        except Exception as exc:
            _log.warning(
                "[AI辅助搜索-重排|ai_search_sdk.llm|rerank_hits_llm|Agent执行|跳过] "
                "node=%s; error_type=%s; error_message=%s",
                node.id,
                type(exc).__name__,
                str(exc)[:120],
            )
    return hits[: max(1, int(limit or 10))]
