# -*- coding: utf-8 -*-
"""链接分析队列排序：最近提交/重新执行在最左。"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

tm = importlib.import_module("app.services.task_manager")


def setup_function():
    tm._task_store.clear()
    tm._queue_tick = 0


def test_list_tasks_recent_touch_on_left():
    tm._task_store["a"] = {
        "task_id": "a",
        "status": "pending",
        "link": "https://a/a",
        "queue_seq": 1,
        "priority": 1,
        "created_at": "2026-06-01T10:00:01",
        "updated_at": "2026-06-01T10:00:01",
    }
    tm._task_store["c"] = {
        "task_id": "c",
        "status": "pending",
        "link": "https://a/c",
        "queue_seq": 3,
        "priority": 3,
        "created_at": "2026-06-01T10:00:03",
        "updated_at": "2026-06-01T10:00:03",
    }
    tm._task_store["b"] = {
        "task_id": "b",
        "status": "running",
        "link": "https://a/b",
        "queue_seq": 2,
        "priority": 2,
        "created_at": "2026-06-01T10:00:02",
        "updated_at": "2026-06-01T10:00:02",
    }
    ids = [t["task_id"] for t in tm.list_tasks()]
    assert ids == ["c", "b", "a"]


def test_create_task_appends_to_front():
    tid1 = tm.create_task("小红书", "https://xhs.example/1")
    tid2 = tm.create_task("小红书", "https://xhs.example/2")
    ids = [t["task_id"] for t in tm.list_tasks()]
    assert ids == [tid2, tid1]
    assert tm.get_task(tid2)["queue_seq"] > tm.get_task(tid1)["queue_seq"]


def test_restart_existing_task_moves_to_front():
    tid1 = tm.create_task("小红书", "https://xhs.example/1")
    tid2 = tm.create_task("小红书", "https://xhs.example/2")
    tm.restart_existing_task(
        tid1,
        platform="小红书",
        link="https://xhs.example/1",
        full_rerun=True,
    )
    ids = [t["task_id"] for t in tm.list_tasks()]
    assert ids[0] == tid1


def test_move_pending_swaps_queue_seq():
    tid1 = tm.create_task("小红书", "https://xhs.example/1")
    tid2 = tm.create_task("小红书", "https://xhs.example/2")
    assert tm.move_task_priority(tid1, "up") is True
    ids = [t["task_id"] for t in tm.list_tasks()]
    assert ids == [tid1, tid2]
