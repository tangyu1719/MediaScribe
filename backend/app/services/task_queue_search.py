"""链接文档化队列 — 标题/作者/链接字段筛选与近义词扩展。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qsl

from .follow_up_search import expand_search_terms, _normalize_token
from .task_source_meta import enrich_task_source_fields
from .link_hash import normalize_link_for_hash, extract_link_fields


def _task_title_blob(task: Dict[str, Any]) -> str:
    parts = [
        str(task.get("link_title") or ""),
        str(task.get("doc_title") or ""),
        str(task.get("task_note") or ""),
        str(task.get("task_keywords") or ""),
    ]
    meta = task.get("extracted_metadata") if isinstance(task.get("extracted_metadata"), dict) else {}
    for k in ("keyword1", "keyword2", "domain", "module", "tags"):
        v = meta.get(k)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    return " ".join(p for p in parts if p).strip()


def _link_blob(task: Dict[str, Any]) -> str:
    link = str(task.get("link") or task.get("canonical_url") or "").strip()
    normalized = normalize_link_for_hash(link) if link else ""
    fields = extract_link_fields(link) if link else {}
    parts = [link, normalized]
    if fields:
        parts.extend(f"{k}={v}" for k, v in sorted(fields.items()))
    return " ".join(p for p in parts if p).strip()


def _match_link_terms(blob: str, query: str) -> bool:
    if not (query or "").strip():
        return True
    terms = expand_search_terms(query)
    if not terms:
        return True
    blob_low = (blob or "").lower()
    blob_compact = re.sub(r"[\s_\-·、，。！？；：（）()【】/\\|]+", "", blob_low)
    for term in terms:
        t = (term or "").lower()
        tn = _normalize_token(term)
        if not t:
            continue
        if t in blob_low or (tn and tn in blob_compact):
            return True
    return False


def _blob_match(blob: str, terms: List[str]) -> bool:
    if not terms:
        return True
    blob_low = (blob or "").lower()
    blob_compact = re.sub(r"[\s_\-·、，。！？；：（）()【】/\\|]+", "", blob_low)
    for term in terms:
        t = (term or "").lower()
        tn = _normalize_token(term)
        if not t:
            continue
        if t in blob_low or (tn and tn in blob_compact):
            return True
    return False


def match_task_title(task: Dict[str, Any], query: str) -> bool:
    terms = expand_search_terms(query)
    if not terms:
        return True
    return _blob_match(_task_title_blob(task), terms)


def match_task_link(task: Dict[str, Any], query: str) -> bool:
    if not (query or "").strip():
        return True
    blob = _link_blob(task)
    if _match_link_terms(blob, query):
        return True
    # 字段级宽松匹配：query 中的 xsec_token / token / sec_uid 等标准字段可直接命中。
    fields = extract_link_fields(query)
    if not fields:
        return False
    link_fields = extract_link_fields(str(task.get("link") or task.get("canonical_url") or ""))
    for k, v in fields.items():
        if not v:
            continue
        tv = str(link_fields.get(k) or "").strip().lower()
        if tv and tv == v.strip().lower():
            return True
    return False


def match_task_author(task: Dict[str, Any], query: str) -> bool:
    terms = expand_search_terms(query)
    if not terms:
        return True
    row = enrich_task_source_fields(task)
    blob = " ".join(
        filter(
            None,
            [
                str(row.get("author_name") or ""),
                str(row.get("author_id") or ""),
            ],
        )
    )
    return _blob_match(blob, terms)


def match_task_read_status(task: Dict[str, Any], read_filter: str) -> bool:
    rf = (read_filter or "all").strip().lower()
    if rf == "all":
        return True
    st = str(task.get("status") or "").lower()
    rs = str(task.get("read_status") or ("unread" if st == "completed" else "")).lower()
    if rf == "unread":
        return st == "completed" and rs != "read"
    if rf == "read":
        return st == "completed" and rs == "read"
    return True


def match_task_sources(task: Dict[str, Any], sources: Optional[List[str]]) -> bool:
    if not sources:
        return True
    row = enrich_task_source_fields(task)
    src = str(row.get("import_source") or "other").strip() or "other"
    allowed = {str(s).strip() for s in sources if str(s).strip()}
    if not allowed:
        return True
    if src in allowed:
        return True
    if "other" in allowed and src not in {
        "manual",
        "subscription_creator",
        "subscription_favorites",
        "chat",
        "catalog_seed",
        "rss",
        "link_scan",
    }:
        return True
    return False


def sort_tasks(
    tasks: List[Dict[str, Any]],
    *,
    sort: str = "default",
) -> List[Dict[str, Any]]:
    mode = (sort or "default").strip().lower()

    def _key_default(t: Dict[str, Any]):
        return (
            -(int(t.get("queue_seq") or t.get("priority") or 0)),
            str(t.get("updated_at") or t.get("created_at") or ""),
        )

    def _key_updated(t: Dict[str, Any]):
        return str(t.get("updated_at") or t.get("created_at") or "")

    def _key_importance(t: Dict[str, Any]):
        imp = int(t.get("importance") or 5)
        return (-imp, -(int(t.get("queue_seq") or 0)), _key_updated(t))

    if mode == "updated":
        return sorted(tasks, key=_key_updated, reverse=True)
    if mode == "importance":
        return sorted(tasks, key=_key_importance)
    return sorted(tasks, key=_key_default, reverse=True)


def filter_tasks(
    tasks: List[Dict[str, Any]],
    *,
    title_query: str = "",
    author_query: str = "",
    read_filter: str = "all",
    sources: Optional[List[str]] = None,
    enable_title: bool = True,
    enable_link: bool = True,
    enable_author: bool = False,
    enable_read: bool = False,
    enable_source: bool = False,
    sort: str = "default",
) -> Dict[str, Any]:
    rows = [enrich_task_source_fields(dict(t)) for t in (tasks or [])]
    terms = expand_search_terms(title_query) if enable_title and (title_query or "").strip() else []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if enable_title and terms and not (match_task_title(row, title_query) or (enable_link and match_task_link(row, title_query))):
            continue
        if enable_author and (author_query or "").strip() and not match_task_author(row, author_query):
            continue
        if enable_read and not match_task_read_status(row, read_filter):
            continue
        if enable_source and not match_task_sources(row, sources):
            continue
        if terms:
            row = dict(row)
            row["matched_terms"] = terms[:12]
        out.append(row)
    out = sort_tasks(out, sort=sort)
    return {
        "items": out,
        "total": len(out),
        "expanded_terms": terms,
    }


def collect_author_facets(tasks: List[Dict[str, Any]], *, limit: int = 24) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for raw in tasks or []:
        row = enrich_task_source_fields(dict(raw))
        name = str(row.get("author_name") or "").strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    items = [{"author_name": k, "count": v} for k, v in counts.items()]
    items.sort(key=lambda x: (-x["count"], x["author_name"]))
    return items[: max(1, int(limit or 24))]
