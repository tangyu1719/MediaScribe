"""链接文档化意图路由：从用户话术识别平台链接，决定是否走评论抓取/流水线而非通用联网搜索。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s\]\)\"'<>]+", re.I)
_XHS_ID_RE = re.compile(
    r"(?:小红书\s*(?:号|账号|用户)?|xhs\s*(?:id)?|red\s*id)\s*[:：]?\s*(\d{6,12})",
    re.I,
)
_LINK_DOC_KW = (
    "链接文档", "文档化", "读取评论", "读评论", "抓评论", "评论抓取",
    "评论区", "爬虫", "抓取", "转写", "开始处理", "流水线",
)
_SOCIAL_KW = ("小红书", "xhs", "抖音", "douyin", "b站", "bilibili", "笔记", "explore")


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
    low = (url or "").lower()
    if "xiaohongshu.com" in low or "xhslink.com" in low:
        return "小红书"
    if "douyin.com" in low:
        return "抖音"
    if "bilibili.com" in low or "b23.tv" in low:
        return "B站"
    return ""


def extract_xhs_numeric_id(text: str) -> Optional[str]:
    m = _XHS_ID_RE.search(text or "")
    if m:
        return m.group(1)
    if re.search(r"小红书|xhs|red\s*book", text or "", re.I):
        digits = re.findall(r"\b(\d{8,12})\b", text or "")
        if len(digits) == 1:
            return digits[0]
    return None


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

    # 评论抓取不在路由层自动执行，仅 LLM 在用户勾选 read_comments 后调用 scrape_comments 工具
    run_comment_scrape = False
    run_link_pipeline = False

    skip_web_search = link_doc_relevant and (
        bool(urls) or only_xhs_id or wants_comments or wants_crawl
    )

    guidance = ""
    if only_xhs_id:
        guidance = (
            f"检测到小红书号 {xhs_id}，但本产品是「链接文档化」：需要可解析的笔记/作品链接"
            "（形如 https://www.xiaohongshu.com/explore/...?xsec_token=...）。"
            "请在「链接文档化」页粘贴该链接并勾选「读取评论」，或由 MCP 工具 scrape_comments 传入完整 URL。"
            "仅凭数字号无法直接爬用户主页。"
        )
    elif link_doc_relevant and not urls and (wants_comments or wants_crawl):
        guidance = (
            "当前话术属于链接文档化/评论抓取，但未检测到 http(s) 链接。"
            "请粘贴小红书/B站/抖音作品链接（小红书需带 xsec_token）。"
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
    }
