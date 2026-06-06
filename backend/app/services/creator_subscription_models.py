"""社媒博主订阅 — MariaDB ORM 模型（须 SBA_DATABASE_URL 为 mysql/mariadb）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CreatorSubBase(DeclarativeBase):
    pass


class Subscription(CreatorSubBase):
    __tablename__ = "creator_subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    creator_id: Mapped[str] = mapped_column(String(128), index=True)
    profile_url: Mapped[str] = mapped_column(String(1024))
    display_name: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    cron_override: Mapped[str | None] = mapped_column(String(64), nullable=True)
    read_comments: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_analyze: Mapped[bool] = mapped_column(Boolean, default=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cursor_offset: Mapped[int] = mapped_column(Integer, default=0)
    last_note_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cursor_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    initial_backfill_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("platform", "creator_id", name="uk_creator_sub_platform_creator"),
    )


class SubscriptionSeenNote(CreatorSubBase):
    __tablename__ = "creator_subscription_seen_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subscription_id: Mapped[str] = mapped_column(String(32), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    note_id: Mapped[str] = mapped_column(String(64))
    canonical_url: Mapped[str] = mapped_column(String(1024))
    url_hash: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(32), default="unknown")
    title: Mapped[str] = mapped_column(String(512), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    analysis_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(16), default="pending")

    __table_args__ = (
        UniqueConstraint("platform", "note_id", name="uk_seen_platform_note"),
        Index("ix_seen_sub", "subscription_id"),
    )


class SyncRun(CreatorSubBase):
    __tablename__ = "creator_sync_runs"

    sync_run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SyncRunItem(CreatorSubBase):
    __tablename__ = "creator_sync_run_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sync_run_id: Mapped[str] = mapped_column(String(32), index=True)
    note_id: Mapped[str] = mapped_column(String(64))
    canonical_url: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(32), default="unknown")
    title: Mapped[str] = mapped_column(String(512), default="")
    analysis_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(16), default="pending")
    error_message: Mapped[str] = mapped_column(Text, default="")


class CreatorDigest(CreatorSubBase):
    __tablename__ = "creator_digests"

    digest_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sync_run_id: Mapped[str] = mapped_column(String(32), index=True)
    subscription_id: Mapped[str] = mapped_column(String(32), index=True)
    digest_md: Mapped[str] = mapped_column(Text, default="")
    digest_json: Mapped[str] = mapped_column(Text, default="{}")
    llm_model: Mapped[str] = mapped_column(String(128), default="")
    rag_degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CreatorProfileRun(CreatorSubBase):
    """UP 画像流水线运行记录。"""

    __tablename__ = "creator_profile_runs"

    profile_run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="")
    catalog_count: Mapped[int] = mapped_column(Integer, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    deep_ok_count: Mapped[int] = mapped_column(Integer, default=0)
    deep_fail_count: Mapped[int] = mapped_column(Integer, default=0)
    light_profile_json: Mapped[str] = mapped_column(Text, default="{}")
    selection_json: Mapped[str] = mapped_column(Text, default="{}")
    deep_profile_json: Mapped[str] = mapped_column(Text, default="{}")
    profile_md: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    llm_model: Mapped[str] = mapped_column(String(128), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CreatorProfileDoc(CreatorSubBase):
    """UP 画像固化文档（每个订阅保留最新 + 历史版本）。"""

    __tablename__ = "creator_profile_docs"

    profile_doc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(32), index=True)
    profile_run_id: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    red_id: Mapped[str] = mapped_column(String(64), default="")
    creator_id: Mapped[str] = mapped_column(String(128), default="")
    industry: Mapped[str] = mapped_column(String(256), default="")
    domain: Mapped[str] = mapped_column(String(256), default="")
    niche: Mapped[str] = mapped_column(String(256), default="")
    persona_summary: Mapped[str] = mapped_column(Text, default="")
    target_audience: Mapped[str] = mapped_column(Text, default="")
    content_style: Mapped[str] = mapped_column(String(256), default="")
    deep_directions_json: Mapped[str] = mapped_column(Text, default="[]")
    recent_topics_json: Mapped[str] = mapped_column(Text, default="[]")
    content_type_distribution_json: Mapped[str] = mapped_column(Text, default="{}")
    output_analysis_json: Mapped[str] = mapped_column(Text, default="{}")
    selected_notes_json: Mapped[str] = mapped_column(Text, default="[]")
    profile_json: Mapped[str] = mapped_column(Text, default="{}")
    profile_md: Mapped[str] = mapped_column(Text, default="")
    profile_md_path: Mapped[str] = mapped_column(String(1024), default="")
    llm_model: Mapped[str] = mapped_column(String(128), default="")
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_profile_doc_sub_latest", "subscription_id", "is_latest"),)


class FavoritesHabit(CreatorSubBase):
    """小红书收藏夹 — 用户收藏习惯画像（按订阅持久化）。"""

    __tablename__ = "xhs_favorites_habits"

    subscription_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    red_id: Mapped[str] = mapped_column(String(64), default="")
    habit_json: Mapped[str] = mapped_column(Text, default="{}")
    persona_md: Mapped[str] = mapped_column(Text, default="")
    total_collected: Mapped[int] = mapped_column(Integer, default=0)
    llm_model: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
