"""输出目录内 Markdown 读写与选区标记（网页 Ctrl+Q；兼容旧版行标记侧车）。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .file_naming import is_under_output_dir, resolve_output_abs
from .task_manager import get_output_dir

_log = logging.getLogger("sba.output_file_io")

MARKS_SCHEMA = "sublime-span-marks"
MARKS_SCHEMA_LEGACY = "sublime-line-marks"
MARKS_VERSION = 2
MARKS_SUFFIX = ".sublime-marks.json"
MARKS_FOOTER_BEGIN = "<!-- sba-marks-footer-v2 -->"
MARKS_FOOTER_END = "<!-- /sba-marks-footer -->"
MARKS_FENCE = "sba-marks"
# 与 Sublime Text 3 ColorMarker.py 文末汇总块一致（50 个 =）
ST3_SUMMARY_LINE = "=" * 50

_EDITABLE_SUFFIXES = frozenset({".md", ".txt", ".markdown", ".mdx"})


def safe_output_basename(name: str) -> str:
    """仅允许 output 目录下的 basename，禁止路径穿越。"""
    raw = (name or "").strip().replace("\\", "/")
    base = Path(raw).name
    if not base or base in (".", "..") or ".." in raw:
        raise ValueError("非法文件名")
    if base.startswith("."):
        raise ValueError("不允许隐藏文件")
    return base


def _ensure_editable(name: str) -> None:
    low = name.lower()
    if not any(low.endswith(s) for s in _EDITABLE_SUFFIXES):
        raise ValueError("仅支持 md/txt/markdown/mdx 文件")


def output_file_mtime_ms(name: str) -> Optional[int]:
    """读取 output 目录内文件的磁盘修改时间（毫秒），不存在则返回 None。"""
    try:
        abs_p = resolve_output_file(name)
    except (ValueError, FileNotFoundError, PermissionError):
        return None
    if not abs_p.is_file():
        return None
    return int(abs_p.stat().st_mtime * 1000)


def resolve_output_file(name: str) -> Path:
    base = safe_output_basename(name)
    _ensure_editable(base)
    abs_p = resolve_output_abs(base)
    if not abs_p:
        raise FileNotFoundError("文件不存在")
    abs_p = abs_p.resolve()
    if not abs_p.is_file():
        raise FileNotFoundError("文件不存在")
    if not is_under_output_dir(abs_p):
        raise PermissionError("仅允许访问输出目录内文件")
    return abs_p


def _resolve_save_source(name: str = "", abs_path: str = "") -> Path:
    """按绝对路径或 basename 定位待保存的源文件。"""
    from .fs_browse import is_under_allowed_root

    raw = (abs_path or "").strip()
    if raw:
        p = Path(raw).resolve()
        if not p.is_file():
            raise FileNotFoundError("文件不存在")
        _ensure_editable(p.name)
        if not is_under_output_dir(p) and not is_under_allowed_root(p):
            raise PermissionError("仅允许保存输出/白名单目录内文件")
        return p
    if not (name or "").strip():
        raise ValueError("缺少 file 或 path")
    return resolve_output_file(name)


def marks_sidecar_path(md_abs: Path) -> Path:
    return md_abs.parent / f"{md_abs.name}{MARKS_SUFFIX}"


def _normalize_text_st3(text: str) -> str:
    """与 ColorMarker.normalize_text 一致。"""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ").replace("\u3000", " ")
    t = t.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(t.split())


def _compute_order_fingerprint(texts: List[str]) -> str:
    """与 ColorMarker.compute_order_fingerprint 一致。"""
    norm = [_normalize_text_st3(t) for t in (texts or [])]
    raw = f"{len(norm)}|{'||'.join(norm)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def find_summary_start(text: str) -> int:
    """查找文末「标记内容汇总」块起始 offset（ColorMarker.find_summary_start）。"""
    lines = (text or "").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line == "标记内容汇总":
            if i > 0 and set(lines[i - 1].strip()) <= {"="} and len(lines[i - 1].strip()) >= 10:
                start_pos = 0
                for j in range(i - 1):
                    start_pos += len(lines[j]) + 1
                return start_pos
    return -1


def _occurrence_index_in_body(body: str, start: int, end: int, needle: str = "") -> int:
    """选中片段在正文中的第几次出现（1-based，ColorMarker.occurrence_index_in_body）。"""
    needle = needle or (body or "")[max(0, start) : max(0, end)]
    if not needle:
        return 1
    pos_sel = max(0, int(start))
    occ = 0
    search_start = 0
    while True:
        p = body.find(needle, search_start)
        if p == -1:
            break
        occ += 1
        if p <= pos_sel < p + len(needle):
            return occ
        search_start = p + 1
    return 1


def _encode_footer_item_text(text: str) -> str:
    """文末汇总单行编码：保留换行，避免多行选区写入时被拆成多条。"""
    return (text or "").replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n")


def _decode_footer_item_text(text: str) -> str:
    """解码文末汇总条目中的 \\n / \\\\。"""
    out: List[str] = []
    i = 0
    src = text or ""
    while i < len(src):
        if src[i] == "\\" and i + 1 < len(src):
            nxt = src[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(src[i])
        i += 1
    return "".join(out)


def parse_st3_summary_items(summary_text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """解析文末汇总块条目（ColorMarker.parse_summary_items）。"""
    items: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {"count": None, "order_fp": ""}
    for line in (summary_text or "").split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("COUNT:"):
            try:
                meta["count"] = int(s.split(":", 1)[1].strip())
            except Exception:
                meta["count"] = None
            continue
        if s.startswith("ORDER_FP:"):
            meta["order_fp"] = s.split(":", 1)[1].strip()
            continue
        dot = s.find(". ")
        if dot > 0 and s[:dot].isdigit():
            body = s[dot + 2 :].strip()
            cnt: Optional[int] = None
            if body.startswith("[CNT="):
                rb = body.find("]")
                if rb > 5:
                    try:
                        cnt = int(body[5:rb])
                    except Exception:
                        cnt = None
                    body = body[rb + 1 :].strip()
            if body and body != "暂无标记内容":
                items.append({"text": _decode_footer_item_text(body), "cnt": cnt})
    return items, meta


def _markdown_strip_for_search(text: str) -> str:
    """去掉常见 Markdown 包裹符，便于汇总条目与正文模糊匹配。"""
    t = text or ""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\[(.+?)\]\([^)]+\)", r"\1", t)
    return t


def _search_needles(item: str) -> List[str]:
    """汇总条目在正文中定位时依次尝试的候选串（精确 → 去 MD → 归一空白）。"""
    needles: List[str] = []
    for cand in (
        item,
        item.strip(),
        _markdown_strip_for_search(item),
        _markdown_strip_for_search(item).strip(),
        " ".join(item.split()),
    ):
        if cand and cand not in needles:
            needles.append(cand)
    return needles


def _find_item_span(
    body: str, item: str, cnt_hint: Optional[int]
) -> Optional[Tuple[int, int, str]]:
    """在正文中定位汇总条目；支持 CNT 与 Markdown/空白模糊匹配。"""
    for needle in _search_needles(item):
        positions: List[int] = []
        start = 0
        while True:
            p = body.find(needle, start)
            if p == -1:
                break
            positions.append(p)
            start = p + 1
        if not positions:
            continue
        if isinstance(cnt_hint, int) and cnt_hint >= 1:
            idx = cnt_hint - 1
            if idx >= len(positions):
                continue
        else:
            idx = 0
        pick = positions[idx]
        matched = body[pick : pick + len(needle)]
        return pick, pick + len(needle), matched
    return None


def _mark_exact_text(body: str, m: Dict[str, Any]) -> str:
    """写入汇总时使用正文精确切片，避免 name 字段被 strip 后与正文不一致。"""
    start, end = int(m["start"]), int(m["end"])
    if 0 <= start < end <= len(body or ""):
        return (body or "")[start:end]
    name = str(m.get("name") or "").strip()
    return name or _span_label(body or "", start, end)


def marks_from_st3_summary(body: str, summary_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按汇总顺序 + CNT 在正文中恢复选区（ColorMarker.recover_regions_from_summary_order）。"""
    if not summary_items:
        return []
    content_text = body or ""
    text_seen_count: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for entry in summary_items:
        item = str((entry or {}).get("text") or "").strip()
        cnt_hint = (entry or {}).get("cnt")
        if not item:
            continue
        k = _normalize_text_st3(item)
        if isinstance(cnt_hint, int) and cnt_hint >= 1:
            idx_hint: Optional[int] = cnt_hint
        else:
            idx_hint = text_seen_count.get(k, 0) + 1
            text_seen_count[k] = text_seen_count.get(k, 0) + 1

        span = _find_item_span(content_text, item, idx_hint if isinstance(idx_hint, int) else None)
        if not span:
            continue
        pick, end, matched = span
        key = f"{pick}:{end}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "start": pick,
                "end": end,
                "name": matched,
                "line": _offset_to_line(content_text, pick),
            }
        )
    out.sort(key=lambda x: x["start"])
    return out


