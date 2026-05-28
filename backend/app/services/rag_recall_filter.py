"""RAG 召回元数据筛选：词表扫描、query 提议、Milvus 硬筛（==）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

_FILTER_FIELDS = ("domain", "module", "doc_type", "keyword1", "keyword2")


def kb_metadata_vocabulary() -> Dict[str, List[str]]:
    """从登记册汇总当前库元数据词汇（供术语映射与表单下拉）。"""
    from .kb_rag import load_merged_file_records

    vocab: Dict[str, Set[str]] = {k: set() for k in _FILTER_FIELDS}
    for row in load_merged_file_records() or []:
        if not isinstance(row, dict):
            continue
        for key in _FILTER_FIELDS:
            v = str(row.get(key) or "").strip()
            if v:
                vocab[key].add(v)
    return {k: sorted(vocab[k]) for k in _FILTER_FIELDS}


def _tokenize_query(q: str) -> List[str]:
    text = (q or "").strip()
    if not text:
        return []
    parts = re.split(r"[\s,，、；;|/]+", text)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2 and p not in out:
            out.append(p)
    for m in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        t = m.group()
        if t not in out:
            out.append(t)
    return out[:24]


def propose_rag_filter_form(
    query: str,
    *,
    slot_snapshot: Optional[Dict[str, Any]] = None,
    enhancement_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从用户 query / 槽位 / 增强检索词推断筛选表单初值（全等于硬筛；空=不筛）。
  内部术语默认映射到当前库词表。
    """
    vocab = kb_metadata_vocabulary()
    slot = slot_snapshot or {}
    enhance = enhancement_snapshot or {}

    terms: List[str] = []
    for src in (
        enhance.get("search_keyword_queries"),
        enhance.get("retrieval_hints"),
        slot.get("retrieval_terms"),
        slot.get("entities"),
    ):
        if isinstance(src, list):
            terms.extend(str(x).strip() for x in src if str(x).strip())
    terms.extend(_tokenize_query(query))

    # 去重保序
    seen: Set[str] = set()
    uniq: List[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    form: Dict[str, str] = {k: "" for k in _FILTER_FIELDS}
    mapping_notes: List[str] = []

    def _match_vocab(field: str, token: str) -> Optional[str]:
        options = vocab.get(field) or []
        if token in options:
            return token
        for opt in options:
            if token in opt or opt in token:
                return opt
        return None

    for token in uniq:
        matched_any = False
        for field in ("domain", "module", "doc_type"):
            if form[field]:
                continue
            hit = _match_vocab(field, token)
            if hit:
                form[field] = hit
                mapping_notes.append(f"{token}→{field}={hit}")
                matched_any = True
                break
        if matched_any:
            continue
        for field in ("keyword1", "keyword2"):
            if form[field]:
                continue
            hit = _match_vocab(field, token)
            if hit:
                form[field] = hit
                mapping_notes.append(f"{token}→{field}={hit}")
                break
            if len(token) >= 2 and not form["keyword1"]:
                form["keyword1"] = token[:128]
                mapping_notes.append(f"{token}→keyword1(提议)")
                break

    # 槽位 domain/module 优先
    if str(slot.get("domain") or "").strip():
        form["domain"] = str(slot["domain"]).strip()
    if str(slot.get("module") or "").strip():
        form["module"] = str(slot["module"]).strip()

    return {
        "filter": form,
        "vocabulary": vocab,
        "extracted_terms": uniq[:16],
        "term_mapping_notes": mapping_notes,
        "match_mode": {k: "eq" for k in _FILTER_FIELDS},
    }


def filter_dict_to_document_metadata(filter_form: Dict[str, Any]):
    """将表单转为 DocumentMetadata（仅非空字段参与硬筛）。"""
    from rag_tools import DocumentMetadata

    f = filter_form or {}
    return DocumentMetadata(
        domain=str(f.get("domain") or "").strip(),
        module=str(f.get("module") or "").strip(),
        doc_type=str(f.get("doc_type") or "").strip(),
        keyword1=str(f.get("keyword1") or "").strip(),
        keyword2=str(f.get("keyword2") or "").strip(),
    )


def active_filter_fields(filter_form: Dict[str, Any]) -> Dict[str, str]:
    """仅返回需要硬筛的字段。"""
    out: Dict[str, str] = {}
    for k in _FILTER_FIELDS:
        v = str((filter_form or {}).get(k) or "").strip()
        if v:
            out[k] = v
    return out
