"""UP 画像选篇规则兜底 — 单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.creator_profile_llm import _fallback_select


def test_fallback_select_time_spread():
    pool = [
        {"note_id": "a", "published_at": "2024-01-01T00:00:00"},
        {"note_id": "b", "published_at": "2024-03-01T00:00:00"},
        {"note_id": "c", "published_at": "2024-06-01T00:00:00"},
        {"note_id": "d", "published_at": "2024-09-01T00:00:00"},
        {"note_id": "e", "published_at": "2024-12-01T00:00:00"},
    ]
    ids = _fallback_select(pool, 5, 10)
    assert len(ids) == 5
    assert ids[0] == "a"
    assert ids[-1] == "e"
