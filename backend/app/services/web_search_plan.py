"""联网 / RAG 检索词计划：先抽原问关键词，编排段再做业务映射；二者用途分离。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# 口语/虚词，不参与关键词检索
_STOP_FRAGMENTS = (
    "可以找到", "能够", "能否", "请问", "帮我", "一下", "做一个", "分析给我",
    "相关信息", "相关", "信息", "这个小红书", "小红书号", "吗", "呢", "的",
    "是否", "怎么", "什么", "关于", "查询", "搜索", "找到", "好了", "完成", "进度",
    "结果", "怎么样", "怎样", "链接分析",
)

_STATUS_INQUIRY_FRAGMENTS = (
    "好了吗", "完成了吗", "结果呢", "进度", "怎么样了", "搞定了吗", "出来了吗",
    "好了没", "分析好了", "处理好了", "链接分析好了",
    "什么情况", "到底什么情况", "执行到哪", "任务执行",
)

_DOMAIN_HINTS = ("小红书", "小红薯", "笔记", "账号", "B站", "bilibili", "微博", "抖音")


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _strip_conversational(text: str) -> str:
    t = _normalize_space(text)
    for frag in _STOP_FRAGMENTS:
        t = t.replace(frag, " ")
    return _normalize_space(t)


def is_status_inquiry_message(message: str) -> bool:
    m = (message or "").strip()
    if not m:
        return False
    return any(h in m for h in _STATUS_INQUIRY_FRAGMENTS) and len(m) <= 48


def extract_base_keywords(
    original_query: str,
    rewritten_query: str = "",
    *,
    max_tokens: int = 12,
) -> List[str]:
    """仅从原问 + 改写句抽词（编排前/联网专用），不含业务映射检索词。"""
    terms: List[str] = []
    for text in (_normalize_space(original_query), _normalize_space(rewritten_query)):
        if not text:
            continue
        for t in _tokenize_keywords(text):
            if t not in terms:
                terms.append(t)
    return terms[:max_tokens]


def _tokenize_keywords(text: str) -> List[str]:
    """从文本提取可用于搜索引擎的短词组。"""
    t = _strip_conversational(text)
    if not t:
        return []
    parts = re.split(r"[\s,，。；;：:?？!！/|]+", t)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 2:
            continue
        if p.isdigit() and len(p) < 6:
            continue
        if p in out:
            continue
        out.append(p)
    return out[:12]


def _pack_query(tokens: List[str], *, max_words: int = 6) -> str:
    """将 token 压成 3–6 词的检索串。"""
    if not tokens:
        return ""
    words: List[str] = []
    for tok in tokens:
        sub = tok.split()
        for w in sub:
            w = w.strip()
            if not w or w in words:
                continue
            words.append(w)
            if len(words) >= max_words:
                break
        if len(words) >= max_words:
            break
    return " ".join(words[:max_words]) if words else ""


def _queries_from_terms(terms: List[str], *, max_queries: int = 3) -> List[str]:
    queries: List[str] = []
    max_queries = max(1, min(int(max_queries or 3), 5))

    def _push(q: str) -> None:
        q = _normalize_space(q)
        if not q or len(q) < 3:
            return
        if len(q) > 120:
            q = q[:120]
        if q not in queries:
            queries.append(q)

    id_tokens = [t for t in terms if t.isdigit() and 6 <= len(t) <= 16]
    domain_tokens = [t for t in terms if any(h in t for h in _DOMAIN_HINTS)]

    for t in terms[:6]:
        packed = _pack_query(_tokenize_keywords(t) or [t], max_words=6)
        if packed:
            _push(packed)

    if id_tokens:
        dom = domain_tokens[0] if domain_tokens else ""
        if not dom and any("小红书" in x for x in terms):
            dom = "小红书"
        for nid in id_tokens[:2]:
            if dom:
                _push(_pack_query([dom, "账号", nid], max_words=6))
            else:
                _push(nid)

    if not queries and terms:
        _push(_pack_query(terms[:4], max_words=6))

    return queries[:max_queries]


def build_web_search_keyword_queries(
    *,
    original_query: str,
    rewritten_query: str = "",
    max_queries: int = 3,
) -> List[str]:
    """
    联网搜索专用：仅「原问 + 改写句」抽词后的检索串。
    禁止混入编排段 search_keyword_queries / retrieval_hints（业务映射词）。
    """
    original = _normalize_space(original_query)
    rewritten = _normalize_space(rewritten_query)

    # 当前句仅为追问进度时，用主任务原问抽词，避免「好了吗」搜出一堆无关结果
    if is_status_inquiry_message(rewritten) and original and original != rewritten:
        rewritten = original
    elif is_status_inquiry_message(rewritten) and not original:
        return []

    base_terms = extract_base_keywords(original or rewritten, rewritten)
    if not base_terms and rewritten:
        base_terms = _tokenize_keywords(_strip_conversational(rewritten))
    return _queries_from_terms(base_terms, max_queries=max_queries)


def build_rag_search_keyword_queries(
    rewritten_query: str,
    *,
    original_query: str = "",
    slot_snapshot: Optional[Dict[str, Any]] = None,
    enhancement_snapshot: Optional[Dict[str, Any]] = None,
    max_queries: int = 8,
) -> List[str]:
    """
    RAG 专用：业务映射检索词（编排段） + 原问抽取词，合并去重。
    """
    slot = slot_snapshot or {}
    enhance = enhancement_snapshot or {}
    rewritten = _normalize_space(rewritten_query)
    original = _normalize_space(original_query)

    merged: List[str] = []
    for t in extract_base_keywords(original, rewritten):
        if t not in merged:
            merged.append(t)

    for src in (
        enhance.get("search_keyword_queries"),
        enhance.get("retrieval_hints"),
        slot.get("retrieval_terms"),
        slot.get("entities"),
    ):
        if not isinstance(src, list):
            continue
        for item in src:
            s = _normalize_space(str(item))
            if s and s not in merged:
                merged.append(s)

    for t in _tokenize_keywords(rewritten):
        if t not in merged:
            merged.append(t)

    return merged[: max(4, min(int(max_queries or 8), 16))]


def build_rag_retrieve_query(
    *,
    rewritten_query: str,
    original_query: str = "",
    slot_snapshot: Optional[Dict[str, Any]] = None,
    enhancement_snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    """RAG kb_search 用查询串：改写句 + 合并关键词（含业务映射）。"""
    terms = build_rag_search_keyword_queries(
        rewritten_query,
        original_query=original_query,
        slot_snapshot=slot_snapshot,
        enhancement_snapshot=enhancement_snapshot,
    )
    rewritten = _normalize_space(rewritten_query) or _normalize_space(original_query)
    if not terms:
        return rewritten
    # 优先保留改写句语义，关键词作补充
    extra = " ".join(terms[:6])
    if extra and extra not in rewritten:
        return f"{rewritten} {extra}".strip()[:500]
    return rewritten[:500]


# 兼容旧名：编排/RAG 路径默认走业务映射合并
def build_search_keyword_queries(
    rewritten_query: str,
    *,
    slot_snapshot: Optional[Dict[str, Any]] = None,
    enhancement_snapshot: Optional[Dict[str, Any]] = None,
    max_queries: int = 3,
    original_query: str = "",
    for_web_search: bool = False,
) -> List[str]:
    if for_web_search:
        return build_web_search_keyword_queries(
            original_query=original_query or rewritten_query,
            rewritten_query=rewritten_query,
            max_queries=max_queries,
        )
    return build_rag_search_keyword_queries(
        rewritten_query,
        original_query=original_query,
        slot_snapshot=slot_snapshot,
        enhancement_snapshot=enhancement_snapshot,
        max_queries=max_queries,
    )


def build_web_search_plan(
    *,
    rewritten_query: str,
    original_query: str = "",
    slot_snapshot: Optional[Dict[str, Any]] = None,
    enhancement_snapshot: Optional[Dict[str, Any]] = None,
    task_user_query: str = "",
    continue_main: bool = False,
) -> Dict[str, Any]:
    """
    联网搜索计划。
    - 仅使用原问/改写句抽词（忽略 enhancement_snapshot 业务映射词）。
    - 续接主任务且当前为追问时，用 task_user_query 作为原问抽词来源。
    """
    _ = slot_snapshot, enhancement_snapshot  # 联网路径 deliberately 不使用业务映射

    original = _normalize_space(original_query)
    rewritten = _normalize_space(rewritten_query) or original
    task_q = _normalize_space(task_user_query)

    if (continue_main or is_status_inquiry_message(rewritten)) and task_q:
        original = task_q
        if is_status_inquiry_message(rewritten):
            rewritten = task_q

    search_queries = build_web_search_keyword_queries(
        original_query=original,
        rewritten_query=rewritten,
    )
    objective = _strip_conversational(rewritten)[:160] or _strip_conversational(original)[:160]
    skip = bool(is_status_inquiry_message(_normalize_space(original_query)) and not task_q and not search_queries)

    return {
        "objective": objective,
        "rewritten_query": rewritten,
        "original_query": original or original_query,
        "task_user_query": task_q,
        "search_queries": search_queries,
        "primary_query": search_queries[0] if search_queries else objective[:80],
        "used_full_user_message": False,
        "used_business_mapping": False,
        "skip_web_search": skip,
        "keyword_source": "original_and_rewrite_only",
    }


def resolve_web_search_plan_for_tool(
    *,
    tool_query: str,
    tool_search_queries: Optional[List[str]] = None,
    current_message: str = "",
    task_user_query: str = "",
    rewritten_query: str = "",
    continue_main: bool = False,
) -> Dict[str, Any]:
    """
    工具/ReAct 调用 web_search 时统一解析计划（防止 LLM 传入口语追问或编排污染词）。
    """
    if tool_search_queries and not continue_main and not is_status_inquiry_message(current_message):
        qs = [str(x).strip() for x in tool_search_queries if str(x).strip()]
        if qs:
            return {
                "search_queries": qs[:5],
                "primary_query": qs[0],
                "keyword_source": "tool_explicit",
                "skip_web_search": False,
            }

    return build_web_search_plan(
        rewritten_query=rewritten_query or tool_query or current_message,
        original_query=current_message or tool_query,
        task_user_query=task_user_query,
        continue_main=continue_main,
    )
