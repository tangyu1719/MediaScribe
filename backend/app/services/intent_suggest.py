"""意图纠偏：内置备选 + LLM 推测（真实调用）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .chat_feedback import INTENT_LABELS
from .pipeline_prompt_segments import build_intent_suggest_prompt
from .structured_json import GREEDY_DECODE_PARAMS, DOMAIN_INTENT_LABELS, parse_intent_suggest_items

logger = logging.getLogger("sba.intent_suggest")

# 前端纠正下拉与内置 code 映射
INTENT_CODE_MAP: Dict[str, str] = {
    "知识库": "kb",
    "资料处理": "doc_process",
    "研发运维": "devops",
    "业务系统": "business",
    "社媒分析": "social",
    "通用": "general",
}


def build_builtin_alternatives(detected_intent: str) -> List[Dict[str, Any]]:
    code = (detected_intent or "").strip()
    if code in DOMAIN_INTENT_LABELS:
        detected_code = code
    else:
        detected_code = INTENT_CODE_MAP.get(code, "")
    out: List[Dict[str, Any]] = []
    for label in INTENT_LABELS:
        alt_code = INTENT_CODE_MAP.get(label, "general")
        if alt_code == detected_code or label == detected_intent:
            continue
        out.append({"code": alt_code, "label": label, "source": "builtin"})
    return out


def suggest_intents_llm(
    question: str,
    answer: str,
    detected_intent: str,
    detected_label: str,
) -> List[Dict[str, Any]]:
    q = (question or "").strip()
    a = (answer or "").strip()
    if not q or len(a) < 10:
        return []
    from .ai_chat import load_chat_llm_config, resolve_chat_api_credentials
    from .pipeline_llm import PipelineNode, openai_chat_url, pipeline_settings

    enum_text = "、".join(f"{k}={v}" for k, v in DOMAIN_INTENT_LABELS.items())
    prompt = build_intent_suggest_prompt(q, a, detected_intent, detected_label, enum_text)
    greedy = GREEDY_DECODE_PARAMS

    # 优先 Ollama，失败走 QA 网关
    nodes = []
    from .pipeline_llm import get_pipeline_llm

    pl = get_pipeline_llm()
    if pl:
        nodes.append(pl)
    cfg = load_chat_llm_config()
    creds = resolve_chat_api_credentials(cfg)
    route = (cfg.get("gateway_task_type_route") or {}).get("qa") or creds.get("model") or ""
    if creds.get("api_key") and route:
        nodes.append(
            PipelineNode(
                id="gateway_intent_suggest",
                name="Gateway Intent Suggest",
                provider=creds.get("provider") or "ark",
                base_url=creds.get("base_url") or "",
                api_key=creds.get("api_key") or "",
                model=route,
            )
        )

    import httpx
    from .pipeline_llm import PipelineConcurrencyGuard

    for node in nodes:
        try:
            with PipelineConcurrencyGuard():
                resp = httpx.post(
                    openai_chat_url(node.base_url),
                    json={
                        "model": node.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "temperature": greedy["temperature"],
                        "top_p": greedy["top_p"],
                        "max_tokens": 256,
                    },
                    headers={
                        "Authorization": f"Bearer {node.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=pipeline_settings()["pipeline_timeout_sec"] if node.id == "ollama_pipeline" else 12.0,
                )
            if resp.status_code != 200:
                continue
            raw = resp.json()["choices"][0]["message"]["content"]
            parsed = parse_intent_suggest_items(raw)
            out: List[Dict[str, Any]] = []
            for item in parsed:
                code = item.get("code", "unknown")
                label = item.get("label") or ""
                summary = item.get("summary") or ""
                if not label:
                    continue
                if code in DOMAIN_INTENT_LABELS:
                    label = DOMAIN_INTENT_LABELS[code]
                out.append({"code": code, "label": label, "summary": summary, "source": "llm"})
            if out:
                return out
        except Exception as exc:
            logger.warning(
                "[AI问答-意图纠偏|intent_suggest|LLM推测|Agent执行|跳过] node=%s; error_type=%s; error_message=%s",
                node.id,
                type(exc).__name__,
                str(exc)[:120],
            )
    return []


def build_intent_alternatives(
    *,
    question: str,
    answer: str,
    detected_intent: str,
    detected_label: str,
    retrieval_terms: Optional[List[str]] = None,
    include_llm: bool = True,
) -> Dict[str, Any]:
    builtin = build_builtin_alternatives(detected_intent)
    term_hints = [str(t).strip() for t in (retrieval_terms or []) if str(t).strip()][:8]
    suggested = (
        suggest_intents_llm(question, answer, detected_intent, detected_label) if include_llm else []
    )
    shown: List[str] = []
    for b in builtin:
        shown.append(f"builtin:{b['code']}")
    for s in suggested:
        shown.append(f"llm:{s.get('code')}:{s.get('label')}")
    for h in term_hints:
        shown.append(f"term:{h}")
    return {
        "detected_intent": detected_intent,
        "detected_intent_label": detected_label,
        "builtin": builtin,
        "suggested": suggested,
        "term_hints": term_hints,
        "intent_suggestions_shown": shown,
        "llm_powered": bool(suggested),
    }
