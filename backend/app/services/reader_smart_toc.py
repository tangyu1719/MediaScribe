"""辅助阅读 — 智能章节目录：优先 Markdown 结构，LLM 补充摘要与层级优化。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

from .ai_chat import _async_iter_llm_token_stream, load_chat_llm_config, resolve_chat_api_credentials
from .reader_agent import resolve_doc_text_for_chat

_LOG = logging.getLogger("sba.reader_smart_toc")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^```")
_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


def _slugify(title: str, index: int) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (title or "").strip().lower()).strip("-")
    if not base:
        base = "section"
    return f"md-sec-{index}-{base[:48]}"


def parse_md_headings(text: str) -> List[Dict[str, Any]]:
    """从 Markdown 源码解析标题（跳过代码块内行）。"""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    in_fence = False
    out: List[Dict[str, Any]] = []
    idx = 0
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(stripped)
        if not m:
            continue
        level = len(m.group(1))
        title = re.sub(r"\s+#+\s*$", "", m.group(2)).strip()
        title = re.sub(r"[*_`]", "", title).strip()
        if not title:
            continue
        hid = _slugify(title, idx)
        out.append({"id": hid, "title": title, "level": level, "line": line_no, "hint": ""})
        idx += 1
    return out


def _structure_items(headings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": h["id"],
            "title": h["title"],
            "level": h["level"],
            "line": h.get("line"),
            "hint": "",
        }
        for h in headings
    ]


def _section_excerpt(text: str, headings: List[Dict[str, Any]], *, max_chars: int = 4200) -> str:
    """为 LLM 提供各章节开头片段，便于生成 hint。"""
    if not text or not headings:
        return (text or "")[:max_chars]
    lines = text.replace("\r\n", "\n").split("\n")
    chunks: List[str] = []
    used = 0
    for i, h in enumerate(headings[:24]):
        start = max(0, int(h.get("line") or 1) - 1)
        end = len(lines)
        if i + 1 < len(headings):
            end = max(start + 1, int(headings[i + 1].get("line") or end) - 1)
        body = "\n".join(lines[start:min(end, start + 12)]).strip()
        block = f"### [{h['id']}] L{h.get('line')} {h['title']}\n{body[:320]}"
        if used + len(block) > max_chars:
            break
        chunks.append(block)
        used += len(block) + 2
    return "\n\n".join(chunks) if chunks else text[:max_chars]


def _parse_llm_toc_json(raw: str, headings: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    text = (raw or "").strip()
    if not text:
        return None
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list) or not arr:
        return None

    by_id = {h["id"]: h for h in headings}
    by_title = {h["title"]: h for h in headings}
    merged: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for row in arr:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        base = by_id.get(rid) or by_title.get(title)
        if not base and title:
            for h in headings:
                if h["title"] == title or title in h["title"] or h["title"] in title:
                    base = h
                    break
        if not base:
            continue
        if base["id"] in seen_ids:
            continue
        seen_ids.add(base["id"])
        try:
            level = int(row.get("level") or base["level"])
        except (TypeError, ValueError):
            level = base["level"]
        level = max(1, min(6, level))
        hint = str(row.get("hint") or row.get("summary") or "").strip()[:80]
        merged.append(
            {
                "id": base["id"],
                "title": base["title"],
                "level": level,
                "line": base.get("line"),
                "hint": hint,
            }
        )

    for h in headings:
        if h["id"] not in seen_ids:
            merged.append(
                {
                    "id": h["id"],
                    "title": h["title"],
                    "level": h["level"],
                    "line": h.get("line"),
                    "hint": "",
                }
            )
    merged.sort(key=lambda x: int(x.get("line") or 0))
    return merged if merged else None


async def _llm_enrich_toc(
    *,
    doc_name: str,
    doc_text: str,
    headings: List[Dict[str, Any]],
    model: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], bool]:
    cfg = load_chat_llm_config()
    creds = resolve_chat_api_credentials(cfg)
    api_key = creds.get("api_key") or ""
    base_url = creds.get("base_url") or ""
    provider = creds.get("provider") or "ark"
    use_model = (model or "").strip() or creds.get("model") or ""
    if not api_key or not use_model:
        return _structure_items(headings), False

    struct_json = json.dumps(
        [{"id": h["id"], "title": h["title"], "level": h["level"], "line": h.get("line")} for h in headings],
        ensure_ascii=False,
    )
    excerpt = _section_excerpt(doc_text, headings)
    system = (
        "你是文档阅读助手，任务是为 Markdown 文档生成智能章节目录 JSON。\n"
        "硬性规则：\n"
        "1. 必须以用户提供的「结构标题列表」为主，不得编造文档中不存在的章节；\n"
        "2. 每个输出项的 id 必须来自结构列表；title 与结构一致（可去掉多余 markdown 符号）；\n"
        "3. hint 为 4~16 字的章节要点，依据对应段落摘录，无依据则留空字符串；\n"
        "4. level 与结构一致，不得随意升降层级；\n"
        "5. 只输出 JSON 数组，无 markdown 围栏与解释。"
    )
    user = (
        f"文档：{doc_name or '未命名'}\n\n"
        f"结构标题列表（优先，不可删改 id）：\n{struct_json}\n\n"
        f"各章节摘录：\n{excerpt}\n\n"
        "输出格式示例：\n"
        '[{"id":"md-sec-0-xxx","title":"章节名","level":2,"hint":"要点"}]'
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    buf = ""
    try:
        async for tok in _async_iter_llm_token_stream(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=use_model,
            messages=messages,
            temperature=0.2,
            max_tokens=900,
            thinking_enabled=False,
        ):
            buf += tok
    except Exception as ex:
        _LOG.warning(
            "[辅助阅读-智能目录|reader_smart_toc._llm_enrich_toc|%s|Agent执行|LLM失败] "
            "error_type=%s; error_message=%s",
            doc_name or "doc",
            type(ex).__name__,
            ex,
        )
        return _structure_items(headings), False

    parsed = _parse_llm_toc_json(buf, headings)
    if parsed:
        return parsed, True
    _LOG.info(
        "[辅助阅读-智能目录|reader_smart_toc._llm_enrich_toc|%s|Agent执行|解析降级] "
        "raw_len=%s",
        doc_name or "doc",
        len(buf),
    )
    return _structure_items(headings), False


def _content_fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


async def build_smart_toc(
    *,
    doc_name: str,
    doc_text: str,
    doc_version: Optional[int] = None,
    local_revision: Optional[int] = None,
    use_llm: bool = True,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    doc_res = resolve_doc_text_for_chat(
        doc_name=doc_name,
        doc_text=doc_text,
        doc_version=doc_version,
        local_revision=local_revision,
    )
    text = str(doc_res.get("text") or "")
    headings = parse_md_headings(text)
    fp = _content_fingerprint(text)

    if not headings:
        return {
            "ok": True,
            "items": [],
            "source": "empty",
            "llm_powered": False,
            "fingerprint": fp,
            "doc_version": doc_res.get("version"),
            "heading_count": 0,
        }

    items = _structure_items(headings)
    llm_powered = False
    source = "structure"

    if use_llm and len(headings) >= 1:
        items, llm_powered = await _llm_enrich_toc(
            doc_name=doc_name,
            doc_text=text,
            headings=headings,
            model=model,
        )
        source = "llm" if llm_powered else "structure"

    _LOG.info(
        "[辅助阅读-智能目录|reader_smart_toc.build_smart_toc|%s|Agent执行|完成] "
        "source=%s; llm_powered=%s; count=%s; ok=true",
        doc_name or "doc",
        source,
        llm_powered,
        len(items),
    )
    return {
        "ok": True,
        "items": items,
        "source": source,
        "llm_powered": llm_powered,
        "fingerprint": fp,
        "doc_version": doc_res.get("version"),
        "heading_count": len(headings),
    }
