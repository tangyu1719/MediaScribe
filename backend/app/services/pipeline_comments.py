"""链接沉淀 —— 评论文本解析、观点提炼与摘要/MD 拼装。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_DEFAULT_COMMENTS_VIEWPOINT_PROMPT = (
    "你是评论区观点分析师。请阅读「原文/帖子上下文」与「评论区全文」，完成去噪、分层与观点提炼。\n"
    "要求：\n"
    "1. 忽略灌水、广告、与主题无关内容；\n"
    "2. 按评论层次归类每一则有效发言（可多行）：\n"
    "   - **提问**：向作者请教（怎么学、怎么选型、面试考什么等）；\n"
    "   - **围观/附和**：识别身份、感慨、转发类短评；\n"
    "   - **作者回复**：作者解答（含 tradeoff、一线面经、踩坑、职级等）；\n"
    "   - **观点派别**：与原文主题相关的立场/方法分歧（可 2–6 派）。\n"
    "3. 输出 Markdown 表格，表头固定为：\n"
    "| 层次 | 角色/派别 | 观点原句 | 精简解释 | AI分析（正确性/启发/可采纳性） |\n"
    "4. 每行须含：观点原句（尽量引用原话）+ 精简解释 + AI分析（是否正确、有无启发、面试/落地是否值得采纳）；\n"
    "5. 表格后附「总览」：归纳可操作方法、考点、需谨慎的 tradeoff、对读者的行动建议（3–6 条）。\n\n"
    "【原文上下文】\n{article_context}\n\n"
    "【评论区】\n{comments}"
)

DEFAULT_COMMENTS_SECTION_TEMPLATE = """## 【评论区】

{comments_analysis}

