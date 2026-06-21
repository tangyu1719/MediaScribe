"""关注 UP 列表 — 筛选与近义词扩展（规则引擎，不调用 LLM）。"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Set

# 领域近义词组：查询命中任一词时扩展整组用于匹配
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("agent", "智能体", "ai agent", "agentic", "智能助手", "助手"),
    ("llm", "大模型", "语言模型", "gpt", "chatgpt", "claude", "qwen", "通义"),
    ("java", "后端", "spring", "jvm", "微服务"),
    ("面试", "面经", "求职", "校招", "社招", "hc", "offer"),
    ("编程", "代码", "开发", "coder", "程序员", "码农"),
    ("rag", "检索增强", "知识库", "向量", "embedding", "milvus"),
    ("画像", "人设", "ip", "定位", "垂类"),
    ("小红书", "xhs", "薯", "笔记", "博主", "up"),
    ("ai", "人工智能", "aigc", "机器学习", "深度学习"),
    ("产品", "pm", "产品经理", "需求"),
    ("运营", "增长", "流量", "爆款"),
    ("设计", "ui", "ux", "交互"),
)


def _normalize_token(tok: str) -> str:
    t = (tok or "").strip().lower()
    t = re.sub(r"[\s_\-·、，。！？；：（）()【】/\\|]+", "", t)
    return t


def _tokenize_query(query: str) -> List[str]:
    raw = (query or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\s,，;；/|+]+", raw)
    out: List[str] = []
    seen: Set[str] = set()
    for p in parts:
        n = _normalize_token(p)
        if len(n) < 1:
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    if not out and raw:
        n = _normalize_token(raw)
        if n:
            out.append(n)
    return out


def expand_search_terms(query: str) -> List[str]:
    """将用户查询扩展为近义词集合（用于 OR 匹配）。"""
    seeds = _tokenize_query(query)
    if not seeds:
        return []
    expanded: List[str] = []
    seen: Set[str] = set()
    for seed in seeds:
        if seed not in seen:
            seen.add(seed)
            expanded.append(seed)
        for group in _SYNONYM_GROUPS:
            group_norm = {_normalize_token(g) for g in group}
            if seed in group_norm:
                for g in group:
                    gn = _normalize_token(g)
                    if gn and gn not in seen:
                        seen.add(gn)
                        expanded.append(gn)
    return expanded


def build_search_blob(
    *,
    display_name: str = "",
    sample_titles: Iterable[str] = (),
    source: str = "",
    creator_id: str = "",
) -> str:
    """拼接可检索文本（展示名 + 样例标题 + 来源）。"""
    chunks = [
        (display_name or "").strip(),
        (source or "").strip(),
        (creator_id or "").strip(),
    ]
    for t in sample_titles or []:
        s = str(t or "").strip()
        if s:
            chunks.append(s)
    return " ".join(chunks)


def match_follow_up(row: Dict[str, Any], terms: List[str]) -> bool:
    """任一扩展词命中 search_blob / display_name / sample_titles 即保留。"""
    if not terms:
        return True
    blob = str(row.get("search_blob") or "")
    if not blob:
        blob = build_search_blob(
            display_name=str(row.get("display_name") or ""),
            sample_titles=row.get("sample_titles") or [],
            source=str(row.get("source") or ""),
            creator_id=str(row.get("creator_id") or ""),
        )
    blob_low = blob.lower()
    blob_compact = re.sub(r"[\s_\-·、，。！？；：（）()【】/\\|]+", "", blob_low)
    for term in terms:
        t = (term or "").lower()
        tn = _normalize_token(term)
        if not t:
            continue
        if t in blob_low or (tn and tn in blob_compact):
            return True
    return False


def filter_follow_ups(
    items: List[Dict[str, Any]],
    *,
    query: str = "",
    subscribed: str = "all",
) -> List[Dict[str, Any]]:
    """内存筛选：近义词查询 + 是否已订阅。"""
    terms = expand_search_terms(query)
    sub_filter = (subscribed or "all").strip().lower()
    out: List[Dict[str, Any]] = []
    for row in items:
        if sub_filter == "yes" and not row.get("already_subscribed"):
            continue
        if sub_filter == "no" and row.get("already_subscribed"):
            continue
        if not match_follow_up(row, terms):
            continue
        row = dict(row)
        if terms:
            row["matched_terms"] = terms[:12]
        out.append(row)
    return out
