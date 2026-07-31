"""UP 画像 — 轻量网页原文拉取（禁止音视频下载/转写）。"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .pipeline_executor import get_profile_executor
from .task_manager import create_task, get_output_dir, update_task

_log = logging.getLogger("sba.creator_profile_article")
_CHAIN = "社媒订阅-UP画像-原文拉取"
_PROFILE_SEM: Optional[asyncio.Semaphore] = None


def _safe_log_link(link: str) -> str:
    """日志仅保留路由信息，禁止记录小红书访问 token。"""
    return re.sub(r"(?i)([?&]xsec_token=)[^&\s]+", r"\1***", str(link or ""))


def _public_note_url(link: str) -> str:
    """对外产物仅保留小红书笔记路径，不持久化或返回访问 token。"""
    return str(link or "").split("?", 1)[0].split("#", 1)[0]


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
        "media_processing": False,
        "skip_media_download": True,
        "skip_whisper": True,
        "skip_html": True,
        "skip_feishu": True,
        "source": "creator_profile",
    }


def _fetch_lightweight_page(link: str) -> Dict[str, Any]:
    """Only parse the XHS page HTML; never download media or invoke OCR/Whisper."""
    from .xiaohongshu_article import LinkAnalyzer

    analyzer = LinkAnalyzer()
    force_graphic = getattr(analyzer, "_analyze_xiaohongshu_v2", None)
    if callable(force_graphic):
        # The public analyzer intentionally routes video notes to the media pipeline.
        # Profile sampling needs only the page description/metadata, so reuse its
        # HTML parser while forcing the non-media branch and keeping image OCR off.
        result = force_graphic(link, force_graphic=True, include_image_ocr=False)
    else:
        result = analyzer.analyze_link(link, include_image_ocr=False)
    return dict(result) if isinstance(result, dict) else {}


async def _run_in_profile_pool(link: str) -> Dict[str, Any]:
    async with _sem():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(get_profile_executor(), _fetch_lightweight_page, link)


def _clean_page_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lines = []
    seen = set()
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def _render_lightweight_note_md(
    *,
    note: Dict[str, Any],
    link: str,
    page: Dict[str, Any],
) -> tuple[str, str, str]:
    title = _clean_page_text(note.get("title") or page.get("title") or note.get("note_id"))
    body = _clean_page_text(page.get("text_content"))
    summary = _clean_page_text(page.get("summary"))
    if summary and summary not in body:
        body = "\n\n".join(part for part in (body, summary) if part)

    content_type = _clean_page_text(note.get("content_type") or page.get("type") or "unknown")
    published_at = _clean_page_text(note.get("published_at"))
    evidence_level = "page_text" if len(body) >= 80 else "catalog_metadata"
    page_error = _clean_page_text(page.get("error"))

    lines = [
        f"# {title or '小红书笔记'}",
        "",
        "## 轻量采集说明",
        "",
        "- 采集方式：仅解析网页正文与元数据",
        "- 资源约束：未下载音视频，未调用 FFmpeg/Whisper，未做图片 OCR",
        f"- 证据级别：{evidence_level}",
        f"- 内容类型：{content_type or 'unknown'}",
    ]
    if published_at:
        lines.append(f"- 发布时间：{published_at}")
    lines.append(f"- 来源链接：{link}")
    if page_error:
        lines.append(f"- 网页解析提示：{page_error}")
    lines.extend(["", "## 页面可见内容", "", body or "（页面未提取到足够正文，仅保留上述可追溯元数据。）"])
    return "\n".join(lines).strip() + "\n", body, evidence_level


def _write_lightweight_note_md(task_id: str, content: str) -> str:
    out_dir = get_output_dir() / "creator_profile_sources"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{task_id}.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


def article_text_usable(text: str, *, min_len: int = 300) -> bool:
    """判断拉取到的原文是否可用于深度画像（排除「页面不见了」等占位）。"""
    body = (text or "").strip()
    if len(body) < min_len:
        return False
    bad_markers = ("你访问的页面不见了", "页面不存在", "笔记不存在", "内容无法展示")
    return not any(m in body for m in bad_markers)


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
    link = str(note.get("pipeline_url") or note.get("canonical_url") or "")
    public_link = _public_note_url(link)
    note_id = str(note.get("note_id") or "")
    link_source = str(note.get("link_source") or "")
    link_resolved = bool(note.get("link_resolved"))
    if not link:
        return {"ok": False, "note_id": note_id, "error": "missing_url"}
    if link_source == "bare_explore" or not link_resolved or "xsec_token" not in link:
        return {
            "ok": False,
            "note_id": note_id,
            "error": "link_unresolved",
            "canonical_url": str(note.get("canonical_url") or ""),
            "pipeline_url": str(note.get("pipeline_url") or ""),
            "link_source": link_source or "bare_explore",
        }

    _log.info(
        "[%s|creator_profile_article.run_article_only_for_note|%s|Agent执行|流水线输入] "
        "link=%s; link_source=%s; has_token=%s",
        _CHAIN,
        note_id,
        _safe_log_link(link)[:160],
        link_source,
        "xsec_token" in link,
    )

    task_id = create_task(
        platform,
        public_link,
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
        page = await asyncio.wait_for(_run_in_profile_pool(link), timeout=timeout_sec)
    except asyncio.TimeoutError:
        update_task(task_id, status="failed", error="lightweight_page_timeout")
        return {"ok": False, "note_id": note_id, "task_id": task_id, "error": "pipeline_timeout"}
    except Exception as ex:
        update_task(task_id, status="failed", error=str(ex)[:500])
        return {
            "ok": False,
            "note_id": note_id,
            "task_id": task_id,
            "error": f"lightweight_page_failed: {ex}",
        }

    md_content, article, evidence_level = _render_lightweight_note_md(
        note=note,
        link=public_link,
        page=page,
    )
    doc_path = _write_lightweight_note_md(task_id, md_content)
    title = note.get("title") or page.get("title") or note_id
    update_task(
        task_id,
        status="completed",
        stage="UP画像-轻量网页采集完成",
        progress=100,
        pipeline_route="creator_profile_light",
        doc_path=doc_path,
        doc_title=title,
        link_title=title,
        resume_context={
            "ai_analysis": {
                "article": article,
                "summary": str(page.get("summary") or ""),
                "evidence_level": evidence_level,
            }
        },
    )

    return {
        "ok": True,
        "note_id": note_id,
        "task_id": task_id,
        "title": title,
        "published_at": note.get("published_at"),
        "content_type": note.get("content_type"),
        "canonical_url": public_link,
        "pipeline_url": public_link,
        "link_source": note.get("link_source") or "",
        "doc_path": doc_path,
        "article": article,
        "evidence_level": evidence_level,
        "media_processing": False,
        "heavy_services_used": [],
        "warning": str(page.get("error") or ""),
    }