def split_embedded_marks(text: str) -> Tuple[str, List[Any]]:
    """从 MD 正文剥离底部标记节，返回 (正文, marks 列表)。优先 ST3 汇总块。"""
    src = text or ""
    marks: List[Any] = []

    summary_start = find_summary_start(src)
    if summary_start != -1:
        summary_text = src[summary_start:]
        body = src[:summary_start].rstrip("\n")
        if body:
            body += "\n"
        items, _ = parse_st3_summary_items(summary_text)
        if items:
            marks = marks_from_st3_summary(body, items)
        return body, marks

    block_re = re.compile(
        rf"\n---\n## 选区标记\n.*?{re.escape(MARKS_FOOTER_END)}\n?",
        re.DOTALL,
    )
    m = block_re.search(src)
    if not m:
        return src, marks

    block = m.group(0)
    fence_re = re.compile(
        rf"```{re.escape(MARKS_FENCE)}\s*\n(.*?)\n```",
        re.DOTALL,
    )
    fm = fence_re.search(block)
    if fm:
        try:
            parsed = json.loads(fm.group(1))
            if isinstance(parsed, list):
                marks = parsed
        except Exception:
            marks = []

    body = src[: m.start()].rstrip("\n")
    if body:
        body += "\n"
    return body, marks


def build_marks_footer_md(body: str, marks: List[Dict[str, Any]]) -> str:
    """生成 ColorMarker 兼容的文末「标记内容汇总」块。"""
    cleaned = normalize_marks(marks or [], body or "")
    base = (body or "").rstrip("\n")

    block_lines = [ST3_SUMMARY_LINE, "标记内容汇总", ST3_SUMMARY_LINE]
    if not cleaned:
        block_lines.extend(["COUNT: 0", "ORDER_FP: " + _compute_order_fingerprint([]), "暂无标记内容", ST3_SUMMARY_LINE])
        block = "\n\n" + "\n".join(block_lines) + "\n"
        return (base + block) if base else block

    raw_items: List[str] = []
    numbered: List[str] = []
    for i, m in enumerate(cleaned, 1):
        exact = _mark_exact_text(body, m)
        raw_items.append(exact)
        cnt = _occurrence_index_in_body(body, int(m["start"]), int(m["end"]), exact)
        numbered.append(f"{i}. [CNT={cnt}] {_encode_footer_item_text(exact)}")

    order_fp = _compute_order_fingerprint(raw_items)
    block_lines.extend([f"COUNT: {len(raw_items)}", f"ORDER_FP: {order_fp}"])
    block_lines.extend(numbered)
    block_lines.append(ST3_SUMMARY_LINE)
    block = "\n\n" + "\n".join(block_lines) + "\n"
    return (base + block) if base else block


