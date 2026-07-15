"""AI 辅助搜索 — 多 Provider 结果合并与排序。"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from .types import SearchHit


def merge_hits(hits: Iterable[SearchHit], *, limit: int = 10) -> List[SearchHit]:
    """按 provider_id+id 去重，保留更高分条目。"""
    best: Dict[Tuple[str, str], SearchHit] = {}
    for hit in hits or []:
        key = (hit.provider_id, hit.id)
        prev = best.get(key)
        if prev is None or hit.score > prev.score:
            best[key] = hit
    merged = list(best.values())
    merged.sort(key=lambda h: (-h.score, h.title))
    cap = max(1, int(limit or 10))
    return merged[:cap]


def boost_exact_title(query: str, hits: List[SearchHit]) -> List[SearchHit]:
    q = (query or "").strip().lower()
    if not q:
        return hits
    out: List[SearchHit] = []
    for hit in hits:
        title = (hit.title or "").strip().lower()
        if title == q:
            hit.score = max(hit.score, 1.0)
            hit.match_reason = hit.match_reason or "标题完全匹配"
        elif title.startswith(q):
            hit.score = max(hit.score, 0.9)
        out.append(hit)
    out.sort(key=lambda h: (-h.score, h.title))
    return out
