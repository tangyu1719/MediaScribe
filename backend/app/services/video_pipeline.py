"""视频处理流水线 —— 导入 src/agent/video_downloader.py 已有函数"""
from __future__ import annotations
import asyncio
import importlib
import inspect
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from .config import load_pipeline_config, resolve_agent_dir

_HERE = Path(__file__).resolve()
_BACKEND_DIR = None
for _p in _HERE.parents:
    if (_BACKEND_DIR is None) and (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _BACKEND_DIR = (_p / "backend").resolve()
if _BACKEND_DIR is None:
    _BACKEND_DIR = _HERE.parents[3] / "backend"

_AGENT_DIR = resolve_agent_dir()
_agent_path = str(_AGENT_DIR)
if _agent_path not in sys.path:
    sys.path.insert(0, _agent_path)

# uvicorn --reload 不监视 src/agent；每次转写前 reload，避免进程内缓存旧版 speech_to_text
import video_downloader as _video_downloader  # noqa: E402

download_video = _video_downloader.download_video
VIDEO_DIR = _video_downloader.VIDEO_DIR
_log = logging.getLogger(__name__)


def _reload_speech_to_text():
    """重新加载 video_downloader，返回最新的 speech_to_text（含 strict 参数检测）。"""
    mod = importlib.reload(_video_downloader)
    # reload 会清空 video_downloader._whisper_pool，必须在 reload 之后重新注册实例池
    try:
        from .whisper_pool import register_whisper_pool_with_downloader

        register_whisper_pool_with_downloader(mod)
    except Exception as ex:
        _log.warning(
            "[链接沉淀文档-视频转写|video_pipeline._reload_speech_to_text|whisper_pool|硬编执行|注册] "
            "reload 后注册失败; error=%s",
            ex,
        )
    fn = mod.speech_to_text
    has_strict = "strict" in inspect.signature(fn).parameters
    return fn, has_strict, str(getattr(mod, "__file__", ""))


def invoke_speech_to_text(
    video_path: str,
    *,
    log_callback=None,
    progress_callback=None,
    llm_config=None,
    user_prompt: str = "",
    strict: bool = True,
) -> Optional[Dict[str, Any]]:
    """调用 Whisper 转写；兼容旧版 speech_to_text（无 strict 时用 transcribe_quality 兜底）。"""
    fn, has_strict, mod_file = _reload_speech_to_text()
    kwargs: Dict[str, Any] = {
        "log_callback": log_callback,
        "progress_callback": progress_callback,
        "llm_config": llm_config,
        "user_prompt": user_prompt,
    }
    if has_strict:
        kwargs["strict"] = strict
    elif strict:
        _log.warning(
            "[链接沉淀文档-视频转写|video_pipeline.invoke_speech_to_text|speech_to_text|硬编执行|兼容] "
            "旧版 speech_to_text 无 strict; module=%s; 将用 transcribe_quality 门禁",
            mod_file,
        )
    try:
        result = fn(video_path, **kwargs)
    except TypeError as exc:
        if "strict" in str(exc) and "strict" in kwargs:
            _log.warning(
                "[链接沉淀文档-视频转写|video_pipeline.invoke_speech_to_text|speech_to_text|硬编执行|重试] "
                "strict 参数不被接受，降级重试; module=%s; error=%s",
                mod_file,
                exc,
            )
            kwargs.pop("strict", None)
            result = fn(video_path, **kwargs)
            has_strict = False
        else:
            raise
    if strict and not has_strict and result is not None:
        from .transcribe_quality import assess_transcript

        raw = (result.get("full_text") or result.get("transcript") or "").strip()
        assessment = assess_transcript(
            raw,
            transcribe_source=str(result.get("transcribe_source") or ""),
            transcript_meta=result if isinstance(result, dict) else None,
        )
        if not assessment.ok:
            return {
                "ok": False,
                "error_code": assessment.error_code,
                "error_message": assessment.error_message,
            }
    return result


# 模块加载时探测一次（仅日志，不阻断启动——避免 uvicorn 子进程路径不一致）
try:
    _st_fn, _st_strict, _st_file = _reload_speech_to_text()
    speech_to_text = _st_fn
    _log.info(
        "[链接沉淀文档-视频转写|video_pipeline|video_downloader|硬编执行|加载] "
        "module=%s; strict_ok=%s",
        _st_file,
        _st_strict,
    )
except Exception as _load_ex:
    speech_to_text = _video_downloader.speech_to_text
    _log.warning(
        "[链接沉淀文档-视频转写|video_pipeline|video_downloader|硬编执行|加载] "
        "reload_failed; error=%s",
        _load_ex,
    )
from .link_hash import normalize_link_for_hash, url_hash as link_url_hash
from .task_manager import get_task, add_log, update_task, get_output_dir
from .history_manager import add_or_update_task_in_history, sync_html_artifact_for_task
from .document_consolidation import run_document_consolidation, extract_title_from_summary, clean_title
from .ops import ops_monitor_task
from .file_naming import render_output_template, build_output_md_path
from .transcribe_quality import assess_transcript, assess_video_file, sanitize_transcript_for_pipeline
from .pipeline_logging import pipeline_log
from .pipeline_finalize import complete_task_after_md, mark_pipeline_running
from .pipeline_stages import PipelineStageTracker, mark_failure_from_task, remap_resume_stage
from .pipeline_checkpoint import clear_pipeline_cache

from .pipeline_executor import (
    get_blocking_executor as _io_executor,
    get_llm_executor as _llm_executor,
    get_background_executor as _bg_executor,
)


def _make_log_cb(task_id: str):
    def cb(msg: str, level: str = "INFO"):
        add_log(task_id, msg, level)
    return cb


def _make_progress_cb(task_id: str):
    def cb(progress: int, msg: str):
        update_task(task_id, progress=progress, stage=msg)
        # 不将进度更新写入日志，避免日志刷屏
        # 只有关键阶段才记录日志（由调用方控制）
    return cb


def _fail_task_transcribe(task_id: str, *, error_code: str, error_message: str, link: str = "", **extra):
    """转写/下载失败：可观测错误码 + 任务 failed，不进入 LLM 沉淀。"""
    payload = {"error_code": error_code, "link": (link or "")[:120], **extra}
    detail = "; ".join(f"{k}={v}" for k, v in payload.items())
    pipeline_log(
        task_id,
        "链接沉淀文档-视频转写",
        "video_pipeline.process_video_pipeline",
        link[:80] if link else task_id,
        "硬编执行",
        "转写门禁拒绝",
        "ERROR",
        error_message=error_message[:300],
        **{k: str(v)[:200] for k, v in extra.items()},
    )
    add_log(task_id, f"[转写失败] {error_code}: {error_message}; {detail}", "ERROR")
    update_task(
        task_id,
        status="failed",
        error=f"{error_code}: {error_message}",
        stage="转写失败",
        transcribe_error_code=error_code,
        transcribe_degraded=bool(extra.get("transcribe_degraded")),
    )
    mark_failure_from_task(task_id, f"{error_code}: {error_message}", route="video", stage_id="transcribe")


def generate_document(result_data: Dict, original_link: str, platform: str, task_id: str) -> str:
    """生成 Markdown —— 纯格式化，不涉及业务逻辑"""
    return generate_document_with_comments(result_data, original_link, platform, task_id)


def _save_comments_to_file(comments_result, original_link: str, platform: str, task_id: str) -> str:
    """保存评论到独立文件，返回文件路径"""
    from .comment_scraper import CommentResult
    if not isinstance(comments_result, CommentResult):
        return ""

    norm = normalize_link_for_hash(original_link)
    uh = link_url_hash(original_link)
    ts = int(time.time())
    comments_path = str(get_output_dir() / f"{platform}_评论_{uh}_{ts}.md")

    lines = [f"# {platform} 评论区内容\n\n"]
    lines.append(f"- 原始链接: {original_link}\n")
    lines.append(f"- 抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- 总评论数: {comments_result.fetched_count}\n\n")

    for i, comment in enumerate(comments_result.comments, 1):
        lines.append(f"## 评论 {i}\n\n")
        lines.append(f"**作者**: {comment.author}  ")
        lines.append(f"**时间**: {comment.time_str}  ")
        lines.append(f"**点赞**: {comment.likes}  ")
        if comment.location:
            lines.append(f"**地点**: {comment.location}  ")
        lines.append(f"\n{comment.text}\n\n")

        if comment.replies:
            lines.append("**回复**: \n\n")
            for reply in comment.replies:
                lines.append(f"- **{reply.author}** 回复 **{reply.reply_to}**: {reply.text}\n")
            lines.append("\n")

    lines.append("---\n*评论内容由自动抓取工具生成*\n")

    with open(comments_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))

    add_log(task_id, f"评论文件保存成功: {comments_path}")
    return comments_path


