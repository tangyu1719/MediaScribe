"""规范化链接并生成稳定 url_hash（与落盘文件名、队列判重对齐）。"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 不参与判重的追踪/签名参数（同一条笔记 token 会变）
_TRACKING_QUERY_KEYS = frozenset({
    "xsec_token", "xsec_source", "share_id", "share_channel", "share_from_user_hidden",
    "app_platform", "app_version", "ignoreengage", "author_share", "xhsshare", "shareredid",
    "apptime", "timestamp", "source", "from", "referrer", "utm_source", "utm_medium",
    "utm_campaign", "spm", "sec_uid", "mid", "iid", "did", "vid", "callback", "_t", "t",
    "share_from", "share_to", "enter_from", "open_source",
})

# 需要参与「字段级宽松匹配」的标准字段。即使不进入稳定 hash，也要保留给搜索/路由。
_STANDARD_QUERY_KEYS = frozenset({
    "xsec_token", "xsec_source", "share_id", "share_channel", "share_from_user_hidden",
    "app_platform", "app_version", "ignoreengage", "author_share", "xhsshare", "shareredid",
    "apptime", "timestamp", "source", "from", "referrer", "utm_source", "utm_medium",
    "utm_campaign", "spm", "sec_uid", "mid", "iid", "did", "vid", "callback", "_t", "t",
    "share_from", "share_to", "enter_from", "open_source", "xhs_token", "token",
})


def extract_link_fields(raw: str) -> dict[str, str]:
    """提取链接中的标准字段，供更宽松的字段级匹配与路由使用。"""
    u = coerce_pasted_link(raw)
    if not u:
        return {}
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    try:
        p = urlparse(u)
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for k, v in parse_qsl(p.query, keep_blank_values=False):
        key = (k or "").strip().lower()
        if key and key in _STANDARD_QUERY_KEYS and v:
            out[key] = v.strip()
    return out


def _canonical_path(netloc: str, path: str) -> str:
    """按平台提取内容主键路径，忽略追踪 query。"""
    netloc = (netloc or "").lower()
    path = path or "/"
    if "xiaohongshu.com" in netloc or "xhslink.com" in netloc:
        m = re.search(r"/(?:explore|discovery/item)/([a-f0-9]{18,32})", path, re.I)
        if m:
            return f"/explore/{m.group(1).lower()}"
    if "douyin.com" in netloc or "iesdouyin.com" in netloc:
        m = re.search(r"/(?:video|note|article)/(\d+)", path, re.I)
        if m:
            return f"/video/{m.group(1)}"
    if "bilibili.com" in netloc or "b23.tv" in netloc:
        m = re.search(r"/video/(BV[\w]+)", path, re.I)
        if m:
            return f"/video/{m.group(1).upper()}"
        m2 = re.search(r"av(\d+)", path, re.I)
        if m2:
            return f"/video/av{m2.group(1)}"
    return path.rstrip("/") or "/"


def coerce_pasted_link(raw: str) -> str:
    """
    从分享口令/整段粘贴文本中提取首个可用 http(s) 链接。
    抖音/小红书复制文案常夹带标题、口令与表情，不可整段当作 URL。
    """
    from .link_doc_routing import extract_http_urls

    text = (raw or "").strip()
    if not text:
        return ""
    urls = extract_http_urls(text)
    if urls:
        return urls[0]
    # 无 scheme 的常见主站/短链
    m = re.search(
        r"(?:"
        r"v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com|"
        r"www\.xiaohongshu\.com|xhslink\.com|"
        r"www\.bilibili\.com|m\.bilibili\.com|b23\.tv|"
        r"mp\.weixin\.qq\.com"
        r")/[^\s\]\)\"'<>，。；!?#]+",
        text,
        re.I,
    )
    if m:
        return "https://" + m.group(0).rstrip(".,;:!?）)]}")
    line = text.splitlines()[0].strip()
    if re.match(r"^https?://", line, re.I) and " " not in line:
        return line.rstrip(".,;:!?）)]}")
    return ""


def normalize_link_for_hash(raw: str) -> str:
    """
    将用户粘贴的 URL 规范为用于判重/哈希的「稳定形态」。
    同一条小红书/抖音/B 站内容，即使 xsec_token 等不同，也应得到相同 hash。
    """
    u = coerce_pasted_link(raw)
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    try:
        p = urlparse(u)
    except ValueError:
        return u.strip()
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    if not netloc:
        return u.strip()
    path = _canonical_path(netloc, p.path or "")
    q_pairs = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=False)
        if (k or "").lower() not in _TRACKING_QUERY_KEYS
    ]
    q_pairs.sort(key=lambda x: x[0].lower())
    query = urlencode(q_pairs)
    try:
        return urlunparse((scheme, netloc, path, "", query, ""))
    except ValueError:
        return u.strip()


def url_hash(link: str, n: int = 8) -> str:
    norm = normalize_link_for_hash(link)
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:n]


def links_same_identity(a: str, b: str) -> bool:
    """两条链接是否指向同一内容（用于历史/队列判重）。"""
    if not a or not b:
        return False
    if a.strip() == b.strip():
        return True
    return url_hash(a) == url_hash(b)
