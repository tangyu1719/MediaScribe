"""批次 digest — RAG 检索 + 真实 LLM 预览摘要。"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .kb_rag import kb_search

_log = logging.getLogger("sba.creator_digest")
_CHAIN = "社媒订阅-增量拉取-单条分析-批次digest"


def _load_llm_cfg() -> Dict[str, Any]:
    base = Path(__file__).resolve().parents[2]
    agent_dir = base.parent / "src" / "agent"
    for cp in [base / "config.json", agent_dir / "config.json"]:
        if cp.exists():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _invoke_llm(system: str, user: str) -> Dict[str, Any]:
    cfg = _load_llm_cfg()
    api_key = (cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
    base_url = (
        cfg.get("volcengine_base_url") or cfg.get("openai_base_url") or "https://ark.cn-beijing.volces.com/api/v3"
    ).strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    model = (cfg.get("ai_chat_model") or "").strip()
    if not api_key or not model:
        return {"ok": False, "error": "未配置 LLM 网关（volcengine_api_key + ai_chat_model）"}

    agent_dir = Path(__file__).resolve().parents[2].parent / "src" / "agent"
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    try:
        from provider_adapters import invoke_chat_completion_raw, _extract_openai_message_dict
    except ImportError as ex:
        return {"ok": False, "error": f"provider_adapters 不可用: {ex}"}

    try:
        data = invoke_chat_completion_raw(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.3,
            max_tokens=2500,
            timeout=120.0,
            thinking_enabled=False,
            tools=None,
        )
        msg = _extract_openai_message_dict(data)
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        return {"ok": True, "content": content.strip(), "model": model}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def _rag_for_item(title: str, summary: str) -> Dict[str, Any]:
    query = f"{title}\n{summary}".strip()[:2000]
    if not query:
        return {"hits": [], "degraded": True}
    try:
        hits = kb_search(query, top_k=5)
        return {"hits": hits or [], "degraded": False}
    except Exception as ex:
        _log.warning(
            "[%s|creator_digest._rag_for_item|RAG|工具执行|检索] 失败; error=%s",
            _CHAIN,
            ex,
        )
        return {"hits": [], "degraded": True}


def generate_digest(
    *,
    subscription: Dict[str, Any],
    sync_run_id: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    items: [{note_id, title, content_type, canonical_url, analysis_task_id, summary, analysis_status, error_message}]
    """
    rag_degraded = False
    enriched: List[Dict[str, Any]] = []
    rag_evidence: List[Dict[str, Any]] = []

    for it in items:
        summary = (it.get("summary") or it.get("title") or "").strip()
        rag = _rag_for_item(it.get("title") or "", summary)
        if rag.get("degraded"):
            rag_degraded = True
        top = (rag.get("hits") or [])[:3]
        for h in top:
            rag_evidence.append(
                {
                    "note_id": it.get("note_id"),
                    "score": h.get("score"),
                    "text": (h.get("text") or h.get("content") or "")[:300],
                    "source": h.get("source") or h.get("file_name") or "",
                }
            )
        enriched.append(
            {
                "note_id": it.get("note_id"),
                "title": it.get("title"),
                "content_type": it.get("content_type"),
                "canonical_url": it.get("canonical_url"),
                "analysis_status": it.get("analysis_status"),
                "task_id": it.get("analysis_task_id"),
                "summary_excerpt": summary[:800],
                "rag_top": top,
            }
        )

    system = (
        "你是知识库运营分析助手。根据用户订阅博主的一批新笔记（含摘要与 RAG 检索片段），"
        "生成中文 Markdown 日报预览。必须包含：\n"
        "1) 一句话总览；2) 按主题归类（列表）；3) 逐篇简述；4) 与现有知识库关系："
        "novel（新颖）/ overlap（高度重合）/ extension（延伸补充）/ unknown（RAG 不可用时不判断重合，写 unknown）。\n"
        "禁止编造 RAG 未提供的库内观点；RAG 片段为空时 kb_relation 必须为 unknown。\n"
        "最后一行输出 JSON 块 ```json ... ``` 包含 digest_json 结构："
        "summary_one_liner, topic_buckets[{topic,count,note_ids}], items[{note_id,title,content_type,kb_relation,kb_relation_reason,novelty_score,task_id}]."
    )
    user_payload = {
        "subscription": {
            "display_name": subscription.get("display_name"),
            "platform": subscription.get("platform"),
            "creator_id": subscription.get("creator_id"),
        },
        "sync_run_id": sync_run_id,
        "items": enriched,
        "rag_degraded": rag_degraded,
    }
    user = json.dumps(user_payload, ensure_ascii=False, default=str)[:24000]

    llm = _invoke_llm(system, user)
    if not llm.get("ok"):
        _log.error(
            "[%s|creator_digest.generate_digest|digest|Agent执行|LLM] 失败; error=%s",
            _CHAIN,
            llm.get("error"),
        )
        return {"ok": False, "error": llm.get("error")}

    raw = llm.get("content") or ""
    digest_json: Dict[str, Any] = {
        "summary_one_liner": "",
        "topic_buckets": [],
        "items": [],
        "rag_evidence": rag_evidence,
    }
    md = raw
    jmatch = raw.rfind("```json")
    if jmatch >= 0:
        md = raw[:jmatch].strip()
        tail = raw[jmatch + 7 :]
        if tail.startswith("json"):
            tail = tail[4:]
        tail = tail.strip()
        if tail.endswith("```"):
            tail = tail[:-3].strip()
        try:
            digest_json = {**digest_json, **json.loads(tail)}
        except Exception:
            pass

    if not digest_json.get("summary_one_liner"):
        digest_json["summary_one_liner"] = f"本次 sync 共 {len(items)} 篇笔记"

    return {
        "ok": True,
        "digest_md": md,
        "digest_json": digest_json,
        "llm_model": llm.get("model") or "",
        "rag_degraded": rag_degraded,
    }
