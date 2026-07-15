"""SearchBox SDK — 命中高亮（类 ES highlight）。"""
from __future__ import annotations

import re
from typing import Dict, List

from .types import SearchHit


def apply_highlights(hits: List[SearchHit], terms: List[str]) -> List[SearchHit]:
    if not terms:
        return hits
    out: List[SearchHit] = []
    for hit in hits:
        hl: Dict[str, List[str]] = {}
        for field_name, text in (
            ("title", hit.title),
            ("subtitle", hit.subtitle),
            ("description", hit.description),
        ):
            marked = _mark_terms(text, terms)
            if marked != text:
                hl[field_name] = [marked]
        if hl:
            hit = SearchHit(
                id=hit.id,
                title=hit.title,
                provider_id=hit.provider_id,
                subtitle=hit.subtitle,
                description=hit.description,
                category=hit.category,
                score=hit.score,
                match_reason=hit.match_reason,
                payload=hit.payload,
                highlight=hl,
            )
        out.append(hit)
    return out


def _mark_terms(text: str, terms: List[str]) -> str:
    if not text:
        return text
    result = text
    for term in sorted({t for t in terms if t}, key=len, reverse=True):
        try:
            result = re.sub(re.escape(term), f"<em>{term}</em>", result, flags=re.IGNORECASE)
        except re.error:
            continue
    return result
