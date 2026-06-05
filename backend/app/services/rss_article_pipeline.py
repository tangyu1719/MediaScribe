"""RSS 文章 → 链接沉淀流水线：全文抓取 → 摘要 MD → 任务中心卡片。"""
from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Dict, Optional

from .config import load_pipeline_config
from .document_consolidation import run_document_consolidation
from .file_naming import resolve_doc_title, output_basename
from .pipeline_executor import get_blocking_executor as _io_executor, get_llm_executor as _llm_executor
from .pipeline_finalize import complete_task_after_md, mark_pipeline_running
from .pipeline_stages import PipelineStageTracker, mark_failure_from_task
from .rss_content_fetch import fetch_article_full_text
from .rss_reader import attach_item_document, get_item_by_id
from . import task_manager as _tm
from .video_pipeline import generate_document_with_comments

_log = logging.getLogger("sba.rss_article_pipeline")
CHAIN = "RSS订阅阅读-链接沉淀"
LOG_MODULE = "rss_article_pipeline"


def _log(task_id: str, msg: str, level: str = "INFO") -> None:
    _tm.add_log(task_id, msg, level)


async def process_rss_article_pipeline(
    task_id: str,
    *,
    rss_item_id: str = "",
    user_id: str = "",
) -> None:
    """RSS 单篇沉淀：抓取全文 → LLM 摘要 → 标准 output_template MD。"""
    task = _tm.get_task(task_id)
    if not task:
        return

    link = (task.get("link") or "").strip()
    user_prompt = (task.get("user_prompt") or "").strip()
    cfg = load_pipeline_config()
    loop = asyncio.get_running_loop()

    item: Dict[str, Any] = {}
    if rss_item_id and user_id:
        try:
            item = get_item_by_id(user_id, rss_item_id) or {}
        except ValueError:
            item = {}

    link_title = (item.get("title") or task.get("link_title") or "").strip()
    feed_name = (item.get("feed_title") or "").strip()
    if link_title and not task.get("link_title"):
        _tm.update_task(task_id, link_title=link_title, content_type="文章", route_type="rss_article")

    tracker = PipelineStageTracker(
        task_id,
        route="rss_article",
        existing_stages=task.get("pipeline_stages"),
        resume_from=task.get("resume_from") or None,
        resume_context=task.get("resume_context"),
    )
    mark_pipeline_running(task_id)
    _tm.update_task(task_id, pipeline_route="rss_article", platform="RSS", status="running")

    try:
        source_text = ""
        if tracker.should_run("fetch_fulltext"):
            tracker.start("fetch_fulltext")
            _tm.update_task(task_id, stage="抓取 RSS 全文", progress=15)
            _log(task_id, f"开始抓取全文: {link}")

            def _do_fetch() -> dict:
                return fetch_article_full_text(
                    link,
                    feed_summary=(item.get("summary") or ""),
                    feed_title=link_title,
                )

            fetched = await loop.run_in_executor(_io_executor(), _do_fetch)
            if not fetched.get("ok"):
                err = fetched.get("error") or "全文抓取失败"
                tracker.fail("fetch_fulltext", err)
                _tm.update_task(task_id, status="failed", error=err)
                mark_failure_from_task(task_id, err, route="rss_article", stage_id="fetch_fulltext")
                return
            source_text = (fetched.get("text") or "").strip()
            _log(
                task_id,
                f"全文抓取完成; source={fetched.get('source')}; chars={fetched.get('char_len')}",
            )
            tracker.complete(
                "fetch_fulltext",
                {"char_len": len(source_text), "source": fetched.get("source")},
                persist_payload={"source_text": source_text, "fetch_source": fetched.get("source")},
            )
        else:
            ck = tracker.ctx_get("fetch_fulltext")
            source_text = (ck.get("source_text") or "").strip()
            if not source_text:
                tracker.fail("fetch_fulltext", "断点缺少全文缓存")
                return

        ai_summary = ""
        article_text = ""
        title = ""
        if tracker.should_run("ai_analysis"):
            tracker.start("ai_analysis")
            _tm.update_task(task_id, stage="AI 整理与摘要", progress=55, status="consolidating")
            cfg_run = {**cfg, "_task_id": task_id, "_log_chain": CHAIN}

            def _consolidate() -> dict:
                return run_document_consolidation(
                    text=source_text,
                    llm_cfg=cfg_run,
                    user_prompt=user_prompt,
                    stage_label="RSS 文章沉淀",
                    log_cb=lambda msg, lvl="INFO": _log(task_id, msg, lvl),
                )

            consolidation = await loop.run_in_executor(_llm_executor(), _consolidate)
            ai_summary = (consolidation.get("ai_summary") or "").strip()
            article_text = (consolidation.get("article") or "").strip() or source_text
            if not ai_summary:
                err = consolidation.get("error_message") or "AI 摘要失败"
                tracker.fail("ai_analysis", err)
                _tm.update_task(task_id, status="failed", error=err)
                mark_failure_from_task(task_id, err, route="rss_article", stage_id="ai_analysis")
                return

            def _resolve_title() -> str:
                hint = link_title or feed_name or "RSS文章"
                return resolve_doc_title(
                    ai_summary,
                    link,
                    link_title=hint,
                    fallback=hint,
                    log_cb=lambda msg: _log(task_id, msg),
                    platform="RSS",
                )

            title = await loop.run_in_executor(_io_executor(), _resolve_title)
            _tm.update_task(task_id, doc_title=title)
            tracker.complete(
                "ai_analysis",
                {"ai_summary_len": len(ai_summary), "article_len": len(article_text), "doc_title": title},
                persist_payload={
                    "ai_summary": ai_summary,
                    "article": article_text,
                    "title": title,
                    "link_title": link_title or feed_name,
                },
            )
        else:
            ck = tracker.ctx_get("ai_analysis")
            ai_summary = (ck.get("ai_summary") or "").strip()
            article_text = (ck.get("article") or "").strip()
            title = (ck.get("title") or "").strip()
            if not ai_summary:
                tracker.fail("ai_analysis", "断点缺少摘要缓存")
                return

        doc_path = ""
        if tracker.should_run("generate_md"):
            tracker.start("generate_md")
            _tm.update_task(task_id, stage="生成 Markdown", progress=88, status="generating")

            def _gen_md() -> str:
                return generate_document_with_comments(
                    {
                        "ai_summary": ai_summary,
                        "article": article_text,
                        "title": title,
                        "link_title": link_title or feed_name or title,
                        "content_type": "文章",
                        "transcribe_source": "rss_fulltext",
                    },
                    link,
                    "RSS",
                    task_id,
                    user_prompt=user_prompt,
                    cfg=cfg,
                )

            doc_path = await loop.run_in_executor(_io_executor(), _gen_md)
            if not doc_path:
                tracker.fail("generate_md", "MD 生成失败")
                _tm.update_task(task_id, status="failed", error="MD 生成失败")
                return
            tracker.complete(
                "generate_md",
                {"doc_path": doc_path, "doc_filename": output_basename(doc_path)},
                persist_payload={"doc_path": doc_path, "title": title},
            )
        else:
            ck = tracker.ctx_get("generate_md")
            doc_path = (ck.get("doc_path") or "").strip()
            if not doc_path:
                tracker.fail("generate_md", "断点缺少 MD 路径")
                return

        complete_task_after_md(
            task_id,
            doc_path=doc_path,
            link=link,
            platform="RSS",
            tracker=tracker,
            title=title,
            url_hash=task.get("url_hash") or "",
        )

        if rss_item_id and user_id:
            attach_item_document(
                user_id,
                rss_item_id,
                doc_path=doc_path,
                doc_filename=output_basename(doc_path),
                task_id=task_id,
            )
        _log(task_id, f"RSS 沉淀完成: {doc_path}")

    except Exception as ex:
        _log(task_id, f"RSS 沉淀异常: {ex}", "ERROR")
        _log(task_id, traceback.format_exc(), "ERROR")
        _tm.update_task(task_id, status="failed", error=str(ex), pipeline_route="rss_article")
        mark_failure_from_task(task_id, str(ex), route="rss_article")
