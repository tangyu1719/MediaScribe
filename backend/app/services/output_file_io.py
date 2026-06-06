"""输出目录内 Markdown 读写与选区标记（网页 Ctrl+Q；兼容旧版行标记侧车）。"""
from __future__ import annotations

import json
import logging
import re
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


def marks_sidecar_path(md_abs: Path) -> Path:
    return md_abs.parent / f"{md_abs.name}{MARKS_SUFFIX}"


def split_embedded_marks(text: str) -> Tuple[str, List[Any]]:
    """从 MD 正文剥离底部选区标记节，返回 (正文, 原始 marks 列表)。"""
    src = text or ""
    marks: List[Any] = []

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
    """生成写入 MD 文件底部的选区标记节（人类可读 + 机器 JSON）。"""
    cleaned = normalize_marks(marks or [], body or "")
    base = (body or "").rstrip("\n")
    if not cleaned:
        return (base + "\n") if base else ""
    sidecar_items = [_mark_to_sidecar_item(m, body or "") for m in cleaned]
    lines: List[str] = []
    if base:
        lines.append("")
    lines.extend(
        [
            "---",
            "## 选区标记",
            "",
            "> 本节由 SuperBizAgent 自动维护（Ctrl+Q）。侧车：`*.sublime-marks.json`",
            "",
        ]
    )
    for i, m in enumerate(cleaned, 1):
        name = str(m.get("name") or "").strip() or _span_label(body, int(m["start"]), int(m["end"]))
        line_no = int(m.get("line") or _offset_to_line(body, int(m["start"])))
        lines.append(f"{i}. **第 {line_no} 行** · `{name}`")
    lines.extend(
        [
            "",
            MARKS_FOOTER_BEGIN,
            "",
            f"```{MARKS_FENCE}",
            json.dumps(sidecar_items, ensure_ascii=False, indent=2),
            "```",
            "",
            MARKS_FOOTER_END,
            "",
        ]
    )
    if base:
        return base + "\n" + "\n".join(lines)
    return "\n".join(lines)


def _read_file_body(md_abs: Path) -> str:
    try:
        raw = md_abs.read_text(encoding="utf-8")
    except Exception:
        return ""
    body, _ = split_embedded_marks(raw)
    return body


def _write_sidecar(md_abs: Path, marks: List[Dict[str, Any]], body: str) -> Path:
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


def _sync_marks_to_md_file(md_abs: Path, marks: List[Dict[str, Any]], body: Optional[str] = None) -> None:
    """将标记写入 MD 文件底部并同步侧车。"""
    body = body if body is not None else _read_file_body(md_abs)
    cleaned = normalize_marks(marks or [], body or "")
    full = build_marks_footer_md(body or "", cleaned)
    md_abs.write_text(full, encoding="utf-8")
    _write_sidecar(md_abs, cleaned, body or "")


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
    by_line: Dict[int, Dict[str, Any]] = {}
    for item in marks or []:
        norm = normalize_mark_item(item, text)
        if not norm:
            continue
        line = int(norm.get("line") or 0)
        if line < 1:
            continue
        by_line[line] = norm
    out = list(by_line.values())
    out.sort(key=lambda x: x["start"])
    return out


def read_marks(md_abs: Path) -> List[Dict[str, Any]]:
    try:
        raw = md_abs.read_text(encoding="utf-8")
    except Exception:
        raw = ""
    body, embedded = split_embedded_marks(raw)
    if embedded:
        return normalize_marks(embedded, body)
    sidecar = marks_sidecar_path(md_abs)
    if not sidecar.is_file():
        return []
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as e:
        _log.warning(
            "[链接沉淀文档-MD预览|output_file_io.read_marks|%s|硬编执行|读取] 侧车解析失败; error_type=%s; error_message=%s",
            md_abs.name,
            type(e).__name__,
            e,
        )
        return []
    marks = data.get("marks") if isinstance(data, dict) else []
    if not isinstance(marks, list):
        return []
    return normalize_marks(marks, body)