def generate_document_with_comments(
    result_data: Dict,
    original_link: str,
    platform: str,
    task_id: str,
    user_prompt: str = "",
    comments_data = None,
    comments_file_path: str = "",
    cfg: Optional[Dict] = None,
) -> str:
    """生成 Markdown —— 按 config output_template 模板格式。"""
    cfg = cfg or load_pipeline_config()
    norm = normalize_link_for_hash(original_link)
    uh = link_url_hash(original_link)
    ts = int(time.time())

    summary = result_data.get("ai_summary", result_data.get("summary", ""))
    doc_name = (result_data.get("title") or "").strip()
    if not doc_name:
        try:
            doc_name = extract_title_from_summary(summary, original_link)
        except Exception:
            doc_name = platform

    content_type = result_data.get("content_type", "视频")
    if not content_type or content_type == "unknown":
        content_type = "视频"

    article = result_data.get("article", "")
    if not article:
        segments = result_data.get("segments", [])
        if segments:
            article_lines = []
            for seg in segments:
                start = seg.get("start_time", 0)
                text = seg.get("text", "")
                t = time.strftime("%H:%M:%S", time.gmtime(start))
                article_lines.append(f"[{t}] {text}")
            article = "\n".join(article_lines)
        else:
            article = result_data.get("full_text", result_data.get("transcript", ""))

    transcribe_source = (result_data.get("transcribe_source") or "").strip() or "audio_whisper"
    naming_rule = (cfg.get("file_naming_rule") or "").strip()
    doc_path, _ = build_output_md_path(
        doc_name,
        content_type,
        naming_rule=naming_rule if "{doc_title}" in naming_rule else "",
    )

    from .pipeline_comments import (
        append_comments_section_to_md,
        format_comments_file_link,
        render_comments_section,
    )

    viewpoint = (result_data.get("comments_viewpoint") or "").strip()
    cfp = (comments_file_path or result_data.get("comments_file_path") or "").strip()
    comments_section = render_comments_section(
        cfg.get("comments_section_template") or "",
        comments_analysis=viewpoint,
        comments_file_path=cfp,
    )
    output_tpl = (cfg.get("output_template") or "").strip()
    from .link_meta_extract import format_meta_json_block, get_meta_extract_config

    meta_cfg = get_meta_extract_config(cfg)
    meta_block = format_meta_json_block(
        result_data.get("extracted_metadata") or {},
        fields=meta_cfg.get("fields") or [],
    )
    task_note = str(result_data.get("task_note") or "").strip()
    task_keywords = str(result_data.get("task_keywords") or "").strip()
    md = render_output_template(
        output_tpl,
        platform=platform,
        link=original_link,
        article=article,
        summary=summary,
        content_type=content_type,
        transcribe_source=transcribe_source,
        link_title=(result_data.get("link_title") or doc_name),
        doc_title=doc_name,
        comments_section=comments_section,
        comments_analysis=viewpoint,
        comments_file_link=format_comments_file_link(cfp),
        meta_json=meta_block,
        task_note=task_note,
        task_keywords=task_keywords,
    )
    if not md.strip():
        from .file_naming import resolve_effective_doc_title

        h1 = resolve_effective_doc_title(
            doc_title=doc_name,
            link_title=(result_data.get("link_title") or doc_name),
            platform=platform,
            content_type=content_type,
            summary=summary,
        )
        md = f"""# {h1}

## 分析信息
- 分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 原始链接: {original_link}
- 平台: {platform}
- 类型: {content_type}
- 转写链路: {transcribe_source}

## 原始内容
正文：
{article}

## AI分析摘要
{summary}
"""
    md = append_comments_section_to_md(
        md,
        cfg,
        comments_analysis=viewpoint,
        comments_file_path=cfp,
    )
    md += """
---
*由视频转文字处理工具自动生成*
"""
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(md)

    add_log(task_id, f"文档生成成功: {doc_path}")
    return doc_path


