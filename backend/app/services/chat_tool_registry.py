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
    "tool_rag_search": "rag_search",
    "tool_rag_index": "rag_search",
    "tool_comment_scraper": "scrape_comments",
    "tool_doc_analyze": "document_analyze",
    "tool_cache_rw": "cache_query",
    "tool_ops_snapshot": "ops_overview",
    "tool_rss_reader": "rss_list_recent",
    "tool_xhs_user_search": "xhs_user_search",
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
        tid, reused = reuse_or_enqueue_task(
            plat, url, user_prompt=user_prompt[:500], comments=comments_cfg, action="start",
        )
        add_log(tid, f"AI 工具 link_pipeline_start: {url}; read_comments={rc}; reused={reused}")
        from .pipeline_scheduler import request_video_pipeline_async

        asyncio.create_task(request_video_pipeline_async(tid))
        return _json_result({
            "ok": True,
            "async": True,
            "task_id": tid,
            "reused": reused,
            "platform": plat,
            "read_comments": rc,
            "hint": "在链接文档化页查看任务进度与 MD/HTML 产出",
        })

    tools.append(StructuredTool.from_function(
        coroutine=link_pipeline_start,
        name="link_pipeline_start",
        description=(
            "提交链接文档化流水线（转写/图文/MD/HTML）。"
            "仅当当前主任务尚无同链接流水线时调用；"
            "用户追问进度/缓存/「好了吗」时禁止调用，应改用 cache_query。"
            "read_comments_flag 仅在为 true 时读取评论；comment_count 可选 10/20/50/0(全量)，默认 10。"
        ),
    ))

    async def xhs_user_search(
        red_id: str,
        user_prompt: str = "",
    ) -> str:
        """通过小红书号搜索用户主页，解析 profile URL 并启动画像分析流水线。"""
        rid = (red_id or "").strip()
        if not rid:
            return _json_result({"ok": False, "error": "请提供小红书号（数字 ID）"})
        if not rid.isdigit() or len(rid) < 6:
            return _json_result({"ok": False, "error": f"小红书号格式异常：{rid}，应为纯数字（6-15 位）"})

        from .creator_feed_adapter import resolve_xhs_red_id
        from .task_manager import reuse_or_enqueue_task, add_log
        from .video_pipeline import process_video_pipeline
        from .span_orchestration import get_active_span_context
        # Step 1: 通过小红书号解析用户主页
        try:
            resolved = resolve_xhs_red_id(rid)
        except Exception as ex:
            _LOG.warning("xhs_user_search: resolve failed for %s: %s", rid, ex)
            return _json_result({
                "ok": False,
                "error": f"未找到小红书号 {rid} 对应的用户：{ex}",
                "hint": "请确认小红书号正确，或直接提供用户主页链接（如 https://www.xiaohongshu.com/user/profile/xxx）",
            })

        creator_id = str(resolved.get("creator_id") or "")
        profile_url = str(resolved.get("profile_url") or f"https://www.xiaohongshu.com/user/profile/{creator_id}")
        display_name = str(resolved.get("display_name") or rid)

        # Step 2: 启动链接文档化流水线
        span_ctx = get_active_span_context() or {}
        main_tid = str(span_ctx.get("task_id") or "").strip()
        plat = "小红书"
        up = (user_prompt or "").strip() or f"分析小红书用户 {display_name}（小红书号 {rid}）的主页内容，做用户画像"
        tid, reused = reuse_or_enqueue_task(plat, profile_url, user_prompt=up[:500], comments={"enabled": False}, action="start")
        add_log(tid, f"AI 工具 xhs_user_search: red_id={rid} -> creator_id={creator_id} url={profile_url}; reused={reused}")

        from .pipeline_scheduler import request_video_pipeline_async

        asyncio.create_task(request_video_pipeline_async(tid))
        return _json_result({
            "ok": True,
            "async": True,
            "task_id": tid,
            "reused": reused,
            "platform": plat,
            "red_id": rid,
            "creator_id": creator_id,
            "display_name": display_name,
            "profile_url": profile_url,
            "hint": f"已解析小红书号 {rid} → 用户 {display_name}（{creator_id}），流水线已启动。在链接文档化页查看进度。",
        })

    tools.append(StructuredTool.from_function(
        coroutine=xhs_user_search,
        name="xhs_user_search",
        description=(
            "通过小红书号（数字 ID）搜索并解析用户主页，自动启动画像分析流水线。"
            "当用户提到「小红书号」「red id」「搜小红书用户」并提供数字 ID 时优先调用。"
            "red_id 为小红书号（纯数字，如 9545679835）；user_prompt 为可选的额外分析指令。"
            "内部自动完成：red_id → 浏览器搜索 → 解析 profile URL → 启动文档化流水线。"
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
