"""断点恢复：阶段状态机 + 磁盘缓存推断；启动全量扫描未终止态。"""
from __future__ import annotations

import importlib
import json
from pathlib import Path


def _ps():
    return importlib.import_module("app.services.pipeline_stages")


def _tm():
    return importlib.import_module("app.services.task_manager")


def _ck():
    return importlib.import_module("app.services.pipeline_checkpoint")


def test_reconcile_resume_from_failed_stage():
    ps = _ps()
    task = {
        "task_id": "t1",
        "url_hash": "uh1",
        "pipeline_route": "xiaohongshu_graphic",
        "status": "running",
        "pipeline_stages": {
            "extract": {"status": "completed", "label": "内容提取"},
            "ocr": {"status": "failed", "label": "OCR补偿", "error": "timeout"},
            "assemble": {"status": "pending", "label": "原文装配"},
        },
        "resume_context": {"extract": {"raw_text": "hello"}},
    }
    patch = ps.reconcile_resume_from_stages(task)
    assert patch["resume_from"] == "ocr"
    assert patch["failed_stage"] == "ocr"
    assert patch["pipeline_stages"]["ocr"]["status"] == "pending"
    assert patch["resume_context"]["extract"]["raw_text"] == "hello"


def test_reconcile_resume_from_disk_cache():
    ps = _ps()
    ck = _ck()
    tm = _tm()
    uh = "uh_cache_test"
    tid = "cache01"
    ck.save_stage_payload(tid, "extract", {"raw_text": "from_disk"}, url_hash=uh)
    task = {
        "task_id": tid,
        "url_hash": uh,
        "pipeline_route": "xiaohongshu_graphic",
        "status": "transcribing",
        "pipeline_stages": {},
    }
    patch = ps.reconcile_resume_from_stages(task)
    assert "extract" in patch["resume_context"]
    assert patch["pipeline_stages"]["extract"]["status"] == "completed"
    # 有缓存的 extract 已完成，断点应在下一阶 ocr
    assert patch["resume_from"] == "ocr"
    # cleanup
    cache_dir = tm.get_output_dir() / ".pipeline_cache" / uh
    if cache_dir.exists():
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)


def test_interrupt_running_marks_failed_with_resume():
    tm = _tm()
    tm._task_store.clear()
    tid = "run_resume"
    tm._task_store[tid] = {
        "task_id": tid,
        "link": "https://www.xiaohongshu.com/explore/run_resume",
        "url_hash": "uh_run_resume",
        "status": "transcribing",
        "stage": "语音转文字",
        "pipeline_route": "video",
        "pipeline_stages": {
            "download": {"status": "completed", "label": "下载视频"},
            "transcribe": {"status": "in_progress", "label": "语音转文字"},
        },
    }
    changed = tm.reconcile_interrupt_task_for_resume(tm._task_store[tid])
    assert changed
    row = tm._task_store[tid]
    assert row["status"] == "failed"
    assert row["resume_from"] == "transcribe"
    assert "断点恢复" in row["stage"]
    tm._task_store.clear()


def test_scan_non_terminal_on_startup_imports_history():
    tm = _tm()
    from app.services.link_hash import url_hash as link_url_hash

    tm._task_store.clear()
    tm._dismissed_task_ids.clear()
    tm._dismissed_url_hashes.clear()
    tm._queue_persist_loaded = True
    link = "https://www.xiaohongshu.com/explore/non_terminal"
    uh = link_url_hash(link)
    hist = {
        "id": "hist_nt",
        "task_id": "hist_nt",
        "link": link,
        "url_hash": uh,
        "platform": "小红书",
        "status": "running",
        "stage": "内容提取",
        "pipeline_route": "xiaohongshu_graphic",
        "pipeline_stages": {
            "extract": {"status": "in_progress", "label": "内容提取"},
        },
        "created_at": "2026-06-06T10:00:00",
        "updated_at": "2026-06-06T10:01:00",
    }

    def fake_list(_limit):
        return [hist]

    orig = tm._list_history_rows_for_queue
    tm._list_history_rows_for_queue = lambda limit: fake_list(limit)

    import app.services.span_audit as sa

    orig_span = sa.list_pipeline_span_tasks
    sa.list_pipeline_span_tasks = lambda **kwargs: []
    try:
        fixed = tm.scan_non_terminal_pipeline_links_on_startup()
        assert fixed >= 1
        row = tm.find_task_by_url_hash(uh)
        assert row is not None
        assert row["status"] == "failed"
        assert row.get("resume_from") == "extract"
    finally:
        tm._list_history_rows_for_queue = orig
        sa.list_pipeline_span_tasks = orig_span
        tm._task_store.clear()