def _persist_task_snapshot(task_id: str) -> None:
    """将内存任务（含 HTML/日志）同步到 history.json。"""
    task = get_task(task_id)
    if not task:
        return
    snap = sync_html_artifact_for_task(dict(task))
    if snap.get("html_path") != task.get("html_path") or snap.get("html_status") != task.get(
        "html_status"
    ):
        update_task(
            task_id,
            html_path=snap.get("html_path"),
            html_status=snap.get("html_status"),
            html_message=snap.get("html_message"),
        )
    add_or_update_task_in_history(snap)


def start_html_generation(
    doc_path: str,
    task_id: str,
    title: str = "",
    platform: str = "",
    link: str = "",
    html_step_id: str = "",
):
    """启动长页 HTML 后台生成 —— 三级降级架构（完全参照 video_gui.py _longpage_worker）

    架构（与原项目完全一致）：
      Tier 1: run_full_longpage_delivery_job → 编排Agent + 图例Agent（双Agent架构）
      Tier 2: run_diagram_longpage_job        → 图例Agent（图表+图例）
      Tier 3: write_longpage_from_pipeline_md  → 简易单次LLM（兜底）
    """
    import os
    from .config import load_pipeline_config
    from .pipeline_logging import enrich_pipeline_llm_cfg, pipeline_log

    cfg = enrich_pipeline_llm_cfg(load_pipeline_config())
    if not cfg.get("longpage_html_enabled", True):
        add_log(task_id, "[HTML] 未启用，跳过"); return

    # 设置 Agent Spec 路径（指向 web 后端的本地副本）
    _agent_spec_dir = (_BACKEND_DIR / "agents" / "summary").resolve()
    if _agent_spec_dir.exists():
        os.environ["AGENT_HTML_LONGPAGE_SPEC"] = str(_agent_spec_dir / "AGENT_HTML_LONGPAGE.md")

    update_task(task_id, html_status="async_pending", html_message="HTML 双Agent架构后台生成中...")

    ep_asm = (cfg.get("longpage_html_assembler_agent") or cfg.get("ai_chat_model") or "").strip()
    if not ep_asm:
        tr = cfg.get("gateway_task_type_route") or {}
        if isinstance(tr, dict):
            ep_asm = (tr.get("code") or tr.get("summary") or "").strip()
    pipeline_log(
        task_id,
        "链接沉淀文档-HTML长页",
        "video_pipeline.start_html_generation",
        Path(doc_path).name,
        "启动",
        "Agent执行",
        "HTML 三级降级后台任务已提交",
        max_bytes=int(cfg.get("longpage_html_max_bytes", 20 * 1024 * 1024)),
        sync_timeout_sec=int(cfg.get("longpage_html_timeout_sec", 60)),
        async_timeout_sec=int(cfg.get("longpage_html_async_timeout_sec", 600)),
        multiphase=bool(cfg.get("longpage_multiphase_enabled", False)),
        legend_agent_enabled=bool(cfg.get("longpage_legend_agent_enabled", True)),
        html_step_id=html_step_id or "",
    )
    add_log(task_id, "[HTML] 启动三级降级架构 HTML 生成...")
    add_log(task_id, f"[HTML] Tier1: delivery_pipeline / Tier2: diagram_pipeline / Tier3: simple_fallback")
    add_log(task_id, f"[HTML] 超时: sync={cfg.get('longpage_html_timeout_sec', 60)}s async={cfg.get('longpage_html_async_timeout_sec', 600)}s")

    def _run_html():
        try:
            if str(_AGENT_DIR) not in sys.path: sys.path.insert(0, str(_AGENT_DIR))

            max_b = int(cfg.get("longpage_html_max_bytes", 20*1024*1024))
            to_sec = int(cfg.get("longpage_html_timeout_sec", 60))
            async_to = int(cfg.get("longpage_html_async_timeout_sec", 600))
            use_async_diag = cfg.get("longpage_html_async_diagram_pipeline", True)
            multiphase = cfg.get("longpage_multiphase_enabled", False)
            meta_ov = {
                "doc_title": title or "文档",
                "platform": platform,
                "link": link,
                "longpage_mechanical_diagram_legend": bool(
                    cfg.get("longpage_fallback_diagram_legend_enabled", True)
                ),
            }

            def _lp_log(msg, level="INFO"):
                add_log(task_id, f"[HTML] {msg}")

            cfg_overlay = dict(cfg)
            cfg_overlay["longpage_pipeline_log_fn"] = _lp_log
            cfg_overlay["_diagram_pipeline_log_fn"] = _lp_log

            # ─── Tier 1: 完整双Agent交付流水线（编排 + 图例） ───
            try:
                from longpage_delivery_pipeline import run_full_longpage_delivery_job
                add_log(task_id, f"[HTML] Tier1: 启动完整双Agent交付流水线 (multiphase={multiphase})...")
                if multiphase:
                    add_log(task_id, "[HTML] 多阶段编排模式已启用")
                res, bundle = run_full_longpage_delivery_job(
                    cfg_overlay, str(doc_path),
                    meta_override=meta_ov, max_bytes=max_b,
                    timeout_sec=float(async_to), embed_agent_spec=False,
                )
                _write_bundle(doc_path, bundle)
                if bundle.get("fallback_mechanical"):
                    add_log(
                        task_id,
                        f"[HTML] Tier1 编排失败已机械兜底（含图例补全={cfg_overlay.get('longpage_fallback_diagram_legend_enabled', True)}）"
                        f" errors={bundle.get('errors')}",
                        "WARNING",
                    )
                _apply_result(res, task_id, html_step_id=html_step_id)
                return
            except ImportError:
                add_log(task_id, "[HTML] Tier1 delivery_pipeline 不可用，降级到 Tier2")
            except Exception as e:
                add_log(task_id, f"[HTML] Tier1 异常: {e}，降级到 Tier2", "WARNING")

            # ─── Tier 2: 图例管线（图表 + 图例 Agent） ───
            try:
                if use_async_diag:
                    from longpage_diagram_pipeline import run_diagram_longpage_job
                    add_log(task_id, "[HTML] Tier2: 启动图例管线 (planner + parallel draw + legend)...")
                    res, bundle = run_diagram_longpage_job(
                        cfg_overlay, str(doc_path),
                        meta_override=meta_ov, max_bytes=max_b,
                        timeout_sec=float(async_to), embed_agent_spec=False,
                    )
                    _write_bundle(doc_path, bundle)
                    _apply_result(res, task_id, html_step_id=html_step_id)
                    return
            except ImportError:
                add_log(task_id, "[HTML] Tier2 diagram_pipeline 不可用，降级到 Tier3")
            except Exception as e:
                add_log(task_id, f"[HTML] Tier2 异常: {e}，降级到 Tier3", "WARNING")

            # ─── Tier 3: 简易单次 LLM 兜底 ───
            add_log(task_id, "[HTML] Tier3: 使用简易单次 LLM 兜底方案...")
            from longpage_html import write_longpage_from_pipeline_md
            res = write_longpage_from_pipeline_md(
                str(doc_path), max_bytes=max_b, timeout_sec=to_sec,
                meta_override=meta_ov, gateway_config=dict(cfg_overlay),
            )
            _apply_result(res, task_id, html_step_id=html_step_id)

        except ImportError:
            update_task(task_id, html_status="skipped", html_message="longpage_html 模块不可用")
            add_log(task_id, "[HTML] 全部 Tiers 不可用，跳过")
            _finish_html_span(html_step_id, task_id, "skipped", "", "longpage 模块不可用")
            _persist_task_snapshot(task_id)
        except Exception as e:
            update_task(task_id, html_status="failed", html_message=str(e)[:100])
            add_log(task_id, f"[HTML] 异常: {e}", "ERROR")
            import traceback
            add_log(task_id, traceback.format_exc(), "ERROR")
            _finish_html_span(html_step_id, task_id, "failed", "", str(e))
            _persist_task_snapshot(task_id)

    _bg_executor().submit(_run_html)


