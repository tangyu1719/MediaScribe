"""小红书图文处理服务 —— 参照 video_gui._run_xiaohongshu_analysis 原样搬运

完整链路（与原项目完全一致）：
  URL → link_analyzer.analyze_link() → 类型检测 → OCR补偿 → 原文装配
      → run_document_consolidation → extract_title_from_summary → generate_md → 飞书上传
"""
from __future__ import annotations
import asyncio
import hashlib
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
_BACKEND_DIR = None
for _p in _HERE.parents:
    if (_AGENT_DIR is None) and (_p / "src" / "agent").is_dir():
        _AGENT_DIR = (_p / "src" / "agent").resolve()
    if (_BACKEND_DIR is None) and (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _BACKEND_DIR = (_p / "backend").resolve()
if _BACKEND_DIR is None:
    _BACKEND_DIR = _HERE.parents[3] / "backend"
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from link_analyzer import LinkAnalyzer

from . import task_manager as _tm
from .history_manager import add_or_update_task_in_history
from .pipeline_stages import PipelineStageTracker, mark_failure_from_task, stage_label
from .pipeline_checkpoint import clear_pipeline_cache, list_cached_stage_ids
from .document_consolidation import (
    run_document_consolidation, extract_title_from_summary, clean_title,
)
from .file_naming import (
    resolve_link_title,
    resolve_doc_title,
    build_output_md_path,
    output_basename,
    render_output_template,
    preview_from_analyzer_result,
)
from .ops import ops_monitor_task
from .pipeline_logging import pipeline_log, log_span_event
from .feishu_pipeline import start_feishu_upload_async
from .pipeline_finalize import complete_task_after_md, mark_pipeline_running
from .span_audit import create_task as _span_create_task, create_step as _span_create_step, start_step as _span_start_step, finish_step as _span_finish_step, patch_task_snapshot as _span_patch_task
from .config import load_pipeline_config

from .pipeline_executor import get_blocking_executor as _io_executor, get_llm_executor as _llm_executor

CHAIN = "链接沉淀文档-小红书图文"
LOG_MODULE = "xiaohongshu_article"


def _log(task_id: str, msg: str, level: str = "INFO"):
    _tm.add_log(task_id, f"[{threading.current_thread().name}] {msg}", level)


def _plog(task_id: str, obj: str, phase: str, action: str, level: str = "INFO", **kwargs):
    pipeline_log(task_id, CHAIN, f"{LOG_MODULE}", obj, phase, "硬编执行", action, level, **kwargs)


def _begin_span(
    task_id: str,
    session_id: str,
    step_type: str,
    step_name: str,
    input_payload: Optional[Dict] = None,
):
    step = _span_create_step(task_id, session_id, step_type, step_name)
    sid = step["step_id"]
    inp = input_payload or {}
    log_span_event(
        task_id, CHAIN, LOG_MODULE, step_name,
        step_id=sid, step_name=step_name, step_type=step_type,
        event="创建", status="created",
        input_keys=",".join(sorted(inp.keys()))[:200],
    )
    _span_start_step(sid, input_payload=inp)
    log_span_event(
        task_id, CHAIN, LOG_MODULE, step_name,
        step_id=sid, step_name=step_name, step_type=step_type,
        event="开始", status="running",
    )
    return step


def _end_span(
    step: Dict,
    *,
    status: str,
    output_payload: Optional[Dict] = None,
    error_code: str = "",
    error_message: str = "",
    open_layer: Optional[Dict] = None,
    task_id: str = "",
):
    sid = step["step_id"]
    name = step.get("step_name", "")
    stype = step.get("step_type", "")
    _span_finish_step(
        sid,
        status=status,
        output_payload=output_payload or {},
        error_code=error_code,
        error_message=error_message,
        open_layer=open_layer or {"decision": "continue"},
    )
    log_span_event(
        task_id or step.get("task_id", ""),
        CHAIN, LOG_MODULE, name,
        step_id=sid, step_name=name, step_type=stype,
        event="结束", status=status,
        error_code=error_code or "",
        output_keys=",".join(sorted((output_payload or {}).keys()))[:200],
    )

def _load_config() -> Dict:
    return load_pipeline_config()


# ─── 节点1: 图文提取（参照 _run_xiaohongshu_analysis:11162） ───

def _extract_xiaohongshu_content(
    link: str,
    task_id: str,
    *,
    prefetched: Optional[Dict] = None,
) -> Optional[Dict]:
    try:
        import time as _time

        t0 = _time.perf_counter()
        if prefetched and isinstance(prefetched, dict) and not prefetched.get("error"):
            timing = prefetched.get("_timing") or {}
            _log(
                task_id,
                "[小红书图文沉淀] 复用路由阶段 LinkAnalyzer 结果，跳过二次 analyze_link; "
                f"timing_total_ms={timing.get('total_ms', '')}",
            )
            result = prefetched
        else:
            analyzer = LinkAnalyzer()
            _log(task_id, "开始分析小红书链接（轻量解析，不含 OCR）…")
            result = analyzer.analyze_link(link, include_image_ocr=False)
            timing = (result or {}).get("_timing") or {}
            _log(
                task_id,
                f"[小红书图文沉淀] analyze_link 完成; elapsed_ms={int((_time.perf_counter() - t0) * 1000)}; "
                f"timing_total_ms={timing.get('total_ms', '')}; phases={timing.get('phases_ms', {})}",
            )

        if not result or result.get("error"):
            err = (result or {}).get("error", "分析失败")
            _log(task_id, f"小红书内容检测失败：{err}", "ERROR")
            return None

        content_type = result.get("type", "")
        _log(task_id, f"小红书内容类型检测结果：{content_type}")

        if content_type in ("video", "xiaohongshu_video"):
            _log(task_id, "检测到小红书视频，图文链路不支持，请使用视频处理", "WARNING")
            return None
        elif content_type == "xiaohongshu":
            _log(task_id, "检测到小红书图文，继续图文分析...")
        else:
            _log(task_id, f"未知内容类型：{content_type}，默认按图文处理", "WARNING")

        _log(task_id,
            f"[小红书图文沉淀] 提取统计："
            f"text_len={len((result.get('text_content') or '').strip())} | "
            f"image_links={len(result.get('image_links', []) or [])} | "
            f"image_ocr={len(result.get('image_analysis', []) or [])}"
        )
        return result

    except Exception as e:
        _log(task_id, f"小红书图文提取异常：{e}", "ERROR")
        return None


# ─── 节点2: OCR 补偿（参照原项目 OCR补偿段） ───

def _ocr_compensation(result: Dict, task_id: str) -> Dict:
    image_links = list(result.get("image_links", []) or [])
    image_analysis = list(result.get("image_analysis", []) or [])

    if image_links and not image_analysis:
        import time as _time

        _log(task_id, f"[小红书图文沉淀] 检测到 image_links={len(image_links)} 但 image_analysis=0，开始OCR补偿...")
        t_ocr_all = _time.perf_counter()
        analyzer = LinkAnalyzer()
        recovered = []
        for idx, img_url in enumerate(image_links, 1):
            try:
                _log(task_id, f"[小红书图文沉淀][OCR] start idx={idx} url={img_url}")
                img_data = analyzer.download_image(img_url)
                if not img_data: continue
                ocr_result = analyzer.ocr_image(img_data)
                if not ocr_result: continue
                img_text = (analyzer.extract_text_from_ocr(ocr_result) or "").strip()
                if not img_text: continue
                recovered.append({"url": img_url, "text": img_text, "index": idx})
                if idx < len(image_links): time.sleep(1.0)
            except Exception as e:
                _log(task_id, f"[小红书图文沉淀] OCR补偿失败 idx={idx}: {e}", "WARNING")
        if recovered:
            result["image_analysis"] = recovered
        _log(
            task_id,
            f"[小红书图文沉淀] OCR补偿结束; ok={len(recovered)}; "
            f"elapsed_ms={int((_time.perf_counter() - t_ocr_all) * 1000)}",
        )
    return result


# ─── 节点3: 原文装配（参照 _build_xiaohongshu_raw_text:7715） ───

def _build_xiaohongshu_raw_text(result: Dict) -> str:
    result = result or {}
    title = (result.get("title") or "").strip()
    if title.endswith(" - 小红书"):
        title = title[:-5].strip()
    text_content = (result.get("text_content") or "").strip()
    image_analysis = list(result.get("image_analysis", []) or [])
    image_links = list(result.get("image_links", []) or [])

    lines = []
    if title: lines.append(f"# {title}")
    if text_content: lines.append("## 正文\n" + text_content)

    ocr_parts = []
    seen_urls = set()
    for i, img in enumerate(image_analysis, 1):
        t = (img.get("text") or "").strip()
        idx = img.get("index", i)
        u = (img.get("url") or "").strip()
        if u: seen_urls.add(u)
        block = [f"[图片{idx}]"]
        if u: block.append(f"来源：{u}")
        block.append(t if t else "（OCR未识别到文本）")
        ocr_parts.append("\n".join(block))
    if image_links:
        missing = [u for u in image_links if u and u not in seen_urls]
        for j, u in enumerate(missing, 1):
            ocr_parts.append(f"[图片补充{j}]\n来源：{u}\n（OCR未识别到文本）")
    if ocr_parts: lines.append("## 图片OCR\n" + "\n\n".join(ocr_parts))

    raw = "\n\n".join([x for x in lines if x]).strip()
    return raw if raw else (result.get("summary") or "").strip()


# ─── 生成 MD ───

def _generate_md(result_data: Dict, link: str, platform: str, task_id: str, cfg: Optional[Dict] = None) -> str:
    cfg = cfg or _load_config()
    title = (result_data.get("title") or "小红书内容").strip()
    ai_summary = result_data.get("ai_summary", "")
    article = result_data.get("article", ai_summary)
    comments_text = result_data.get("comments_text", "")
    link_title = (result_data.get("link_title") or "").strip()

    content_type = result_data.get("content_type", "图文")
    naming_rule = (cfg.get("file_naming_rule") or "").strip()
    doc_path, _basename = build_output_md_path(
        title,
        content_type,
        naming_rule=naming_rule if "{doc_title}" in naming_rule else "",
    )
    transcribe_source = (result_data.get("transcribe_source") or "").strip() or "link_analyzer"
    output_tpl = (cfg.get("output_template") or "").strip()
    md = render_output_template(
        output_tpl,
        platform=platform,
        link=link,
        article=article,
        summary=ai_summary,
        content_type=content_type,
        transcribe_source=transcribe_source,
        link_title=link_title,
        doc_title=title,
    )
    if not md.strip():
        md = f"""# {platform}{content_type}分析

## 分析信息
- 分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 原始链接: {link}
- 平台: {platform}
- 类型: {content_type}
- 转写链路: {transcribe_source}

## 原始内容
正文：
{article}

## AI分析摘要
{ai_summary}
"""
    if comments_text:
        md += f"""
## 评论区

{comments_text}
"""

    md += """
---
*由多模态文档化助手自动生成*
"""
    with open(doc_path, "w", encoding="utf-8") as f: f.write(md)
    _log(task_id, f"文档生成成功: {doc_path}")
    return doc_path


# ─── 主流水线 ───

async def process_xiaohongshu_article_pipeline(task_id: str, user_prompt: str = "", comments_data = None):
    task: Optional[Dict[str, Any]] = None
    try:
        task = _tm.get_task(task_id)
        if not task:
            return
        link = task["link"]
        _tm.update_task(task_id, pipeline_route="xiaohongshu_graphic", stage="小红书图文分析", status="running")
        loop = asyncio.get_running_loop()
        trace_id = f"trace_{task_id}"
        session_id = task.get("session_id", "") or task_id
        cfg = _load_config()
        naming_rule = (cfg.get("file_naming_rule") or "").strip()
        comments_cfg = task.get("comments") or {"enabled": False}
        read_comments = bool(comments_cfg.get("enabled"))

        _span_create_task(task.get("session_id", ""), link, task_id=task_id)
        _span_patch_task(
            task_id,
            fixed={"task_id": task_id, "trace_id": trace_id, "source_url": link},
            open_layer={"objective": "小红书统一处理链路", "current_assessment": "开始执行", "decision": "continue"},
        )
        _plog(task_id, link[:80], "入口", "小红书图文流水线开始", link=link, trace_id=trace_id, read_comments=read_comments)

        tracker = PipelineStageTracker(
            task_id,
            route="xiaohongshu_graphic",
            existing_stages=task.get("pipeline_stages"),
            resume_from=(task.get("resume_from") or None),
            resume_context=task.get("resume_context"),
        )
        mark_pipeline_running(task_id)
        if tracker.resume_from:
            cached = list_cached_stage_ids(task.get("url_hash") or "")
            _log(
                task_id,
                f"断点恢复：从「{stage_label('xiaohongshu_graphic', tracker.resume_from)}」继续"
                + (f"；磁盘缓存步骤={','.join(cached)}" if cached else ""),
            )
        result: Optional[Dict[str, Any]] = None
        link_title = (task.get("link_title") or "").strip()

        # ── 提取 + OCR ──
        if tracker.should_run("extract") or tracker.should_run("ocr"):
            extract_span = _begin_span(task_id, session_id, "retrieval", "链接识别/内容提取", {"link": link, "user_prompt_len": len(user_prompt or "")})
            tracker.start("extract")
            _tm.update_task(task_id, status="extracting", stage="提取小红书图文内容", progress=10)
            prefetch = task.get("link_analyzer_prefetch")
            result = await loop.run_in_executor(
                _io_executor(),
                lambda: _extract_xiaohongshu_content(link, task_id, prefetched=prefetch),
            )
            if not result:
                _end_span(extract_span, status="failed", error_code="XHS_EXTRACT_FAILED", error_message="小红书内容提取失败", open_layer={"decision": "stop"}, task_id=task_id)
                tracker.fail("extract", "小红书内容提取失败")
                return
            _end_span(extract_span, status="completed", task_id=task_id, output_payload={"ok": True, "title": (result.get("title") or "")[:120]})
            tracker.complete("extract", {"title": (result.get("title") or "")[:120]})
            link_title = resolve_link_title(link, platform="小红书", analyzer_title=(result.get("title") or ""), log_cb=lambda msg: _log(task_id, msg)) or link_title
            if link_title:
                _tm.update_task(task_id, link_title=link_title, progress=25, **{k: v for k, v in preview_from_analyzer_result(result, link, "小红书").items() if v})
            else:
                _tm.update_task(task_id, progress=25, **{k: v for k, v in preview_from_analyzer_result(result, link, "小红书").items() if v})
            ocr_span = _begin_span(task_id, session_id, "retrieval", "OCR补偿", {"image_links": len(result.get("image_links", []) or [])})
            tracker.start("ocr")
            _tm.update_task(task_id, status="ocr", stage="OCR补偿", progress=35)
            result = await loop.run_in_executor(_io_executor(), lambda: _ocr_compensation(result, task_id))
            _end_span(ocr_span, status="completed", task_id=task_id, output_payload={"ok": True})
            tracker.complete("ocr", {"title": link_title}, persist_payload=result)
        else:
            tracker.log_skip("extract")
            tracker.log_skip("ocr")
            result = tracker.ctx_get("ocr")
            if not result:
                tracker.fail("ocr", "断点缺少 OCR 缓存，无法恢复")
                return
            link_title = (result.get("title") or link_title or "").strip() or link_title

        # ── 评论区 ──
        if read_comments:
            if tracker.should_run("comments"):
                comments_span = _begin_span(task_id, session_id, "retrieval", "评论区抓取", {"link": link})
                tracker.start("comments")
                _tm.update_task(task_id, status="comments", stage="抓取评论区", progress=45)
                try:
                    if comments_data is not None and comments_data.comments:
                        from .comment_scraper import format_comments_as_text
                        result["comments"] = comments_data
                        result["comments_text"] = format_comments_as_text(comments_data)
                        _end_span(comments_span, status="completed", task_id=task_id, output_payload={"count": comments_data.fetched_count})
                        tracker.complete("comments", {"fetched_count": comments_data.fetched_count}, persist_payload={"comments_text": result.get("comments_text", "")})
                    else:
                        from .comment_scraper import scrape_comments, format_comments_as_text
                        comment_max_count = int((comments_cfg.get("count") or 0)) or None
                        if comment_max_count == 0:
                            comment_max_count = None
                        comments_result = await loop.run_in_executor(
                            _io_executor(),
                            lambda: scrape_comments(link, platform="xiaohongshu", max_count=comment_max_count, sort_by=comments_cfg.get("sort", "hot")),
                        )
                        if comments_result.comments:
                            result["comments"] = comments_result
                            result["comments_text"] = format_comments_as_text(comments_result)
                            _end_span(comments_span, status="completed", task_id=task_id, output_payload={"count": comments_result.fetched_count})
                            tracker.complete("comments", {"fetched_count": comments_result.fetched_count}, persist_payload={"comments_text": result.get("comments_text", "")})
                        else:
                            _end_span(comments_span, status="failed", error_code="XHS_COMMENTS_EMPTY", error_message=comments_result.error or "无评论", task_id=task_id)
                            tracker.complete("comments", {"fetched_count": 0})
                except Exception as exc:
                    _end_span(comments_span, status="failed", error_code="XHS_COMMENTS_FAILED", error_message=str(exc), task_id=task_id)
                    tracker.complete("comments", {"fetched_count": 0})
            else:
                tracker.log_skip("comments")
                ck = tracker.ctx_get("comments")
                if ck.get("comments_text"):
                    result["comments_text"] = ck["comments_text"]

        # ── 原文装配 ──
        source_text = ""
        if tracker.should_run("assemble"):
            assemble_span = _begin_span(task_id, session_id, "retrieval", "原文装配", {"source": "xiaohongshu_raw"})
            tracker.start("assemble")
            _tm.update_task(task_id, status="assembling", stage="原文装配", progress=50)
            source_text = await loop.run_in_executor(_io_executor(), lambda: _build_xiaohongshu_raw_text(result))
            if not source_text:
                _end_span(assemble_span, status="failed", error_code="XHS_ASSEMBLY_EMPTY", error_message="原文装配为空", task_id=task_id)
                tracker.fail("assemble", "原文装配为空")
                return
            _end_span(assemble_span, status="completed", task_id=task_id, output_payload={"text_length": len(source_text)})
            tracker.complete("assemble", {"raw_text_len": len(source_text)}, persist_payload={"source_text": source_text, "link_title": link_title})
        else:
            tracker.log_skip("assemble")
            ck = tracker.ctx_get("assemble")
            source_text = (ck.get("source_text") or "").strip()
            if not source_text:
                tracker.fail("assemble", "断点缺少原文缓存，无法恢复")
                return

        # ── AI 润色 + 摘要 ──
        ai_summary = ""
        article_text = ""
        title = ""
        if tracker.should_run("ai_analysis"):
            consolidate_span = _begin_span(task_id, session_id, "llm_call", "原文整理+摘要", {"text_length": len(source_text)})
            tracker.start("ai_analysis")
            _tm.update_task(task_id, status="consolidating", stage="AI润色+摘要", progress=65)
            cfg = {**cfg, "_task_id": task_id, "_log_chain": CHAIN}

            def _ops_cb(link, error_message, stage, error_type):
                try:
                    ops_monitor_task(link=link, task_id=task_id, status="failed", logs=[], error_info=f"{error_type}: {error_message}")
                except Exception:
                    pass

            consolidation = await loop.run_in_executor(
                _llm_executor(),
                lambda: run_document_consolidation(
                    text=source_text,
                    llm_cfg=cfg,
                    user_prompt=user_prompt,
                    stage_label="小红书图文沉淀",
                    log_cb=lambda msg, lvl="INFO": _log(task_id, msg, lvl),
                    ops_cb=_ops_cb,
                ),
            )
            ai_summary = (consolidation.get("ai_summary") or "").strip()
            article_text = (consolidation.get("article") or "").strip()
            ok_sum = bool(ai_summary)
            _end_span(consolidate_span, status="completed" if ok_sum else "failed", task_id=task_id, error_code="" if ok_sum else "XHS_SUMMARY_FAILED")
            if not ok_sum:
                tracker.fail("ai_analysis", "AI摘要失败")
                return
            title_span = _begin_span(task_id, session_id, "summary", "标题提取", {})
            title = await loop.run_in_executor(
                _io_executor(),
                lambda: resolve_doc_title(ai_summary, link, link_title=link_title, fallback=(result.get("title") or "小红书内容"), log_cb=lambda msg: _log(task_id, msg), platform="小红书"),
            )
            _tm.update_task(task_id, doc_title=title)
            _end_span(title_span, status="completed", task_id=task_id, output_payload={"doc_title": title})
            tracker.complete(
                "ai_analysis",
                {"ai_summary_len": len(ai_summary), "doc_title": title},
                persist_payload={"ai_summary": ai_summary, "article": article_text or source_text, "title": title, "link_title": link_title},
            )
        else:
            tracker.log_skip("ai_analysis")
            ck = tracker.ctx_get("ai_analysis")
            ai_summary = (ck.get("ai_summary") or "").strip()
            article_text = (ck.get("article") or "").strip()
            title = (ck.get("title") or "").strip()
            link_title = (ck.get("link_title") or link_title or "").strip()
            if not ai_summary:
                tracker.fail("ai_analysis", "断点缺少摘要缓存，无法恢复")
                return
            _tm.update_task(task_id, doc_title=title)

        # ── 生成 MD ──
        doc_path = ""
        if tracker.should_run("generate_md"):
            md_span = _begin_span(task_id, session_id, "llm_call", "Markdown生成", {"doc_title": title})
            tracker.start("generate_md")
            _tm.update_task(task_id, status="generating", stage="生成Markdown", progress=90)
            doc_path = await loop.run_in_executor(
                _io_executor(),
                lambda: _generate_md(
                    {
                        "ai_summary": ai_summary,
                        "article": article_text or source_text,
                        "title": title,
                        "link_title": link_title,
                        "content_type": (task.get("content_type") or "图文"),
                    },
                    link,
                    "小红书",
                    task_id,
                    cfg=cfg,
                ),
            )
            if not doc_path:
                _end_span(md_span, status="failed", error_code="XHS_MD_FAILED", error_message="文档生成失败", task_id=task_id)
                tracker.fail("generate_md", "文档生成失败")
                return
            _end_span(md_span, status="completed", task_id=task_id, output_payload={"doc_path": doc_path})
            tracker.complete("generate_md", {"doc_path": doc_path, "doc_filename": output_basename(doc_path)}, persist_payload={"doc_path": doc_path, "title": title})
            _tm.update_task(
                task_id,
                doc_filename=output_basename(doc_path),
                doc_path=doc_path,
                progress=90,
            )
        else:
            tracker.log_skip("generate_md")
            ck = tracker.ctx_get("generate_md")
            doc_path = (ck.get("doc_path") or "").strip()
            if not doc_path:
                tracker.fail("generate_md", "断点缺少 MD 路径，无法恢复")
                return

        # ── MD 完成即任务完成；飞书/HTML 后台继续 ──
        html_span = _begin_span(task_id, session_id, "llm_call", "HTML长页生成", {"doc_path": doc_path})
        complete_task_after_md(
            task_id,
            doc_path=doc_path,
            tracker=tracker,
            link=link,
            platform="小红书",
            title=title,
            html_step_id=html_span.get("step_id") or "",
            url_hash=task.get("url_hash") or "",
        )
        if tracker.should_run("feishu_upload"):
            start_feishu_upload_async(
                doc_path,
                task_id,
                link=link,
                user_prompt=user_prompt,
                cfg=cfg,
                log_cb=lambda msg, lvl="INFO": _log(task_id, msg, lvl),
                pipeline_route="xiaohongshu_graphic",
            )
        else:
            tracker.log_skip("feishu_upload")
        _span_patch_task(task_id, open_layer={"current_assessment": "流程完成", "decision": "stop"})

    except Exception as e:
        import traceback

        _log(task_id, f"处理异常: {e}", "ERROR")
        _log(task_id, traceback.format_exc(), "ERROR")
        try:
            ops_monitor_task(link=(task or {}).get("link", ""), task_id=task_id, status="failed", logs=[], error_info=str(e))
        except Exception:
            pass
        _tm.update_task(task_id, status="failed", error=str(e), pipeline_route="xiaohongshu_graphic")
        mark_failure_from_task(task_id, str(e), route="xiaohongshu_graphic")
