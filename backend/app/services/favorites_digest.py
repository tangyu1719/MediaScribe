"""收藏夹批次 digest — 含习惯画像与优先级排序。"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

from .creator_digest import _invoke_llm, _rag_for_item
from .favorites_habit import rank_items_by_priority

_log = logging.getLogger("sba.favorites_digest")
_CHAIN = "小红书收藏夹-增量分析-批次digest"


def generate_favorites_digest(
    *,
    subscription: Dict[str, Any],
    sync_run_id: str,
    items: List[Dict[str, Any]],
    habit: Dict[str, Any],
) -> Dict[str, Any]:
    habit_json = habit.get("habit_json") or habit if isinstance(habit, dict) else {}
    if "habit_json" in habit:
        habit_json = habit["habit_json"]

    ranked = rank_items_by_priority(items, habit_json)
    rag_degraded = False
    enriched: List[Dict[str, Any]] = []
    rag_evidence: List[Dict[str, Any]] = []

    for it in ranked:
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
                "author_name": it.get("author_name"),
                "author_followers": it.get("author_followers"),
                "text_chars": it.get("text_chars"),
                "priority_score": it.get("priority_score"),
                "priority_rank": it.get("priority_rank"),
                "analysis_status": it.get("analysis_status"),
                "task_id": it.get("analysis_task_id"),
                "summary_excerpt": summary[:800],
                "rag_top": top,
                "published_date": it.get("published_date"),
                "like_count": it.get("like_count") or 0,
                "comment_count": it.get("comment_count") or 0,
                "hashtags": it.get("hashtags") or [],
                "cover_url": it.get("cover_url") or "",
                "task_snapshot": {
                    "task_id": it.get("analysis_task_id"),
                    "status": it.get("analysis_status"),
                    "summary": summary[:800],
                    "author_name": it.get("author_name"),
                    "priority_rank": it.get("priority_rank"),
                },
            }
        )

    persona_md = habit.get("persona_md") or ""
    system = (
        "你是个人知识管理助手。根据用户小红书收藏夹本批次新增笔记（含摘要、作者信息、优先级分），"
        "结合用户历史收藏习惯画像，生成中文 Markdown 收藏说明。必须包含：\n"
        "1) 一句话总览（本批新增收藏主题）；\n"
        "2) 新增主题归类（按领域/话题列表，说明每类大概几篇）；\n"
        "3) 优先级 Top 列表（按 priority_rank，说明为何重要：文字量/作者影响力/与用户习惯契合）；\n"
        "4) 逐篇简述（标题 + 核心 takeaway）；\n"
        "5) 用户收藏习惯观察（与 habit 画像对照：常收藏的作者/类型/话题）；\n"
        "6) 与现有知识库关系：novel/overlap/extension/unknown（RAG 空则 unknown）。\n"
        "禁止编造未提供的笔记内容或 RAG 片段。\n"
        "最后一行输出 JSON 块 ```json ... ``` 包含："
        "summary_one_liner, topic_buckets[{topic,count,note_ids}], "
        "priority_items[{note_id,title,priority_rank,priority_score,importance_reason}], "
        "habit_insights[{dimension,observation}], "
        "items[{note_id,title,content_type,kb_relation,kb_relation_reason,task_id}]."
    )
    user_payload = {
        "subscription": {
            "display_name": subscription.get("display_name"),
            "platform": subscription.get("platform"),
            "creator_id": subscription.get("creator_id"),
        },
        "sync_run_id": sync_run_id,
        "habit_profile": habit_json,
        "habit_persona_md": persona_md[:4000],
        "items": enriched,
        "rag_degraded": rag_degraded,
    }
    user = json.dumps(user_payload, ensure_ascii=False, default=str)[:28000]

    llm = _invoke_llm(system, user)
    if not llm.get("ok"):
        _log.error(
            "[%s|favorites_digest.generate_favorites_digest|digest|Agent执行|LLM] 失败; error=%s",
            _CHAIN,
            llm.get("error"),
        )
        return {"ok": False, "error": llm.get("error")}

    raw = llm.get("content") or ""
    digest_json: Dict[str, Any] = {
        "summary_one_liner": "",
        "topic_buckets": [],
        "priority_items": [
            {
                "note_id": it.get("note_id"),
                "title": it.get("title"),
                "priority_rank": it.get("priority_rank"),
                "priority_score": it.get("priority_score"),
            }
            for it in ranked
        ],
        "habit_insights": [],
        "items": [],
        "rag_evidence": rag_evidence,
        "ranked_items": ranked,
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
        digest_json["summary_one_liner"] = f"本批新增收藏 {len(items)} 篇"

    # 更新习惯画像 persona（真实 LLM 输出中的习惯观察写入 habit）
    persona_update = md
    if habit.get("subscription_id"):
        try:
            from .creator_subscription_store import save_favorites_habit

            save_favorites_habit(
                subscription_id=habit["subscription_id"],
                red_id=habit.get("red_id") or "",
                habit_json=habit_json,
                persona_md=persona_update[:12000],
                total_collected=int(habit_json.get("total_analyzed") or 0),
                llm_model=llm.get("model") or "",
            )
        except Exception as ex:
            _log.warning(
                "[%s|favorites_digest|habit|Agent执行|持久化] 失败; error=%s",
                _CHAIN,
                ex,
            )

    return {
        "ok": True,
        "digest_md": md,
        "digest_json": digest_json,
        "llm_model": llm.get("model") or "",
        "rag_degraded": rag_degraded,
        "ranked_items": ranked,
    }
