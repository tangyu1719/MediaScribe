"""通用网页 / 微信公众号文章抓取 —— 供 web_article 流水线路由使用。"""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Dict, List
from urllib.parse import urlparse

from .file_naming import _fetch_link_html, _link_fetch_headers
from .rss_content_fetch import _plain_from_html

_log = logging.getLogger("sba.web_article_fetch")

_WECHAT_HOSTS = ("mp.weixin.qq.com",)
_MIN_ARTICLE_CHARS = 80


def is_wechat_article_url(url: str) -> bool:
    low = (url or "").lower()
    return any(h in low for h in _WECHAT_HOSTS)


def is_web_article_url(url: str) -> bool:
    """非视频平台、应走 HTML 全文抓取而非 yt-dlp 的链接。"""
    low = (url or "").lower()
    if is_wechat_article_url(low):
        return True
    video_hosts = (
        "xiaohongshu.com", "xhslink.com",
        "douyin.com", "iesdouyin.com",
        "bilibili.com", "b23.tv",
        "youtube.com", "youtu.be",
        "v.qq.com", "youku.com", "iqiyi.com",
    )
    if any(h in low for h in video_hosts):
        return False
    parsed = urlparse(low)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _extract_wechat_body(html: str) -> tuple[str, str]:
    """从微信公众号 HTML 提取标题与正文（优先 #js_content）。"""
    title = ""
    body = ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
        title_el = soup.select_one("#activity-name") or soup.find("h1", class_="rich_media_title")
        if title_el:
            title = title_el.get_text(strip=True)
        if not title:
            t = soup.find("title")
            title = (t.get_text(strip=True) if t else "").replace("- 微信公众号", "").strip()
        content_el = (
            soup.select_one("#js_content")
            or soup.select_one(".rich_media_content")
            or soup.select_one("#img-content")
        )
        if content_el:
            body = content_el.get_text("\n", strip=True)
        if len(body) < _MIN_ARTICLE_CHARS:
            fallback = _plain_from_html(html)
            if len(fallback) > len(body):
                body = fallback
    except ImportError:
        body = _plain_from_html(html)
        m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
        if m:
            title = unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    return title, body


def fetch_wechat_article(url: str, *, timeout: float = 25.0) -> Dict[str, Any]:
    """抓取微信公众号文章。"""
    link = (url or "").strip()
    try:
        html = _fetch_link_html(link, timeout=min(timeout, 20.0))
        if not html:
            return {"ok": False, "text": "", "title": "", "source": "wechat", "error": "HTTP 抓取为空"}
        title, body = _extract_wechat_body(html)
        if len(body) < _MIN_ARTICLE_CHARS:
            return {
                "ok": False,
                "text": body,
                "title": title,
                "source": "wechat",
                "char_len": len(body),
                "error": f"微信公众号正文过短（{len(body)} 字）",
            }
        return {
            "ok": True,
            "text": body[:120000],
            "title": title,
            "source": "wechat_js_content",
            "char_len": len(body),
            "error": "",
        }
    except Exception as ex:
        _log.warning(
            "[链接沉淀文档-网页抓取|web_article_fetch.fetch_wechat_article|wechat|工具执行|失败] "
            "抓取异常; error_message=%s",
            ex,
        )
        return {"ok": False, "text": "", "title": "", "source": "wechat", "error": str(ex)[:300]}


def fetch_web_article(url: str, *, timeout: float = 25.0) -> Dict[str, Any]:
    """按 URL 类型分发网页文章抓取。"""
    link = (url or "").strip()
    if is_wechat_article_url(link):
        return fetch_wechat_article(link, timeout=timeout)

    try:
        import requests

        resp = requests.get(link, headers=_link_fetch_headers(link), timeout=timeout)
        if resp.status_code != 200:
            return {
                "ok": False,
                "text": "",
                "title": "",
                "source": "generic",
                "error": f"HTTP {resp.status_code}",
            }
        html = resp.text or ""
        text = _plain_from_html(html)
        title = ""
        try:
            from bs4 import BeautifulSoup

            t = BeautifulSoup(html, "html.parser").find("title")
            title = (t.get_text(strip=True) if t else "").strip()
        except ImportError:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            if m:
                title = unescape(m.group(1)).strip()
        if len(text) < _MIN_ARTICLE_CHARS:
            return {
                "ok": False,
                "text": text,
                "title": title,
                "source": "generic",
                "char_len": len(text),
                "error": f"网页正文过短（{len(text)} 字）",
            }
        return {
            "ok": True,
            "text": text[:120000],
            "title": title,
            "source": "generic_html",
            "char_len": len(text),
            "error": "",
        }
    except Exception as ex:
        return {"ok": False, "text": "", "title": "", "source": "generic", "error": str(ex)[:300]}
