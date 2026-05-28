"""Agent 个性化 Prompt 版本库：仅追加（INSERT），不在业务层物理删除历史行。

数据库：优先 `SBA_DATABASE_URL`（推荐 MySQL：`mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4`），
未配置时使用项目 `output/sba_agent_personalization.sqlite3`。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

_BASE = Path(__file__).resolve().parents[2]
_DEFAULT_SQLITE = _BASE / "output" / "sba_agent_personalization.sqlite3"


class Base(DeclarativeBase):
    pass


class SbaAgentTemplateMeta(Base):
    __tablename__ = "sba_agent_template_meta"

    template_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    display_label: Mapped[str] = mapped_column(String(512), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SbaAgentPromptRevision(Base):
    __tablename__ = "sba_agent_prompt_revision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    template_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    layers_json: Mapped[str] = mapped_column(Text)
    rendered_system: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_engine = None
_SessionLocal = None


def _database_url() -> str:
    u = (os.environ.get("SBA_DATABASE_URL") or "").strip()
    if u:
        return u
    _DEFAULT_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_SQLITE.as_posix()}"


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = _database_url()
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args, future=True)
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(_engine, expire_on_commit=False, class_=Session)
    return _engine


def session_scope() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def next_version(db: Session, template_key: str) -> int:
    row = db.execute(
        select(func.coalesce(func.max(SbaAgentPromptRevision.version), 0)).where(
            SbaAgentPromptRevision.template_key == template_key
        )
    ).scalar_one()
    return int(row or 0) + 1


def insert_revision(
    template_key: str,
    layers_json: str,
    rendered_system: str,
    *,
    kind: str,
    display_label: str,
) -> int:
    with session_scope() as db:
        ver = next_version(db, template_key)
        rev = SbaAgentPromptRevision(
            template_key=template_key,
            version=ver,
            layers_json=layers_json,
            rendered_system=rendered_system,
            created_at=datetime.utcnow(),
        )
        db.add(rev)
        meta = db.get(SbaAgentTemplateMeta, template_key)
        now = datetime.utcnow()
        if meta is None:
            db.add(
                SbaAgentTemplateMeta(
                    template_key=template_key,
                    kind=kind,
                    display_label=display_label or template_key,
                    is_active=True,
                    updated_at=now,
                )
            )
        else:
            meta.display_label = display_label or meta.display_label
            meta.updated_at = now
            meta.is_active = True
        db.commit()
        return ver


def get_latest_revision(template_key: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        r = db.execute(
            select(SbaAgentPromptRevision)
            .where(SbaAgentPromptRevision.template_key == template_key)
            .order_by(SbaAgentPromptRevision.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not r:
            return None
        return {
            "version": r.version,
            "layers_json": r.layers_json,
            "rendered_system": r.rendered_system,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }


def list_revisions(template_key: str, limit: int = 40) -> List[Dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(SbaAgentPromptRevision)
            .where(SbaAgentPromptRevision.template_key == template_key)
            .order_by(SbaAgentPromptRevision.version.desc())
            .limit(limit)
        ).scalars().all()
        out = []
        for r in rows:
            preview = (r.rendered_system or "")[:400]
            out.append(
                {
                    "version": r.version,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                    "rendered_preview": preview + ("…" if len(r.rendered_system or "") > 400 else ""),
                    "layers_json": r.layers_json,
                    "rendered_system": r.rendered_system,
                }
            )
        return out


def list_active_custom_keys() -> List[Dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(SbaAgentTemplateMeta).where(
                SbaAgentTemplateMeta.kind == "custom", SbaAgentTemplateMeta.is_active.is_(True)
            )
        ).scalars().all()
        out = []
        for m in rows:
            sub = db.execute(
                select(func.max(SbaAgentPromptRevision.version)).where(
                    SbaAgentPromptRevision.template_key == m.template_key
                )
            ).scalar_one()
            out.append(
                {
                    "template_key": m.template_key,
                    "label": m.display_label,
                    "version": int(sub or 0),
                    "updated_at": m.updated_at.isoformat() if m.updated_at else "",
                }
            )
        return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)


def deactivate_custom(template_key: str) -> bool:
    if not template_key.startswith("custom:"):
        return False
    with session_scope() as db:
        meta = db.get(SbaAgentTemplateMeta, template_key)
        if meta:
            meta.is_active = False
            meta.updated_at = datetime.utcnow()
            db.commit()
            return True
    return False


def db_status() -> Dict[str, Any]:
    url = _database_url()
    masked = url
    if "@" in url and "://" in url:
        try:
            head, tail = url.split("://", 1)
            cred, host = tail.split("@", 1)
            if ":" in cred:
                user = cred.split(":", 1)[0]
                masked = f"{head}://{user}:***@{host}"
        except Exception:
            pass
    return {"driver": "mysql" if "mysql" in url else "sqlite", "url_masked": masked}
