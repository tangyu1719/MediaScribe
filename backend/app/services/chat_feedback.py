"""AI 问答用户反馈：打分 + 意图准确率统计 + 意图分类纠正。"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger("sba.chat_feedback")

_FEEDBACK_DIR = Path(os.environ.get("SBA_FEEDBACK_DIR", "output/feedback"))
_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

# 意图分类目标标签（与 _infer_domain_module 对齐）
INTENT_LABELS = [
    "知识库", "资料处理", "研发运维", "业务系统", "社媒分析", "通用",
]
MODE_LABELS = ["简单问答", "新建主任务", "延续主任务"]


def _feedback_path(session_id: str, message_index: int) -> Path:
    return _FEEDBACK_DIR / f"{session_id}_{message_index}.json"


def save_feedback(
    session_id: str,
    message_index: int,
    *,
    rating: Optional[int] = None,
    intent_liked: Optional[bool] = None,
    detected_intent: Optional[Dict[str, Any]] = None,
    corrected_intent: Optional[str] = None,
    corrected_intent_label: Optional[str] = None,
    comment: Optional[str] = None,
    user_id: str = "",
) -> Dict[str, Any]:
    """保存或更新用户反馈（未传字段保留已有值）。"""
    p = _feedback_path(session_id, message_index)
    existing: Dict[str, Any] = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text("utf-8"))
        except Exception:
            existing = {}

    resolved_rating = max(1, min(5, int(rating if rating is not None else existing.get("rating") or 3)))
    row: Dict[str, Any] = {
        "session_id": session_id,
        "message_index": message_index,
        "rating": resolved_rating,
        "intent_liked": intent_liked if intent_liked is not None else existing.get("intent_liked"),
        "detected_intent": detected_intent if detected_intent is not None else existing.get("detected_intent"),
        "corrected_intent": corrected_intent if corrected_intent is not None else existing.get("corrected_intent"),
        "corrected_intent_label": corrected_intent_label if corrected_intent_label is not None else existing.get("corrected_intent_label"),
        "comment": str(comment)[:500] if comment is not None else str(existing.get("comment") or "")[:500],
        "user_id": str(user_id or existing.get("user_id") or "").strip(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not row.get("created_at"):
        row["created_at"] = existing.get("created_at") or row["updated_at"]
    p.write_text(json.dumps(row, ensure_ascii=False, indent=2), "utf-8")
    return row


def get_feedback(session_id: str, message_index: int) -> Optional[Dict[str, Any]]:
    p = _feedback_path(session_id, message_index)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def list_feedback_for_session(session_id: str) -> List[Dict[str, Any]]:
    """按会话批量加载反馈（前端会话恢复回显）。"""
    sid = str(session_id or "").strip()
    if not sid:
        return []
    out: List[Dict[str, Any]] = []
    prefix = f"{sid}_"
    for p in _FEEDBACK_DIR.glob(f"{sid}_*.json"):
        try:
            row = json.loads(p.read_text("utf-8"))
            out.append(row)
        except Exception:
            continue
    out.sort(key=lambda r: int(r.get("message_index") or 0))
    return out


def list_all_feedback(
    *,
    limit: int = 100,
    offset: int = 0,
    rating_min: Optional[int] = None,
    rating_max: Optional[int] = None,
    intent_liked: Optional[bool] = None,
    keyword: str = "",
) -> List[Dict[str, Any]]:
    """列出所有反馈记录，支持筛选。"""
    records: List[Dict[str, Any]] = []
    for p in sorted(_FEEDBACK_DIR.glob("*.json"), reverse=True):
        try:
            row = json.loads(p.read_text("utf-8"))
        except Exception:
            continue
        if rating_min is not None and row.get("rating", 0) < rating_min:
            continue
        if rating_max is not None and row.get("rating", 0) > rating_max:
            continue
        if intent_liked is not None and row.get("intent_liked") is not intent_liked:
            continue
        if keyword:
            kw = keyword.lower()
            comment = str(row.get("comment", "")).lower()
            detected = json.dumps(row.get("detected_intent") or {}, ensure_ascii=False).lower()
            if kw not in comment and kw not in detected:
                continue
        records.append(row)
    return records[offset:offset + limit]


def compute_intent_accuracy(*, days: int = 30) -> Dict[str, Any]:
    """
    基于用户确认率计算意图识别准确率。
    准确率 = 用户点赞意图的次数 / 总评价次数
    """
    total = 0
    liked = 0
    by_label: Dict[str, Dict[str, int]] = {}
    for label in INTENT_LABELS:
        by_label[label] = {"total": 0, "liked": 0}
    for mode in MODE_LABELS:
        by_label[mode] = {"total": 0, "liked": 0}

    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=days)

    for p in _FEEDBACK_DIR.glob("*.json"):
        try:
            row = json.loads(p.read_text("utf-8"))
        except Exception:
            continue
        try:
            ts = datetime.fromisoformat(str(row.get("updated_at") or row.get("created_at", "")))
            if ts < cutoff:
                continue
        except Exception:
            pass
        il = row.get("intent_liked")
        if il is None:
            continue
        total += 1
        if il:
            liked += 1
        # 按检测意图分组
        detected = row.get("detected_intent") or {}
        label = str(detected.get("domain") or detected.get("mode") or "通用")
        if label not in by_label:
            by_label[label] = {"total": 0, "liked": 0}
        by_label[label]["total"] += 1
        if il:
            by_label[label]["liked"] += 1

    accuracy = round(liked / total * 100, 1) if total > 0 else 0
    by_label_pct = {}
    for label, counts in by_label.items():
        if counts["total"] > 0:
            by_label_pct[label] = {
                "total": counts["total"],
                "accuracy": round(counts["liked"] / counts["total"] * 100, 1),
            }

    # 找出准确率最低的意图（需要改进的）
    worst = sorted(by_label_pct.items(), key=lambda x: x[1]["accuracy"])[:5]

    return {
        "total_feedback": total,
        "overall_accuracy": accuracy,
        "by_label": by_label_pct,
        "worst_intents": [
            {"label": label, "total": info["total"], "accuracy": info["accuracy"]}
            for label, info in worst
        ],
        "days": days,
    }
