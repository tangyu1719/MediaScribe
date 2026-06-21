"""SBA 行标记 — 与 SuperBizAgent Web 预览、ColorMarker 文末汇总互通。"""
import hashlib
import json
import os
import re

import sublime
import sublime_plugin

MARKS_SUFFIX = ".sublime-marks.json"
REGION_KEY = "sba_line_marks"
_APPLYING_KEY = "sba_line_marks_applying"
_REMAP_KEY = "sba_line_marks_remap_scheduled"
_LOADED_VIEWS = set()
ST3_SUMMARY_LINE = "=" * 50


def _sidecar_path(view):
    path = view.file_name()
    if not path:
        return None
    return path + MARKS_SUFFIX


def _normalize_text(text):
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ").replace("\u3000", " ")
    t = t.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(t.split())


def _markdown_strip(text):
    t = text or ""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\[(.+?)\]\([^)]+\)", r"\1", t)
    return t


def _search_needles(item):
    needles = []
    for cand in (
        item,
        item.strip(),
        _markdown_strip(item),
        _markdown_strip(item).strip(),
        " ".join(item.split()),
    ):
        if cand and cand not in needles:
            needles.append(cand)
    return needles


def _find_summary_start(text):
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


def _parse_summary_items(summary_text):
    items = []
    meta = {"count": None, "order_fp": ""}
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
            cnt = None
            if body.startswith("[CNT="):
                rb = body.find("]")
                if rb > 5:
                    try:
                        cnt = int(body[5:rb])
                    except Exception:
                        cnt = None
                    body = body[rb + 1 :].strip()
            if body and body != "暂无标记内容":
                items.append({"text": body, "cnt": cnt})
    return items, meta


def _find_item_span(body, item, cnt_hint):
    for needle in _search_needles(item):
        positions = []
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


def _marks_from_summary_body(body, summary_items):
    if not summary_items:
        return []
    content_text = body or ""
    text_seen_count = {}
    out = []
    seen = set()
    for entry in summary_items:
        item = str((entry or {}).get("text") or "").strip()
        cnt_hint = (entry or {}).get("cnt")
        if not item:
            continue
        k = _normalize_text(item)
        if isinstance(cnt_hint, int) and cnt_hint >= 1:
            idx_hint = cnt_hint
        else:
            idx_hint = text_seen_count.get(k, 0) + 1
            text_seen_count[k] = text_seen_count.get(k, 0) + 1
        span = _find_item_span(content_text, item, idx_hint if isinstance(idx_hint, int) else None)
        if not span:
            continue
        pick, end, matched = span
        key = "%s:%s" % (pick, end)
        if key in seen:
            continue
        seen.add(key)
        out.append({"start": pick, "end": end, "name": matched, "line": _offset_to_line(content_text, pick)})
    out.sort(key=lambda x: x["start"])
    return out


def _load_marks_from_footer(view):
    """优先从 MD 文末「标记内容汇总」恢复（与 ColorMarker 一致）。"""
    if not view or not view.is_valid():
        return []
    full = view.substr(sublime.Region(0, view.size()))
    summary_start = _find_summary_start(full)
    if summary_start == -1:
        return []
    body = full[:summary_start]
    items, _meta = _parse_summary_items(full[summary_start:])
    if not items:
        return []
    marks = _marks_from_summary_body(body, items)
    return marks


def _line_label(view, line):
    reg = _line_region(view, line)
    if reg is None:
        return ""
    return view.substr(reg).strip().replace("\n", " ")[:120]


def _offset_to_line(text, offset):
    return (text or "")[: max(0, offset)].count("\n") + 1


def _normalize_loaded_mark(view, item):
    if isinstance(item, int) and item >= 1:
        item = {"line": item, "name": ""}
    if not isinstance(item, dict):
        return None

    name = str(item.get("name") or "").strip()
    start_raw = item.get("start")
    end_raw = item.get("end")
    if start_raw is not None and end_raw is not None:
        start = int(start_raw)
        end = int(end_raw)
        size = view.size()
        summary_start = _find_summary_start(view.substr(sublime.Region(0, size)))
        content_end = summary_start if summary_start != -1 else size
        if start < 0 or end <= start or end > content_end:
            return None
        line = int(item.get("line") or 0) or (view.rowcol(start)[0] + 1)
        if not name:
            name = view.substr(sublime.Region(start, end)).strip().replace("\n", " ")[:120]
        return {"line": line, "start": start, "end": end, "name": name}

    line = int(item.get("line") or 0)
    if line < 1:
        return None
    reg = _line_region(view, line)
    if reg is None:
        return None
    if not name:
        name = _line_label(view, line)
    return {"line": line, "start": reg.a, "end": reg.b, "name": name}


