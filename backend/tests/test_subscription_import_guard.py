"""订阅同步：url_hash / 历史库判重。"""
from __future__ import annotations

import importlib
from unittest.mock import patch


def test_check_already_imported_history_completed():
    guard = importlib.import_module("app.services.subscription_import_guard")
    hist = {
        "status": "completed",
        "doc_path": "output/foo.md",
        "url_hash": "uh_hist",
    }

    with patch.object(guard, "is_note_seen", return_value=False):
        with patch.object(guard, "is_url_hash_seen", return_value=False):
            with patch.object(guard, "get_task_history", return_value=hist):
                with patch.object(guard, "_history_doc_exists", return_value=True):
                    ok, reason = guard.check_already_imported(
                        "xiaohongshu", "note1", "uh_hist"
                    )
    assert ok is True
    assert reason == "history_completed"


def test_check_already_imported_fresh():
    guard = importlib.import_module("app.services.subscription_import_guard")

    with patch.object(guard, "is_note_seen", return_value=False):
        with patch.object(guard, "is_url_hash_seen", return_value=False):
            with patch.object(guard, "get_task_history", return_value=None):
                ok, reason = guard.check_already_imported(
                    "xiaohongshu", "note_new", "uh_new"
                )
    assert ok is False
    assert reason == ""
