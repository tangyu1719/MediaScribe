"""队列展示：历史回填、同链合并优先保留执行中。"""
from __future__ import annotations

import importlib


def _tm():
    return importlib.import_module("app.services.task_manager")


def test_merge_history_restores_completed_card():
    tm = _tm()
    tm._task_store.clear()
    tid = "hist001"
    hist = {
        "id": tid,
        "task_id": tid,
        "link": "https://www.xiaohongshu.com/explore/abc123",
        "url_hash": "uh_abc",
        "platform": "小红书",
        "status": "completed",
        "stage": "完成",
        "progress": 100,
        "created_at": "2026-05-25T10:00:00",
        "updated_at": "2026-05-25T10:05:00",
        "link_title": "测试笔记",
    }

    def fake_list(_limit):
        return [hist]

    orig = tm._list_history_rows_for_queue
    tm._list_history_rows_for_queue = lambda limit: fake_list(limit)
    try:
        added = tm.merge_history_into_queue(limit=10)
        assert added == 1
        rows = tm.list_queue_tasks()
        assert len(rows) == 1
        assert rows[0]["task_id"] == tid
        assert rows[0]["status"] == "completed"
    finally:
        tm._list_history_rows_for_queue = orig
        tm._task_store.clear()


def test_consolidate_keeps_running_over_newer_pending():
    tm = _tm()
    tm._task_store.clear()
    tm._task_store["run1"] = {
        "task_id": "run1",
        "link": "https://www.xiaohongshu.com/explore/same",
        "url_hash": "uh_same",
        "status": "running",
        "created_at": "2026-05-25T09:00:00",
        "priority": 1,
    }
    tm._task_store["pend1"] = {
        "task_id": "pend1",
        "link": "https://www.xiaohongshu.com/explore/same",
        "url_hash": "uh_same",
        "status": "pending",
        "created_at": "2026-05-25T11:00:00",
        "priority": 2,
    }
    removed = tm.consolidate_queue_by_url_hash()
    assert removed == 1
    assert "run1" in tm._task_store
    assert "pend1" not in tm._task_store
    tm._task_store.clear()
