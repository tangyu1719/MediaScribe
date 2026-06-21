"""AI 问答固定节点 Pipeline：Ollama 小模型 → 网关大模型 → 规则。

用于意图领域分类 + Query 改写 + 检索词组装（前置预处理，非主问答流式）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .pipeline_llm import (
    PipelineConcurrencyGuard,
    PipelineNode,
    get_pipeline_llm,
    openai_chat_url,
    pipeline_settings,
)
from .pipeline_prompt_segments import build_preprocess_prompt
from .structured_json import (
    DOMAIN_INTENT_LABELS,
    GREEDY_DECODE_PARAMS,
    build_repair_prompt,
    openai_json_response_format,
    parse_preprocess_output,
)

logger = logging.getLogger("sba.agent_pipeline")


@dataclass
class PipelineResult:
    original_query: str
    intent: str
    intent_label: str
    rewritten_query: str
    query_keywords: list[str] = field(default_factory=list)
    retrieval_terms: list[str] = field(default_factory=list)
    rag_query: str = ""
    pipeline_source: str = "rule"  # rule | llm | llm_gateway


def _rule_domain(query: str) -> Tuple[str, str]:
    q = query or ""
    if any(x in q for x in ("知识库", "RAG", "召回", "检索", "向量", "Milvus", "Embedding")):
        return "kb", "知识库"
    if any(x in q for x in ("文档", "链接", "网页", "页面", "评论", "飞书")):
        return "doc_process", "资料处理"
    if any(x in q for x in ("接口", "API", "SDK", "代码", "报错", "异常", "日志")):
        return "devops", "研发运维"
    if any(x in q for x in ("订单", "用户", "退款", "支付", "商品")):
        return "business", "业务系统"
    if any(x in q for x in ("小红书", "小红薯", "xhs", "笔记号")):
        return "social", "社媒分析"
    return "general", "通用"


def _has_business_signal(query: str) -> bool:
    q = query or ""
    keys = (
        "知识库", "RAG", "文档", "链接", "接口", "API", "代码", "报错",
        "小红书", "订单", "Milvus", "检索", "流水线", "Agent", "工具",
    )
    return any(k in q for k in keys)


def _coerce_intent(intent: str, query: str, fallback: str) -> str:
    code = (intent or fallback or "general").strip()
    if code == "chitchat" and _has_business_signal(query):
        logger.info(
            "[AI问答-Pipeline|agent_pipeline|意图纠正|硬编执行|降级] chitchat→general; query=%s",
            query[:80],
        )
        return "general"
    return code or fallback


def _extract_keywords(text: str) -> list[str]:
    q = (text or "").strip()
    keywords: list[str] = []
    for marker in ("知识库", "RAG", "文档", "接口", "报错", "小红书", "流水线", "Agent"):
        if marker in q and marker not in keywords:
            keywords.append(marker)
    for token in re.split(r"[\s，。；;、/?？]+", q):
        token = token.strip()
        if 2 <= len(token) <= 24 and token not in keywords:
            keywords.append(token)
    return keywords[:8]


def _rule_rewrite(query: str, history: list[dict]) -> str:
    q = query.strip()
    if any(p in q for p in ("这个", "那个", "刚才", "上面", "它")) and history:
        for h in reversed(history):
            if h.get("role") == "user" and h.get("content"):
                return f"{h['content'][:80]}；追问：{q}"
    return q


def _build_rag_query(rewritten: str, keywords: list[str], terms: list[str]) -> str:
    parts = [rewritten.strip(), *keywords, *terms]
    merged = " ".join(dict.fromkeys(p for p in parts if p))
    return merged or rewritten.strip()


def _call_llm_with_node(
    node: PipelineNode,
    query: str,
    history: list[dict],
    *,
    timeout: Optional[float] = None,
) -> dict | None:
    import httpx

    cfg = pipeline_settings()
    timeout = timeout if timeout is not None else cfg["pipeline_timeout_sec"]
    hist = "\n".join([f"{h['role']}:{h['content'][:120]}" for h in history[-6:]])
    prompt = build_preprocess_prompt(hist, query)
    url = openai_chat_url(node.base_url)
    greedy = GREEDY_DECODE_PARAMS
    base_payload: dict = {
        "model": node.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": greedy["temperature"],
        "top_p": greedy["top_p"],
        "max_tokens": 256,
    }

    def _post(messages: list[dict], *, with_json_mode: bool) -> httpx.Response:
        payload = dict(base_payload)
        payload["messages"] = messages
        if with_json_mode:
            payload["response_format"] = openai_json_response_format()
        return httpx.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {node.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    with PipelineConcurrencyGuard():
        try:
            resp = _post([{"role": "user", "content": prompt}], with_json_mode=True)
            if resp.status_code >= 400:
                resp = _post([{"role": "user", "content": prompt}], with_json_mode=False)
            if resp.status_code != 200:
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            result = parse_preprocess_output(content)
            if result:
                logger.info(
                    "[AI问答-Pipeline|agent_pipeline|%s|Greedy JSON|硬编执行|完成] ok=true",
                    node.name,
                )
                return result
            repair_prompt = build_repair_prompt(prompt, content)
            resp2 = _post([{"role": "user", "content": repair_prompt}], with_json_mode=True)
            if resp2.status_code >= 400:
                resp2 = _post([{"role": "user", "content": repair_prompt}], with_json_mode=False)
            if resp2.status_code == 200:
                content2 = resp2.json()["choices"][0]["message"]["content"]
                result2 = parse_preprocess_output(content2)
                if result2:
                    logger.info("[AI问答-Pipeline|agent_pipeline|JSON修复重试|硬编执行|完成] ok=true")
                    return result2
        except Exception as exc:
            logger.warning(
                "[AI问答-Pipeline|agent_pipeline|本地LLM|硬编执行|降级] error_type=%s; error_message=%s",
                type(exc).__name__,
                str(exc)[:120],
            )
    return None


def _call_llm_gateway_preprocess(query: str, history: list[dict]) -> dict | None:
    from .ai_chat import load_chat_llm_config, resolve_chat_api_credentials

    cfg = load_chat_llm_config()
    creds = resolve_chat_api_credentials(cfg)
    route = (cfg.get("gateway_task_type_route") or {}).get("qa") or creds.get("model") or ""
    api_key = creds.get("api_key") or ""
    base_url = creds.get("base_url") or ""
    if not api_key or not route:
        return None
    node = PipelineNode(
        id="gateway_pipeline",
        name="Gateway Pipeline Fallback",
        provider=creds.get("provider") or "ark",
        base_url=base_url,
        api_key=api_key,
        model=route,
    )
    return _call_llm_with_node(node, query, history, timeout=8.0)


def _llm_preprocess(query: str, history: list[dict]) -> tuple[dict | None, str]:
    pipeline_node = get_pipeline_llm()
    if pipeline_node:
        result = _call_llm_with_node(pipeline_node, query, history)
        if result:
            return result, "llm"
    if pipeline_settings()["pipeline_gateway_llm_fallback"]:
        result = _call_llm_gateway_preprocess(query, history)
        if result:
            return result, "llm_gateway"
    return None, "rule"


def _pipeline_result_from_llm(
    q: str,
    llm_data: dict,
    rule_code: str,
    rule_label: str,
    *,
    source: str,
) -> PipelineResult:
    intent = _coerce_intent(str(llm_data.get("intent") or rule_code), q, rule_code)
    label = DOMAIN_INTENT_LABELS.get(intent, rule_label)
    if intent == "chitchat":
        return PipelineResult(
            original_query=q,
            intent=intent,
            intent_label=label,
            rewritten_query=q,
            pipeline_source=source,
        )
    rewritten = str(llm_data.get("rewritten_query") or q).strip()
    keywords = [str(x) for x in (llm_data.get("query_keywords") or []) if x][:8]
    if not keywords:
        keywords = _extract_keywords(q)
    terms = [str(x) for x in (llm_data.get("retrieval_terms") or []) if x][:8]
    rag_query = _build_rag_query(rewritten, keywords, terms)
    return PipelineResult(
        original_query=q,
        intent=intent,
        intent_label=label,
        rewritten_query=rewritten,
        query_keywords=keywords,
        retrieval_terms=terms,
        rag_query=rag_query,
        pipeline_source=source,
    )


def run_agent_pipeline(query: str, history: list[dict] | None = None) -> PipelineResult:
    """固定节点：领域意图 → 问句改写/关键词 → 组装 rag_query。"""
    history = history or []
    q = (query or "").strip()
    rule_code, rule_label = _rule_domain(q)

    # 纯寒暄快径
    if len(q) <= 12 and not _has_business_signal(q):
        chit_keys = ("你好", "谢谢", "再见", "嗨", "早上好", "晚安")
        if any(k in q for k in chit_keys):
            return PipelineResult(
                original_query=q,
                intent="chitchat",
                intent_label=DOMAIN_INTENT_LABELS["chitchat"],
                rewritten_query=q,
                pipeline_source="rule",
            )

    llm_data, source = _llm_preprocess(q, history)
    if llm_data:
        return _pipeline_result_from_llm(q, llm_data, rule_code, rule_label, source=source)

    rewritten = _rule_rewrite(q, history)
    keywords = _extract_keywords(q)
    terms: list[str] = []
    rag_query = _build_rag_query(rewritten, keywords, terms)
    return PipelineResult(
        original_query=q,
        intent=rule_code,
        intent_label=rule_label,
        rewritten_query=rewritten,
        query_keywords=keywords,
        retrieval_terms=terms,
        rag_query=rag_query,
        pipeline_source="rule",
    )


def merge_pipeline_into_snapshot(snap: Dict[str, Any], pipeline: PipelineResult) -> Dict[str, Any]:
    """将 pipeline 结果写入意图改写快照（供反馈与 RAG 使用）。"""
    out = dict(snap or {})
    out["domain"] = pipeline.intent_label
    out["domain_code"] = pipeline.intent
    out["rewritten_query"] = pipeline.rewritten_query
    out["query_keywords"] = pipeline.query_keywords
    out["retrieval_terms"] = pipeline.retrieval_terms
    out["rag_query"] = pipeline.rag_query
    out["pipeline_source"] = pipeline.pipeline_source
    if pipeline.intent == "chitchat":
        out["needs_rag"] = False
    return out