def _load_marks_from_sidecar(view):
    sidecar = _sidecar_path(view)
    if not sidecar or not os.path.isfile(sidecar):
        return []
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    marks = data.get("marks") if isinstance(data, dict) else []
    if not isinstance(marks, list):
        return []
    out = []
    seen = set()
    for item in marks:
        norm = _normalize_loaded_mark(view, item)
        if not norm:
            continue
        key = "%s:%s" % (norm["start"], norm["end"])
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    out.sort(key=lambda x: x["start"])
    return out


def _load_marks(view):
    footer_marks = _load_marks_from_footer(view)
    if footer_marks:
        return footer_marks
    return _load_marks_from_sidecar(view)


def _sidecar_item(view, item):
    line = int(item.get("line") or 0)
    name = str(item.get("name") or "").strip() or _line_label(view, line)
    start = item.get("start")
    end = item.get("end")
    reg = _line_region(view, line)
    if start is not None and end is not None and reg is not None:
        start_i = int(start)
        end_i = int(end)
        if (start_i, end_i) != (reg.a, reg.b):
            return {"line": line, "name": name, "start": start_i, "end": end_i}
    return {"line": line, "name": name}


def _occurrence_index_in_body(view, region):
    """正文区内全字符匹配：选中片段是第几次出现（1-based），与 ColorMarker CNT 一致。"""
    if region is None or region.empty():
        return 1
    needle = view.substr(region)
    if not needle:
        return 1
    full = view.substr(sublime.Region(0, view.size()))
    summary_start = _find_summary_start(full)
    body_end = summary_start if summary_start != -1 else view.size()
    if region.begin() >= body_end:
        return 1
    haystack = view.substr(sublime.Region(0, body_end))
    pos_sel = region.begin()
    occ = 0
    start = 0
    while True:
        p = haystack.find(needle, start)
        if p == -1:
            break
        occ += 1
        if p == pos_sel:
            return occ
        start = p + 1
    return 1


def _compute_order_fingerprint(texts):
    """次序指纹：按标记文本顺序，与 ColorMarker ORDER_FP 一致。"""
    norm = [_normalize_text(t) for t in (texts or [])]
    raw = "%s|%s" % (len(norm), "||".join(norm))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _marks_to_regions(view, marks):
    full = view.substr(sublime.Region(0, view.size()))
    summary_start = _find_summary_start(full)
    content_end = summary_start if summary_start != -1 else view.size()
    regions = []
    for item in marks:
        start = item.get("start")
        end = item.get("end")
        if start is not None and end is not None:
            reg = sublime.Region(int(start), int(end))
            if 0 <= reg.a < reg.b <= content_end:
                regions.append(reg)
            continue
        reg = _line_region(view, int(item.get("line") or 0))
        if reg is not None and reg.b <= content_end:
            regions.append(reg)
    regions.sort(key=lambda r: (r.a, r.b))
    return regions


def _write_footer_summary(view, edit, marks):
    """
    写入 MD 文末「标记内容汇总」（ColorMarker / Web 同款：全字符 + [CNT=n] + ORDER_FP）。
    只拷 .md 到新路径即可恢复红框。
    """
    regions = _marks_to_regions(view, marks)
    raw_items = []
    numbered_lines = []
    for i, reg in enumerate(regions):
        content = view.substr(reg).strip()
        if not content:
            continue
        cnt = _occurrence_index_in_body(view, reg)
        raw_items.append(content)
        numbered_lines.append("%d. [CNT=%d] %s" % (i + 1, cnt, content))

    order_fp = _compute_order_fingerprint(raw_items)
    summary_text = "\n\n" + ST3_SUMMARY_LINE + "\n"
    summary_text += "标记内容汇总\n"
    summary_text += ST3_SUMMARY_LINE + "\n"
    summary_text += "COUNT: %d\n" % len(raw_items)
    summary_text += "ORDER_FP: %s\n" % order_fp
    if numbered_lines:
        summary_text += "\n".join(numbered_lines)
    else:
        summary_text += "暂无标记内容"
    summary_text += "\n" + ST3_SUMMARY_LINE

    full = view.substr(sublime.Region(0, view.size()))
    summary_start = _find_summary_start(full)
    if summary_start != -1:
        view.erase(edit, sublime.Region(summary_start, view.size()))
    view.insert(edit, view.size(), summary_text)


