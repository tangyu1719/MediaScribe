"""订阅批量入队：失败分类与审计逻辑。"""
from __future__ import annotations

import importlib
from unittest.mock import patch


def test_classify_failure_shutdown_full_rerun():
    gate = importlib.import_module("app.services.subscription_batch_gate")
    action, max_att = gate.classify_failure("cannot schedule new futures after shutdown", "pipeline")
    assert action == "full_rerun"
    assert max_att == 3


def test_classify_failure_empty_input_skip():
    gate = importlib.import_module("app.services.subscription_batch_gate")
    action, max_att = gate.classify_failure("PIPE_INVALID_INPUT_EMPTY", "normalize")
    assert action == "skip"
    assert max_att == 0


def test_classify_failure_bare_link_repair():
    gate = importlib.import_module("app.services.subscription_batch_gate")
    action, max_att = gate.classify_failure("缺少 xsec_token", "bare_link")
    assert action == "repair_link"
    assert max_att == 1


def test_audit_subscription_batch_ok_when_all_imported():
    gate = importlib.import_module("app.services.subscription_batch_gate")
    store = importlib.import_module("app.services.creator_subscription_store")
    hist = importlib.import_module("app.services.history_manager")
    tm = importlib.import_module("app.services.task_manager")

    rows = [
        {
            "note_id": "n1",
            "platform": "xiaohongshu",
            "canonical_url": "https://www.xiaohongshu.com/explore/n1?xsec_token=abc",
            "analysis_status": "already_imported",
            "analysis_task_id": "t1",
        },
    ]

    with patch.object(store, "list_seen_notes_by_subscription", return_value=rows):
        with patch.object(store, "list_sync_run_items", return_value=[]):
            with patch.object(hist, "get_task_history", return_value=None):
                with patch.object(tm, "get_task", return_value=None):
                    with patch.object(tm, "_ensure_queue_persistence_loaded", return_value=None):
                        report = gate.audit_subscription_batch(
                            "sub_test",
                            expected_total=1,
                        )
    assert report.ok is True
    assert report.already_imported == 1
    assert report.failures == []


def test_audit_subscription_batch_detects_failed_task():
    gate = importlib.import_module("app.services.subscription_batch_gate")
    store = importlib.import_module("app.services.creator_subscription_store")
    hist = importlib.import_module("app.services.history_manager")
    tm = importlib.import_module("app.services.task_manager")

    rows = [
        {
            "note_id": "n2",
            "platform": "xiaohongshu",
            "canonical_url": "https://www.xiaohongshu.com/explore/n2?xsec_token=abc",
            "analysis_status": "queued",
            "analysis_task_id": "t2",
        },
    ]
    task = {"status": "failed", "error": "下载视频失败", "failed_stage_label": "download"}

    with patch.object(store, "list_seen_notes_by_subscription", return_value=rows):
        with patch.object(store, "list_sync_run_items", return_value=[]):
            with patch.object(hist, "get_task_history", return_value=None):
                with patch.object(tm, "get_task", return_value=task):
                    with patch.object(tm, "_ensure_queue_persistence_loaded", return_value=None):
                        report = gate.audit_subscription_batch("sub_test")
    assert report.ok is False
    assert len(report.failures) == 1
    assert report.failures[0].action == "full_rerun"
