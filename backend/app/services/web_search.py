"""联网搜索：多源回退，默认 cn.bing.com（国内可访问），不逐条抓取目标页。"""
from __future__ import annotations

import json
import logging
import re
import time
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

_LOG = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_BING_HOSTS = (
    "https://cn.bing.com/search",
    "https://www.bing.com/search",
)
_DDG_URL = "https://html.duckduckgo.com/html/?q="


def _load_cfg() -> Dict[str, Any]:
    base = Path(__file__).resolve().parents[2]
    for cp in [base / "config.json", base.parent / "src" / "agent" / "config.json"]:
        if cp.exists():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _clean_html_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_html(url: str, *, timeout: float, headers: Optional[Dict[str, str]] = None) -> str:
    hdr = {"User-Agent": _DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if headers:
        hdr.update(headers)
    req = Request(url, headers=hdr)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _is_blocked_href(href: str) -> bool:
    if not href or not href.startswith("http"):
        return True
    host = (urlparse(href).netloc or "").lower()
    blocked = (
        "bing.com", "microsoft.com", "duckduckgo.com",
        "go.microsoft.com", "javascript:", "about:blank",
    )
    return any(b in host for b in blocked)


def parse_bing_html(html: str, *, max_results: int = 5) -> List[Dict[str, str]]:
    """从 Bing 搜索结果页解析条目（可单测）。"""
    items: List[Dict[str, str]] = []
    blocks = re.findall(
        r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>([\s\S]*?)</li>',
        html,
        flags=re.I,
    )
    for block in blocks:
        am = re.search(r'<a[^>]+href="([^"]+)"', block, flags=re.I)
        if not am:
            continue
        href = unescape(am.group(1).strip())
        if _is_blocked_href(href):
            continue
        tm = re.search(r'<a[^>]*>([\s\S]*?)</a>', block, flags=re.I)
        title = _clean_html_text(tm.group(1)) if tm else ""
        sm = re.search(r'<p[^>]*>([\s\S]*?)</p>', block, flags=re.I)
        snippet = _clean_html_text(sm.group(1)) if sm else ""
        if not title and not snippet:
            continue
        items.append({
            "title": title[:200] or href[:80],
            "url": href,
            "snippet": snippet[:400],
        })
        if len(items) >= max_results:
            break
    return items


def parse_duckduckgo_html(html: str, *, max_results: int = 5) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>[\s\S]*?)</a>',
        html,
        flags=re.I,
    ):
        href = unescape(match.group("href"))
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query)
            href = unescape(qs.get("uddg", [href])[0])
        if _is_blocked_href(href):
            continue
        title = _clean_html_text(match.group("title"))
        sn = re.search(
            r'<a[^>]+class="result__a"[^>]+href="' + re.escape(match.group("href"))
            + r'"[\s\S]*?</a>\s*<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)</a>',
            html,
            flags=re.I,
        )
        snippet = _clean_html_text(sn.group(1)) if sn else ""
        items.append({"title": title[:200], "url": href, "snippet": snippet[:400]})
        if len(items) >= max_results:
            break
    return items


def _search_bing(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    last_err = ""
    for host in _BING_HOSTS:
        url = f"{host}?q={quote_plus(query)}"
        try:
            html = _fetch_html(url, timeout=timeout)
            items = parse_bing_html(html, max_results=max_results)
            if items:
                return items
            last_err = f"{host}: 解析到 0 条"
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last_err = f"{host}: {e}"
            _LOG.warning("[联网搜索|web_search._search_bing|%s|工具执行|回退] Bing 失败; error=%s", query[:40], e)
    raise RuntimeError(last_err or "Bing 搜索无结果")


def _search_duckduckgo(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    url = _DDG_URL + quote_plus(query)
    html = _fetch_html(url, timeout=min(timeout, 8.0))
    items = parse_duckduckgo_html(html, max_results=max_results)
    if not items:
        raise RuntimeError("DuckDuckGo 解析到 0 条")
    return items


def web_search(query: str, max_results: int = 5, *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """
    联网搜索统一入口。返回 {query, results:[{title,url,snippet}], provider, error?}。
    至少一种源成功则 error 为空；全部失败则 results=[] 且 error 说明原因。
    """
    q = (query or "").strip()
    if not q:
        return {"query": q, "results": [], "provider": "", "error": "空查询"}

    cfg = _load_cfg()
    tmo = float(timeout if timeout is not None else cfg.get("web_search_timeout_sec", 25))
    max_results = max(1, min(int(max_results or 5), 10))
    errors: List[str] = []

    # 1) Bing（国内优先 cn.bing.com）
    try:
        items = _search_bing(q, max_results, tmo)
        return {"query": q, "results": items, "provider": "bing-html", "error": None}
    except Exception as e:
        errors.append(str(e))

    # 2) DuckDuckGo 回退（境外环境）
    try:
        items = _search_duckduckgo(q, max_results, tmo)
        return {"query": q, "results": items, "provider": "duckduckgo-html", "error": None}
    except Exception as e:
        errors.append(str(e))

    err_msg = "; ".join(errors) if errors else "无可用搜索源"
    _LOG.error(
        "[联网搜索|web_search.web_search|%s|工具执行|失败] 全部源失败; error=%s",
        q[:60],
        err_msg,
    )
    return {"query": q, "results": [], "provider": "", "error": err_msg}


def web_search_for_chat(query: str, max_results: int = 5) -> Dict[str, Any]:
    """供 ai_chat 调用；记录耗时。"""
    t0 = time.perf_counter()
    out = web_search(query, max_results=max_results)
    out["cost_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


def web_search_multi_for_chat(
    search_queries: List[str],
    *,
    max_results_per_query: int = 3,
    objective: str = "",
) -> Dict[str, Any]:
    """
    按关键词拆分多路检索并合并去重（URL 维度）。
    禁止仅用整句口语化 user message 单次搜索。
    """
    t0 = time.perf_counter()
    queries = [_normalize_q(q) for q in (search_queries or []) if _normalize_q(q)]
    if not queries and objective:
        queries = [_normalize_q(objective[:80])]
    if not queries:
        return {
            "objective": objective,
            "search_queries": [],
            "query": "",
            "results": [],
            "provider": "",
            "error": "无有效检索词",
            "per_query": [],
            "cost_ms": 0,
        }

    merged: List[Dict[str, str]] = []
    seen_urls: set[str] = set()
    per_query: List[Dict[str, Any]] = []
    errors: List[str] = []
    provider = ""

    for q in queries[:5]:
        one = web_search(q, max_results=max_results_per_query)
        per_query.append({
            "query": q,
            "results": one.get("results") or [],
            "provider": one.get("provider") or "",
            "error": one.get("error"),
        })
        if one.get("provider") and not provider:
            provider = str(one.get("provider"))
        if one.get("error"):
            errors.append(f"{q[:30]}: {one.get('error')}")
        for item in one.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            merged.append(item)

    err_msg = "; ".join(errors) if errors and not merged else (errors[0] if errors and not merged else None)
    cost_ms = int((time.perf_counter() - t0) * 1000)
    _LOG.info(
        "[联网搜索|web_search.web_search_multi_for_chat|plan|工具执行|完成] "
        "queries=%s; merged=%s; cost_ms=%s",
        len(queries),
        len(merged),
        cost_ms,
    )
    return {
        "objective": objective,
        "search_queries": queries,
        "query": queries[0],
        "results": merged[: max(5, max_results_per_query * len(queries))],
        "provider": provider,
        "error": err_msg,
        "per_query": per_query,
        "cost_ms": cost_ms,
    }


def _normalize_q(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip())
