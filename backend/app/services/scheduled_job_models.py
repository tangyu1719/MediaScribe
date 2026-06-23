"""定时任务 — ORM 模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ScheduledJobBase(DeclarativeBase):
    pass


class ScheduledJob(ScheduledJobBase):
    __tablename__ = "scheduled_jobs"

    job_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(64), default="system")
    description: Mapped[str] = mapped_column(Text, default="")
    frequency_preset: Mapped[str] = mapped_column(String(32), default="24H")
    custom_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    custom_interval_minutes: Mapped[int] = mapped_column(Integer, default=0)
    daily_hour: Mapped[int] = mapped_column(Integer, default=8)
    daily_minute: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScheduledJobRun(ScheduledJobBase):
    __tablename__ = "scheduled_job_runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_key: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="scheduled")
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(String(512), default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(128), default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
