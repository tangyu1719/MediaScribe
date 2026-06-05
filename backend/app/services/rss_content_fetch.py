"""RSS 文章全文抓取 —— 按原文链接拉取正文，供链接沉淀流水线使用。"""
from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any, Optional
from urllib.parse import urlparse

from .file_naming import _fetch_link_html, _link_fetch_headers

_log = logging.getLogger("sba.rss_content_fetch")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


def _plain_from_html(html: str) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()
        selectors = (
            "article .markdown-body",
            ".markdown-body",
            "article",
            "main",
            '[role="main"]',
            ".post-content",
            ".entry-content",
            ".article-content",
            "#content",
        )
        node = None
        for sel in selectors:
            node = soup.select_one(sel)
            if node and len((node.get_text() or "").strip()) > 80:
                break
        if node is None:
            node = soup.body or soup
        text = node.get_text("\n", strip=True) if node else ""
    except ImportError:
        text = _TAG_RE.sub("\n", unescape(html or ""))
        text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _fetch_github_release_text(url: str, *, timeout: float = 25.0) -> str:
    """GitHub Release 页：优先 API，其次 markdown-body。"""
    low = (url or "").lower()
    if "github.com" not in low or "/releases" not in low:
        return ""
    try:
        import requests

        m = re.search(r"github\.com/([^/]+)/([^/]+)/releases/tag/([^/?#]+)", url, re.I)
        if m:
            owner, repo, tag = m.group(1), m.group(2), m.group(3)
            api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
            resp = requests.get(
                api,
                headers={**_link_fetch_headers(url), "Accept": "application/vnd.github+json"},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                body = (data.get("body") or "").strip()
                if body:
                    return body
    except Exception as ex:
        _log.info(
            "[RSS订阅阅读-全文抓取|rss_content_fetch|github_api|工具执行|降级] API 失败; error_message=%s",
            ex,
        )
    html = _fetch_link_html(url, timeout=min(timeout, 12.0))
    return _plain_from_html(html)


def _fetch_generic_article_text(url: str, *, timeout: float = 25.0) -> str:
    try:
        import requests

        resp = requests.get(url, headers=_link_fetch_headers(url), timeout=timeout)
        if resp.status_code != 200:
            return ""
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            try:
                data = resp.json()
                for key in ("content", "body", "text", "article", "description"):
                    val = data.get(key) if isinstance(data, dict) else None
                    if isinstance(val, str) and len(val.strip()) > 80:
                        return val.strip()
            except json.JSONDecodeError:
                pass
        return _plain_from_html(resp.text or "")
    except Exception as ex:
        _log.warning(
            "[RSS订阅阅读-全文抓取|rss_content_fetch|http|工具执行|失败] 请求异常; url=%s; error_message=%s",
            url[:120],
            ex,
        )
        return ""


def fetch_article_full_text(
    url: str,
    *,
    feed_summary: str = "",
    feed_title: str = "",
    min_chars: int = 120,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """
    抓取 RSS 条目原文全文。
    返回: {ok, text, source, title_hint, char_len, error}
    """
    link = (url or "").strip()
    if not link:
        return {"ok": False, "text": "", "source": "", "error": "缺少原文链接"}
    parsed = urlparse(link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "text": "", "source": "", "error": "链接格式无效"}

    text = ""
    source = ""
    if "github.com" in link.lower():
        text = _fetch_github_release_text(link, timeout=timeout)
        if text:
            source = "github_release"

    if not text:
        text = _fetch_generic_article_text(link, timeout=timeout)
        if text:
            source = "html_extract"

    if len(text) < min_chars and (feed_summary or "").strip():
        merged = (feed_summary or "").strip()
        if text:
            merged = f"{text}\n\n---\n\n{merged}"
        text = merged
        source = source or "feed_summary_fallback"

    if len(text) < min_chars:
        return {
            "ok": False,
            "text": text,
            "source": source,
            "title_hint": (feed_title or "").strip(),
            "char_len": len(text),
            "error": f"全文过短（{len(text)} 字），无法沉淀",
        }

    return {
        "ok": True,
        "text": text[:120000],
        "source": source or "unknown",
        "title_hint": (feed_title or "").strip(),
        "char_len": len(text),
        "error": "",
    }
