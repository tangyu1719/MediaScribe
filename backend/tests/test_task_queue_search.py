from app.services.follow_up_search import expand_search_terms
from app.services.task_queue_search import filter_tasks, match_task_title
from app.services.task_source_meta import (
    build_source_label,
    enrich_task_source_fields,
    SOURCE_SUB_FAVORITES,
    SOURCE_MANUAL,
    SOURCE_SUB_CREATOR,
)


def test_enrich_subscription_from_label():
    row = enrich_task_source_fields(
        {
            "task_id": "x1",
            "source_label": "自动订阅博主：某UP",
            "import_source": "",
        }
    )
    assert row["import_source"] == SOURCE_SUB_CREATOR
    assert "某UP" in row["source_label"]


def test_enrich_subscription_from_subscription_id(monkeypatch):
    def _fake_get_subscription(sid):
        return {
            "subscription_id": sid,
            "platform": "xiaohongshu_favorites",
            "display_name": "三点、水",
        }

    monkeypatch.setattr(
        "app.services.creator_subscription_store.get_subscription",
        _fake_get_subscription,
    )
    row = enrich_task_source_fields(
        {
            "task_id": "x2",
            "subscription_id": "sub123",
            "url_hash": "abc",
        }
    )
    assert row["import_source"] == SOURCE_SUB_FAVORITES
    assert "收藏夹" in row["source_label"]


def test_build_source_label_favorites():
    lbl = build_source_label(
        SOURCE_SUB_FAVORITES,
        display_name="三点、水",
        platform="xiaohongshu_favorites",
    )
    assert "收藏夹" in lbl
    assert "三点、水" in lbl


def test_title_synonym_match():
    task = {"link_title": "Agent 智能体入门", "doc_title": "", "extracted_metadata": {}}
    assert match_task_title(task, "智能体")
    terms = expand_search_terms("java")
    assert "java" in terms or "后端" in terms


def test_filter_tasks_multi():
    rows = [
        {
            "task_id": "a",
            "link_title": "Java 面试",
            "status": "completed",
            "read_status": "unread",
            "importance": 8,
            "import_source": SOURCE_MANUAL,
            "source_label": "导入链接",
            "author_name": "张三",
            "queue_seq": 2,
            "updated_at": "2026-01-02T10:00:00",
        },
        {
            "task_id": "b",
            "link_title": "Python 入门",
            "status": "completed",
            "read_status": "read",
            "importance": 3,
            "import_source": "subscription_creator",
            "source_label": "自动订阅博主：某UP",
            "author_name": "李四",
            "queue_seq": 1,
            "updated_at": "2026-01-01T10:00:00",
        },
    ]
    out = filter_tasks(
        rows,
        title_query="面试",
        enable_title=True,
        enable_read=True,
        read_filter="unread",
        sort="importance",
    )
    assert len(out["items"]) == 1
    assert out["items"][0]["task_id"] == "a"
