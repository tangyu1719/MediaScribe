"""收藏夹用户习惯画像 — 统计 + 优先级打分。"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from .creator_subscription_store import get_favorites_habit, save_favorites_habit

_log = logging.getLogger("sba.favorites_habit")
_CHAIN = "小红书收藏夹-习惯画像-优先级"


def _empty_habit() -> Dict[str, Any]:
    return {
        "author_counts": {},
        "author_names": {},
        "content_type_counts": {},
        "topic_keywords": {},
        "top_authors": [],
        "preferred_content_types": [],
        "interest_topics": [],
        "total_analyzed": 0,
        "avg_text_chars": 0,
    }


def get_habit(subscription_id: str) -> Dict[str, Any]:
    row = get_favorites_habit(subscription_id)
    if not row:
        return {"subscription_id": subscription_id, "habit_json": _empty_habit(), "persona_md": ""}
    return row


def _extract_keywords(title: str, summary: str, limit: int = 8) -> List[str]:
    text = f"{title} {summary}".strip()
    if not text:
        return []
    parts = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z]{3,}", text)
    stop = {"小红书", "笔记", "视频", "图文", "分析", "内容", "用户", "作者"}
    out: List[str] = []
    for p in parts:
        if p in stop or p in out:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def update_habit_from_batch(
    *,
    subscription_id: str,
    red_id: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """根据本批次已分析收藏更新习惯统计。"""
    existing = get_favorites_habit(subscription_id)
    habit = dict((existing or {}).get("habit_json") or _empty_habit())
    author_counts: Counter = Counter(habit.get("author_counts") or {})
    author_names: Dict[str, str] = dict(habit.get("author_names") or {})
    ctype_counts: Counter = Counter(habit.get("content_type_counts") or {})
    topic_kw: Counter = Counter(habit.get("topic_keywords") or {})

    text_lens: List[int] = []
    prev_total = int(habit.get("total_analyzed") or 0)
    prev_avg = float(habit.get("avg_text_chars") or 0)

    for it in items:
        if it.get("analysis_status") != "completed":
            continue
        aid = (it.get("author_id") or "").strip()
        aname = (it.get("author_name") or "").strip()
        if aid:
            author_counts[aid] += 1
            if aname:
                author_names[aid] = aname
        ctype = (it.get("content_type") or "unknown").strip()
        ctype_counts[ctype] += 1
        tc = int(it.get("text_chars") or 0)
        if tc > 0:
            text_lens.append(tc)
        for kw in _extract_keywords(it.get("title") or "", it.get("summary") or ""):
            topic_kw[kw] += 1

    new_done = sum(1 for it in items if it.get("analysis_status") == "completed")
    total_analyzed = prev_total + new_done
    if text_lens:
        batch_avg = sum(text_lens) / len(text_lens)
        if prev_total > 0:
            habit["avg_text_chars"] = round(
                (prev_avg * prev_total + batch_avg * new_done) / max(total_analyzed, 1), 1
            )
        else:
            habit["avg_text_chars"] = round(batch_avg, 1)

    habit["author_counts"] = dict(author_counts)
    habit["author_names"] = author_names
    habit["content_type_counts"] = dict(ctype_counts)
    habit["topic_keywords"] = dict(topic_kw.most_common(80))
    habit["top_authors"] = [
        {"author_id": k, "author_name": author_names.get(k, k), "count": v}
        for k, v in author_counts.most_common(15)
    ]
    habit["preferred_content_types"] = [k for k, _ in ctype_counts.most_common(5)]
    habit["interest_topics"] = [k for k, _ in topic_kw.most_common(12)]
    habit["total_analyzed"] = total_analyzed

    saved = save_favorites_habit(
        subscription_id=subscription_id,
        red_id=red_id,
        habit_json=habit,
        persona_md=(existing or {}).get("persona_md") or "",
        total_collected=total_analyzed,
    )
    _log.info(
        "[%s|favorites_habit.update_habit_from_batch|%s|硬编执行|更新] ok=true; batch=%s; total=%s",
        _CHAIN,
        subscription_id,
        new_done,
        total_analyzed,
    )
    return saved


def compute_priority_score(item: Dict[str, Any], habit: Dict[str, Any]) -> float:
    """综合文字量、作者粉丝、收藏习惯计算优先级分（越高越重要）。"""
    score = 0.0
    text_chars = int(item.get("text_chars") or len(item.get("summary") or "") or 0)
    score += min(text_chars / 400.0, 12.0) * 1.2

    followers = int(item.get("author_followers") or 0)
    if followers > 0:
        score += min(math.log10(followers + 1) * 2.5, 8.0)

    author_counts = habit.get("author_counts") or {}
    aid = (item.get("author_id") or "").strip()
    if aid and aid in author_counts:
        cnt = author_counts[aid]
        max_cnt = max(author_counts.values()) if author_counts else 1
        score += (cnt / max(max_cnt, 1)) * 10.0

    topics = habit.get("topic_keywords") or {}
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    for kw, cnt in list(topics.items())[:20]:
        if kw and (kw in title or kw in summary):
            score += min(cnt * 0.4, 3.0)

    ctype_counts = habit.get("content_type_counts") or {}
    ctype = item.get("content_type") or "unknown"
    if ctype in ctype_counts:
        score += min(ctype_counts[ctype] * 0.3, 4.0)

    return round(score, 2)


def rank_items_by_priority(
    items: List[Dict[str, Any]], habit: Dict[str, Any]
) -> List[Dict[str, Any]]:
    ranked = []
    for it in items:
        copy = dict(it)
        copy["priority_score"] = compute_priority_score(it, habit)
        ranked.append(copy)
    ranked.sort(key=lambda x: x.get("priority_score") or 0, reverse=True)
    for idx, it in enumerate(ranked, start=1):
        it["priority_rank"] = idx
    return ranked
