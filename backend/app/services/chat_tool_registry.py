"""AI 对话页统一工具注册：内置 Tool Call + MCP + SKILL（不按 Agent 裁剪）。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger("sba.chat_tools")

# 与 builtin_tools.list_builtin_tools 对齐的 function 名映射（AI 对话实际可调用名）
_BUILTIN_ID_TO_FN = {
    "tool_link_pipeline": "link_pipeline_start",
    "tool_video_visual_extract": "video_visual_extract",
    "tool_rag_search": "rag_search",
    "tool_rag_index": "rag_search",
    "tool_comment_scraper": "scrape_comments",
    "tool_doc_analyze": "document_analyze",
    "tool_cache_rw": "cache_query",
    "tool_ops_snapshot": "ops_overview",
    "tool_rss_reader": "rss_list_recent",
    "tool_xhs_user_search": "xhs_user_search",
    "tool_xhs_cookie_sync": "sync_xhs_cookies",
    "tool_local_file_list": "local_file_list",
    "tool_local_file_read": "local_file_read",
    "tool_local_file_write": "local_file_write",
    "tool_local_file_info": "local_file_info",
    "tool_local_file_delete": "local_file_delete",
    "tool_local_file_mkdir": "local_file_mkdir",
    "tool_local_file_move": "local_file_move",
    "tool_local_file_copy": "local_file_copy",
    "tool_local_file_find": "local_file_find",
    "tool_local_file_grep": "local_file_grep",
}


def is_tools_inventory_query(message: str) -> bool:
    """用户询问「有哪些工具 / 内置 tool call」等元问题。"""
    q = (message or "").strip()
    ql = q.lower()
    if not q:
        return False
    # 知识库检索/总结类主任务：含 MCP 等词但不是工具清单问询
    kb_task_hints = (
        "知识库", "检索", "召回", "总结", "文档", "资料", "rag", "milvus", "向量",
        "搜索知识", "查知识", "总结反馈",
    )
    tool_list_must = (
        "有哪些工具", "什么工具", "哪些工具", "工具列表", "工具清单", "能使用哪些",
        "可以用什么工具", "内置工具", "内置 tool", "内置tool", "挂载", "注册",
        "你到底能使用",
    )
    if any(h in q or h in ql for h in kb_task_hints):
        if not any(h in q for h in tool_list_must):
            return False
    hints = (
        "有哪些工具", "什么工具", "哪些工具", "工具列表", "工具清单", "能使用哪些",
        "可以用什么工具", "内置工具", "tool call", "toolcall", "tool_call",
        "skill", "function calling", "函数调用", "挂载", "注册",
        "你到底能使用", "内置tool", "内置 tool",
        "mcp 工具", "mcp工具", "有哪些mcp", "有哪些 mcp",
    )
    return any(h in ql or h in q for h in hints)


def is_streaming_meta_query(message: str) -> bool:
    """用户询问 SSE/流式输出/为何不是逐字显示等产品架构元问题。"""
    q = (message or "").strip()
    ql = q.lower()
    if not q:
        return False
    hints = (
        "流式", "流式输出", "streaming", "stream", "sse", "server-sent",
        "逐字", "打字机", "chunk", "分块", "event-stream", "readablestream",
        "为啥不是流式", "为什么不是流式", "不是流式", "为何不是流式",
        "一次性返回", "请求-响应", "非流式", "没有流式", "没流式",
        "怎么不是逐字", "为什么不逐字", "为何不是逐字",
    )
    if any(h in q or h in ql for h in hints):
        return True
    if re.search(r"(流式|streaming|sse|逐字|分块)", ql) and re.search(
        r"(为什么|为何|为啥|怎么|是不是|有没有|不是|没)", q
    ):
        return True
    return False


def format_streaming_architecture_markdown() -> str:
    """SuperBizAgent Web 问答真实传输架构说明（禁止 LLM 编造相反结论）。"""
    return "\n".join(
        [
            "## 本系统**已启用 SSE 流式传输**",
            "",
            "SuperBizAgent Web 问答**不是**「普通 POST 一次性返回全文」模式，链路如下：",
            "",
            "1. **前端**：`fetch('POST /api/chat/stream')` + `response.body.getReader()`（ReadableStream），解析 `text/event-stream`。",
            "2. **后端**：FastAPI `StreamingResponse`，分块推送 SSE 事件（如 `stream_open`、`answer_delta`、`step_think_delta`、`thought_step_end` 等）。",
            "3. **正文展示**：前端收到 `answer_delta` 后按打字机队列逐字渲染；思考链/编排步骤也有独立 SSE 事件。",
            "",
            "若感觉「不像流式」，常见原因：",
            "- 编排阶段（意图识别、RAG 预取、工具绑定等）耗时较长，**最终回答**的 `answer_delta` 尚未开始；",
            "- LLM/网关 token 缓冲导致间隔偏大；",
            "- 浏览器后台标签页节流，打字动画变慢。",
            "",
            "**请勿**向用户声称本系统未启用 SSE、未实现前端流式渲染，或只能一次性 POST 返回全文。",
        ]
    )


def format_tools_catalog_markdown(meta: Dict[str, Any]) -> str:
    """根据 load_all_chat_tools 的 meta 生成可展示给用户的真实工具清单。"""
    from .builtin_tools import list_builtin_tools

    lines = [
        "## 本系统 AI 对话页**真实可调用**工具（禁止编造未列出的工具名）",
        "",
        f"合计 **{meta.get('total', 0)}** 个（内置 {meta.get('builtin_count', 0)} · MCP {meta.get('mcp_count', 0)} · SKILL {meta.get('skill_count', 0)}）。",
        "以下名称与 function calling / Tool Call 注册表一致，**不是**泛化的「代码生成器」「合规网页搜索」等虚构能力。",
        "",
    ]
    if meta.get("mcp_error"):
        lines.append(f"> MCP 连接说明：{meta['mcp_error'][:300]}")
        lines.append("")

    by_src: Dict[str, List[Dict[str, str]]] = {"builtin": [], "mcp": [], "skill": []}
    for row in meta.get("tools") or []:
        src = row.get("source") or "builtin"
        by_src.setdefault(src, []).append(row)

    lines.append("### 一、内置 Tool Call（服务端已实现）")
    lines.append("| 工具页 ID | 实际调用名 | 说明 |")
    lines.append("|-----------|------------|------|")
    for bt in list_builtin_tools():
        tid = bt.get("id") or ""
        fn = _BUILTIN_ID_TO_FN.get(tid, tid.replace("tool_", ""))
        mounted = any(r.get("name") == fn for r in by_src.get("builtin", []) + by_src.get("mcp", []))
        mark = "✓ 已挂载" if mounted else "— 未挂载"
        desc = (bt.get("description") or "")[:100].replace("|", "/")
        lines.append(f"| {tid} | `{fn}` | {desc} ({mark}) |")
    lines.append("")
    lines.append("另：`web_search` 为联网搜索（Bing 优先），非站内爬虫。")
    if not meta.get("read_comments_enabled"):
        lines.append("`scrape_comments` 仅当用户勾选「读取评论」后才会出现在工具列表。")
    lines.append("")

    if by_src.get("mcp"):
        lines.append("### 二、MCP 工具")
        for r in by_src["mcp"]:
            lines.append(f"- `{r.get('name')}`：{(r.get('description') or '')[:160]}")
        lines.append("")

    if by_src.get("skill"):
        lines.append("### 三、SKILL 工具")
        for r in by_src["skill"]:
            lines.append(f"- `{r.get('name')}`：{(r.get('description') or '')[:160]}")
        lines.append("")

    lines.append("### 使用说明")
    lines.append("- 链接文档化、评论抓取、RAG、流水线等请通过上表**具名工具**调用，不要声称具备未注册能力。")
    lines.append("- 查询工具能力时**不要**对用户问题做联网搜索。")
    return "\n".join(lines)


def _json_result(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def _platform_from_link(link: str) -> str:
    low = (link or "").lower()
    if "xiaohongshu.com" in low or "xhslink.com" in low:
        return "小红书"
    if "douyin.com" in low:
        return "抖音"
    if "bilibili.com" in low or "b23.tv" in low:
        return "B站"
    return "抖音"


def build_internal_chat_tools(*, read_comments: bool = False) -> List[Any]:
    """将 builtin_tools 清单中 internal 能力封装为 LangChain StructuredTool。"""
    try:
        from langchain_core.tools import StructuredTool
    except ImportError:
        _LOG.warning("未安装 langchain_core，跳过内置工具封装")
        return []

    tools: List[Any] = []

    async def link_pipeline_start(
        link: str,
        platform: str = "",
        user_prompt: str = "",
        read_comments_flag: bool = False,
        comment_count: int = 10,
        comment_sort: str = "hot",
        video_transcript_mode: str = "audio_only",
    ) -> str:
        from .task_manager import reuse_or_enqueue_task, add_log
        from .video_pipeline import process_video_pipeline
        from .link_doc_routing import platform_from_url
        from .span_orchestration import get_active_span_context

        url = (link or "").strip()
        if not url.startswith(("http://", "https://")):
            return _json_result({"ok": False, "error": "link 须为 http(s) URL"})
        span_ctx = get_active_span_context() or {}
        main_tid = str(span_ctx.get("task_id") or "").strip()
        if main_tid:
            from .chat_context_memory import guard_link_pipeline_start

            guarded = guard_link_pipeline_start(
                main_task_id=main_tid,
                user_message=user_prompt or "",
                tool_args={"link": url, "platform": platform},
                continue_main=False,
            )
            if guarded is not None:
                return _json_result(guarded)
        plat = (platform or "").strip() or platform_from_url(url) or _platform_from_link(url)
        from .pipeline_comments import normalize_comments_count

        rc = bool(read_comments_flag or read_comments)
        count = normalize_comments_count(comment_count, default=10) if rc else 10
        sort = (comment_sort or "hot").strip() or "hot"
        comments_cfg = {"enabled": rc, "count": count, "sort": sort}
        from .video_visual.link_transcript import normalize_video_transcript_mode

        vt_mode = normalize_video_transcript_mode(video_transcript_mode)
        pipe_opts = {"video_transcript_mode": vt_mode}
        tid, reused, _ = reuse_or_enqueue_task(
            plat, url, user_prompt=user_prompt[:500], comments=comments_cfg, action="start",
            pipeline_options=pipe_opts,
        )
        add_log(tid, f"AI 工具 link_pipeline_start: {url}; read_comments={rc}; video_transcript_mode={vt_mode}; reused={reused}")
        from .pipeline_scheduler import request_video_pipeline_async

        asyncio.create_task(request_video_pipeline_async(tid))
        return _json_result({
            "ok": True,
            "async": True,
            "task_id": tid,
            "reused": reused,
            "platform": plat,
            "read_comments": rc,
            "video_transcript_mode": vt_mode,
            "hint": "在链接文档化页查看任务进度与 MD/HTML 产出",
        })

    async def video_visual_extract(
        link: str,
        video_transcript_mode: str = "visual_frames",
        include_audio: bool = False,
    ) -> str:
        """单链视频→原文（不跑完整沉淀）；适合课件/PPT 快速抽画面文字。"""
        from .video_visual.link_transcript import extract_video_transcript_for_pipeline, normalize_video_transcript_mode
        from .video_visual.common import download_video_for_visual

        url = (link or "").strip()
        if not url.startswith(("http://", "https://")):
            return _json_result({"ok": False, "error": "link 须为 http(s) URL"})
        mode = normalize_video_transcript_mode(video_transcript_mode)
        if include_audio and mode == "visual_frames":
            mode = "hybrid"
        path = await asyncio.get_running_loop().run_in_executor(
            None, lambda: download_video_for_visual(url),
        )
        if not path:
            return _json_result({"ok": False, "error": "视频下载失败", "url": url})
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: extract_video_transcript_for_pipeline(path, mode, strict_whisper=False),
        )
        plain = (result.get("full_text") or result.get("transcript") or "").strip()
        return _json_result({
            "ok": bool(result.get("ok") and plain),
            "url": url,
            "video_transcript_mode": mode,
            "plain_text_len": len(plain),
            "plain_text_preview": plain[:2000],
            "visual_segment_count": result.get("visual_segment_count"),
            "coverage_score": result.get("coverage_score"),
            "error_code": result.get("error_code"),
            "error_message": result.get("error_message"),
        })

    tools.append(StructuredTool.from_function(
        coroutine=video_visual_extract,
        name="video_visual_extract",
        description=(
            "从视频链接提取原文生文（画面 OCR / 音频 / 混合），不进入完整 MD 沉淀流水线。"
            "video_transcript_mode: audio_only | visual_frames | hybrid。课件/PPT 请用 visual_frames。"
        ),
    ))

    tools.append(StructuredTool.from_function(
        coroutine=link_pipeline_start,
        name="link_pipeline_start",
        description=(
            "提交视频/图文链接，服务端执行下载、转写、摘要与 Markdown/HTML 产出。"
            "video_transcript_mode: audio_only 仅 Whisper；visual_frames 智能画面 OCR；hybrid 音频+画面。"
            "read_comments_flag 仅在为 true 时读取评论；comment_count 可选 10/20/50/0(全量)，默认 10。"
        ),
    ))

    async def xhs_user_search(
        red_id: str,
        user_prompt: str = "",
    ) -> str:
        """通过小红书号解析用户并执行五阶段人物画像（主页 catalog → 选篇 → 笔记流水线 → 深度画像）。"""
        rid = (red_id or "").strip()
        if not rid:
            return _json_result({"ok": False, "error": "请提供小红书号（数字 ID）"})
        if not rid.isdigit() or len(rid) < 6:
            return _json_result({"ok": False, "error": f"小红书号格式异常：{rid}，应为纯数字（6-15 位）"})

        from .creator_profile_runner import run_xhs_chat_profile
        from .tool_chat_resilience import extract_error_code

        up = (user_prompt or "").strip() or f"分析小红书用户（小红书号 {rid}）的内容与人设，输出人物画像"
        try:
            result = await run_xhs_chat_profile(red_id=rid, user_prompt=up)
        except Exception as ex:
            _LOG.warning("xhs_user_search: profile failed for %s: %s", rid, ex)
            code = extract_error_code(str(ex))
            return _json_result({
                "ok": False,
                "error_code": code or "PROFILE_FAILED",
                "error": str(ex),
                "hint": "可先调用 sync_xhs_cookies 同步登录态；CDP 9223 未就绪时会尝试 HTTP+Cookie 回退",
            })

        if not result.get("ok"):
            code = str(result.get("error_code") or extract_error_code(str(result.get("error") or "")) or "PROFILE_FAILED")
            hint = "可先调用 sync_xhs_cookies；若 PROFILE_CATALOG_EMPTY 请确认 CDP Chrome 9223 已登录小红书"
            if code in ("SUB_XHS_CDP_REQUIRED", "SUB_XHS_COOKIE_UNAVAILABLE"):
                hint = "请先启动 CDP Chrome（9223）并调用 sync_xhs_cookies 同步 Cookie"
            return _json_result({**result, "error_code": code, "hint": hint})

        return _json_result({
            **result,
            "async": False,
            "hint": (
                f"已完成人物画像：{result.get('display_name')}（{result.get('creator_id')}），"
                f"采样 {result.get('deep_ok_count')} 篇笔记。"
                f"完整文档：{result.get('profile_md_path')}"
            ),
        })

    tools.append(StructuredTool.from_function(
        coroutine=xhs_user_search,
        name="xhs_user_search",
        description=(
            "通过小红书号（数字 ID）搜索用户并生成人物画像。"
            "当用户提到「小红书号」「red id」「搜小红书用户」「人物画像」并提供数字 ID 时优先调用。"
            "流程：解析 red_id → 拉主页笔记目录 → 轻量画像 → 选取若干笔记链接（非主页 URL）"
            "→ 逐条链接分析拉原文 → LLM 深度画像整合。"
            "red_id 为纯数字；user_prompt 为分析侧重点（如内容风格、行业定位）。"
            "不将用户主页 profile URL 当作单条链接提交流水线。"
        ),
    ))

    async def sync_xhs_cookies(force: bool = False) -> str:
        """从本机 Chrome（CDP 9223）同步小红书登录 Cookie 到后端，与订阅页「从 Chrome 同步 Cookie」相同。"""
        from .cookie_manager import diagnose_xhs_cookies
        from .xhs_local_browser import ensure_xhs_cookies_synced

        result = dict(ensure_xhs_cookies_synced(force=bool(force)))
        result["cookie_diagnosis"] = diagnose_xhs_cookies()
        return _json_result(result)

    tools.append(StructuredTool.from_function(
        coroutine=sync_xhs_cookies,
        name="sync_xhs_cookies",
        description=(
            "从本机 Chrome（CDP 端口 9223）同步小红书登录 Cookie 到后端。"
            "当 xhs_user_search / 小红书抓取报 SUB_XHS_COOKIE_UNAVAILABLE 或 Cookie 未就绪时优先调用。"
            "与 UI「从 Chrome 同步 Cookie」按钮等价；force=true 时强制重新同步。"
        ),
    ))

    def rag_search(query: str, top_k: int = 5) -> str:
        from .kb_rag import kb_search
        from .span_orchestration import get_active_span_context

        span_ctx = get_active_span_context() or {}
        meta_filt = span_ctx.get("rag_metadata_filter") if isinstance(span_ctx.get("rag_metadata_filter"), dict) else None
        hits = kb_search(
            query,
            top_k=max(1, min(int(top_k or 5), 20)),
            span_ctx=span_ctx,
            metadata_filter=meta_filt,
        )
        return _json_result({"ok": True, "count": len(hits), "hits": hits, "metadata_filter": meta_filt or {}})

    tools.append(StructuredTool.from_function(
        func=rag_search,
        name="rag_search",
        description="RAG 语义检索：在当前 Milvus 知识库中检索与 query 相关的片段。",
    ))

    def web_search_chat(query: str = "", search_queries: str = "", max_results: int = 5) -> str:
        from .web_search import web_search_for_chat, web_search_multi_for_chat
        from .web_search_plan import resolve_web_search_plan_for_tool
        from .span_orchestration import get_active_span_context

        task_user_q = ""
        current_msg = query or ""
        span_ctx = get_active_span_context() or {}
        main_tid = str(span_ctx.get("task_id") or "").strip()
        if main_tid:
            try:
                from .span_audit import get_task

                mt = get_task(main_tid) or {}
                task_user_q = str(mt.get("user_query") or mt.get("query_summary") or "")
            except Exception:
                pass

        qs: list = []
        if search_queries:
            try:
                import json as _json

                parsed = _json.loads(search_queries)
                qs = parsed if isinstance(parsed, list) else [str(search_queries)]
            except Exception:
                qs = [q.strip() for q in str(search_queries).split("|") if q.strip()]

        plan = resolve_web_search_plan_for_tool(
            tool_query=query,
            tool_search_queries=qs or None,
            current_message=current_msg,
            task_user_query=task_user_q,
            rewritten_query=query,
            continue_main=bool(task_user_q),
        )
        if plan.get("skip_web_search"):
            return _json_result({
                "ok": False,
                "skipped": True,
                "reason": "当前为进度追问，无有效联网检索词；请查 cache_query 或既有流水线",
                "search_queries": [],
            })
        qs = list(plan.get("search_queries") or [])
        if len(qs) > 1:
            out = web_search_multi_for_chat(
                qs,
                max_results_per_query=max(1, min(int(max_results or 5), 5)),
                objective=str(plan.get("objective") or query or "")[:160],
            )
        else:
            out = web_search_for_chat(
                qs[0] if qs else str(plan.get("primary_query") or query),
                max_results=max(1, min(int(max_results or 5), 10)),
            )
        if isinstance(out, dict):
            out["keyword_source"] = plan.get("keyword_source")
            out["search_queries"] = qs
        return _json_result(out)

    tools.append(StructuredTool.from_function(
        func=web_search_chat,
        name="web_search",
        description=(
            "联网搜索（Bing 优先）：检索词仅来自用户原问/改写句抽词，不含编排业务映射词。"
            "追问进度（好了吗）时请用 cache_query 或查流水线，勿调用本工具。"
            "可传 search_queries（JSON 短关键词数组）或 query。"
        ),
    ))

    if read_comments:
        def scrape_comments_tool(
            url: str,
            platform: str = "",
            max_count: int = 20,
            sort_by: str = "hot",
        ) -> str:
            from .comment_scraper import scrape_comments, format_comments_as_text
            cr = scrape_comments(url, platform=platform or "", max_count=max_count, sort_by=sort_by)
            return _json_result({
                "ok": not bool(cr.error),
                "platform": cr.platform,
                "fetched_count": cr.fetched_count,
                "error": cr.error,
                "text": format_comments_as_text(cr)[:12000],
            })

        tools.append(StructuredTool.from_function(
            func=scrape_comments_tool,
            name="scrape_comments",
            description=(
                "抓取小红书/B站/抖音作品链接下的评论区（须完整 URL，小红书需 xsec_token）。"
                "仅当用户已在对话页勾选「读取评论」时本工具才可用。"
            ),
        ))

    def document_analyze(path: str) -> str:
        from pathlib import Path
        from .document import analyze_document
        p = Path(path)
        if not p.is_file():
            return _json_result({"ok": False, "error": f"文件不存在: {path}"})
        try:
            r = analyze_document(str(p))
            return _json_result({"ok": True, "result": r})
        except Exception as e:
            return _json_result({"ok": False, "error": str(e)})

    tools.append(StructuredTool.from_function(
        func=document_analyze,
        name="document_analyze",
        description="解析服务端可读路径上的文档（PDF/Office 等），返回文本与元数据。",
    ))

    def cache_query_tool(keyword: str = "", artifact: str = "", limit: int = 20) -> str:
        from .cache import cache_query
        rows = cache_query(keyword=keyword or None, artifact=artifact or None, limit=max(1, min(int(limit or 20), 100)))
        return _json_result({"ok": True, "rows": rows})

    tools.append(StructuredTool.from_function(
        func=cache_query_tool,
        name="cache_query",
        description="查询 Redis 中间缓存（可按 keyword、artifact 过滤）。追问流水线进度/缓存结果时优先使用，keyword 填 pipeline task_id 或账号关键词。",
    ))

    def ops_overview() -> str:
        from .ops import ops_get_overview
        return _json_result(ops_get_overview())

    tools.append(StructuredTool.from_function(
        func=ops_overview,
        name="ops_overview",
        description="OPS 观测：调用统计、事件与运维建议摘要。",
    ))

    def rss_list_recent(
        limit: int = 10,
        query: str = "",
        starred_only: bool = False,
        unread_only: bool = False,
    ) -> str:
        from .rss_reader import rss_list_for_tool

        return _json_result(
            rss_list_for_tool(
                limit=limit,
                query=query,
                starred_only=starred_only,
                unread_only=unread_only,
            )
        )

    tools.append(StructuredTool.from_function(
        func=rss_list_recent,
        name="rss_list_recent",
        description=(
            "列出当前登录用户在 RSS 阅读器中的近期文章（标题、摘要、链接、已读/星标）。"
            "用户询问订阅资讯、RSS、未读或星标文章时调用；勿编造未返回的条目。"
        ),
    ))

    def local_file_list(
        path: str = "",
        recursive: bool = False,
        max_depth: int = 3,
        limit: int = 500,
    ) -> str:
        from .local_file_ops import list_local_path

        return _json_result(list_local_path(path, recursive=recursive, max_depth=max_depth, limit=limit))

    tools.append(StructuredTool.from_function(
        func=local_file_list,
        name="local_file_list",
        description=(
            "列举白名单目录：默认一层；recursive=true 时递归（max_depth/limit 控规模）。"
            "path 为空返回 FS_ALLOW_ROOTS 根。大型整理前先用 local_file_find/grep。"
        ),
    ))

    def local_file_read(path: str, limit: int = 50000) -> str:
        from .local_file_ops import read_local_file

        return _json_result(read_local_file(path, limit=limit))

    tools.append(StructuredTool.from_function(
        func=local_file_read,
        name="local_file_read",
        description=(
            "读取白名单内 UTF-8 文本文件内容。"
            "path 为绝对路径；limit 可选最大字符数（默认 50000）。"
            "用户要查看 MD/TXT/JSON 等本地文本文件时调用。"
        ),
    ))

    def local_file_write(path: str, content: str, append: bool = False) -> str:
        from .local_file_ops import write_local_file

        return _json_result(write_local_file(path, content, append=append))

    tools.append(StructuredTool.from_function(
        func=local_file_write,
        name="local_file_write",
        description=(
            "写入或追加白名单内文本文件；可自动创建父目录。"
            "append=true 追加，false 覆盖。内容上限 512KB。"
            "用户要求保存笔记、写入临时文件或更新本地文本时调用。"
        ),
    ))

    def local_file_info(path: str) -> str:
        from .local_file_ops import info_local_file

        return _json_result(info_local_file(path))

    tools.append(StructuredTool.from_function(
        func=local_file_info,
        name="local_file_info",
        description=(
            "查询白名单内文件或目录元信息：类型、大小、修改时间。"
            "用户询问文件是否存在、多大、何时修改时调用。"
        ),
    ))

    def local_file_delete(path: str, recursive: bool = False) -> str:
        from .local_file_ops import delete_local_path

        return _json_result(delete_local_path(path, recursive=recursive))

    tools.append(StructuredTool.from_function(
        func=local_file_delete,
        name="local_file_delete",
        description=(
            "删除白名单内文件；recursive=true 可删目录树（非空目录）。"
            "整理/清理任务时调用；删除前须确认 path。"
        ),
    ))

    def local_file_mkdir(path: str, parents: bool = True) -> str:
        from .local_file_ops import mkdir_local_path

        return _json_result(mkdir_local_path(path, parents=parents))

    tools.append(StructuredTool.from_function(
        func=local_file_mkdir,
        name="local_file_mkdir",
        description="在白名单内创建目录；parents=true 自动创建父目录。整理任务前建目标文件夹时调用。",
    ))

    def local_file_move(source: str, dest: str, overwrite: bool = False) -> str:
        from .local_file_ops import move_local_path

        return _json_result(move_local_path(source, dest, overwrite=overwrite))

    tools.append(StructuredTool.from_function(
        func=local_file_move,
        name="local_file_move",
        description=(
            "移动或重命名白名单内文件/目录（source -> dest）。"
            "overwrite=true 覆盖已存在目标。文件整理、归档、改名时调用。"
        ),
    ))

    def local_file_copy(source: str, dest: str, overwrite: bool = False, recursive: bool = True) -> str:
        from .local_file_ops import copy_local_path

        return _json_result(copy_local_path(source, dest, overwrite=overwrite, recursive=recursive))

    tools.append(StructuredTool.from_function(
        func=local_file_copy,
        name="local_file_copy",
        description=(
            "复制/粘贴白名单内文件或目录（source -> dest，等同文件管理器复制粘贴）。"
            "recursive=true 复制整个目录树。备份、批量整理时调用。"
        ),
    ))

    def local_file_find(
        root: str,
        glob_pattern: str = "**/*",
        name_contains: str = "",
        min_size_bytes: int = 0,
        max_size_bytes: int = 0,
        modified_after: str = "",
        limit: int = 500,
    ) -> str:
        from .local_file_ops import find_local_files

        return _json_result(find_local_files(
            root,
            glob_pattern=glob_pattern,
            name_contains=name_contains,
            min_size_bytes=min_size_bytes,
            max_size_bytes=max_size_bytes,
            modified_after=modified_after,
            limit=limit,
        ))

    tools.append(StructuredTool.from_function(
        func=local_file_find,
        name="local_file_find",
        description=(
            "递归查找文件（Cursor 式 glob）：按 glob_pattern、名称关键词、大小、修改时间过滤。"
            "示例 glob：*.md、**/*.py。大型文件查找/整理任务的首选入口；默认跳过 node_modules/.git。"
        ),
    ))

    def local_file_grep(
        pattern: str,
        path: str = "",
        glob: str = "",
        case_insensitive: bool = False,
        output_mode: str = "content",
        head_limit: int = 200,
        context_before: int = 0,
        context_after: int = 0,
    ) -> str:
        from .local_file_ops import grep_local_files

        return _json_result(grep_local_files(
            pattern,
            path=path,
            glob=glob,
            case_insensitive=case_insensitive,
            output_mode=output_mode,
            head_limit=head_limit,
            context_before=context_before,
            context_after=context_after,
        ))

    tools.append(StructuredTool.from_function(
        func=local_file_grep,
        name="local_file_grep",
        description=(
            "Cursor 式 grep：白名单内正则搜索文件内容。"
            "output_mode=content|files_with_matches|count；glob 过滤扩展名如 *.py；"
            "context_before/after 带上下文行。按内容定位文件后配合 move/copy 整理。"
        ),
    ))

    return tools


def build_skill_chat_tools() -> List[Any]:
    """将 skills_registry 中每条 SKILL 暴露为可调用工具（返回正文供 Agent 遵循）。"""
    try:
        from langchain_core.tools import StructuredTool
    except ImportError:
        return []
    from .skill_registry import list_skills, get_skill

    tools: List[Any] = []
    for meta in list_skills():
        sid = str(meta.get("id") or "")
        if not sid:
            continue
        sname = str(meta.get("name") or sid)[:40]
        cmd = str(meta.get("command") or "").strip()
        desc = str(meta.get("description") or "")[:500]

        def _make_invoke(skill_id: str):
            def invoke_skill(user_request: str = "") -> str:
                row = get_skill(skill_id)
                if not row:
                    return _json_result({"ok": False, "error": f"SKILL 不存在: {skill_id}"})
                try:
                    from .board_usage_stats import record_skill_invoke

                    record_skill_invoke(skill_id)
                except Exception:
                    pass
                try:
                    from .skill_usage_archive_service import record_skill_usage_start

                    record_skill_usage_start(
                        skill_id=skill_id,
                        skill_name=str(row.get("name") or ""),
                        user_request=(user_request or "")[:2000],
                        trigger="invoke",
                    )
                except Exception:
                    pass
                body = (row.get("body_md") or "").strip()
                return _json_result({
                    "ok": True,
                    "skill_id": skill_id,
                    "name": row.get("name"),
                    "command": row.get("command"),
                    "body_md": body[:16000],
                    "user_request": (user_request or "")[:2000],
                    "execution_mode": "documentation_only",
                })
            return invoke_skill

        fn = _make_invoke(sid)
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", f"skill_{sid[:12]}")
        tools.append(StructuredTool.from_function(
            func=fn,
            name=safe_name,
            description=f"SKILL「{sname}」{(' 命令'+cmd) if cmd else ''}。{desc}",
        ))
    return tools


def load_orchestration_tools_catalog(*, read_comments: bool = False) -> Tuple[List[Any], Dict[str, Any]]:
    """
    编排段快速工具目录：仅内置 + SKILL，不连接 MCP（避免首屏卡在「发现工具」）。
    完整 MCP 在执行段 handoff 前由 ensure_execution_tools 加载。
    """
    internal = build_internal_chat_tools(read_comments=read_comments)
    skills = build_skill_chat_tools()
    by_name: Dict[str, Any] = {}
    catalog: List[Dict[str, str]] = []

    def _add(tool: Any, source: str) -> None:
        name = getattr(tool, "name", None) or ""
        if not name or name in by_name:
            return
        by_name[name] = tool
        catalog.append({
            "name": name,
            "source": source,
            "description": (getattr(tool, "description", None) or "")[:300],
        })

    for t in internal:
        _add(t, "builtin")
    for t in skills:
        _add(t, "skill")

    merged = list(by_name.values())
    meta = {
        "total": len(merged),
        "builtin_count": len(internal),
        "mcp_count": 0,
        "skill_count": len(skills),
        "mcp_error": "",
        "mcp_pending": True,
        "discovery_stage": "builtin_only",
        "tools": catalog,
        "read_comments_enabled": read_comments,
    }
    return merged, meta


async def ensure_execution_tools(
    runtime_tools: List[Any],
    runtime_meta: Dict[str, Any],
    *,
    read_comments: bool = False,
) -> Tuple[List[Any], Dict[str, Any]]:
    """执行段前补全 MCP 工具（若编排段仅加载了内置目录）。"""
    if runtime_meta.get("discovery_stage") == "full" and not runtime_meta.get("mcp_pending"):
        return runtime_tools, runtime_meta
    try:
        from .chat_warmup import get_cached_tools

        cached = get_cached_tools(read_comments=read_comments)
        if cached:
            full_tools, full_meta = cached
            full_meta = dict(full_meta)
            full_meta["discovery_stage"] = "full"
            full_meta["mcp_pending"] = False
            full_meta["warmup_cache"] = True
            return full_tools, full_meta
    except Exception:
        pass
    full_tools, full_meta = await load_all_chat_tools(read_comments=read_comments)
    full_meta["discovery_stage"] = "full"
    full_meta["mcp_pending"] = False
    return full_tools, full_meta


async def load_all_chat_tools(*, read_comments: bool = False) -> Tuple[List[Any], Dict[str, Any]]:
    """
    加载 AI 对话页全部工具（不按 agent_id / tools_scope 过滤）。
    返回 (langchain_tools, discovery_meta)。
    """
    try:
        from .chat_warmup import get_cached_tools

        cached = get_cached_tools(read_comments=read_comments)
        if cached:
            tools, meta = cached
            out_meta = dict(meta)
            out_meta.setdefault("discovery_stage", "full")
            out_meta["mcp_pending"] = False
            out_meta["warmup_cache"] = True
            return list(tools), out_meta
    except Exception:
        pass

    internal = build_internal_chat_tools(read_comments=read_comments)
    skills = build_skill_chat_tools()
    mcp_tools: List[Any] = []
    mcp_err = ""
    try:
        from . import mcp_langchain as _mcp_lc
        mcp_tools, mcp_err = await _mcp_lc.mcp_get_langchain_tools()
    except Exception as e:
        mcp_err = str(e)
        _LOG.warning("MCP 工具加载失败: %s", e)

    by_name: Dict[str, Any] = {}
    catalog: List[Dict[str, str]] = []

    def _add(tool: Any, source: str) -> None:
        name = getattr(tool, "name", None) or ""
        if not name:
            return
        if name in by_name:
            return
        by_name[name] = tool
        catalog.append({
            "name": name,
            "source": source,
            "description": (getattr(tool, "description", None) or "")[:300],
        })

    _comment_names = {"scrape_comments", "scrape_comments_json"}
    for t in mcp_tools:
        nm = getattr(t, "name", "") or ""
        if nm in _comment_names and not read_comments:
            continue
        _add(t, "mcp")
    for t in internal:
        nm = getattr(t, "name", "") or ""
        if nm in by_name:
            continue
        _add(t, "builtin")
    for t in skills:
        _add(t, "skill")

    merged = list(by_name.values())
    meta = {
        "total": len(merged),
        "builtin_count": len(internal),
        "mcp_count": len(mcp_tools),
        "skill_count": len(skills),
        "mcp_error": mcp_err,
        "tools": catalog,
        "read_comments_enabled": read_comments,
        "discovery_stage": "full",
        "mcp_pending": False,
    }
    _LOG.info(
        "[AI问答-工具发现|chat_tool_registry.load_all_chat_tools|catalog|硬编执行|完成] "
        "已加载工具; total=%s; builtin=%s; mcp=%s; skill=%s; mcp_error=%s",
        meta["total"], meta["builtin_count"], meta["mcp_count"], meta["skill_count"],
        mcp_err or "none",
    )
    return merged, meta
