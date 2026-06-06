"""UP 画像 — MariaDB 持久化。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from .creator_subscription_models import CreatorProfileDoc, CreatorProfileRun
from .creator_subscription_store import SubscriptionDbError, get_engine, session_scope

_CHAIN = "社媒订阅-UP画像-持久化"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _run_to_dict(row: CreatorProfileRun) -> Dict[str, Any]:
    def _j(raw: str) -> Any:
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}

    return {
        "profile_run_id": row.profile_run_id,
        "subscription_id": row.subscription_id,
        "trigger": row.trigger,
        "status": row.status,
        "stage": row.stage,
        "catalog_count": row.catalog_count,
        "selected_count": row.selected_count,
        "deep_ok_count": row.deep_ok_count,
        "deep_fail_count": row.deep_fail_count,
        "light_profile_json": _j(row.light_profile_json),
        "selection_json": _j(row.selection_json),
        "deep_profile_json": _j(row.deep_profile_json),
        "profile_md": row.profile_md,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "llm_model": row.llm_model,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _doc_to_dict(row: CreatorProfileDoc) -> Dict[str, Any]:
    def _j(raw: str, default: Any) -> Any:
        try:
            return json.loads(raw or (json.dumps(default) if not isinstance(default, str) else default))
        except Exception:
            return default

    profile_json = _j(row.profile_json, {})
    selected_notes = _j(row.selected_notes_json, [])
    sampled_articles = profile_json.get("sampled_articles") or []
    if sampled_articles and selected_notes:
        by_id = {str(a.get("note_id") or ""): a for a in sampled_articles}
        merged = []
        for n in selected_notes:
            extra = by_id.get(str(n.get("note_id") or "")) or {}
            merged.append({**n, **{k: v for k, v in extra.items() if v not in (None, "") or k not in n}})
        selected_notes = merged
    return {
        "profile_doc_id": row.profile_doc_id,
        "subscription_id": row.subscription_id,
        "profile_run_id": row.profile_run_id,
        "display_name": row.display_name,
        "red_id": row.red_id,
        "creator_id": row.creator_id,
        "industry": row.industry,
        "domain": row.domain,
        "niche": row.niche,
        "persona_summary": row.persona_summary,
        "target_audience": row.target_audience,
        "content_style": row.content_style,
        "deep_directions": _j(row.deep_directions_json, []),
        "recent_topics": _j(row.recent_topics_json, []),
        "content_type_distribution": _j(row.content_type_distribution_json, {}),
        "output_analysis": _j(row.output_analysis_json, {}),
        "selected_notes": selected_notes,
        "sampled_articles": sampled_articles,
        "profile_json": profile_json,
        "profile_md": row.profile_md,
        "profile_md_path": row.profile_md_path,
        "llm_model": row.llm_model,
        "is_latest": row.is_latest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_profile_run(subscription_id: str, *, trigger: str = "manual") -> Dict[str, Any]:
    get_engine()
    rid = _new_id("prun")
    with session_scope() as db:
        row = CreatorProfileRun(
            profile_run_id=rid,
            subscription_id=subscription_id,
            trigger=trigger,
            status="pending",
            stage="init",
            started_at=datetime.utcnow(),
        )
        db.add(row)
        db.flush()
        return _run_to_dict(row)


def update_profile_run(profile_run_id: str, **fields) -> None:
    with session_scope() as db:
        row = db.get(CreatorProfileRun, profile_run_id)
        if not row:
            return
        for k, v in fields.items():
            if not hasattr(row, k):
                continue
            if k.endswith("_json") and isinstance(v, (dict, list)):
                setattr(row, k, json.dumps(v, ensure_ascii=False))
            else:
                setattr(row, k, v)
        if fields.get("status") in ("completed", "partial", "failed") and not row.finished_at:
            row.finished_at = datetime.utcnow()


def get_profile_run(profile_run_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.get(CreatorProfileRun, profile_run_id)
        return _run_to_dict(row) if row else None


def get_latest_profile_run(subscription_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.execute(
            select(CreatorProfileRun)
            .where(CreatorProfileRun.subscription_id == subscription_id)
            .order_by(desc(CreatorProfileRun.created_at))
            .limit(1)
        ).scalar_one_or_none()
        return _run_to_dict(row) if row else None


def save_profile_doc(
    *,
    subscription_id: str,
    profile_run_id: str,
    payload: Dict[str, Any],
    profile_md: str,
    profile_md_path: str = "",
    llm_model: str = "",
) -> Dict[str, Any]:
    did = _new_id("pdoc")
    with session_scope() as db:
        db.execute(
            select(CreatorProfileDoc).where(
                CreatorProfileDoc.subscription_id == subscription_id,
                CreatorProfileDoc.is_latest.is_(True),
            )
        )
        for old in db.execute(
            select(CreatorProfileDoc).where(
                CreatorProfileDoc.subscription_id == subscription_id,
                CreatorProfileDoc.is_latest.is_(True),
            )
        ).scalars():
            old.is_latest = False

        row = CreatorProfileDoc(
            profile_doc_id=did,
            subscription_id=subscription_id,
            profile_run_id=profile_run_id,
            display_name=str(payload.get("display_name") or ""),
            red_id=str(payload.get("red_id") or ""),
            creator_id=str(payload.get("creator_id") or ""),
            industry=str(payload.get("industry") or ""),
            domain=str(payload.get("domain") or ""),
            niche=str(payload.get("niche") or ""),
            persona_summary=str(payload.get("persona_summary") or ""),
            target_audience=str(payload.get("target_audience") or ""),
            content_style=str(payload.get("content_style") or ""),
            deep_directions_json=json.dumps(payload.get("deep_directions") or [], ensure_ascii=False),
            recent_topics_json=json.dumps(payload.get("recent_topics") or [], ensure_ascii=False),
            content_type_distribution_json=json.dumps(
                payload.get("content_type_distribution") or {}, ensure_ascii=False
            ),
            output_analysis_json=json.dumps(payload.get("output_analysis") or {}, ensure_ascii=False),
            selected_notes_json=json.dumps(payload.get("selected_notes") or [], ensure_ascii=False),
            profile_json=json.dumps(payload, ensure_ascii=False),
            profile_md=profile_md,
            profile_md_path=profile_md_path,
            llm_model=llm_model,
            is_latest=True,
        )
        db.add(row)
        db.flush()
        return _doc_to_dict(row)


def get_latest_profile_doc(subscription_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        row = db.execute(
            select(CreatorProfileDoc)
            .where(
                CreatorProfileDoc.subscription_id == subscription_id,
                CreatorProfileDoc.is_latest.is_(True),
            )
            .limit(1)
        ).scalar_one_or_none()
        return _doc_to_dict(row) if row else None


def list_profile_runs(subscription_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(CreatorProfileRun)
            .where(CreatorProfileRun.subscription_id == subscription_id)
            .order_by(desc(CreatorProfileRun.created_at))
            .limit(max(1, min(limit, 50)))
        ).scalars().all()
        return [_run_to_dict(r) for r in rows]
