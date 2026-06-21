"""定时任务频率与异常提案。"""
from __future__ import annotations

import importlib


def test_resolve_trigger_30min():
    svc = importlib.import_module("app.services.scheduled_job_service")
    ttype, params = svc.resolve_trigger({"frequency_preset": "30MIN"})
    assert ttype == "interval"
    assert params["minutes"] == 30


def test_match_exception_shutdown():
    prop = importlib.import_module("app.services.ops_exception_proposals")
    hits = prop.match_exceptions("cannot schedule new futures after shutdown")
    assert hits and hits[0]["code"] == "PIPE_EXECUTOR_SHUTDOWN"
