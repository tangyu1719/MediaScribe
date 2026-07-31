"""批量媒体失败时 OPS 后台分析必须去重，避免压垮数据库连接池。"""
from __future__ import annotations

from app.services import ops_hooks


def test_log_and_span_failure_share_one_task_cooldown(monkeypatch):
    scheduled = []
    monkeypatch.delenv("OPS_LOG_INCIDENT_DISABLE", raising=False)
    monkeypatch.setattr(ops_hooks, "_run_async", lambda fn, *a, **kw: scheduled.append(fn))
    with ops_hooks._incident_lock:
        ops_hooks._incident_last_ts.clear()

    ops_hooks.ops_dispatch_log_incident(
        "媒体内容提取失败 X1001",
        "ERROR",
        task_id="pipe-same",
    )
    ops_hooks.ops_dispatch_span_failure(
        {
            "task_id": "pipe-same",
            "step_id": "step-x1001",
            "step_name": "内容提取",
            "step_type": "pipeline",
            "status": "failed",
            "error_message": "X1001",
        }
    )

    assert len(scheduled) == 1
