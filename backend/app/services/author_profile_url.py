"""各平台作者主页 URL 构建（基于 pipeline 已提取的 author_id / 链接参数）。"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

_SUPPORTED_PLATFORMS = frozenset({"小红书", "抖音", "B站", "微信", "微信公众号"})


def detect_platform_from_link(link: str, platform: str = "") -> str:
    if platform:
        p = platform.strip()
        if p in ("微信公众号", "wechat", "weixin"):
            return "微信"
        return p
    low = (link or "").lower()
    if "xiaohongshu.com" in low or "xhslink.com" in low:
        return "小红书"
    if "douyin.com" in low or "iesdouyin" in low:
        return "抖音"
    if "bilibili.com" in low or "b23.tv" in low:
        return "B站"
    if "mp.weixin.qq.com" in low:
        return "微信"
    return ""


def _weixin_biz_from_link(link: str) -> str:
    if not link:
        return ""
    try:
        qs = parse_qs(urlparse(link).query)
        biz = (qs.get("__biz") or [""])[0]
        return str(biz or "").strip()
    except Exception:
        return ""


def build_author_profile_url(
    *,
    platform: str = "",
    author_id: str = "",
    author_name: str = "",
    link: str = "",
) -> str:
    """
    根据平台与 author_id 生成作者主页链接。
    无可靠 ID 时返回空字符串（前端仅展示用户名文本）。
    """
    plat = detect_platform_from_link(link, platform)
    aid = (author_id or "").strip()
    if not plat:
        return ""

    if plat == "小红书":
        if aid and re.fullmatch(r"[0-9a-fA-F]{16,32}", aid):
            return f"https://www.xiaohongshu.com/user/profile/{aid}"
        return ""

    if plat == "抖音":
        if aid and (aid.startswith("MS4w") or len(aid) >= 20):
            return f"https://www.douyin.com/user/{aid}"
        if aid and aid.isdigit():
            return f"https://www.douyin.com/user/{aid}"
        return ""

    if plat == "B站":
        mid = aid
        if mid and mid.isdigit():
            return f"https://space.bilibili.com/{mid}"
        m = re.search(r"space\.bilibili\.com/(\d+)", link or "", re.I)
        if m:
            return f"https://space.bilibili.com/{m.group(1)}"
        return ""

    if plat == "微信":
        biz = aid if aid.startswith("Mz") else _weixin_biz_from_link(link)
        if biz:
            return f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz={biz}#wechat_redirect"
        return ""

    _ = author_name
    return ""


def enrich_author_profile_url(task: dict) -> dict:
    """为任务 dict 补充 author_profile_url 字段。"""
    if not task or not isinstance(task, dict):
        return task
    row = dict(task)
    if not str(row.get("author_profile_url") or "").strip():
        row["author_profile_url"] = build_author_profile_url(
            platform=str(row.get("platform") or ""),
            author_id=str(row.get("author_id") or ""),
            author_name=str(row.get("author_name") or ""),
            link=str(row.get("link") or ""),
        )
    return row
