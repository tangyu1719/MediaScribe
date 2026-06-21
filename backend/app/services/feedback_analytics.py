"""用户反馈数据分析（JSON 文件存储版，对齐 HaiChiAgent 看板指标）。"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .chat_feedback import INTENT_LABELS, MODE_LABELS, _FEEDBACK_DIR, list_all_feedback


def _load_rows_in_period(*, days: int) -> List[Dict[str, Any]]:
    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=max(1, days))
    rows: List[Dict[str, Any]] = []
    for p in _FEEDBACK_DIR.glob("*.json"):
        try:
            row = __import__("json").loads(p.read_text("utf-8"))
        except Exception:
            continue
        try:
            ts = datetime.fromisoformat(str(row.get("updated_at") or row.get("created_at", "")))
            if ts < cutoff:
                continue
        except Exception:
            pass
        rows.append(row)
    return rows


def compute_feedback_dashboard(*, days: int = 30) -> Dict[str, Any]:
    rows = _load_rows_in_period(days=days)
    intent_counts: Counter[str] = Counter()
    intent_ratings: Dict[str, List[int]] = defaultdict(list)
    intent_liked_map: Dict[str, List[bool]] = defaultdict(list)
    failed_intents: Counter[str] = Counter()
    corrected_intents: Counter[str] = Counter()
    daily_positive: Dict[str, int] = defaultdict(int)
    rating_dist: Counter[int] = Counter()
    low_rating_samples: List[Dict[str, Any]] = []
    comment_samples: List[Dict[str, Any]] = []

    for row in rows:
        detected = row.get("detected_intent") or {}
        intent = str(detected.get("domain") or detected.get("domain_code") or detected.get("mode") or "通用")
        rating = int(row.get("rating") or 0)
        if rating:
            intent_counts[intent] += 1
            intent_ratings[intent].append(rating)
            rating_dist[rating] += 1
        il = row.get("intent_liked")
        if il is not None:
            intent_liked_map[intent].append(bool(il))
        if il is False:
            failed_intents[intent] += 1
            corrected = str(row.get("corrected_intent_label") or row.get("corrected_intent") or "").strip()
            if corrected:
                corrected_intents[corrected] += 1
        day = str(row.get("updated_at") or row.get("created_at") or "")[:10]
        if rating >= 4 and day:
            daily_positive[day] += 1
        if rating and rating <= 2 and len(low_rating_samples) < 8:
            low_rating_samples.append(
                {
                    "session_id": row.get("session_id"),
                    "message_index": row.get("message_index"),
                    "rating": rating,
                    "intent": intent,
                    "comment": str(row.get("comment") or "")[:120],
                }
            )
        if row.get("comment") and len(comment_samples) < 8:
            comment_samples.append(
                {
                    "session_id": row.get("session_id"),
                    "rating": rating,
                    "comment": str(row.get("comment"))[:200],
                }
            )

    total_fb = len([r for r in rows if r.get("rating")])
    intent_eval = len([r for r in rows if r.get("intent_liked") is not None])
    liked = sum(1 for r in rows if r.get("intent_liked") is True)
    intent_accuracy = round(liked / intent_eval * 100, 1) if intent_eval else 0.0
    ratings = [int(r.get("rating") or 0) for r in rows if r.get("rating")]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0

    intent_pie = [{"intent": k, "label": k, "count": v} for k, v in intent_counts.most_common()]
    intent_score = []
    for k, vals in intent_ratings.items():
        likes = intent_liked_map.get(k, [])
        intent_score.append(
            {
                "intent": k,
                "label": k,
                "avg_rating": round(sum(vals) / len(vals), 2),
                "count": len(vals),
                "like_rate": round(sum(1 for x in likes if x) / len(likes), 2) if likes else None,
            }
        )
    intent_score.sort(key=lambda x: x["avg_rating"], reverse=True)

    date_range = []
    today = datetime.now().date()
    span = min(max(1, days), 90)
    start = today - timedelta(days=span - 1)
    cur = start
    while cur <= today:
        date_range.append(cur.isoformat())
        cur += timedelta(days=1)

    positive_trend = [{"date": d, "count": daily_positive.get(d, 0)} for d in date_range]

    return {
        "period_days": days,
        "total_feedback": total_fb,
        "intent_eval_count": intent_eval,
        "overall_intent_accuracy": intent_accuracy,
        "avg_rating": avg_rating,
        "rating_histogram": {str(k): v for k, v in sorted(rating_dist.items())},
        "intent_pie": intent_pie,
        "intent_score": intent_score,
        "failed_intent_rank": [
            {"intent": k, "fail_count": v} for k, v in failed_intents.most_common(10)
        ],
        "corrected_intent_rank": [
            {"label": k, "count": v} for k, v in corrected_intents.most_common(10)
        ],
        "positive_review_trend": positive_trend,
        "low_rating_samples": low_rating_samples,
        "comment_samples": comment_samples,
        "flow_pipeline": {
            "title": f"用户反馈漏斗（近 {days} 天）",
            "stages": [
                {"id": "feedback", "label": "用户反馈", "count": total_fb},
                {"id": "intent_eval", "label": "意图评价", "count": intent_eval},
                {"id": "rating", "label": "星级评分", "count": len(ratings)},
                {"id": "low_rating", "label": "低分(≤2)", "count": sum(1 for r in ratings if r <= 2)},
                {"id": "dashboard", "label": "看板聚合", "count": total_fb},
            ],
        },
        "intent_labels": INTENT_LABELS,
        "mode_labels": MODE_LABELS,
        "recent_items": list_all_feedback(limit=20, offset=0),
    }