{comments_file_link}
"""

# 供前端 IAG / 文档说明：评论区 MD 片段可用占位符
COMMENTS_SECTION_TEMPLATE_VARS = (
    "comments_analysis",
    "comments_file_link",
    "comments_section",
)

_JSON_OUTPUT_RULE_VIEWPOINT = (
    "\n【输出格式-硬性】优先输出 Markdown 表格 + 总览段落；禁止 JSON 包裹。"
)


def resolve_comments_text(
    *,
    comments_data: Any = None,
    comments_text: str = "",
    comments_file_path: str = "",
    max_chars: int = 12000,
) -> str:
    if (comments_text or "").strip():
        return (comments_text or "").strip()[:max_chars]

    if comments_data is not None:
        try:
            from .comment_scraper import CommentResult, format_comments_as_text

            if isinstance(comments_data, CommentResult):
                txt = format_comments_as_text(comments_data).strip()
                return txt[:max_chars] if txt else ""
        except Exception:
            pass

    fp = (comments_file_path or "").strip()
    if fp and Path(fp).is_file():
        try:
            raw = Path(fp).read_text(encoding="utf-8", errors="ignore").strip()
            if raw:
                return raw[:max_chars]
        except Exception:
            pass
    return ""


def compose_summary_input(article_text: str, comments_block: str = "") -> str:
    """整理后正文 + 评论观点块（非原始全量评论），送入摘要 Agent。"""
    article = (article_text or "").strip()
    comments = (comments_block or "").strip()
    if not comments:
        return article
    heading = "评论区观点提炼" if "| 派别 |" in comments or "|派别|" in comments else "评论区"
    block = f"## {heading}\n\n{comments}"
    if article:
        return f"{article}\n\n{block}".strip()
    return block


def format_comments_file_link(comments_file_path: str) -> str:
    fp = (comments_file_path or "").strip()
    if not fp:
        return ""
    name = Path(fp).name
    return f"评论原文已单独保存，请查看: [{name}](./{name})" if name else ""


def build_comments_section_context(
    *,
    comments_analysis: str = "",
    comments_file_path: str = "",
) -> Dict[str, str]:
    """评论区 MD 模板变量（与 output_template 解耦）。"""
    analysis = (comments_analysis or "").strip()
    link_line = format_comments_file_link(comments_file_path)
    return {
        "comments_analysis": analysis,
        "comments_file_link": link_line,
        "comments_section": "",  # 由 render_comments_section 填充
    }


def render_comments_section(
    template: str,
    *,
    comments_analysis: str = "",
    comments_file_path: str = "",
) -> str:
    """
    渲染【评论区】片段模板（嵌在 AI 分析章节之后）。
    占位符：{comments_analysis} {comments_file_link} {comments_section}
    """
    ctx = build_comments_section_context(
        comments_analysis=comments_analysis,
        comments_file_path=comments_file_path,
    )
    if not ctx["comments_analysis"] and not ctx["comments_file_link"]:
        return ""

    tpl = (template or DEFAULT_COMMENTS_SECTION_TEMPLATE).strip()
    if not tpl:
        return ""

    from .file_naming import apply_naming_template

    rendered = apply_naming_template(tpl, **ctx).strip()
    ctx["comments_section"] = rendered
    # 允许模板内嵌套引用 {comments_section}
    if "{comments_section}" in tpl:
        rendered = apply_naming_template(tpl, **ctx).strip()
    return rendered


def append_comments_section_to_md(
    md: str,
    cfg: Dict,
    *,
    comments_analysis: str = "",
    comments_file_path: str = "",
) -> str:
    """在成品 MD 中追加【评论区】；若正文已含 {comments_section} 占位则不再重复追加。"""
    body = md or ""
    if "{comments_section}" in body:
        section = render_comments_section(
            cfg.get("comments_section_template") or DEFAULT_COMMENTS_SECTION_TEMPLATE,
            comments_analysis=comments_analysis,
            comments_file_path=comments_file_path,
        )
        from .file_naming import apply_naming_template

        return apply_naming_template(
            body,
            comments_section=section,
            comments_analysis=(comments_analysis or "").strip(),
            comments_file_link=format_comments_file_link(comments_file_path),
        ).strip()

    section = render_comments_section(
        cfg.get("comments_section_template") or DEFAULT_COMMENTS_SECTION_TEMPLATE,
        comments_analysis=comments_analysis,
        comments_file_path=comments_file_path,
    )
    if not section:
        return body
    return f"{body.rstrip()}\n\n{section}\n"


def normalize_comments_count(count: Any, *, default: int = 10) -> int:
    """10/20/50/0(全量)；非法值回退 default。"""
    try:
        n = int(count)
    except (TypeError, ValueError):
        return default
    if n in (0, 10, 20, 50):
        return n
    if n < 0:
        return default
    return default


def run_comments_viewpoint_analysis(
    comments_text: str,
    *,
    article_context: str = "",
    llm_cfg: Dict,
    user_prompt: str = "",
    log_cb: Optional[Callable] = None,
    stage_label: str = "评论区观点提炼",
) -> str:
    """
    独立 LLM 调用：从全量评论中提炼观点表（dual 模式）。
    与原文整理/摘要共用 system_prompt（角色层一致），执行 prompt 分离。
    """
    comments = (comments_text or "").strip()
    if not comments:
        return ""

    from .pipeline_logging import (
        enrich_pipeline_llm_cfg,
        invoke_llm_via_gateway,
        llm_timeout_for_text_len,
        log_llm_done,
        log_llm_prepare,
        pipeline_log,
    )

    def log(msg: str, level: str = "INFO") -> None:
        if log_cb:
            log_cb(msg, level)

    task_id = (llm_cfg.get("_task_id") or stage_label or "comments").strip()
    chain = llm_cfg.get("_log_chain") or "链接沉淀文档-评论观点"
    log_module = "pipeline_comments.run_comments_viewpoint_analysis"
    log_obj = stage_label[:80]

    llm_cfg = enrich_pipeline_llm_cfg(dict(llm_cfg))
    ctx = (article_context or "").strip()[:8000]
    body = comments[:12000]

    prompt_tpl = (llm_cfg.get("comments_viewpoint_prompt") or _DEFAULT_COMMENTS_VIEWPOINT_PROMPT).strip()
    try:
        rendered = prompt_tpl.format(article_context=ctx, comments=body, text=body, transcript=ctx)
    except Exception:
        rendered = (
            prompt_tpl.replace("{article_context}", ctx)
            .replace("{comments}", body)
            .replace("{text}", body)
            .replace("{transcript}", ctx)
        )

    system_prompt = (llm_cfg.get("system_prompt") or "你是一个专业的内容分析助手。").strip()
    rules = (llm_cfg.get("comments_viewpoint_rules") or "").strip() + _JSON_OUTPUT_RULE_VIEWPOINT
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (f"分析规则：\n{rules}\n\n{rendered}").strip()},
    ]
    up = (user_prompt or llm_cfg.get("comments_user_prompt") or "").strip()
    if up:
        messages.append({"role": "user", "content": up})

    routes = log_llm_prepare(
        task_id,
        chain,
        log_module,
        log_obj,
        role="评论观点Agent",
        text_len=len(body) + len(ctx),
        cfg=llm_cfg,
        agent_name="comments_viewpoint_agent",
        task_type="summary",
    )
    timeout_sec, timeout_desc = llm_timeout_for_text_len(len(body) + len(ctx))
    routes = {**routes, "timeout_sec": timeout_sec}
    log(f"[{stage_label}] 评论观点提炼输入 {len(body)} 字，超时 {timeout_desc}", "INFO")

    t0 = time.perf_counter()
    try:
        ret = invoke_llm_via_gateway(
            llm_cfg,
            agent_name="comments_viewpoint_agent",
            task_type="summary",
            messages=messages,
            temperature=0.25,
            max_tokens=8192,
            timeout_sec=timeout_sec,
            retry_index=0,
        )
        out = (ret.get("content") or ret.get("text") or "").strip()
        ok = bool(out)
        log_llm_done(
            task_id,
            chain,
            log_module,
            log_obj,
            role="评论观点Agent",
            routes=routes,
            ok=ok,
            out_len=len(out),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
        pipeline_log(
            task_id,
            chain,
            log_module,
            log_obj,
            "评论观点",
            "Agent执行",
            "观点提炼完成" if ok else "观点提炼空结果",
            "INFO" if ok else "WARNING",
            log_cb=log_cb,
            ok=ok,
            out_len=len(out),
        )
        return out
    except Exception as ex:
        log_llm_done(
            task_id,
            chain,
            log_module,
            log_obj,
            role="评论观点Agent",
            routes=routes,
            ok=False,
            error=str(ex),
        )
        log(f"[{stage_label}] 评论观点提炼异常：{ex}", "WARNING")
        return ""


def prepare_comments_for_summary(
    comments_text: str,
    *,
    article_context: str = "",
    llm_cfg: Dict,
    comments_user_prompt: str = "",
    log_cb: Optional[Callable] = None,
    stage_label: str = "文档沉淀",
) -> tuple[str, str, str]:
    """
    按配置返回送入摘要 Agent 的评论块、观点表、模式。
    mode=dual：先独立观点 LLM；mode=merged：原始评论直接并入摘要输入。
    """
    raw = (comments_text or "").strip()
    if not raw:
        return "", "", "none"

    mode = str(llm_cfg.get("comments_summary_mode") or "dual").strip().lower()
    if mode not in ("dual", "merged"):
        mode = "dual"

    if mode == "merged":
        return raw, "", mode

    viewpoint = run_comments_viewpoint_analysis(
        raw,
        article_context=article_context,
        llm_cfg=llm_cfg,
        user_prompt=comments_user_prompt,
        log_cb=log_cb,
        stage_label=stage_label,
    )
    if viewpoint:
        return viewpoint, viewpoint, mode
    fallback = f"（评论观点提炼未返回结果；已抓取约 {len(raw)} 字评论，详见评论原文文件）"
    return fallback, "", mode
