"""链接沉淀流水线 — 任务历史 MariaDB/MySQL ORM（表 pipeline_task_history）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PipelineHistoryBase(DeclarativeBase):
    pass


class PipelineTaskHistory(PipelineHistoryBase):
    """历史记录 & 任务队列 — 每条链接稳定 hash 对应一行（更新覆盖）。"""

    __tablename__ = "pipeline_task_history"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    link: Mapped[str] = mapped_column(Text, nullable=False, default="")
    normalized_link: Mapped[str] = mapped_column(Text, default="")
    platform: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(512), default="")
    link_title: Mapped[str] = mapped_column(String(512), default="")
    doc_title: Mapped[str] = mapped_column(String(512), default="")
    content_type: Mapped[str] = mapped_column(String(64), default="")
    cover_url: Mapped[str] = mapped_column(String(1024), default="")
    route_type: Mapped[str] = mapped_column(String(64), default="")
    pipeline_route: Mapped[str] = mapped_column(String(64), default="")
    pipeline_stages_json: Mapped[str] = mapped_column(Text, default="{}")
    failed_stage: Mapped[str] = mapped_column(String(64), default="")
    failed_stage_label: Mapped[str] = mapped_column(String(128), default="")
    resume_from: Mapped[str] = mapped_column(String(64), default="")
    resume_context_json: Mapped[str] = mapped_column(Text, default="{}")
    user_prompt: Mapped[str] = mapped_column(Text, default="")
    comments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcribe_error_code: Mapped[str] = mapped_column(String(64), default="")
    doc_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    doc_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_status: Mapped[str] = mapped_column(String(32), default="")
    html_message: Mapped[str] = mapped_column(String(512), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True
    )

    __table_args__ = (
        UniqueConstraint("url_hash", name="uk_pipeline_task_history_url_hash"),
        Index("ix_pipeline_task_history_updated", "updated_at"),
    )
