"""输出目录内 Markdown 读写与行标记（与 ST3 SBA_LineMarks 侧车 JSON 互通）。"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .file_naming import is_under_output_dir, resolve_output_abs
from .task_manager import get_output_dir

_log = logging.getLogger("sba.output_file_io")

MARKS_SCHEMA = "sublime-line-marks"
MARKS_VERSION = 1
MARKS_SUFFIX = ".sublime-marks.json"

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


def _line_label(text: str, max_len: int = 120) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t[:max_len]


def read_marks(md_abs: Path) -> List[Dict[str, Any]]:
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
    out: List[Dict[str, Any]] = []
    for item in marks:
        if isinstance(item, int):
            if item >= 1:
                out.append({"line": item, "name": ""})
            continue
        if not isinstance(item, dict):
            continue
        line = int(item.get("line") or 0)
        if line >= 1:
            out.append({"line": line, "name": str(item.get("name") or "").strip()})
    out.sort(key=lambda x: x["line"])
    return out


def write_marks(md_abs: Path, marks: List[Dict[str, Any]]) -> Path:
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for item in marks or []:
        line = int(item.get("line") or 0)
        if line < 1 or line in seen:
            continue
        seen.add(line)
        name = str(item.get("name") or "").strip()
        cleaned.append({"line": line, "name": name})
    cleaned.sort(key=lambda x: x["line"])

    sidecar = marks_sidecar_path(md_abs)
    payload = {
        "version": MARKS_VERSION,
        "schema": MARKS_SCHEMA,
        "file": md_abs.name,
        "abs_path": str(md_abs.resolve()),
        "marks": cleaned,
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info(
        "[链接沉淀文档-MD预览|output_file_io.write_marks|%s|硬编执行|写入] 行标记已保存; count=%s; sidecar=%s",
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
    """编辑正文后按行内容锚点传递标记行号（对齐 ST3 updateData / 侧车互通）。"""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    max_scan = max(len(new_lines), len(old_lines), 1) + 2

    for m in marks or []:
        old_line = int(m.get("line") or 0)
        if old_line < 1:
            continue
        name = str(m.get("name") or "").strip()
        if not name and 1 <= old_line <= len(old_lines):
            name = _line_label(old_lines[old_line - 1])
        anchor = name.strip()
        if not anchor and 1 <= old_line <= len(old_lines):
            anchor = _line_label(old_lines[old_line - 1])
        if not anchor:
            continue

        found: Optional[int] = None
        for delta in range(0, max_scan):
            candidates: List[int] = []
            if delta == 0:
                candidates = [old_line - 1]
            else:
                candidates = [old_line - 1 + delta, old_line - 1 - delta]
            for idx in candidates:
                if 0 <= idx < len(new_lines):
                    cur = _line_label(new_lines[idx])
                    if cur == anchor or (anchor and anchor in cur):
                        found = idx + 1
                        break
            if found is not None:
                break

        if found is not None and found not in seen:
            seen.add(found)
            label = _line_label(new_lines[found - 1]) if 1 <= found <= len(new_lines) else anchor
            out.append({"line": found, "name": label})

    out.sort(key=lambda x: x["line"])
    return out


def enrich_marks_with_labels(md_abs: Path, marks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        lines = md_abs.read_text(encoding="utf-8").splitlines()
    except Exception:
        return marks
    out = []
    for m in marks:
        line = int(m.get("line") or 0)
        name = str(m.get("name") or "").strip()
        if not name and 1 <= line <= len(lines):
            name = _line_label(lines[line - 1])
        out.append({"line": line, "name": name})
    return out


def read_output_file(name: str) -> Dict[str, Any]:
    abs_p = resolve_output_file(name)
    text = abs_p.read_text(encoding="utf-8")
    marks = enrich_marks_with_labels(abs_p, read_marks(abs_p))
    return {
        "ok": True,
        "file": abs_p.name,
        "path": str(abs_p.resolve()),
        "content": text,
        "marks": marks,
        "marks_sidecar": str(marks_sidecar_path(abs_p)),
        "output_dir": str(get_output_dir().resolve()),
    }


def save_output_file(
    name: str,
    content: str,
    *,
    save_as: str = "",
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
    target.write_text(content or "", encoding="utf-8")

    # 另存为时复制侧车标记
    if target.resolve() != src.resolve():
        src_sidecar = marks_sidecar_path(src)
        if src_sidecar.is_file():
            marks = read_marks(src)
            write_marks(target, marks)
    else:
        # 保存正文后刷新侧车中的行摘要
        marks = enrich_marks_with_labels(target, read_marks(target))
        write_marks(target, marks)

    _log.info(
        "[链接沉淀文档-MD预览|output_file_io.save_output_file|%s|硬编执行|写入] 文件已保存; bytes=%s; save_as=%s",
        target.name,
        len((content or "").encode("utf-8")),
        bool(save_as),
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
    enriched = enrich_marks_with_labels(abs_p, read_marks(abs_p))
    return {
        "ok": True,
        "file": abs_p.name,
        "marks": enriched,
        "marks_sidecar": str(sidecar),
    }
