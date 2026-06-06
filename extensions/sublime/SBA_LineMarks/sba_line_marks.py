"""SBA 行标记 — 与 SuperBizAgent MD 预览侧车 JSON 互通。"""
import json
import os
import sublime
import sublime_plugin

MARKS_SUFFIX = ".sublime-marks.json"
REGION_KEY = "sba_line_marks"
_REMAP_KEY = "sba_line_marks_remap_scheduled"


def _sidecar_path(view):
    path = view.file_name()
    if not path:
        return None
    return path + MARKS_SUFFIX


def _line_label(view, line):
    reg = _line_region(view, line)
    if reg is None:
        return ""
    return view.substr(reg).strip().replace("\n", " ")[:120]


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
        if start < 0 or end <= start or end > size:
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


def _load_marks(view):
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
        line = int(norm["line"])
        if line in seen:
            continue
        seen.add(line)
        out.append(norm)
    out.sort(key=lambda x: x["line"])
    return out


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


def _save_marks(view, marks):
    sidecar = _sidecar_path(view)
    path = view.file_name()
    if not sidecar or not path:
        return
    cleaned = []
    seen = set()
    for item in marks:
        line = int(item.get("line") or 0)
        if line < 1 or line in seen:
            continue
        seen.add(line)
        cleaned.append(_sidecar_item(view, item))
    cleaned.sort(key=lambda x: x["line"])
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
    regions = []
    for item in marks:
        start = item.get("start")
        end = item.get("end")
        if start is not None and end is not None:
            reg = sublime.Region(int(start), int(end))
            if reg.a >= 0 and reg.b <= view.size() and reg.a < reg.b:
                regions.append(reg)
                continue
        reg = _line_region(view, int(item.get("line") or 0))
        if reg is not None:
            regions.append(reg)
    view.add_regions(
        REGION_KEY,
        regions,
        "bookmark",
        "bookmark",
        sublime.HIDDEN | sublime.PERSISTENT,
    )


def _remap_marks_by_anchor(view, marks):
    """编辑后按行内容锚点传递行号（与网页版 remap 同思路）。"""
    if not marks:
        return []
    lines = view.substr(sublime.Region(0, view.size())).splitlines()
    out = []
    seen = set()
    max_scan = max(len(lines), 1) + 2

    for item in marks:
        old_line = int(item.get("line") or 0)
        if old_line < 1:
            continue
        anchor = str(item.get("name") or "").strip()
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

        if found is not None and found not in seen:
            seen.add(found)
            label = lines[found - 1].strip().replace("\n", " ")[:120] if 1 <= found <= len(lines) else anchor
            reg = _line_region(view, found)
            entry = {"line": found, "name": label}
            if reg is not None:
                old_start = item.get("start")
                old_end = item.get("end")
                if old_start is not None and old_end is not None and reg is not None:
                    old_reg = _line_region(view, old_line)
                    if old_reg is not None and (int(old_start), int(old_end)) != (old_reg.a, old_reg.b):
                        pos = view.substr(sublime.Region(0, reg.b)).find(anchor)
                        if pos >= 0:
                            entry["start"] = pos
                            entry["end"] = pos + len(anchor)
                        else:
                            entry["start"] = reg.a
                            entry["end"] = min(reg.a + len(anchor), reg.b)
                    else:
                        entry["start"] = reg.a
                        entry["end"] = reg.b
                else:
                    entry["start"] = reg.a
                    entry["end"] = reg.b
            out.append(entry)

    out.sort(key=lambda x: x["line"])
    return out


def _schedule_remap(view):
    if view.settings().get("is_widget") or not view.file_name():
        return
    if view.settings().get(_REMAP_KEY):
        return
    view.settings().set(_REMAP_KEY, True)

    def _run():
        view.settings().erase(_REMAP_KEY)
        if not view.file_name():
            return
        marks = _load_marks(view)
        if not marks:
            return
        remapped = _remap_marks_by_anchor(view, marks)
        _save_marks(view, remapped)
        _apply_regions(view, remapped)

    sublime.set_timeout(_run, 300)


class SbaLineMarksEventListener(sublime_plugin.EventListener):
    def on_load(self, view):
        if view.settings().get("is_widget"):
            return
        marks = _load_marks(view)
        if marks:
            _apply_regions(view, marks)

    def on_activated(self, view):
        if view.settings().get("is_widget"):
            return
        marks = _load_marks(view)
        _apply_regions(view, marks)

    def on_modified(self, view):
        _schedule_remap(view)


class SbaToggleLineMarkCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        if not view.file_name():
            sublime.status_message("SBA 行标记：请先保存文件")
            return
        line = _current_line(view)
        marks = _load_marks(view)
        idx = next((i for i, m in enumerate(marks) if int(m["line"]) == line), -1)
        if idx >= 0:
            marks.pop(idx)
            sublime.status_message("SBA 行标记：已取消第 %d 行" % line)
        else:
            sel = view.sel()[0] if view.sel() else None
            marks = [m for m in marks if int(m["line"]) != line]
            if sel and not sel.empty():
                start = min(sel.a, sel.b)
                end = max(sel.a, sel.b)
                name = view.substr(sublime.Region(start, end)).strip().replace("\n", " ")[:120]
                marks.append({"line": line, "start": start, "end": end, "name": name})
                sublime.status_message("SBA 行标记：已标记选中文字")
            else:
                reg = _line_region(view, line)
                entry = {"line": line, "name": _line_label(view, line)}
                if reg is not None:
                    entry["start"] = reg.a
                    entry["end"] = reg.b
                marks.append(entry)
                sublime.status_message("SBA 行标记：已标记第 %d 行" % line)
        marks.sort(key=lambda x: x["line"])
        _save_marks(view, marks)
        _apply_regions(view, marks)


class SbaGotoLineMarkCommand(sublime_plugin.TextCommand):
    def run(self, edit, where="next"):
        view = self.view
        marks = _load_marks(view)
        if not marks:
            sublime.status_message("SBA 行标记：暂无标记")
            return
        cur = _current_line(view)
        target = None
        wrapped = False
        if where == "prev":
            for item in reversed(marks):
                if int(item["line"]) < cur:
                    target = item
                    break
            if target is None:
                target = marks[-1]
                wrapped = True
        else:
            for item in marks:
                if int(item["line"]) > cur:
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
            sublime.status_message("SBA 行标记：第 %d 行已失效" % int(target["line"]))
            return
        view.show(reg)
        view.sel().clear()
        view.sel().add(reg)
        if wrapped:
            sublime.status_message("SBA 行标记：首尾传递 -> 第 %d 行" % int(target["line"]))
        else:
            sublime.status_message("SBA 行标记：跳转到第 %d 行" % int(target["line"]))


class SbaReloadLineMarksCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        marks = _load_marks(self.view)
        _apply_regions(self.view, marks)
        sublime.status_message("SBA 行标记：已重载 %d 处" % len(marks))
