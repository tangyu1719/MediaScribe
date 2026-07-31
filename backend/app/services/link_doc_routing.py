"""链接文档化意图路由：从用户话术识别平台链接，决定是否走评论抓取/流水线而非通用联网搜索。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s\]\)\"'<>]+", re.I)
_XHS_ID_RE = re.compile(
    r"(?:小红书\s*(?:号|账号|用户)?|xhs(?![A-Za-z0-9_])\s*(?:id)?|red\s*id)"
    r"\s*[:：]?\s*([A-Za-z0-9_-]{5,24})",
    re.I,
)
_LINK_DOC_KW = (
    "链接文档", "文档化", "读取评论", "读评论", "抓评论", "评论抓取",
    "评论区", "爬虫", "抓取", "转写", "开始处理", "流水线",
)
_SOCIAL_KW = ("小红书", "xhs", "抖音", "douyin", "b站", "bilibili", "笔记", "explore", "微信", "公众号")


def extract_http_urls(text: str) -> List[str]:
    raw = _URL_RE.findall(text or "")
    out: List[str] = []
    seen: set[str] = set()
    for u in raw:
        u = u.rstrip(".,;:!?）)]}")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def platform_from_url(url: str) -> str:
    """从 URL 识别平台；未知域名返回空字符串（禁止默认小红书）。"""
    low = (url or "").lower()
    if "xiaohongshu.com" in low or "xhslink.com" in low:
        return "小红书"
    if "douyin.com" in low or "iesdouyin" in low:
        return "抖音"
    if "bilibili.com" in low or "b23.tv" in low:
        return "B站"
    if "mp.weixin.qq.com" in low:
        return "微信公众号"
    if "zhihu.com" in low:
        return "知乎"
    if "github.com" in low:
        return "GitHub"
    parsed = urlparse(low)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return "通用网页"
    return ""


def resolve_task_platform(link: str, explicit: str = "") -> str:
    """任务入队平台：显式指定 > URL 识别 > 通用网页。"""
    plat = (explicit or "").strip()
    if plat in ("微信", "WeChat", "wechat", "WECHAT"):
        plat = "微信公众号"
    if plat:
        return plat
    from_url = platform_from_url(link)
    return from_url or "通用网页"


def is_web_article_link(url: str) -> bool:
    """是否应走 HTML 文章链路（非 yt-dlp 视频下载）。"""
    from .web_article_fetch import is_web_article_url

    return is_web_article_url(url)


def pipeline_route_hint(url: str) -> str:
    """根据 URL 给出推荐 pipeline_route（供日志/诊断）。"""
    low = (url or "").lower()
    if "xiaohongshu.com" in low or "xhslink.com" in low:
        return "xiaohongshu_graphic|video"
    if "douyin.com" in low or "iesdouyin" in low:
        return "douyin_graphic|video"
    if is_web_article_link(url):
        return "web_article"
    if "bilibili.com" in low or "b23.tv" in low:
        return "video"
    return "video|web_article"


def extract_xhs_account_id(text: str) -> Optional[str]:
    for match in _XHS_ID_RE.finditer(text or ""):
        candidate = match.group(1)
        # Internal tool identifiers are not Xiaohongshu accounts. In particular,
        # `xhs_user_search` used in a recovery instruction used to become `_user_search`.
        if candidate.startswith("_") or candidate.casefold() in {
            "user_search",
            "content_search",
            "search_result",
        }:
            continue
        return candidate
    if re.search(r"小红书|xhs|red\s*book", text or "", re.I):
        digits = re.findall(r"\b(\d{8,12})\b", text or "")
        if len(digits) == 1:
            return digits[0]
    return None


def extract_xhs_numeric_id(text: str) -> Optional[str]:
    """Backward-compatible alias; Xiaohongshu accounts may also be alphanumeric."""
    return extract_xhs_account_id(text)


def analyze_link_doc_intent(message: str, *, read_comments: bool = False) -> Dict[str, Any]:
    """分析是否应走链接文档化主链路（评论抓取 / 流水线），而非 Bing 联网搜索。"""
    q = (message or "").strip()
    urls = extract_http_urls(q)
    platform = platform_from_url(urls[0]) if urls else ""
    xhs_id = extract_xhs_numeric_id(q)
    low = q.lower()

    has_social_kw = any(k in q or k in low for k in _SOCIAL_KW)
    has_link_doc_kw = any(k in q for k in _LINK_DOC_KW)
    wants_comments = any(
        k in q for k in ("评论", "读评论", "读取评论", "抓评论", "评论区", "评论抓取")
    )
    wants_pipeline = any(
        k in q for k in ("文档化", "链接文档", "转写", "流水线", "开始处理", "生成文档")
    )
    wants_crawl = any(k in q for k in ("爬虫", "抓取", "爬取", "看看", "看下", "主页", "笔记"))

    only_xhs_id = bool(xhs_id) and not urls and has_social_kw
    link_doc_relevant = bool(urls) or only_xhs_id or (has_social_kw and (has_link_doc_kw or wants_comments or wants_crawl))

    run_comment_scrape = False
    doc_intent = bool(
        wants_pipeline or wants_crawl or has_link_doc_kw or read_comments or wants_comments
    )
    run_link_pipeline = bool(urls) and not only_xhs_id and doc_intent

    skip_web_search = link_doc_relevant and (
        bool(urls) or only_xhs_id or wants_comments or wants_crawl
    )

    guidance = ""
    if only_xhs_id:
        guidance = (
            f"检测到小红书号 {xhs_id}。请调用 xhs_user_search 工具（red_id={xhs_id}），"
            "通过浏览器自动化解析用户主页并启动画像分析流水线。"
        )
    elif link_doc_relevant and not urls and (wants_comments or wants_crawl):
        guidance = (
            "当前话术属于链接文档化/评论抓取，但未检测到 http(s) 链接。"
            "请粘贴小红书/B站/抖音/微信公众号作品链接（小红书需带 xsec_token）。"
        )

    return {
        "urls": urls,
        "platform": platform,
        "xhs_numeric_id": xhs_id,
        "link_doc_relevant": link_doc_relevant,
        "only_xhs_numeric_id": only_xhs_id,
        "run_comment_scrape": run_comment_scrape,
        "run_link_pipeline": run_link_pipeline,
        "skip_web_search": skip_web_search,
        "guidance": guidance,
        "wants_comments": wants_comments,
        "read_comments": read_comments,
        "pipeline_route_hint": pipeline_route_hint(urls[0]) if urls else "",
    }


async def enqueue_link_pipeline_from_chat(
    link_ctx: Dict[str, Any],
    *,
    user_prompt: str = "",
    read_comments: bool = False,
    comment_count: int = 10,
    comment_sort: str = "hot",
    session_id: str = "",
) -> Dict[str, Any]:
    """对话路由检测到 URL+文档化意图时，自动提交链接沉淀流水线。"""
    if not link_ctx.get("run_link_pipeline") or not link_ctx.get("urls"):
        return {"ok": False, "reason": "no_auto_start"}
    url = str(link_ctx["urls"][0]).strip()
    plat = resolve_task_platform(url, str(link_ctx.get("platform") or ""))
    try:
        from .pipeline_comments import normalize_comments_count
        from .task_manager import reuse_or_enqueue_task, add_log
        from .task_source_meta import SOURCE_CHAT, source_meta_kwargs
        import asyncio

        count = normalize_comments_count(comment_count, default=10)
        sort = (comment_sort or "hot").strip() or "hot"
        comments_cfg = {
            "enabled": bool(read_comments),
            "count": count if read_comments else 10,
            "sort": sort,
        }
        tid, reused, _ = reuse_or_enqueue_task(
            plat,
            url,
            user_prompt=(user_prompt or "")[:500],
            comments=comments_cfg,
            action="start",
            **source_meta_kwargs(SOURCE_CHAT, platform=plat),
        )
        add_log(
            tid,
            f"[链接文档化-对话自动启流水线|link_doc_routing.enqueue_link_pipeline_from_chat|{url}|Agent执行|提交] "
            f"read_comments={read_comments}; count={comments_cfg['count']}; reused={reused}; route_hint={pipeline_route_hint(url)}",
        )

        from .pipeline_scheduler import request_video_pipeline_async

        asyncio.create_task(request_video_pipeline_async(tid))
        return {
            "ok": True,
            "async": True,
            "task_id": tid,
            "reused": reused,
            "platform": plat,
            "url": url,
            "read_comments": read_comments,
            "comment_count": comments_cfg["count"],
            "hint": "已在链接文档化队列启动；可在任务卡片查看进度与 MD 产出",
        }
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:500]}
