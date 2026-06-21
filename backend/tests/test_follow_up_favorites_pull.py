"""收藏笔记 cursor 批次拉博主 — 单测。"""
from app.services.follow_up_favorites_pull import extract_new_authors_batch_from_notes


def _aid(n: int) -> str:
    return f"{n:024x}"


def test_batch_skips_known_and_owner_and_dup_notes():
    owner = _aid(1)
    known = {_aid(2)}
    notes = [
        {"author_id": owner, "author_name": "自己", "title": "t0"},
        {"author_id": _aid(2), "author_name": "已知", "title": "t1"},
        {"author_id": _aid(3), "author_name": "A", "title": "t2"},
        {"author_id": _aid(3), "author_name": "A", "title": "t3"},
        {"author_id": _aid(4), "author_name": "B", "title": "t4"},
    ]
    authors, next_off, scanned, exhausted = extract_new_authors_batch_from_notes(
        notes,
        start_offset=0,
        batch_size=20,
        owner_creator_id=owner,
        known_creator_ids=known,
    )
    assert len(authors) == 2
    assert {a["creator_id"] for a in authors} == {_aid(3), _aid(4)}
    assert next_off == 5
    assert scanned == 5
    assert exhausted is True


def test_batch_stops_at_batch_size_and_saves_offset():
    notes = [{"author_id": _aid(i), "author_name": f"U{i}", "title": f"t{i}"} for i in range(10, 40)]
    authors, next_off, scanned, exhausted = extract_new_authors_batch_from_notes(
        notes,
        start_offset=5,
        batch_size=20,
        known_creator_ids=set(),
    )
    assert len(authors) == 20
    assert next_off == 25
    assert scanned == 20
    assert exhausted is False


def test_batch_resumes_from_offset():
    notes = [{"author_id": _aid(i), "author_name": f"U{i}", "title": f"t{i}"} for i in range(1, 6)]
    known = {_aid(i) for i in range(1, 4)}
    authors, next_off, _, exhausted = extract_new_authors_batch_from_notes(
        notes,
        start_offset=2,
        batch_size=20,
        known_creator_ids=known,
    )
    assert len(authors) == 2
    assert authors[0]["creator_id"] == _aid(4)
    assert next_off == 5
    assert exhausted is True
