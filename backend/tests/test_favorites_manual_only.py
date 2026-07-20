from __future__ import annotations

import asyncio
import inspect

import pytest

from app import main
from app.services import scheduled_job_scheduler, scheduled_job_service, scheduled_job_store


class _FakeScheduler:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.added: list[str] = []

    def remove_job(self, job_id: str) -> None:
        self.removed.append(job_id)

    def add_job(self, *args, **kwargs) -> None:
        self.added.append(str(kwargs.get("id") or ""))


def test_app_startup_does_not_schedule_favorites_sync() -> None:
    source = inspect.getsource(main._startup_deferred_services)
    assert "schedule_favorites_on_startup" not in source
    assert "start_favorites_scheduler" not in source


def test_favorites_job_is_manual_only_even_for_legacy_enabled_row(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeScheduler()
    monkeypatch.setattr(scheduled_job_scheduler, "_scheduler", fake)
    monkeypatch.setattr(
        scheduled_job_scheduler,
        "_job_ids",
        {"favorites_sync_all": "sched_favorites_sync_all"},
    )
    monkeypatch.setattr(
        scheduled_job_store,
        "get_job",
        lambda job_key: {"job_key": job_key, "enabled": True, "frequency_preset": "24H"},
    )

    scheduled_job_scheduler.refresh_job_schedule("favorites_sync_all")

    assert fake.removed == ["sched_favorites_sync_all"]
    assert fake.added == []
    assert "favorites_sync_all" not in scheduled_job_scheduler._job_ids


def test_favorites_default_is_disabled_but_manual_run_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    favorite_job = next(job for job in scheduled_job_service.DEFAULT_JOBS if job["job_key"] == "favorites_sync_all")
    assert favorite_job["enabled"] is False

    async def _fake_execute(job_key: str, trigger: str = "scheduled", **kwargs):
        return {"ok": True, "job_key": job_key, "trigger": trigger}

    monkeypatch.setattr(scheduled_job_service, "execute_job", _fake_execute)

    result = asyncio.run(scheduled_job_service.api_run_job("favorites_sync_all"))

    assert result == {
        "ok": True,
        "job_key": "favorites_sync_all",
        "trigger": "manual_test",
    }