def _save_marks(view, marks):
    sidecar = _sidecar_path(view)
    path = view.file_name()
    if not sidecar or not path:
        return
    cleaned = []
    seen = set()
    for item in marks:
        start = item.get("start")
        end = item.get("end")
        if start is not None and end is not None:
            key = "%s:%s" % (start, end)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(
                {
                    "line": int(item.get("line") or 0) or _offset_to_line(view.substr(sublime.Region(0, view.size())), int(start)),
                    "name": str(item.get("name") or "").strip(),
                    "start": int(start),
                    "end": int(end),
                }
            )
            continue
        line = int(item.get("line") or 0)
        if line < 1 or line in seen:
            continue
        seen.add(line)
        cleaned.append(_sidecar_item(view, item))
    cleaned.sort(key=lambda x: x.get("start", x.get("line", 0)))
    payload = {
        "version": 2,
        "schema": "sublime-span-marks",
        "file": os.path.basename(path),
        "abs_path": os.path.abspath(path),
        "marks": cleaned,
    }
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _line_region(view, line):
    if line < 1:
        return None
    pt = view.text_point(line - 1, 0)
    if pt < 0:
        return None
    return view.line(pt)


def _current_line(view):
    return view.rowcol(view.sel()[0].begin())[0] + 1


def _apply_regions(view, marks):
    full = view.substr(sublime.Region(0, view.size()))
    summary_start = _find_summary_start(full)
    content_end = summary_start if summary_start != -1 else view.size()
    regions = []
    for item in marks:
        start = item.get("start")
        end = item.get("end")
        if start is not None and end is not None:
            reg = sublime.Region(int(start), int(end))
            if reg.a >= 0 and reg.b <= content_end and reg.a < reg.b:
                regions.append(reg)
                continue
        reg = _line_region(view, int(item.get("line") or 0))
        if reg is not None and reg.b <= content_end:
            regions.append(reg)
    view.settings().set(_APPLYING_KEY, True)
    try:
        view.erase_regions(REGION_KEY)
        if not regions:
            return
        view.add_regions(
            REGION_KEY,
            regions,
            "markup.deleted.diff",
            "dot",
            sublime.DRAW_NO_FILL | sublime.DRAW_OUTLINED,
        )
    finally:
        view.settings().erase(_APPLYING_KEY)


def _reload_and_apply(view, force=False, status=True):
    if not view or not view.is_valid() or view.settings().get("is_widget"):
        return
    vid = view.id()
    if not force and vid in _LOADED_VIEWS and view.get_regions(REGION_KEY):
        return
    if view.size() == 0:
        sublime.set_timeout(lambda: _reload_and_apply(view, force=force, status=status), 500)
        return
    marks = _load_marks(view)
    _apply_regions(view, marks)
    _LOADED_VIEWS.add(vid)
    if status and marks:
        sublime.status_message("SBA 行标记：已从文末汇总恢复 %d 处" % len(marks))


def _remap_marks_by_anchor(view, marks):
    if not marks:
        return []
    full = view.substr(sublime.Region(0, view.size()))
    summary_start = _find_summary_start(full)
    body = full[:summary_start] if summary_start != -1 else full
    lines = body.splitlines()
    out = []
    seen = set()
    max_scan = max(len(lines), 1) + 2

    for item in marks:
        start = item.get("start")
        end = item.get("end")
        anchor = str(item.get("name") or "").strip()
        if start is not None and end is not None and anchor:
            span = _find_item_span(body, anchor, None)
            if span:
                pick, end_i, matched = span
                key = "%s:%s" % (pick, end_i)
                if key not in seen:
                    seen.add(key)
                    out.append({"start": pick, "end": end_i, "name": matched, "line": _offset_to_line(body, pick)})
                continue

        old_line = int(item.get("line") or 0)
        if old_line < 1:
            continue
        if not anchor and 1 <= old_line <= len(lines):
            anchor = lines[old_line - 1].strip().replace("\n", " ")[:120]
        if not anchor:
            continue

        found = None
        for delta in range(0, max_scan):
            candidates = [old_line - 1] if delta == 0 else [old_line - 1 + delta, old_line - 1 - delta]
            for idx in candidates:
                if 0 <= idx < len(lines):
                    cur = lines[idx].strip().replace("\n", " ")[:120]
                    if cur == anchor or (anchor and anchor in cur):
                        found = idx + 1
                        break
            if found is not None:
                break

        if found is not None:
            reg = _line_region(view, found)
            if reg is None:
                continue
            span = _find_item_span(body, anchor, None) or (reg.a, reg.b, anchor)
            pick, end_i, matched = span
            key = "%s:%s" % (pick, end_i)
            if key in seen:
                continue
            seen.add(key)
            out.append({"start": pick, "end": end_i, "name": matched, "line": found})

    out.sort(key=lambda x: x["start"])
    return out


