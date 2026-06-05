"""UP 画像 — 仅原文 MD 的链接分析（独立线程池，不抢主链路槽位）。"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .pipeline_executor import get_profile_executor
from .task_manager import create_task, get_task, update_task
from .video_pipeline import process_video_pipeline

_log = logging.getLogger("sba.creator_profile_article")
_CHAIN = "社媒订阅-UP画像-原文拉取"
_PROFILE_SEM: Optional[asyncio.Semaphore] = None


def _sem() -> asyncio.Semaphore:
    global _PROFILE_SEM
    if _PROFILE_SEM is None:
        import os

        n = max(1, min(4, int(os.environ.get("PROFILE_ARTICLE_CONCURRENCY", "2") or "2")))
        _PROFILE_SEM = asyncio.Semaphore(n)
    return _PROFILE_SEM


def _profile_pipeline_options() -> Dict[str, Any]:
    return {
        "article_only": True,
        "skip_html": True,
        "skip_feishu": True,
        "whisper_pool": "profile",
        "source": "creator_profile",
    }


async def _run_in_profile_pool(task_id: str) -> None:
    async with _sem():
        loop = asyncio.get_running_loop()

        def _worker():
            import asyncio as _aio

            _aio.run(process_video_pipeline(task_id))

        await loop.run_in_executor(get_profile_executor(), _worker)


def extract_article_from_md(md_path: str) -> str:
    p = Path(md_path)
    if not p.is_file():
        return ""
    text = p.read_text(encoding="utf-8", errors="ignore")
    for pat in (
        r"##\s*原始内容\s*\n+正文：\s*\n([\s\S]*?)(?:\n##|\n---|\Z)",
        r"##\s*原始内容\s*\n([\s\S]*?)(?:\n##|\n---|\Z)",
        r"正文：\s*\n([\s\S]*?)(?:\n##\s*AI分析摘要|\n---|\Z)",
    ):
        m = re.search(pat, text, re.I)
        if m:
            body = m.group(1).strip()
            if len(body) > 80:
                return body
    return text[-8000:] if len(text) > 8000 else text


async def run_article_only_for_note(
    *,
    note: Dict[str, Any],
    platform: str = "小红书",
    timeout_sec: int = 1800,
) -> Dict[str, Any]:
    link = str(note.get("canonical_url") or "")
    note_id = str(note.get("note_id") or "")
    if not link:
        return {"ok": False, "note_id": note_id, "error": "missing_url"}

    task_id = create_task(
        platform,
        link,
        "",
        {"enabled": False},
        pipeline_options=_profile_pipeline_options(),
    )
    update_task(task_id, stage="UP画像-原文拉取", pipeline_route="creator_profile")

    _log.info(
        "[%s|creator_profile_article.run_article_only_for_note|%s|Agent执行|开始] note_id=%s; task_id=%s",
        _CHAIN,
        note_id,
        note_id,
        task_id,
    )

    try:
        await asyncio.wait_for(_run_in_profile_pool(task_id), timeout=timeout_sec)
    except asyncio.TimeoutError:
        return {"ok": False, "note_id": note_id, "task_id": task_id, "error": "pipeline_timeout"}

    task = get_task(task_id) or {}
    st = task.get("status")
    if st != "completed":
        return {
            "ok": False,
            "note_id": note_id,
            "task_id": task_id,
            "error": task.get("error") or f"pipeline_{st}",
        }

    doc_path = str(task.get("doc_path") or "")
    article = extract_article_from_md(doc_path)
    if not article.strip():
        article = str(task.get("resume_context", {}).get("ai_analysis", {}).get("article") or "")

    return {
        "ok": True,
        "note_id": note_id,
        "task_id": task_id,
        "title": note.get("title") or task.get("link_title") or task.get("doc_title"),
        "published_at": note.get("published_at"),
        "content_type": note.get("content_type"),
        "canonical_url": link,
        "doc_path": doc_path,
        "article": article,
    }