def write_marks(md_abs: Path, marks: List[Dict[str, Any]]) -> Path:
    body = _read_file_body(md_abs)
    cleaned = normalize_marks(marks or [], body)
    _sync_marks_to_md_file(md_abs, cleaned, body)
    sidecar = marks_sidecar_path(md_abs)
    _log.info(
        "[链接沉淀文档-MD预览|output_file_io.write_marks|%s|硬编执行|写入] 选区标记已保存; count=%s; sidecar=%s",
        md_abs.name,
        len(cleaned),
        sidecar.name,
    )
    return sidecar


def remap_marks_on_text_change(
    old_text: str,
    new_text: str,
    marks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """编辑正文后按选区文本锚点传递标记（与网页 remapMarksOnEdit 对齐）。"""
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for m in normalize_marks(marks or [], old_text):
        anchor = (m.get("name") or "").strip() or (old_text or "")[m["start"] : m["end"]]
        if not anchor:
            continue
        pos = (new_text or "").find(anchor)
        if pos < 0 and len(anchor) > 12:
            pos = (new_text or "").find(anchor[: min(len(anchor), 48)])
        if pos < 0:
            continue
        end = pos + len(anchor)
        key = f"{pos}:{end}"
        if key in seen:
            continue
        seen.add(key)
        name = re.sub(r"\s+", " ", anchor.strip())[:120]
        line = _offset_to_line(new_text or "", pos)
        out.append({"start": pos, "end": end, "name": name, "line": line})
    return normalize_marks(out, new_text or "")


def enrich_marks_with_labels(md_abs: Path, marks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    body = _read_file_body(md_abs)
    out: List[Dict[str, Any]] = []
    for m in normalize_marks(marks, body):
        name = str(m.get("name") or "").strip()
        if not name:
            name = _span_label(body, int(m["start"]), int(m["end"]))
        out.append({"start": int(m["start"]), "end": int(m["end"]), "name": name, "line": int(m.get("line") or _offset_to_line(body, int(m["start"])))})
    return out


def read_output_file(name: str) -> Dict[str, Any]:
    abs_p = resolve_output_file(name)
    raw = abs_p.read_text(encoding="utf-8")
    body, _ = split_embedded_marks(raw)
    marks = enrich_marks_with_labels(abs_p, read_marks(abs_p))
    full = build_marks_footer_md(body, marks)
    footer_md = full[len(body) :].lstrip("\n") if marks else ""
    return {
        "ok": True,
        "file": abs_p.name,
        "path": str(abs_p.resolve()),
        "content": body,
        "marks": marks,
        "marks_footer": footer_md,
        "marks_sidecar": str(marks_sidecar_path(abs_p)),
        "output_dir": str(get_output_dir().resolve()),
    }


def save_output_file(
    name: str,
    content: str,
    *,
    save_as: str = "",
    marks: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    src = resolve_output_file(name)
    target_name = safe_output_basename(save_as) if (save_as or "").strip() else src.name
    _ensure_editable(target_name)
    root = get_output_dir().resolve()
    target = (root / target_name).resolve()
    if not is_under_output_dir(target):
        raise PermissionError("目标路径须在输出目录内")
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
    _sync_marks_to_md_file(target, mark_list, body)

    _log.info(
        "[链接沉淀文档-MD预览|output_file_io.save_output_file|%s|硬编执行|写入] 文件已保存; bytes=%s; save_as=%s; marks=%s",
        target.name,
        len((build_marks_footer_md(body, mark_list)).encode("utf-8")),
        bool(save_as),
        len(mark_list),
    )
    return {
        "ok": True,
        "file": target.name,
        "path": str(target.resolve()),
        "saved_as_new": target.resolve() != src.resolve(),
    }


def save_marks(name: str, marks: List[Dict[str, Any]]) -> Dict[str, Any]:
    abs_p = resolve_output_file(name)
    sidecar = write_marks(abs_p, marks)
    body = _read_file_body(abs_p)
    enriched = enrich_marks_with_labels(abs_p, read_marks(abs_p))
    full = build_marks_footer_md(body, enriched)
    footer_md = full[len(body) :].lstrip("\n") if enriched else ""
    return {
        "ok": True,
        "file": abs_p.name,
        "marks": enriched,
        "marks_footer": footer_md,
        "marks_sidecar": str(sidecar),
    }