def _finish_html_span(html_step_id: str, task_id: str, status: str, path: str, message: str = ""):
    if not html_step_id:
        return
    try:
        from .span_audit import finish_step as _span_finish
        from .pipeline_logging import pipeline_log, log_span_event

        pipeline_log(
            task_id,
            "链接沉淀文档-HTML长页",
            "video_pipeline._finish_html_span",
            Path(path).name if path else "html",
            "结束",
            "Agent执行",
            f"HTML 后台生成{status}",
            html_path=path,
            message=message[:200],
        )
        _span_finish(
            html_step_id,
            status="completed" if status in ("completed", "ok") else ("failed" if status == "failed" else "completed"),
            output_payload={"html_path": path, "html_status": status, "html_message": message},
            error_message=message if status == "failed" else "",
        )
        log_span_event(
            task_id, "链接沉淀文档-HTML长页", "video_pipeline", "HTML长页生成",
            step_id=html_step_id, step_name="HTML长页生成", step_type="llm_call",
            event="结束", status=status, html_path=path,
        )
    except Exception:
        pass


def _apply_result(res, task_id: str, html_step_id: str = ""):
    """应用 HTML 生成结果（参照原项目 _apply_longpage_result）"""
    from longpage_html import LongpageBuildResult
    if isinstance(res, LongpageBuildResult):
        update_task(task_id,
            html_path=res.path or "",
            html_status=res.status or "completed",
            html_message=res.message or "OK")
        if res.path:
            add_log(task_id, f"[HTML] 生成成功: {res.path} ({res.bytes_written or 0} bytes)")
        else:
            add_log(task_id, f"[HTML] 生成状态: {res.status} - {res.message}")
        _finish_html_span(html_step_id, task_id, res.status or "completed", res.path or "", res.message or "")
        _persist_task_snapshot(task_id)
    elif res and hasattr(res, 'path'):
        update_task(task_id, html_path=res.path or "",
            html_status=getattr(res, 'status', 'completed'),
            html_message=getattr(res, 'message', 'OK'))
        add_log(task_id, f"[HTML] 生成成功: {res.path}")
        _finish_html_span(html_step_id, task_id, getattr(res, 'status', 'completed'), res.path or "", getattr(res, 'message', ''))
        _persist_task_snapshot(task_id)
    else:
        update_task(task_id, html_status="failed", html_message="结果格式异常")
        add_log(task_id, "[HTML] 结果格式异常", "WARNING")
        _finish_html_span(html_step_id, task_id, "failed", "", "结果格式异常")
        _persist_task_snapshot(task_id)


