"""RBAC 认证数据模型：用户表 + 验证码表。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session


_BASE = Path(__file__).resolve().parents[2]
_DEFAULT_SQLITE = _BASE / "output" / "sba_auth.sqlite3"


class AuthBase(DeclarativeBase):
    pass


class RbacUser(AuthBase):
    __tablename__ = "rbac_user"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    nickname: Mapped[str] = mapped_column(String(64), default="")
    avatar_url: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1 启用 0 禁用
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class RbacVerifyCode(AuthBase):
    __tablename__ = "rbac_verify_code"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="login")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_engine = None
_SessionLocal = None


def _db_url() -> str:
    u = (os.environ.get("SBA_DATABASE_URL") or "").strip()
    if u:
        return u
    _DEFAULT_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_SQLITE.as_posix()}"


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = _db_url()
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(
            url, pool_pre_ping=True, connect_args=connect_args, future=True
        )
        AuthBase.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(_engine, expire_on_commit=False, class_=Session)
    return _engine


def session_scope() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()
