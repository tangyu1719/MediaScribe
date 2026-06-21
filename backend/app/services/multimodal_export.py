"""多模态文档解析后落盘 MD；可选 TXT 与摘要 Agent 沉淀（含结构化元数据）。"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _log_mm(action: str, *, obj: str, stage: str, **kw: Any) -> None:
    parts = "; ".join(f"{k}={v}" for k, v in kw.items() if v is not None and v != "")
    logger.info(
        "[多模态文档-导出|multimodal_export.export_multimodal_document|%s|硬编执行|%s] %s%s",
        obj,
        stage,
        action,
        f"; {parts}" if parts else "",
    )


def _build_export_basename(doc_name: str, *, naming_rule: str, content_type: str = "文档") -> str:
    from .file_naming import apply_naming_template, sanitize_filename_part

    safe = sanitize_filename_part(doc_name)
    date = time.strftime("%m-%d")
    rule = (naming_rule or "").strip()
    if rule and "{doc_title}" in rule:
        basename = apply_naming_template(
            rule,
            doc_title=safe,
            content_type=content_type,
            date=date,
            serial="",
        ).strip()
        if basename and not basename.lower().endswith(".md"):
            basename += ".md"
        return basename or f"{safe}_{content_type}分析.md"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe}_{ts}.md"


def _plain_md_from_text(text: str, *, title: str, source_name: str, pipeline: str) -> str:
    from .file_naming import sanitize_filename_part

    h1 = sanitize_filename_part(title).replace("_", " ") or source_name
    return (
        f"# {h1}\n\n"
        f"> 来源：{source_name}\n"
        f"> 解析链路：{pipeline or 'multimodal'}\n"
        f"> 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{text.strip()}\n"
    )


def _render_summarized_md(
    *,
    cfg: Dict[str, Any],
    text: str,
    link: str,
    source_name: str,
    pipeline: str,
    job_id: str,
) -> Dict[str, Any]:
    from .document_consolidation import extract_title_from_summary, run_document_consolidation
    from .file_naming import render_output_template, resolve_effective_doc_title
    from .link_meta_extract import format_meta_json_block, get_meta_extract_config
    from .pipeline_comments import format_comments_file_link, render_comments_section

    def _cb(msg: str, lvl: str = "INFO") -> None:
        _log_mm(str(msg)[:500], obj=source_name, stage="摘要沉淀", level=lvl)

    consolidation = run_document_consolidation(
        text=text,
        llm_cfg={
            **cfg,
            "_task_id": job_id,
            "_log_chain": "多模态文档-摘要沉淀",
            "_task_note": "",
            "_task_keywords": "",
        },
        stage_label="多模态文档沉淀",
        log_cb=_cb,
    )
    ai_summary = (consolidation.get("ai_summary") or "").strip()
    article_text = (consolidation.get("article") or text).strip()
    extracted_metadata = consolidation.get("extracted_metadata") or {}

    doc_title = source_name
    try:
        doc_title = extract_title_from_summary(ai_summary, link) or source_name
    except Exception:
        pass

    meta_cfg = get_meta_extract_config(cfg)
    meta_block = format_meta_json_block(
        extracted_metadata,
        fields=meta_cfg.get("fields") or [],
    )
    comments_section = render_comments_section(
        cfg.get("comments_section_template") or "",
        comments_analysis="",
        comments_file_path="",
    )
    md_content = render_output_template(
        (cfg.get("output_template") or "").strip(),
        platform="多模态",
        link=link,
        article=article_text,
        summary=ai_summary or article_text,
        content_type="文档",
        transcribe_source=pipeline or "multimodal",
        link_title=source_name,
        doc_title=doc_title,
        comments_section=comments_section,
        comments_analysis="",
        comments_file_link=format_comments_file_link(""),
        meta_json=meta_block,
        task_note="",
        task_keywords="",
    )
    if not md_content.strip():
        h1 = resolve_effective_doc_title(
            doc_title=doc_title,
            link_title=source_name,
            platform="多模态",
            content_type="文档",
            summary=ai_summary,
        )
        md_content = f"# {h1}\n\n## AI分析摘要\n{ai_summary}\n\n## 正文\n{article_text}\n"

    txt_body = ai_summary
    if article_text and article_text != ai_summary:
        txt_body = (txt_body + "\n\n" + article_text).strip() if txt_body else article_text

    return {
        "md_content": md_content,
        "txt_content": txt_body,
        "doc_title": doc_title,
        "ai_summary": ai_summary,
        "article": article_text,
        "extracted_metadata": extracted_metadata,
        "summarize_ok": bool(ai_summary or article_text),
    }


def export_multimodal_document(
    file_path: str,
    *,
    export_txt: bool = False,
    summarize: bool = False,
    **analyze_kwargs: Any,
) -> Dict[str, Any]:
    """解析多模态文件并写入 output/mm_exports 下的 MD（可选 TXT / 摘要沉淀）。"""
    from .config import load_pipeline_config
    from .document import analyze_document
    from .task_manager import get_output_dir

    src = Path(file_path).resolve()
    if not src.is_file():
        return {
            "ok": False,
            "error": "文件不存在",
            "file_path": str(src),
            "md_path": "",
            "txt_path": "",
            "summarized": False,
            "export_txt": export_txt,
        }

    job_id = f"mm_{uuid.uuid4().hex[:12]}"
    t0 = time.time()
    _log_mm("开始解析", obj=src.name, stage="解析", summarize=summarize, export_txt=export_txt)

    base = analyze_document(str(src), job_id=job_id, **analyze_kwargs)
    text = str(base.get("text") or "").strip()
    if not base.get("ok") or not text:
        return {
            **base,
            "md_path": "",
            "txt_path": "",
            "summarized": False,
            "export_txt": export_txt,
            "processing_time": time.time() - t0,
        }

    cfg = load_pipeline_config()
    link = f"file://{src.as_posix()}"
    pipeline = str(base.get("pipeline") or "multimodal")
    stem = src.stem
    doc_title = stem
    extracted_metadata: Dict[str, Any] = {}
    summarized = False
    summarize_error = ""
    md_content = ""
    txt_content = ""

    if summarize:
        summarized = True
        try:
            sm = _render_summarized_md(
                cfg=cfg,
                text=text,
                link=link,
                source_name=stem,
                pipeline=pipeline,
                job_id=job_id,
            )
            md_content = sm["md_content"]
            txt_content = sm["txt_content"] if export_txt else ""
            doc_title = sm["doc_title"]
            extracted_metadata = sm.get("extracted_metadata") or {}
            if not sm.get("summarize_ok"):
                summarize_error = "摘要沉淀未产出有效正文"
        except Exception as ex:
            summarize_error = str(ex)[:500]
            _log_mm(
                "摘要沉淀失败，降级为原文 MD",
                obj=src.name,
                stage="摘要沉淀",
                ok=False,
                error_message=summarize_error,
            )
            summarized = False

    if not md_content:
        norm_md = str(base.get("normalized_md_path") or "").strip()
        if norm_md and Path(norm_md).is_file():
            md_content = Path(norm_md).read_text(encoding="utf-8", errors="ignore")
        elif src.suffix.lower() in {".md", ".markdown"}:
            md_content = text
        else:
            md_content = _plain_md_from_text(
                text,
                title=stem,
                source_name=src.name,
                pipeline=pipeline,
            )
        if export_txt:
            txt_content = text

    out_root = get_output_dir() / "mm_exports"
    out_root.mkdir(parents=True, exist_ok=True)
    naming_rule = (cfg.get("file_naming_rule") or "").strip()
    basename = _build_export_basename(
        doc_title if summarize and not summarize_error else stem,
        naming_rule=naming_rule if summarize and not summarize_error else "",
        content_type="文档",
    )
    md_path = (out_root / basename).resolve()
    md_path.write_text(md_content, encoding="utf-8")

    txt_path = ""
    if export_txt and txt_content.strip():
        txt_path = str(md_path.with_suffix(".txt").resolve())
        Path(txt_path).write_text(txt_content, encoding="utf-8")

    elapsed = time.time() - t0
    _log_mm(
        "导出完成",
        obj=src.name,
        stage="落盘",
        ok=True,
        md_path=str(md_path),
        txt_path=txt_path or "",
        summarized=summarized and not summarize_error,
        elapsed_sec=round(elapsed, 2),
    )

    result = {
        **base,
        "ok": True,
        "md_path": str(md_path),
        "md_basename": md_path.name,
        "txt_path": txt_path,
        "summarized": summarized and not summarize_error,
        "summarize_requested": summarize,
        "summarize_error": summarize_error,
        "export_txt": export_txt,
        "doc_title": doc_title,
        "extracted_metadata": extracted_metadata,
        "processing_time": elapsed,
        "export_dir": str(out_root),
    }
    return result