class SbaLineMarksEventListener(sublime_plugin.EventListener):
    def on_load(self, view):
        if view.settings().get("is_widget"):
            return
        sublime.set_timeout(lambda: _reload_and_apply(view, force=True), 600)

    def on_activated(self, view):
        if view.settings().get("is_widget"):
            return
        if not view.get_regions(REGION_KEY):
            sublime.set_timeout(lambda: _reload_and_apply(view), 400)


class SbaToggleLineMarkCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        if not view.file_name():
            sublime.status_message("SBA 行标记：请先保存文件")
            return
        line = _current_line(view)
        marks = _load_marks(view)
        sel = view.sel()[0] if view.sel() else None
        if sel and not sel.empty():
            start = min(sel.a, sel.b)
            end = max(sel.a, sel.b)
            name = view.substr(sublime.Region(start, end)).strip().replace("\n", " ")[:120]
            idx = next((i for i, m in enumerate(marks) if m.get("start") == start and m.get("end") == end), -1)
            if idx >= 0:
                marks.pop(idx)
                sublime.status_message("SBA 行标记：已取消选区标记")
            else:
                overlap = [
                    i
                    for i, m in enumerate(marks)
                    if int(m.get("start", 0)) < end and start < int(m.get("end", 0))
                ]
                for i in sorted(overlap, reverse=True):
                    marks.pop(i)
                if overlap:
                    sublime.status_message("SBA 行标记：已取消 %d 处重叠标记" % len(overlap))
                else:
                    marks.append({"line": line, "start": start, "end": end, "name": name})
                    sublime.status_message("SBA 行标记：已标记选中文字")
        else:
            idx = next((i for i, m in enumerate(marks) if int(m.get("line") or 0) == line), -1)
            if idx >= 0:
                marks.pop(idx)
                sublime.status_message("SBA 行标记：已取消第 %d 行" % line)
            else:
                reg = _line_region(view, line)
                entry = {"line": line, "name": _line_label(view, line)}
                if reg is not None:
                    entry["start"] = reg.a
                    entry["end"] = reg.b
                marks.append(entry)
                sublime.status_message("SBA 行标记：已标记第 %d 行" % line)
        marks.sort(key=lambda x: x.get("start", x.get("line", 0)))
        _write_footer_summary(view, edit, marks)
        _save_marks(view, marks)
        _apply_regions(view, marks)
        # 同步 ColorMarker 红框键，便于 Ctrl+Alt+方向键跳转与 reload
        cm_regs = _marks_to_regions(view, marks)
        if cm_regs:
            view.add_regions(
                "color_marker_regions",
                cm_regs,
                "markup.deleted.diff",
                "dot",
                sublime.DRAW_NO_FILL | sublime.DRAW_OUTLINED,
            )
        else:
            view.erase_regions("color_marker_regions")


class SbaGotoLineMarkCommand(sublime_plugin.TextCommand):
    def run(self, edit, where="next"):
        view = self.view
        marks = _load_marks(view)
        if not marks:
            sublime.status_message("SBA 行标记：暂无标记")
            return
        cur = view.sel()[0].begin() if view.sel() else 0
        target = None
        wrapped = False
        if where == "prev":
            for item in reversed(marks):
                if int(item.get("start", 0)) < cur:
                    target = item
                    break
            if target is None:
                target = marks[-1]
                wrapped = True
        else:
            for item in marks:
                if int(item.get("start", 0)) > cur:
                    target = item
                    break
            if target is None:
                target = marks[0]
                wrapped = True
        start = target.get("start")
        end = target.get("end")
        if start is not None and end is not None:
            reg = sublime.Region(int(start), int(end))
        else:
            reg = _line_region(view, int(target["line"]))
        if reg is None:
            sublime.status_message("SBA 行标记：标记已失效")
            return
        view.show(reg)
        view.sel().clear()
        view.sel().add(reg)
        if wrapped:
            sublime.status_message("SBA 行标记：首尾传递")
        else:
            sublime.status_message("SBA 行标记：已跳转")


class SbaReloadLineMarksCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _LOADED_VIEWS.discard(self.view.id())
        _reload_and_apply(self.view, force=True)