def _write_bundle(doc_path: str, bundle: dict):
    """写入 delivery/diagram bundle JSON（用于调试和回溯）"""
    try:
        import json as _json
        stem = Path(doc_path).stem
        out = Path(doc_path).parent / f"{stem}.delivery_bundle.json"
        out.write_text(_json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _is_douyin_graphic_url(link: str) -> bool:
    """URL 路径可直接判定为抖音图文（article / note）。"""
    low = (link or "").lower()
    if not ("douyin.com" in low or "iesdouyin" in low):
        return False
    return "/article/" in low or "/note/" in low or "modal_id=" in low


def _is_douyin_article_url(link: str) -> bool:
    """兼容旧名：抖音图文 URL 快判（article + note）。"""
    return _is_douyin_graphic_url(link)


def _probe_douyin_graphic_sync(link: str, task_id: str = "") -> tuple:
    """
    检测抖音链接是否为图文（含 v.douyin.com 短链重定向到 /note/）。
    返回 (is_graphic, content_type_hint)。
    """
    import time as _time

    if _is_douyin_graphic_url(link):
        return True, "douyin_image"

    t0 = _time.perf_counter()
    if task_id:
        add_log(task_id, "[路由] 抖音类型检测开始（LinkAnalyzer._detect_douyin_type）…")
    try:
        from link_analyzer import LinkAnalyzer

        analyzer = LinkAnalyzer()
        dtype = analyzer._detect_douyin_type(link)
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        is_graphic = dtype == "douyin_image"
        if task_id:
            add_log(
                task_id,
                f"[路由] 抖音类型检测: {dtype}; is_graphic={is_graphic}; elapsed_ms={elapsed_ms}",
            )
        return is_graphic, dtype
    except Exception as e:
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        if task_id:
            add_log(
                task_id,
                f"抖音类型检测失败: {e}; elapsed_ms={elapsed_ms}",
                "WARNING",
            )
        else:
            add_log("system", f"抖音类型检测失败: {e}", "WARNING")
    return False, "video"


def _is_xiaohongshu_url(link: str) -> bool:
    return "xiaohongshu.com" in link


def _probe_xiaohongshu_graphic_sync(link: str, task_id: str = "") -> tuple:
    """
    检测小红书链接是否为图文；返回 (is_graphic, analyzer_result)。
    analyzer_result 供图文链路复用，避免二次 analyze_link（单次常 20–40s）。
    """
    import time as _time

    t0 = _time.perf_counter()
    if task_id:
        add_log(task_id, "[路由] 小红书类型检测开始（LinkAnalyzer.analyze_link）…")
    try:
        from link_analyzer import LinkAnalyzer

        analyzer = LinkAnalyzer()
        result = analyzer.analyze_link(link, include_image_ocr=False)
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        if result and not result.get("error"):
            content_type = result.get("type", "")
            is_graphic = content_type == "xiaohongshu"
            if task_id:
                timing = (result or {}).get("_timing") or {}
                add_log(
                    task_id,
                    f"[路由] 小红书类型检测: {content_type}; is_graphic={is_graphic}; "
                    f"elapsed_ms={elapsed_ms}; timing={timing.get('phases_ms', {})}",
                )
            return is_graphic, (result if is_graphic else None)
        if task_id:
            add_log(
                task_id,
                f"[路由] 小红书类型检测无有效结果; elapsed_ms={elapsed_ms}",
                "WARNING",
            )
    except Exception as e:
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        if task_id:
            add_log(
                task_id,
                f"小红书类型检测失败: {e}; elapsed_ms={elapsed_ms}",
                "WARNING",
            )
        else:
            add_log("system", f"小红书类型检测失败: {e}", "WARNING")
    return False, None


def _is_xiaohongshu_graphic_sync(link: str, task_id: str = "") -> bool:
    """兼容旧调用：仅返回是否图文。"""
    is_graphic, _ = _probe_xiaohongshu_graphic_sync(link, task_id)
    return is_graphic


async def process_video_pipeline(task_id: str):
    """完整流水线 —— 含图文路由，评论读取，User Prompt 传递"""
    task = get_task(task_id)
    if not task:
        return

    platform = task["platform"]
    link = task["link"]
    user_prompt = task.get("user_prompt", "")
    cfg = load_pipeline_config()
    naming_rule = (cfg.get("file_naming_rule") or "").strip()
    comments_config = task.get("comments", {"enabled": False, "count": 10, "sort": "hot"})
    log = _make_log_cb(task_id)
    progress = _make_progress_cb(task_id)
    loop = asyncio.get_running_loop()

    if not task.get("link_title"):
        import time as _time

        from .file_naming import resolve_link_title, preview_from_analyzer_result

        add_log(task_id, "[首层标题] 开始从链接解析展示标题…")
        t_title = _time.perf_counter()
        lt = await loop.run_in_executor(
            _io_executor(),
            lambda: resolve_link_title(
                link,
                platform=platform,
                log_cb=lambda m: add_log(task_id, m),
            ),
        )
        add_log(
            task_id,
            f"[首层标题] 解析结束; ok={bool(lt)}; elapsed_ms={int((_time.perf_counter() - t_title) * 1000)}",
        )
        if lt:
            update_task(task_id, link_title=lt)
            add_log(task_id, f"首层标题（链接）: {lt}")

    # 存储评论数据
    comments_data = None
    comments_file_path = None
    tracker = PipelineStageTracker(
        task_id,
        route="video",
        existing_stages=task.get("pipeline_stages"),
        resume_from=task.get("resume_from") or None,
        resume_context=task.get("resume_context"),
    )
    mark_pipeline_running(task_id)

    try:
        # ── 步骤0：读取评论（如果启用）──
        if comments_config.get("enabled"):
            if tracker.should_run("comments"):
                tracker.start("comments")
                update_task(task_id, stage="步骤0: 读取评论", progress=5)
                add_log(task_id, "开始读取评论...")
                try:
                    from .comment_scraper import scrape_comments
                    max_count = comments_config.get("count", 10)
                    sort_by = comments_config.get("sort", "hot")
                    if max_count == 0:
                        max_count = None
                        add_log(task_id, "评论读取: 全量模式")
                    else:
                        add_log(task_id, f"评论读取: 最多 {max_count} 条")

                    result = await loop.run_in_executor(
                        _io_executor(),
                        lambda: scrape_comments(link, platform=platform, max_count=max_count, sort_by=sort_by)
                    )
                    if result and not result.error:
                        comments_data = result
                        add_log(task_id, f"评论读取完成: {result.fetched_count} 条评论")
                        comments_file_path = _save_comments_to_file(result, link, platform, task_id)
                        tracker.complete("comments", {"comments_file_path": comments_file_path, "fetched_count": result.fetched_count})
                    else:
                        add_log(task_id, f"评论读取失败或为空: {result.error if result else '未知错误'}", "WARNING")
                        tracker.complete("comments", {"comments_file_path": "", "fetched_count": 0})
                except Exception as e:
                    add_log(task_id, f"评论读取异常: {e}", "WARNING")
                    tracker.complete("comments", {"comments_file_path": "", "fetched_count": 0})
            else:
                ck = tracker.ctx_get("comments")
                comments_file_path = ck.get("comments_file_path") or None
                if comments_file_path:
                    add_log(task_id, f"断点恢复：复用评论文件 {comments_file_path}")

        # ── 路由1：抖音图文（article / note）→ 图文分析链路 ──
        _link_low = link.lower()
        if "douyin.com" in _link_low or "iesdouyin" in _link_low:
            is_douyin_graphic = _is_douyin_graphic_url(link)
            if not is_douyin_graphic:
                is_douyin_graphic, _dtype_hint = await loop.run_in_executor(
                    _io_executor(),
                    lambda: _probe_douyin_graphic_sync(link, task_id),
                )
            if is_douyin_graphic:
                add_log(task_id, "检测到抖音图文，路由到图文分析链路...")
                prev_route = (task.get("pipeline_route") or "video").strip() or "video"
                rf = (task.get("resume_from") or task.get("failed_stage") or "").strip()
                mapped_rf = remap_resume_stage(rf, prev_route, "douyin_graphic") if rf else ""
                patch: Dict[str, Any] = {
                    "pipeline_route": "douyin_graphic",
                    "stage": "抖音图文分析",
                    "status": "running",
                    "content_type": "图文",
                }
                if mapped_rf and mapped_rf != rf:
                    patch["resume_from"] = mapped_rf
                    if (task.get("failed_stage") or "") == rf:
                        patch["failed_stage"] = mapped_rf
                    from .pipeline_stages import stage_label

                    add_log(
                        task_id,
                        f"[路由] 断点阶段 {rf}→{mapped_rf}（{stage_label(prev_route, rf)}→{stage_label('douyin_graphic', mapped_rf)}）",
                    )
                update_task(task_id, **patch)
                from .douyin_article import process_douyin_article_pipeline
                await process_douyin_article_pipeline(task_id, user_prompt=user_prompt, comments_data=comments_data)
                return

        # ── 路由2：小红书图文 → 小红书图文分析链路 ──
        if _is_xiaohongshu_url(link):
            is_graphic, analyzer_prefetch = await loop.run_in_executor(
                _io_executor(),
                lambda: _probe_xiaohongshu_graphic_sync(link, task_id),
            )
            if is_graphic:
                add_log(task_id, "检测到小红书图文，路由到图文分析链路（复用路由阶段抓取结果）...")
                prev_route = (task.get("pipeline_route") or "video").strip() or "video"
                rf = (task.get("resume_from") or task.get("failed_stage") or "").strip()
                mapped_rf = remap_resume_stage(rf, prev_route, "xiaohongshu_graphic") if rf else ""
                patch: Dict[str, Any] = {
                    "pipeline_route": "xiaohongshu_graphic",
                    "stage": "小红书图文分析",
                    "status": "running",
                }
                if analyzer_prefetch:
                    patch["link_analyzer_prefetch"] = analyzer_prefetch
                if mapped_rf and mapped_rf != rf:
                    patch["resume_from"] = mapped_rf
                    if (task.get("failed_stage") or "") == rf:
                        patch["failed_stage"] = mapped_rf
                    from .pipeline_stages import stage_label

                    add_log(
                        task_id,
                        f"[路由] 断点阶段 {rf}→{mapped_rf}（{stage_label(prev_route, rf)}→{stage_label('xiaohongshu_graphic', mapped_rf)}）",
                    )
                update_task(task_id, **patch)
                from .xiaohongshu_article import process_xiaohongshu_article_pipeline

                await process_xiaohongshu_article_pipeline(
                    task_id, user_prompt=user_prompt, comments_data=comments_data
                )
                return
            else:
                add_log(task_id, "检测到小红书视频，继续视频处理链路...")

        video_path = None
        if tracker.should_run("download"):
            tracker.start("download")
            update_task(task_id, status="downloading", stage="步骤1: 下载视频", progress=10)
            add_log(task_id, f"开始处理 {platform} 链接: {link}")
            video_path = await loop.run_in_executor(
                _io_executor(), lambda: download_video(link, log_callback=log)
            )
            if not video_path:
                tracker.fail("download", "下载视频失败")
                return
            tracker.complete("download", {"video_path": video_path}, persist_payload={"video_path": video_path})
        else:
            video_path = tracker.ctx_get("download").get("video_path")
            add_log(task_id, f"断点恢复：复用已下载视频 {video_path}")
            if not video_path:
                tracker.fail("download", "断点缺少 video_path，无法恢复")
                return

        ok_v, v_code, v_msg = assess_video_file(video_path)
        if not ok_v:
            tracker.fail("download", v_msg)
            _fail_task_transcribe(
                task_id,
                error_code=v_code,
                error_message=v_msg,
                link=link,
                video_path=video_path,
                video_bytes=os.path.getsize(video_path) if os.path.isfile(video_path) else 0,
            )
            return

        transcript = None
        if tracker.should_run("transcribe"):
            tracker.start("transcribe")
            update_task(task_id, status="transcribing", stage="步骤2: 语音转文字", progress=40)
            llm_cfg = cfg.get("llm_config") or cfg
            transcript = await loop.run_in_executor(
                _io_executor(),
                lambda: invoke_speech_to_text(
                    video_path,
                    log_callback=log,
                    progress_callback=progress,
                    llm_config=llm_cfg if llm_cfg.get("apiKey") else None,
                    user_prompt=user_prompt,
                    strict=True,
                )
            )
            if not transcript or transcript.get("ok") is False:
                code = (transcript or {}).get("error_code") or "transcribe_no_result"
                msg = (transcript or {}).get("error_message") or "语音转文字未返回结果"
                tracker.fail("transcribe", msg)
                _fail_task_transcribe(
                    task_id,
                    error_code=code,
                    error_message=msg,
                    link=link,
                )
                return

            tracker.complete("transcribe", transcript, persist_payload=transcript)
        else:
            transcript = dict(tracker.ctx_get("transcribe"))
            add_log(task_id, "断点恢复：复用转写结果")
            if not (transcript.get("full_text") or transcript.get("transcript")):
                tracker.fail("transcribe", "断点缺少转写文本，无法恢复")
                return

        raw_text = (transcript.get("full_text") or transcript.get("transcript") or "").strip()
        cleaned_text, stripped_tail = sanitize_transcript_for_pipeline(raw_text)
        if stripped_tail > 0 and cleaned_text:
            add_log(
                task_id,
                f"[转写清洗] 已剔除尾部 Whisper 幻听循环（约 {stripped_tail} 字），保留有效正文进入沉淀",
                "WARNING",
            )
            raw_text = cleaned_text
            transcript["full_text"] = cleaned_text
            if transcript.get("transcript"):
                transcript["transcript"] = cleaned_text
        assessment = assess_transcript(
            raw_text,
            transcribe_source=str(transcript.get("transcribe_source") or ""),
            transcript_meta=transcript if isinstance(transcript, dict) else None,
        )
        if not assessment.ok:
            tracker.fail("transcribe", assessment.error_message)
            _fail_task_transcribe(
                task_id,
                error_code=assessment.error_code,
                error_message=assessment.error_message,
                link=link,
                transcribe_degraded=assessment.transcribe_degraded,
                repetition_ratio=assessment.repetition_ratio,
                char_len=assessment.char_len,
                transcribe_source=transcript.get("transcribe_source") or "",
            )
            return

        ai_summary = (transcript.get("ai_summary") or "").strip()
        article_text = (transcript.get("article") or "").strip()
        title = (transcript.get("title") or "").strip()

        if tracker.should_run("ai_analysis"):
            tracker.start("ai_analysis")
            update_task(task_id, status="consolidating", stage="步骤3: 原文整理与摘要", progress=70)

            def _ops_cb(link, error_message, stage, error_type):
                try:
                    ops_monitor_task(
                        link=link,
                        task_id=task_id,
                        status="failed",
                        logs=[],
                        error_info=f"{error_type}: {error_message}",
                    )
                except Exception:
                    pass

            from .pipeline_comments import resolve_comments_text
            from .pipeline_options_util import is_article_only

            comments_text = resolve_comments_text(
                comments_data=comments_data,
                comments_file_path=comments_file_path
                or str((task.get("comments") or {}).get("comments_file_path") or ""),
            )
            _article_only = is_article_only(task)
            task_snap_pre = get_task(task_id) or {}
            consolidation = await loop.run_in_executor(
                _llm_executor(),
                lambda: run_document_consolidation(
                    text=raw_text,
                    llm_cfg={
                        **cfg,
                        "_task_id": task_id,
                        "_log_chain": f"链接沉淀文档-{platform}视频",
                        "_task_note": str(task_snap_pre.get("task_note") or ""),
                        "_task_keywords": str(task_snap_pre.get("task_keywords") or ""),
                    },
                    user_prompt=user_prompt,
                    stage_label=f"{platform}视频沉淀",
                    summary_after_article=True,
                    skip_summary=_article_only,
                    comments_text=comments_text,
                    log_cb=lambda msg, lvl="INFO": add_log(task_id, msg, lvl),
                    ops_cb=_ops_cb,
                ),
            )

            ai_summary = (consolidation.get("ai_summary") or "").strip()
            article_text = (consolidation.get("article") or "").strip()
            extracted_metadata = consolidation.get("extracted_metadata") or {}
            if extracted_metadata:
                update_task(task_id, extracted_metadata=extracted_metadata)
            transcript["comments_viewpoint"] = (consolidation.get("comments_viewpoint") or "").strip()
            if not _article_only and not ai_summary:
                tracker.fail("ai_analysis", "摘要生成失败")
                return

            from .file_naming import resolve_doc_title
            task_snap = get_task(task_id) or {}
            link_title = (task_snap.get("link_title") or "").strip()
            if _article_only:
                title = link_title or (transcript.get("title") or platform)
            else:
                try:
                    title = await loop.run_in_executor(
                        _io_executor(),
                        lambda: resolve_doc_title(
                            ai_summary,
                            link,
                            link_title=link_title,
                            fallback=(transcript.get("title") or platform),
                            log_cb=lambda msg: add_log(task_id, msg),
                            platform=platform,
                            source_text_len=len(raw_text or ""),
                        ),
                    )
                except Exception as title_ex:
                    from .pipeline_output_quality import PipelineOutputQualityError

                    if isinstance(title_ex, PipelineOutputQualityError):
                        err_msg = f"[{title_ex.error_code}] {title_ex.message}"
                        update_task(
                            task_id,
                            error_code=title_ex.error_code,
                            error=err_msg,
                            span_stage_hint=title_ex.span_stage,
                        )
                        tracker.fail("ai_analysis", err_msg)
                        return
                    raise
            update_task(task_id, doc_title=title)
            add_log(task_id, f"二层标题（AI摘要）: {title}")
            tracker.complete("ai_analysis", {"ai_summary": ai_summary, "article": article_text or raw_text, "title": title, "link_title": link_title}, persist_payload={"ai_summary": ai_summary, "article": article_text or raw_text, "title": title, "link_title": link_title})
        else:
            ck = tracker.ctx_get("ai_analysis")
            ai_summary = ck.get("ai_summary") or ai_summary
            article_text = ck.get("article") or article_text or raw_text
            title = ck.get("title") or title
            add_log(task_id, "断点恢复：复用摘要结果")

        transcript["ai_summary"] = ai_summary
        transcript["article"] = article_text or raw_text
        transcript["title"] = title
        transcript["link_title"] = (get_task(task_id) or {}).get("link_title") or transcript.get("link_title") or ""
        transcript["transcribe_source"] = transcript.get("transcribe_source") or "audio_whisper"
        task_snap_md = get_task(task_id) or {}
        transcript["extracted_metadata"] = task_snap_md.get("extracted_metadata") or {}
        transcript["task_note"] = task_snap_md.get("task_note") or ""
        transcript["task_keywords"] = task_snap_md.get("task_keywords") or ""

        doc_path = None
        if tracker.should_run("generate_md"):
            tracker.start("generate_md")
            update_task(task_id, status="generating", stage="步骤4: 生成文档", progress=85)
            doc_path = generate_document_with_comments(
                transcript, link, platform, task_id,
                user_prompt=user_prompt,
                comments_data=comments_data,
                comments_file_path=comments_file_path,
                cfg=cfg,
            )
            if not doc_path:
                tracker.fail("generate_md", "生成文档失败")
                return
            from .file_naming import output_basename
            tracker.complete("generate_md", {"doc_path": doc_path, "doc_filename": output_basename(doc_path)}, persist_payload={"doc_path": doc_path}, sync_history=True)
        else:
            doc_path = tracker.ctx_get("generate_md").get("doc_path")
            add_log(task_id, f"断点恢复：复用文档 {doc_path}")
            if not doc_path:
                tracker.fail("generate_md", "断点缺少 doc_path，无法恢复")
                return

        complete_task_after_md(
            task_id,
            doc_path=doc_path,
            tracker=tracker,
            link=link,
            platform=platform,
            title=(transcript.get("title") or ""),
            url_hash=task.get("url_hash") or "",
        )

    except Exception as e:
        import traceback

        add_log(task_id, f"处理异常: {e}", "ERROR")
        add_log(task_id, traceback.format_exc(), "ERROR")
        fail_route = (get_task(task_id) or {}).get("pipeline_route") or "video"
        update_task(task_id, status="failed", error=str(e))
        mark_failure_from_task(task_id, str(e), route=fail_route)
        # 即使失败也添加到历史记录
        task = get_task(task_id)
        if task:
            add_or_update_task_in_history(task)
