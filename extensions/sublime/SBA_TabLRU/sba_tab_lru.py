# -*- coding: utf-8 -*-
"""SBA TabLRU - 记录最近打开/编辑的原文件路径，启动时恢复最近 10 个（FIFO 淘汰）。"""
import json
import os
import time

import sublime
import sublime_plugin

STORE_MAX = 30
RESTORE_MAX = 10
STARTUP_DELAY_MS = 400
BATCH_SIZE = 3
BATCH_DELAY_MS = 30
LRU_FILE = os.path.join(os.path.dirname(__file__), "sba_tab_lru.json")
_STARTUP_DONE = False


def _norm_path(path):
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except Exception:
        return os.path.normcase(os.path.normpath(path))


def _load_items():
    if not os.path.isfile(LRU_FILE):
        return []
    try:
        with open(LRU_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return []

    items = []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        raw_items = data["items"]
    elif isinstance(data, list):
        # 兼容旧版：纯路径数组
        raw_items = [{"path": p, "ts": float(i)} for i, p in enumerate(data)]
    else:
        return []

    seen = set()
    for entry in raw_items:
        if isinstance(entry, str):
            path = entry
            ts = 0.0
        elif isinstance(entry, dict):
            path = entry.get("path")
            ts = entry.get("ts", 0.0)
        else:
            continue
        if not isinstance(path, str) or not path.strip():
            continue
        norm = _norm_path(path)
        if not norm or norm in seen:
            continue
        if not os.path.isfile(path):
            continue
        seen.add(norm)
        try:
            ts = float(ts)
        except Exception:
            ts = 0.0
        items.append({"path": path, "ts": ts, "norm": norm})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def _save_items(items):
    payload = {
        "version": 2,
        "items": [
            {"path": it["path"], "ts": it["ts"]}
            for it in items[:STORE_MAX]
        ],
    }
    try:
        with open(LRU_FILE, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _touch(path, ts=None):
    norm = _norm_path(path)
    if not norm or not os.path.isfile(path):
        return
    if ts is None:
        ts = time.time()

    items = _load_items()
    kept = [it for it in items if it["norm"] != norm]
    kept.insert(0, {"path": path, "ts": ts, "norm": norm})
    _save_items(kept[:STORE_MAX])


def _collect_open_paths():
    open_paths = set()
    for window in sublime.windows():
        if not window:
            continue
        for view in window.views():
            path = view.file_name()
            if path:
                open_paths.add(_norm_path(path))
    return open_paths


def _candidate_paths():
    open_paths = _collect_open_paths()
    out = []
    seen = set()
    for item in _load_items():
        norm = item["norm"]
        if norm in open_paths or norm in seen:
            continue
        seen.add(norm)
        out.append(item["path"])
        if len(out) >= RESTORE_MAX:
            break
    return out


def _open_in_batches(window, paths, index=0):
    batch = paths[index:index + BATCH_SIZE]
    for path in batch:
        try:
            # 直接打开磁盘原文件（Windows 路径含 : 不能加 ENCODED_POSITION）
            window.open_file(path)
        except Exception:
            pass

    next_index = index + BATCH_SIZE
    if next_index < len(paths):
        sublime.set_timeout(
            lambda: _open_in_batches(window, paths, next_index),
            BATCH_DELAY_MS,
        )
        return

    if paths:
        # 恢复完成后聚焦「最近打开」的那一个原文件
        recent = paths[-1]
        sublime.set_timeout(
            lambda p=recent: window.open_file(p),
            50,
        )
        sublime.status_message("SBA TabLRU: 已恢复 %d 个原文件" % len(paths))


def _restore(window):
    paths = _candidate_paths()
    if not paths:
        return 0
    # 先打开较早的，最后聚焦最近的
    _open_in_batches(window, list(reversed(paths)), 0)
    return len(paths)


def _restore_startup():
    global _STARTUP_DONE
    if _STARTUP_DONE:
        return
    window = sublime.active_window()
    if not window:
        sublime.set_timeout(_restore_startup, STARTUP_DELAY_MS)
        return
    _STARTUP_DONE = True
    _restore(window)


def plugin_loaded():
    sublime.set_timeout(_restore_startup, STARTUP_DELAY_MS)


class SbaTabLruEventListener(sublime_plugin.EventListener):
    def _maybe_touch(self, view):
        if not view or view.settings().get("is_widget"):
            return
        path = view.file_name()
        if not path:
            return
        _touch(path)

    def on_load(self, view):
        self._maybe_touch(view)

    def on_activated(self, view):
        self._maybe_touch(view)

    def on_post_save(self, view):
        path = view.file_name()
        if path:
            _touch(path)


class SbaTabLruRestoreCommand(sublime_plugin.WindowCommand):
    def run(self):
        _restore(self.window)
