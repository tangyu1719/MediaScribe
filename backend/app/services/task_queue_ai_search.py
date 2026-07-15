"""链接文档化队列 — AI 意图解析 + 多字段/正文 GREP 检索。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field, field_validator

from .follow_up_search import expand_search_terms, _normalize_token
from .task_queue_search import match_task_read_status, sort_tasks
from .task_source_meta import enrich_task_source_fields

_log = logging.getLogger("sba.task_queue_ai_search")

_CONTENT_MAX_BYTES = 512 * 1024
_FILLER_RE = re.compile(
    r"(帮我|请|麻烦|查询|查一下|搜索|检索|找一下|筛选|过滤|含有|包含|里有|中有|"
    r"的$|^的|一下|看看|有哪些|是什么|什么|哪些|任务|卡片|队列|链接|笔记|文章)"
)
_FIELD_HINTS: Tuple[Tuple[re.Pattern[str], Tuple[str, ...]], ...] = (
    (re.compile(r"备注|备忘|note|批注", re.I), ("task_note",)),
    (re.compile(r"关键词|keyword|关键字", re.I), ("task_keywords",)),
    (re.compile(r"标题|题目|title|名称", re.I), ("link_title", "doc_title")),
    (re.compile(r"链接|url|link|网址", re.I), ("link", "canonical_url")),
    (re.compile(r"作者|author|博主|up主|up\b", re.I), ("author_name", "author_id")),
    (re.compile(r"正文|内容|文档|全文|md|markdown|长页|产物", re.I), ("content",)),
    (re.compile(r"元数据|metadata|标签|tag|领域|模块", re.I), ("extracted_metadata",)),
)
_READ_HINTS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:仅|只要|只看)?未读(?:的|状态|任务)?"), "unread"),
    (re.compile(r"(?:仅|只要|只看)?已读(?:的|状态|任务)?"), "read"),
)


class TextClause(BaseModel):
    fields: List[str] = Field(default_factory=lambda: ["all"])
    terms: List[str] = Field(default_factory=list)
    mode: str = "any"

    @field_validator("fields", "terms", mode="before")
    @classmethod
    def _as_str_list(cls, v: Any) -> List[str]:
        if not v:
            return []
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return [str(x).strip() for x in v if str(x).strip()]


class AiSearchPlan(BaseModel):
    text_clauses: List[TextClause] = Field(default_factory=list)
    read_status: str = "all"
    authors: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    sort: str = "default"
    intent_hint: str = ""
    llm_powered: bool = False


@dataclass
class GrepHit:
    field: str
    term: str
    snippet: str = ""


def _blob_match(blob: str, terms: Sequence[str]) -> bool:
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


def _first_match_snippet(blob: str, term: str, *, width: int = 48) -> str:
    if not blob or not term:
        return ""
    low = blob.lower()
    t = term.lower()
    idx = low.find(t)
    if idx < 0:
        tn = _normalize_token(term)
        compact = re.sub(r"[\s_\-·、，。！？；：（）()【】/\\|]+", "", low)
        if tn:
            idx = compact.find(tn)
            if idx >= 0:
                return f"…{tn}…"
        return ""
    start = max(0, idx - width // 3)
    end = min(len(blob), idx + len(term) + width // 2)
    seg = blob[start:end].replace("\n", " ").strip()
    if start > 0:
        seg = "…" + seg
    if end < len(blob):
        seg = seg + "…"
    return seg


def _task_field_blob(task: Dict[str, Any], field_name: str) -> str:
    if field_name == "content":
        return _read_task_md_content(task)
    if field_name in ("link", "canonical_url"):
        return " ".join(
            filter(
                None,
                [
                    str(task.get("link") or ""),
                    str(task.get("canonical_url") or ""),
                ],
            )
        )
    if field_name in ("link_title", "doc_title", "task_note", "task_keywords", "author_name", "author_id"):
        return str(task.get(field_name) or "")
    if field_name == "extracted_metadata":
        meta = task.get("extracted_metadata") if isinstance(task.get("extracted_metadata"), dict) else {}
        parts: List[str] = []
        for k, v in meta.items():
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            elif v:
                parts.append(f"{k}:{v}")
        return " ".join(parts)
    if field_name == "all":
        parts = [
            str(task.get("link_title") or ""),
            str(task.get("doc_title") or ""),
            str(task.get("task_note") or ""),
            str(task.get("task_keywords") or ""),
            str(task.get("link") or ""),
            str(task.get("canonical_url") or ""),
            str(task.get("author_name") or ""),
        ]
        meta = task.get("extracted_metadata") if isinstance(task.get("extracted_metadata"), dict) else {}
        for k in ("keyword1", "keyword2", "domain", "module", "tags"):
            v = meta.get(k)
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            elif v:
                parts.append(str(v))
        return " ".join(p for p in parts if p)
    return str(task.get(field_name) or "")


def _read_task_md_content(task: Dict[str, Any]) -> str:
    md_ref = str(task.get("doc_path") or task.get("doc_filename") or "").strip()
    if not md_ref:
        return ""
    try:
        from .history_manager import _resolve_doc_path

        path = _resolve_doc_path(md_ref)
        if not path.is_file():
            return ""
        raw = path.read_bytes()
        if len(raw) > _CONTENT_MAX_BYTES:
            raw = raw[:_CONTENT_MAX_BYTES]
        return raw.decode("utf-8", errors="ignore")
    except Exception as exc:
        _log.debug(
            "[链接沉淀-AI检索|task_queue_ai_search._read_task_md_content|doc|硬编执行|跳过] "
            "read_failed; task_id=%s; error_type=%s",
            task.get("task_id"),
            type(exc).__name__,
        )
        return ""


def _detect_read_status(query: str) -> str:
    q = (query or "").strip()
    for pat, status in _READ_HINTS:
        if pat.search(q):
            return status
    return "all"


def _detect_field_hints(query: str) -> Set[str]:
    q = (query or "").strip()
    found: Set[str] = set()
    for pat, fields in _FIELD_HINTS:
        if pat.search(q):
            found.update(fields)
    return found


def _strip_intent_noise(query: str) -> str:
    q = (query or "").strip()
    for pat, _fields in _FIELD_HINTS:
        q = pat.sub(" ", q)
    for pat, _st in _READ_HINTS:
        q = pat.sub(" ", q)
    q = _FILLER_RE.sub(" ", q)
    q = re.sub(r"[，。！？；：、,.!?;:\s]+", " ", q).strip()
    return q


def _query_for_terms(query: str) -> str:
    q = (query or "").strip()
    for pat, _ in _READ_HINTS:
        q = pat.sub(" ", q)
    return q.strip()


def _extract_terms(query: str) -> List[str]:
    core = _strip_intent_noise(_query_for_terms(query))
    if not core:
        return []
    terms = expand_search_terms(core)
    if not terms and core:
        parts = [p.strip() for p in re.split(r"[\s/|]+", core) if p.strip()]
        terms = parts or [core]
    seen: Set[str] = set()
    out: List[str] = []
    for t in terms:
        k = str(t or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out[:12]


def parse_search_intent_rules(query: str) -> AiSearchPlan:
    q = (query or "").strip()
    read_status = _detect_read_status(q)
    field_hints = _detect_field_hints(q)
    terms = _extract_terms(q)
    fields = sorted(field_hints) if field_hints else ["all"]
    clauses: List[TextClause] = []
    if terms:
        clauses.append(TextClause(fields=fields, terms=terms, mode="any"))
    hint_parts: List[str] = []
    if read_status != "all":
        hint_parts.append("已读状态=" + ("未读" if read_status == "unread" else "已读"))
    if field_hints:
        hint_parts.append("字段=" + "/".join(sorted(field_hints)))
    if terms:
        hint_parts.append("词=" + "、".join(terms[:6]))
    return AiSearchPlan(
        text_clauses=clauses,
        read_status=read_status,
        intent_hint="；".join(hint_parts) or q[:80],
        llm_powered=False,
    )


def _merge_llm_plan(base: AiSearchPlan, llm: Dict[str, Any]) -> AiSearchPlan:
    if not llm or not llm.get("llm_powered"):
        return base
    data = llm.get("plan") if isinstance(llm.get("plan"), dict) else llm
    if not isinstance(data, dict):
        return base
    try:
        merged = AiSearchPlan.model_validate(
            {
                "text_clauses": data.get("text_clauses") or [c.model_dump() for c in base.text_clauses],
                "read_status": data.get("read_status") or base.read_status,
                "authors": data.get("authors") or base.authors,
                "sources": data.get("sources") or base.sources,
                "sort": data.get("sort") or base.sort,
                "intent_hint": data.get("intent_hint") or base.intent_hint,
                "llm_powered": True,
            }
        )
        if not merged.text_clauses and base.text_clauses:
            merged.text_clauses = base.text_clauses
        return merged
    except Exception:
        out = base.model_copy(deep=True)
        out.llm_powered = True
        if data.get("intent_hint"):
            out.intent_hint = str(data["intent_hint"])
        rs = str(data.get("read_status") or "").strip().lower()
        if rs in ("unread", "read", "all"):
            out.read_status = rs
        return out


def parse_search_intent_llm(query: str) -> Dict[str, Any]:
    from .ai_search_sdk.llm import expand_query_llm

    q = (query or "").strip()
    if not q:
        return {"llm_powered": False, "plan": {}}
    prompt_extra = (
        "此外请输出 search_plan JSON 字段（与 expanded_terms 同级）："
        '{"text_clauses":[{"fields":["task_note|all|content|link_title|task_keywords|author_name"],"terms":["..."],"mode":"any"}],'
        '"read_status":"all|unread|read","authors":[],"sources":[],"sort":"default|updated|importance","intent_hint":"..."}。'
        "text_clauses 至少 1 条；fields 可多选；用户若说备注/正文/标题/链接/作者/未读/已读须映射到对应字段或 read_status。"
    )
    # 复用 expand 链路：先走专用 prompt
    try:
        from .ai_search_sdk.llm import (
            AiSearchExpandOutput,
            _invoke_openai_chat,
            build_ai_search_expand_prompt,
        )
        from .ai_search_sdk.ollama_config import resolve_ai_search_llm_nodes
        from .ai_search_sdk.structured_json import loads_json, validate_model

        prompt = build_ai_search_expand_prompt(q, domain_hint="链接文档化任务队列") + "\n" + prompt_extra
        for node in resolve_ai_search_llm_nodes():
            raw = _invoke_openai_chat(node, prompt, max_tokens=480)
            if not raw:
                continue
            data = loads_json(raw, kind="object")
            if not isinstance(data, dict):
                continue
            plan_raw = data.get("search_plan") if isinstance(data.get("search_plan"), dict) else data
            plan: Dict[str, Any] = {}
            if isinstance(plan_raw, dict):
                tc = plan_raw.get("text_clauses")
                if isinstance(tc, list) and tc:
                    plan["text_clauses"] = tc
                for k in ("read_status", "authors", "sources", "sort", "intent_hint"):
                    if plan_raw.get(k) not in (None, "", []):
                        plan[k] = plan_raw.get(k)
            expanded = data.get("expanded_terms")
            if isinstance(expanded, list) and expanded and not plan.get("text_clauses"):
                plan["text_clauses"] = [{"fields": ["all"], "terms": expanded, "mode": "any"}]
            validated = validate_model(data, AiSearchExpandOutput) if data else None
            hint = (validated.intent_hint if validated else "") or str(plan.get("intent_hint") or "")
            return {
                "llm_powered": True,
                "node_id": node.id,
                "plan": plan,
                "intent_hint": hint,
                "expanded_terms": (validated.expanded_terms if validated else expanded) or [],
            }
    except Exception as exc:
        _log.warning(
            "[链接沉淀-AI检索|task_queue_ai_search.parse_search_intent_llm|plan|Agent执行|失败] "
            "error_type=%s; error_message=%s",
            type(exc).__name__,
            str(exc)[:160],
        )
    meta = expand_query_llm(q, domain_hint="链接文档化任务队列")
    terms = list(meta.get("expanded_terms") or [])
    plan: Dict[str, Any] = {}
    if terms:
        plan["text_clauses"] = [{"fields": ["all"], "terms": terms, "mode": "any"}]
    if meta.get("intent_hint"):
        plan["intent_hint"] = meta["intent_hint"]
    return {
        "llm_powered": bool(meta.get("llm_powered")),
        "node_id": meta.get("node_id") or "",
        "plan": plan,
        "intent_hint": meta.get("intent_hint") or "",
        "expanded_terms": terms,
    }


def _clause_match(task: Dict[str, Any], clause: TextClause) -> List[GrepHit]:
    terms = [str(t).strip() for t in (clause.terms or []) if str(t).strip()]
    if not terms:
        return []
    fields = [str(f).strip() for f in (clause.fields or ["all"]) if str(f).strip()] or ["all"]
    hits: List[GrepHit] = []
    for fname in fields:
        blob = _task_field_blob(task, fname)
        if not blob:
            continue
        for term in terms:
            if _blob_match(blob, [term]):
                label = "MD正文" if fname == "content" else fname
                hits.append(GrepHit(field=label, term=term, snippet=_first_match_snippet(blob, term)))
                if clause.mode != "all":
                    return hits
    if clause.mode == "all":
        matched_terms = {h.term for h in hits}
        if matched_terms >= set(terms):
            return hits
        return []
    return hits


def grep_task(task: Dict[str, Any], plan: AiSearchPlan) -> List[GrepHit]:
    clauses = plan.text_clauses or []
    if not clauses:
        return [GrepHit(field="*", term="", snippet="")]
    all_hits: List[GrepHit] = []
    for clause in clauses:
        hits = _clause_match(task, clause)
        if not hits:
            return []
        all_hits.extend(hits)
    return all_hits


def execute_ai_search(
    tasks: Sequence[Dict[str, Any]],
    query: str,
    *,
    use_llm: bool = True,
) -> Dict[str, Any]:
    q = (query or "").strip()
    base_plan = parse_search_intent_rules(q)
    llm_meta: Dict[str, Any] = {}
    plan = base_plan
    expanded_terms: List[str] = []
    for clause in base_plan.text_clauses:
        expanded_terms.extend(clause.terms)
    if use_llm:
        llm_meta = parse_search_intent_llm(q)
        plan = _merge_llm_plan(base_plan, llm_meta)
        for clause in plan.text_clauses:
            for t in clause.terms:
                if t and t not in expanded_terms:
                    expanded_terms.append(t)
        for t in llm_meta.get("expanded_terms") or []:
            if t and t not in expanded_terms:
                expanded_terms.append(t)
    rows = [enrich_task_source_fields(dict(t)) for t in (tasks or [])]
    matched: List[Dict[str, Any]] = []
    for row in rows:
        if plan.read_status != "all" and not match_task_read_status(row, plan.read_status):
            continue
        if plan.authors:
            author = str(row.get("author_name") or "").strip()
            if author not in set(plan.authors):
                continue
        if plan.sources:
            src = str(row.get("import_source") or "other").strip() or "other"
            if src not in set(plan.sources):
                continue
        hits = grep_task(row, plan)
        if plan.text_clauses and not hits:
            continue
        item = dict(row)
        item["ai_match_fields"] = sorted({h.field for h in hits if h.field})
        item["ai_match_terms"] = sorted({h.term for h in hits if h.term})
        item["ai_match_snippets"] = [h.snippet for h in hits if h.snippet][:4]
        matched.append(item)
    matched = sort_tasks(matched, sort=plan.sort or "default")
    field_labels = sorted({f for c in plan.text_clauses for f in c.fields})
    grep_fields = "、".join(
        {
            ("MD正文" if f == "content" else f)
            for f in (field_labels or ["全字段"])
        }
    )
    term_labels = "、".join({t for c in plan.text_clauses for t in c.terms} or expanded_terms[:6] or [q[:24]])
    read_note = ""
    if plan.read_status == "unread":
        read_note = " · 未读"
    elif plan.read_status == "read":
        read_note = " · 已读"
    grep_summary = f"{grep_fields} grep「{term_labels}」{read_note} · 命中 {len(matched)} 条"
    applied = {
        "read_status": plan.read_status,
        "enable_read": plan.read_status != "all",
        "authors": plan.authors,
        "sources": plan.sources,
        "sort": plan.sort,
    }
    return {
        "items": matched,
        "matched_task_ids": [str(x.get("task_id") or "") for x in matched if x.get("task_id")],
        "total": len(matched),
        "plan": plan.model_dump(),
        "expanded_terms": expanded_terms[:20],
        "llm_powered": bool(plan.llm_powered or llm_meta.get("llm_powered")),
        "intent_hint": plan.intent_hint or llm_meta.get("intent_hint") or "",
        "grep_summary": grep_summary,
        "applied_filters": applied,
    }
