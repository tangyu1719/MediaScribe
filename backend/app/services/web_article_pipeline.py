"""通用网页 / 微信公众号 → 链接沉淀流水线。"""
from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Dict

from .config import load_pipeline_config
from .document_consolidation import run_document_consolidation
from .file_naming import resolve_doc_title, output_basename, resolve_link_title
from .pipeline_executor import get_blocking_executor as _io_executor, get_llm_executor as _llm_executor
from .pipeline_finalize import complete_task_after_md, mark_pipeline_running
from .pipeline_stages import PipelineStageTracker, mark_failure_from_task
from .web_article_fetch import fetch_web_article, is_wechat_article_url
from . import task_manager as _tm
from .video_pipeline import generate_document_with_comments

_log = logging.getLogger("sba.web_article_pipeline")
CHAIN = "链接沉淀文档-网页文章"


def _log(task_id: str, msg: str, level: str = "INFO") -> None:
    _tm.add_log(task_id, msg, level)


async def process_web_article_pipeline(task_id: str, *, user_prompt: str = "") -> None:
    """网页文章沉淀：抓取全文 → LLM 整理摘要 → 标准 MD。"""
    task = _tm.get_task(task_id)
    if not task:
        return

    link = (task.get("link") or "").strip()
    user_prompt = (user_prompt or task.get("user_prompt") or "").strip()
    platform = (task.get("platform") or "").strip()
    if not platform:
        platform = "微信公众号" if is_wechat_article_url(link) else "通用网页"

    cfg = load_pipeline_config()
    loop = asyncio.get_running_loop()

    link_title = (task.get("link_title") or "").strip()
    if not link_title:
        link_title = await loop.run_in_executor(
            _io_executor(),
            lambda: resolve_link_title(link, platform=platform, log_cb=lambda m: _log(task_id, m)),
        )
        if link_title:
            _tm.update_task(task_id, link_title=link_title)

    tracker = PipelineStageTracker(
        task_id,
        route="web_article",
        existing_stages=task.get("pipeline_stages"),
        resume_from=task.get("resume_from") or None,
        resume_context=task.get("resume_context"),
    )
    mark_pipeline_running(task_id)
    _tm.update_task(
        task_id,
        pipeline_route="web_article",
        platform=platform,
        content_type="文章",
        status="running",
    )

    try:
        source_text = ""
        fetch_title = ""
        if tracker.should_run("fetch_fulltext"):
            tracker.start("fetch_fulltext")
            _tm.update_task(task_id, stage="抓取网页全文", progress=15, status="extracting")
            _log(task_id, f"开始抓取网页全文: {link}")

            fetched = await loop.run_in_executor(
                _io_executor(),
                lambda: fetch_web_article(link),
            )
            if not fetched.get("ok"):
                err = fetched.get("error") or "网页全文抓取失败"
                tracker.fail("fetch_fulltext", err)
                _tm.update_task(task_id, status="failed", error=err, error_code="WEB_FETCH_EMPTY")
                mark_failure_from_task(task_id, err, route="web_article", stage_id="fetch_fulltext")
                return
            source_text = (fetched.get("text") or "").strip()
            fetch_title = (fetched.get("title") or "").strip()
            if fetch_title and not link_title:
                link_title = fetch_title
                _tm.update_task(task_id, link_title=link_title)
            _log(
                task_id,
                f"网页全文抓取完成; source={fetched.get('source')}; chars={fetched.get('char_len')}",
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
                    stage_label=f"{platform}文章沉淀",
                    log_cb=lambda msg, lvl="INFO": _log(task_id, msg, lvl),
                )

            consolidation = await loop.run_in_executor(_llm_executor(), _consolidate)
            block_code = str(consolidation.get("error_code") or "").strip()
            if block_code and consolidation.get("article_source") == "blocked":
                err = f"[{block_code}] {consolidation.get('error_message') or '文档沉淀失败'}"
                tracker.fail("ai_analysis", err)
                _tm.update_task(task_id, status="failed", error=err, error_code=block_code)
                mark_failure_from_task(task_id, err, route="web_article", stage_id="ai_analysis")
                return

            ai_summary = (consolidation.get("ai_summary") or "").strip()
            article_text = (consolidation.get("article") or "").strip() or source_text
            if not ai_summary:
                err = consolidation.get("error_message") or "AI 摘要失败"
                tracker.fail("ai_analysis", err)
                _tm.update_task(task_id, status="failed", error=err)
                mark_failure_from_task(task_id, err, route="web_article", stage_id="ai_analysis")
                return

            def _resolve_title() -> str:
                hint = link_title or fetch_title or f"{platform}文章"
                return resolve_doc_title(
                    ai_summary,
                    link,
                    link_title=hint,
                    fallback=hint,
                    log_cb=lambda msg: _log(task_id, msg),
                    platform=platform,
                    source_text_len=len(source_text),
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
                    "link_title": link_title,
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
                        "link_title": link_title or title,
                        "content_type": "文章",
                        "transcribe_source": "web_article_fetch",
                        "extracted_metadata": (task.get("extracted_metadata") or {}),
                    },
                    link,
                    platform,
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
            platform=platform,
            tracker=tracker,
            title=title,
            url_hash=task.get("url_hash") or "",
        )
        _log(task_id, f"网页文章沉淀完成: {doc_path}")

    except Exception as ex:
        _log(task_id, f"网页文章沉淀异常: {ex}", "ERROR")
        _log(task_id, traceback.format_exc(), "ERROR")
        _tm.update_task(task_id, status="failed", error=str(ex), pipeline_route="web_article")
        mark_failure_from_task(task_id, str(ex), route="web_article")
