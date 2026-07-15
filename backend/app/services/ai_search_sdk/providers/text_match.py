"""通用文本匹配与打分（规则层，不调用 LLM）。"""
from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from ...follow_up_search import _normalize_token


def compact_text(text: str) -> str:
    return re.sub(r"[\s_\-·、，。！？；：（）()【】/\\|]+", "", (text or "").lower())


def score_text_match(blob: str, terms: Iterable[str]) -> Tuple[float, str]:
    """对 searchable blob 与扩展词打分，返回 (score, reason)。"""
    if not terms:
        return 0.0, ""
    blob_low = (blob or "").lower()
    blob_compact = compact_text(blob)
    best = 0.0
    reason = ""
    for term in terms:
        t = (term or "").lower().strip()
        tn = _normalize_token(term)
        if not t:
            continue
        if blob_low == t or blob_compact == tn:
            best = max(best, 1.0)
            reason = f"完全匹配:{term}"
            continue
        if t in blob_low:
            best = max(best, 0.82)
            reason = reason or f"包含:{term}"
            continue
        if tn and tn in blob_compact:
            best = max(best, 0.72)
            reason = reason or f"紧凑匹配:{term}"
            continue
        # 前缀/子串弱匹配
        if len(tn) >= 2 and blob_compact.startswith(tn):
            best = max(best, 0.55)
            reason = reason or f"前缀:{term}"
    return best, reason


def rank_documents(docs, terms: List[str], *, limit: int):
    """对 SearchDocument 列表按规则分排序，返回 (doc, score, reason)。"""
    scored = []
    for doc in docs:
        blob = doc.searchable_text or " ".join(
            filter(None, [doc.title, doc.subtitle, doc.description, doc.category])
        )
        score, reason = score_text_match(blob, terms)
        if score <= 0:
            continue
        scored.append((doc, score, reason))
    scored.sort(key=lambda x: (-x[1], x[0].title))
    return scored[: max(1, int(limit or 10))]