def _read_file_body(md_abs: Path) -> str:
    try:
        raw = md_abs.read_text(encoding="utf-8")
    except Exception:
        return ""
    body, _ = split_embedded_marks(raw)
    return body


def _write_sidecar(md_abs: Path, marks: List[Dict[str, Any]], body: str) -> Path:
    """写入侧车 JSON（仅兼容旧数据读取；Web 新写入不再调用）。"""
    cleaned = normalize_marks(marks or [], body or "")
    sidecar_items = [_mark_to_sidecar_item(m, body or "") for m in cleaned]
    sidecar = marks_sidecar_path(md_abs)
    payload = {
        "version": MARKS_VERSION,
        "schema": MARKS_SCHEMA,
        "file": md_abs.name,
        "abs_path": str(md_abs.resolve()),
        "marks": sidecar_items,
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def _sync_marks_footer_to_md_file(md_abs: Path, marks: List[Dict[str, Any]], body: Optional[str] = None) -> None:
    """将标记写入 MD 文末「标记内容汇总」块（ST3/ColorMarker 互通）；不写入侧车 JSON。"""
    body = body if body is not None else _read_file_body(md_abs)
    cleaned = normalize_marks(marks or [], body or "")
    full = build_marks_footer_md(body or "", cleaned)
    md_abs.write_text(full, encoding="utf-8")


def _sync_marks_to_md_file(md_abs: Path, marks: List[Dict[str, Any]], body: Optional[str] = None) -> None:
    """兼容旧调用名：等同 _sync_marks_footer_to_md_file。"""
    _sync_marks_footer_to_md_file(md_abs, marks, body)


def _span_label(text: str, start: int, end: int, max_len: int = 120) -> str:
    snippet = (text or "")[max(0, start) : max(0, end)]
    t = re.sub(r"\s+", " ", snippet.strip())
    return t[:max_len]


def _line_label(text: str, max_len: int = 120) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t[:max_len]


def _line_to_offset(text: str, line: int) -> int:
    """与前端 md.html lineToOffset 一致（按 \\n 切行）。"""
    lines = (text or "").split("\n")
    pos = 0
    for i in range(min(line - 1, len(lines))):
        pos += len(lines[i]) + 1
    return pos


def _line_span(text: str, line: int) -> Optional[Tuple[int, int]]:
    if line < 1:
        return None
    lines = (text or "").split("\n")
    if line > len(lines):
        return None
    start = _line_to_offset(text, line)
    end = start + len(lines[line - 1])
    if end <= start:
        return None
    return start, end


def _offset_to_line(text: str, offset: int) -> int:
    return (text or "")[: max(0, offset)].count("\n") + 1


def _mark_to_sidecar_item(m: Dict[str, Any], text: str) -> Dict[str, Any]:
    start = int(m["start"])
    end = int(m["end"])
    line = int(m.get("line") or 0) or _offset_to_line(text, start)
    name = str(m.get("name") or "").strip() or _span_label(text, start, end)
    item: Dict[str, Any] = {"line": line, "name": name, "start": start, "end": end}
    lines = (text or "").split("\n")
    full = _line_span(text, line)
    if full and (start, end) == full:
        return {"line": line, "name": name}
    return item


def normalize_mark_item(item: Any, text: str) -> Optional[Dict[str, Any]]:
    """将侧车条目规范为 {start, end, name}；兼容旧 {line, name}。"""
    if isinstance(item, int):
        item = {"line": item, "name": ""}
    if not isinstance(item, dict):
        return None

    name = str(item.get("name") or "").strip()
    start_raw = item.get("start")
    end_raw = item.get("end")
    if start_raw is not None and end_raw is not None:
        start = int(start_raw)
        end = int(end_raw)
    else:
        line = int(item.get("line") or 0)
        span = _line_span(text, line)
        if not span:
            return None
        start, end = span
        if not name and 1 <= line <= len((text or "").split("\n")):
            name = _line_label((text or "").split("\n")[line - 1])

    if start < 0 or end <= start or end > len(text or ""):
        return None
    if not name:
        name = _span_label(text, start, end)
    line = int(item.get("line") or 0) or _offset_to_line(text, start)
    return {"start": start, "end": end, "name": name, "line": line}


def normalize_marks(marks: List[Any], text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in marks or []:
        norm = normalize_mark_item(item, text)
        if not norm:
            continue
        key = f"{norm['start']}:{norm['end']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    out.sort(key=lambda x: x["start"])
    return out


def _read_marks_sidecar_only(md_abs: Path, body: str) -> List[Dict[str, Any]]:
    """仅读侧车 JSON，不再重复读 MD 正文。"""
    sidecar = marks_sidecar_path(md_abs)
    if not sidecar.is_file():
        return []
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as e:
        _log.warning(
            "[链接沉淀文档-MD预览|output_file_io._read_marks_sidecar_only|%s|硬编执行|读取] 侧车解析失败; error_type=%s; error_message=%s",
            md_abs.name,
            type(e).__name__,
            e,
        )
        return []
    marks = data.get("marks") if isinstance(data, dict) else []
    if not isinstance(marks, list):
        return []
    return normalize_marks(marks, body)


def read_marks_from_raw(md_abs: Path, raw: str, body: str) -> List[Dict[str, Any]]:
    """基于已读入的正文解析标记：优先文末 ST3 汇总，无汇总时只读回退侧车 JSON（不写入侧车）。"""
    _, embedded = split_embedded_marks(raw or "")
    if embedded:
        return normalize_marks(embedded, body)
    return _read_marks_sidecar_only(md_abs, body)


def read_marks(md_abs: Path) -> List[Dict[str, Any]]:
    try:
        raw = md_abs.read_text(encoding="utf-8")
    except Exception:
        raw = ""
    body, _ = split_embedded_marks(raw)
    return read_marks_from_raw(md_abs, raw, body)


def write_marks(md_abs: Path, marks: List[Dict[str, Any]]) -> Path:
    body = _read_file_body(md_abs)
    cleaned = normalize_marks(marks or [], body)
    _sync_marks_footer_to_md_file(md_abs, cleaned, body)
    _log.info(
        "[链接沉淀文档-MD预览|output_file_io.write_marks|%s|硬编执行|写入] 选区标记已写入文末汇总; count=%s",
        md_abs.name,
        len(cleaned),
    )
    return md_abs


def _compute_text_edit(old_text: str, new_text: str) -> Optional[Dict[str, int]]:
    """与前端 computeTextEdit 一致：求单次编辑的公共前缀/后缀差异区。"""
    old_text = old_text or ""
    new_text = new_text or ""
    if old_text == new_text:
        return None
    start = 0
    ol, nl = len(old_text), len(new_text)
    while start < ol and start < nl and old_text[start] == new_text[start]:
        start += 1
    old_end, new_end = ol, nl
    while old_end > start and new_end > start and old_text[old_end - 1] == new_text[new_end - 1]:
        old_end -= 1
        new_end -= 1
    return {
        "start": start,
        "old_end": old_end,
        "new_end": new_end,
        "removed": old_end - start,
        "inserted": new_end - start,
    }


def _find_snippet_near(text: str, snip: str, hint: int) -> int:
    """在 hint 附近优先查找 snip，避免全文 index 误匹配。"""
    snip = snip or ""
    if not snip:
        return -1
    text = text or ""
    hint = max(0, min(int(hint), len(text)))
    if text[hint : hint + len(snip)] == snip:
        return hint
    max_d = max(hint, len(text) - hint) + len(snip)
    for d in range(1, max_d + 1):
        a = hint - d
        if a >= 0 and text[a : a + len(snip)] == snip:
            return a
        b = hint + d
        if b + len(snip) <= len(text) and text[b : b + len(snip)] == snip:
            return b
    return -1


def _mark_snippet(text: str, start: int, end: int) -> str:
    s = max(0, min(int(start), len(text or "")))
    e = max(0, min(int(end), len(text or "")))
    if e <= s:
        return ""
    return (text or "")[s:e]


def _resolve_old_snip(
    old_text: str,
    new_text: str,
    start: int,
    end: int,
    name: str,
) -> str:
    """取编辑前片段；若 marks 已在 new 坐标系则优先 new 上的片段。"""
    old_snip = _mark_snippet(old_text, start, end)
    new_snip = _mark_snippet(new_text, start, end)
    name = str(name or "").strip()
    if new_snip and (not old_snip or new_snip != old_snip):
        if not name or new_snip == name or name in new_snip:
            old_snip = new_snip
    if not old_snip and name:
        pos = _find_snippet_near(new_text, name, start)
        if pos < 0:
            pos = _find_snippet_near(old_text, name, start)
        if pos >= 0:
            old_snip = _mark_snippet(new_text, pos, pos + len(name)) or _mark_snippet(
                old_text, pos, pos + len(name)
            )
    return old_snip


def _reanchor_mark_range(
    new_text: str,
    old_snip: str,
    name: str,
    hint: int,
    fallback_start: int,
    fallback_end: int,
) -> Optional[Tuple[int, int]]:
    """按原文片段在新正文中的期望位置重新锚定，防止纯 delta 漂移 1 字。"""
    ns, ne = int(fallback_start), int(fallback_end)
    if ne > ns and new_text[ns:ne] == old_snip:
        return ns, ne
    hint = max(0, min(int(hint), len(new_text)))
    pos = _find_snippet_near(new_text, old_snip, hint)
    if pos < 0 and name:
        pos = _find_snippet_near(new_text, name, hint)
    if pos >= 0:
        if new_text[pos : pos + len(old_snip)] == old_snip:
            return pos, pos + len(old_snip)
        if name and new_text[pos : pos + len(name)] == name:
            return pos, pos + len(name)
    if ne > ns and new_text[ns:ne] == old_snip:
        return ns, ne
    return None


def remap_marks_on_text_change(
    old_text: str,
    new_text: str,
    marks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """编辑正文后按编辑增量传递标记 offset（与前端 adjustMarksForTextEdit 对齐）。"""
    old_text = old_text or ""
    new_text = new_text or ""
    edit = _compute_text_edit(old_text, new_text)
    if not edit:
        return normalize_marks(marks or [], new_text)
    delta = edit["inserted"] - edit["removed"]
    out: List[Dict[str, Any]] = []
    for m in normalize_marks(marks or [], old_text):
        start, end = int(m["start"]), int(m["end"])
        name = str(m.get("name") or "").strip()
        old_snip = _resolve_old_snip(old_text, new_text, start, end, name)
        if not old_snip:
            continue
        anchored: Optional[Tuple[int, int]] = None
        if end <= edit["start"]:
            anchored = _reanchor_mark_range(new_text, old_snip, name, start, start, end)
        elif start >= edit["old_end"]:
            anchored = _reanchor_mark_range(
                new_text, old_snip, name, start + delta, start + delta, end + delta
            )
        else:
            hint = start + delta if start >= edit["old_end"] else edit["start"]
            anchored = _reanchor_mark_range(new_text, old_snip, name, hint, start, end)
        if not anchored:
            continue
        ns, ne = anchored
        if ns < 0 or ne <= ns or ne > len(new_text):
            continue
        label = name or re.sub(r"\s+", " ", new_text[ns:ne].strip())[:120]
        out.append(
            {
                "start": ns,
                "end": ne,
                "name": label,
                "line": _offset_to_line(new_text, ns),
            }
        )
    return normalize_marks(out, new_text)


def enrich_marks_with_labels(md_abs: Path, marks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    body = _read_file_body(md_abs)
    return enrich_marks_with_body(marks, body)


def enrich_marks_with_body(marks: List[Dict[str, Any]], body: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in normalize_marks(marks, body):
        name = str(m.get("name") or "").strip()
        if not name:
            name = _span_label(body, int(m["start"]), int(m["end"]))
        out.append({"start": int(m["start"]), "end": int(m["end"]), "name": name, "line": int(m.get("line") or _offset_to_line(body, int(m["start"])))})
    return out


def read_output_file(name: str, *, with_marks: bool = True) -> Dict[str, Any]:
    abs_p = resolve_output_file(name)
    raw = abs_p.read_text(encoding="utf-8")
    body, _ = split_embedded_marks(raw)
    marks: List[Dict[str, Any]] = []
    footer_md = ""
    if with_marks:
        marks = read_marks_from_raw(abs_p, raw, body)
        marks = enrich_marks_with_body(marks, body)
        if marks:
            full = build_marks_footer_md(body, marks)
            footer_md = full[len(body) :].lstrip("\n")
    try:
        mtime = int(abs_p.stat().st_mtime * 1000)
    except OSError:
        mtime = int(time.time() * 1000)
    return {
        "ok": True,
        "file": abs_p.name,
        "path": str(abs_p.resolve()),
        "content": body,
        "marks": marks,
        "marks_footer": footer_md,
        "marks_sidecar": str(marks_sidecar_path(abs_p)),
        "output_dir": str(get_output_dir().resolve()),
        "mtime": mtime,
    }


def export_output_markdown(content: str, marks: Optional[List[Any]] = None) -> str:
    """组装含文末 ST3 标记块的完整 Markdown，供浏览器另存到本机任意目录。"""
    body, _ = split_embedded_marks(content or "")
    mark_list = normalize_marks(marks or [], body)
    return build_marks_footer_md(body, mark_list)


def save_output_file(
    name: str,
    content: str,
    *,
    save_as: str = "",
    save_dir: str = "",
    marks: Optional[List[Any]] = None,
    abs_path: str = "",
) -> Dict[str, Any]:
    from .fs_browse import is_under_allowed_root, resolve_allowed_directory

    src = _resolve_save_source(name, abs_path)
    target_name = safe_output_basename(save_as) if (save_as or "").strip() else src.name
    _ensure_editable(target_name)
    if (save_dir or "").strip():
        dir_p = resolve_allowed_directory(save_dir)
        target = (dir_p / target_name).resolve()
        if not is_under_allowed_root(target):
            raise PermissionError("目标路径须在白名单目录内")
    elif (save_as or "").strip():
        root = get_output_dir().resolve()
        target = (root / target_name).resolve()
        if not is_under_output_dir(target) and not is_under_allowed_root(target):
            raise PermissionError("目标路径须在输出目录内")
    else:
        target = src.resolve()
    if target != src.resolve() and target.exists():
        raise FileExistsError(f"目标文件已存在: {target_name}")

    target.parent.mkdir(parents=True, exist_ok=True)
    body, _ = split_embedded_marks(content or "")
    if marks is not None:
        mark_list = normalize_marks(marks, body)
    elif target.resolve() == src.resolve():
        mark_list = read_marks(src)
    else:
        mark_list = read_marks(src) if marks_sidecar_path(src).is_file() or src.is_file() else []
    _sync_marks_footer_to_md_file(target, mark_list, body)
    enriched = enrich_marks_with_labels(target, mark_list)
    full_md = build_marks_footer_md(body, mark_list)
    base = (body or "").rstrip("\n")
    footer_md = full_md[len(base) :].lstrip("\n") if mark_list else ""

    _log.info(
        "[链接沉淀文档-MD预览|output_file_io.save_output_file|%s|硬编执行|写入] 文件已保存; bytes=%s; save_as=%s; marks=%s",
        target.name,
        len(full_md.encode("utf-8")),
        bool(save_as),
        len(mark_list),
    )
    return {
        "ok": True,
        "file": target.name,
        "path": str(target.resolve()),
        "saved_as_new": target.resolve() != src.resolve(),
        "marks": enriched,
        "marks_footer": footer_md,
        "marks_sidecar": str(marks_sidecar_path(target)),
        "mtime": output_file_mtime_ms(target.name) or int(time.time() * 1000),
    }


def save_marks(name: str, marks: List[Dict[str, Any]], *, content: Optional[str] = None) -> Dict[str, Any]:
    abs_p = resolve_output_file(name)
    if isinstance(content, str) and content:
        body, _ = split_embedded_marks(content)
    else:
        body = _read_file_body(abs_p)
    cleaned = normalize_marks(marks or [], body)
    _sync_marks_footer_to_md_file(abs_p, cleaned, body)
    enriched = enrich_marks_with_body(cleaned, body)
    full = build_marks_footer_md(body, enriched)
    base = (body or "").rstrip("\n")
    footer_md = full[len(base) :].lstrip("\n") if base else full.lstrip("\n")
    return {
        "ok": True,
        "file": abs_p.name,
        "marks": normalize_marks(enriched, body),
        "marks_footer": footer_md,
        "marks_sidecar": str(marks_sidecar_path(abs_p)),
    }


def import_local_text_to_output(name: str, content: str) -> Dict[str, Any]:
    """将用户本地 MD/TXT 写入 output 目录，与任务产物相同路径供 md.html?file= 打开。"""
    base = safe_output_basename(name)
    _ensure_editable(base)
    root = get_output_dir().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / base).resolve()
    if not is_under_output_dir(target):
        raise PermissionError("目标路径须在输出目录内")
    if target.exists():
        stem = Path(base).stem
        suffix = Path(base).suffix or ".md"
        target = (root / f"{stem}-reader-{int(time.time() * 1000)}{suffix}").resolve()
    body = content or ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _log.info(
        "[文本阅读-本地导入|output_file_io.import_local_text_to_output|%s|硬编执行|写入] 导入完成; ok=true; bytes=%s",
        target.name,
        len(body.encode("utf-8")),
    )
    return {
        "ok": True,
        "file": target.name,
        "path": str(target),
        "output_dir": str(root),
        "mtime": int(target.stat().st_mtime * 1000),
    }
