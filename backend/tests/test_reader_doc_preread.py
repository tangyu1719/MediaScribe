"""辅助阅读 Agent 文档预读（磁盘版本校验）。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services.reader_agent import resolve_doc_text_for_chat

_TEST_ROOT = Path(__file__).resolve().parents[1] / ".pytest_tmp" / "reader_doc_preread"


@pytest.fixture
def output_tmp(monkeypatch):
    root = _TEST_ROOT
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.task_manager.get_output_dir", lambda: root)
    monkeypatch.setattr("app.services.file_naming.get_output_dir", lambda: root)
    yield root
    for p in root.glob("*"):
        try:
            p.unlink()
        except OSError:
            pass


def test_resolve_doc_refreshes_when_disk_mtime_newer(output_tmp):
    name = "sample.md"
    p = output_tmp / name
    p.write_text("v1\n", encoding="utf-8")
    v1_mtime = int(p.stat().st_mtime * 1000)
    time.sleep(0.05)
    p.write_text("v2 with MAG\n", encoding="utf-8")
    v2_mtime = int(p.stat().st_mtime * 1000)
    assert v2_mtime >= v1_mtime

    res = resolve_doc_text_for_chat(
        doc_name=name,
        doc_text="v1\n",
        doc_version=max(0, v1_mtime - 1),
        local_revision=0,
    )
    assert res["refreshed"] is True
    assert "MAG" in res["text"]
    assert res["version"] == v2_mtime


def test_resolve_doc_prefers_unsaved_client_text(output_tmp):
    name = "draft.md"
    p = output_tmp / name
    p.write_text("saved\n", encoding="utf-8")
    disk_mtime = int(p.stat().st_mtime * 1000)
    time.sleep(0.05)
    p.write_text("external edit\n", encoding="utf-8")
    newer_mtime = int(p.stat().st_mtime * 1000)

    res = resolve_doc_text_for_chat(
        doc_name=name,
        doc_text="unsaved editor text\n",
        doc_version=disk_mtime,
        local_revision=3,
    )
    assert res["source"] == "client_unsaved_over_disk"
    assert res["text"] == "unsaved editor text"
    assert res["version"] == newer_mtime
