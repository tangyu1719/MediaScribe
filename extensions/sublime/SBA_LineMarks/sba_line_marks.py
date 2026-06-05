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
    for item in marks:
        if isinstance(item, int) and item >= 1:
            out.append({"line": item, "name": ""})
        elif isinstance(item, dict):
            line = int(item.get("line") or 0)
            if line >= 1:
                out.append({"line": line, "name": str(item.get("name") or "")})
    out.sort(key=lambda x: x["line"])
    return out


def _line_label(view, line):
    reg = _line_region(view, line)
    if reg is None:
        return ""
    return view.substr(reg).strip().replace("\n", " ")[:120]


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
        name = str(item.get("name") or "").strip() or _line_label(view, line)
        cleaned.append({"line": line, "name": name})
    cleaned.sort(key=lambda x: x["line"])
    payload = {
        "version": 1,
        "schema": "sublime-line-marks",
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
            out.append({"line": found, "name": label})

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
            marks.append({"line": line, "name": _line_label(view, line)})
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
                    target = int(item["line"])
                    break
            if target is None:
                target = int(marks[-1]["line"])
                wrapped = True
        else:
            for item in marks:
                if int(item["line"]) > cur:
                    target = int(item["line"])
                    break
            if target is None:
                target = int(marks[0]["line"])
                wrapped = True
        reg = _line_region(view, target)
        if reg is None:
            sublime.status_message("SBA 行标记：第 %d 行已失效" % target)
            return
        view.show(reg)
        view.sel().clear()
        view.sel().add(reg.a)
        if wrapped:
            sublime.status_message("SBA 行标记：首尾传递 -> 第 %d 行" % target)
        else:
            sublime.status_message("SBA 行标记：跳转到第 %d 行" % target)


class SbaReloadLineMarksCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        marks = _load_marks(self.view)
        _apply_regions(self.view, marks)
        sublime.status_message("SBA 行标记：已重载 %d 处" % len(marks))
