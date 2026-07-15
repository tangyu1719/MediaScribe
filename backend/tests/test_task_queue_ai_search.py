"""task_queue_ai_search — 规则意图 + GREP 单测。"""
from app.services.task_queue_ai_search import (
    execute_ai_search,
    grep_task,
    parse_search_intent_rules,
)
from app.services.task_source_meta import SOURCE_MANUAL


def _sample_tasks():
    return [
        {
            "task_id": "t1",
            "link_title": "Java 面试",
            "status": "completed",
            "read_status": "unread",
            "task_note": "待看，重点复习",
            "import_source": SOURCE_MANUAL,
            "author_name": "张三",
            "queue_seq": 2,
            "updated_at": "2026-01-02T10:00:00",
        },
        {
            "task_id": "t2",
            "link_title": "Python 入门",
            "status": "completed",
            "read_status": "read",
            "task_note": "已看完",
            "import_source": SOURCE_MANUAL,
            "author_name": "李四",
            "queue_seq": 1,
            "updated_at": "2026-01-01T10:00:00",
        },
        {
            "task_id": "t3",
            "link_title": "Go 语言",
            "status": "pending",
            "task_note": "待看",
            "import_source": SOURCE_MANUAL,
            "author_name": "王五",
            "queue_seq": 3,
            "updated_at": "2026-01-03T10:00:00",
        },
    ]


def test_parse_note_intent():
    plan = parse_search_intent_rules("帮我查一下备注 待看的")
    assert "task_note" in (plan.text_clauses[0].fields if plan.text_clauses else [])
    assert any("待看" in t for t in (plan.text_clauses[0].terms if plan.text_clauses else []))


def test_grep_task_note():
    tasks = _sample_tasks()
    plan = parse_search_intent_rules("备注 待看")
    hits = grep_task(tasks[0], plan)
    assert hits
    assert grep_task(tasks[1], plan) == []


def test_execute_unread_filter():
    tasks = _sample_tasks()
    result = execute_ai_search(tasks, "未读", use_llm=False)
    ids = set(result["matched_task_ids"])
    assert "t1" in ids
    assert "t2" not in ids


def test_execute_note_and_unread():
    tasks = _sample_tasks()
    result = execute_ai_search(tasks, "备注 待看 未读", use_llm=False)
    ids = set(result["matched_task_ids"])
    assert ids == {"t1"}


def test_execute_returns_grep_summary():
    tasks = _sample_tasks()
    result = execute_ai_search(tasks, "备注 待看", use_llm=False)
    assert result["total"] >= 1
    assert "grep" in result["grep_summary"].lower() or "grep" in result["grep_summary"]
    assert result["grep_summary"]
