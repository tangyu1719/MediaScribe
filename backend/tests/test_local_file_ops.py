"""本地文件操作工具单元测试（使用项目内临时目录，避免沙箱 temp 权限问题）。"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from app.services.chat_tool_registry import build_internal_chat_tools
from app.services.local_file_ops import (
    copy_local_path,
    delete_local_path,
    find_local_files,
    grep_local_files,
    info_local_file,
    list_local_path,
    mkdir_local_path,
    move_local_path,
    read_local_file,
    write_local_file,
)

_TEST_ROOT = Path(__file__).resolve().parents[1] / "data" / "_test_local_file_ops"

_ALL_TOOLS = (
    "local_file_list",
    "local_file_read",
    "local_file_write",
    "local_file_mkdir",
    "local_file_move",
    "local_file_copy",
    "local_file_find",
    "local_file_grep",
    "local_file_info",
    "local_file_delete",
)


def _fresh_workdir() -> Path:
    if _TEST_ROOT.exists():
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
    _TEST_ROOT.mkdir(parents=True, exist_ok=True)
    return _TEST_ROOT


def _cleanup_workdir() -> None:
    if _TEST_ROOT.exists():
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)


def _tool_by_name(name: str):
    tools = build_internal_chat_tools(read_comments=False)
    return next(t for t in tools if getattr(t, "name", "") == name)


def test_local_file_tools_registered():
    tools = build_internal_chat_tools(read_comments=False)
    names = {getattr(t, "name", "") for t in tools}
    for expected in _ALL_TOOLS:
        assert expected in names


def test_local_file_ops_roundtrip():
    work = _fresh_workdir()
    prev = os.environ.get("FS_ALLOW_ROOTS")
    os.environ["FS_ALLOW_ROOTS"] = str(work)
    try:
        sub = work / "ai_chat_test"
        sub.mkdir()
        target = sub / "hello.txt"

        listed = list_local_path(str(sub))
        assert listed["ok"] is True
        assert listed["entries"] == []

        wrote = write_local_file(str(target), "第一行\n第二行\n")
        assert wrote["ok"] is True
        assert target.read_text(encoding="utf-8") == "第一行\n第二行\n"

        info = info_local_file(str(target))
        assert info["ok"] is True
        assert info["type"] == "file"

        read_back = read_local_file(str(target))
        assert read_back["ok"] is True
        assert "第一行" in read_back["text"]

        append = write_local_file(str(target), "第三行\n", append=True)
        assert append["ok"] is True

        deleted = delete_local_path(str(target))
        assert deleted["ok"] is True
        assert not target.exists()
    finally:
        if prev is None:
            os.environ.pop("FS_ALLOW_ROOTS", None)
        else:
            os.environ["FS_ALLOW_ROOTS"] = prev
        _cleanup_workdir()


def test_mkdir_move_copy_delete_tree():
    work = _fresh_workdir()
    prev = os.environ.get("FS_ALLOW_ROOTS")
    os.environ["FS_ALLOW_ROOTS"] = str(work)
    try:
        src_dir = work / "src_tree"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("alpha\n", encoding="utf-8")
        (src_dir / "b.py").write_text("def foo():\n    return 42\n", encoding="utf-8")

        dest_base = work / "archive"
        mk = mkdir_local_path(str(dest_base))
        assert mk["ok"] is True

        cp = copy_local_path(str(src_dir), str(dest_base / "src_copy"))
        assert cp["ok"] is True
        assert (dest_base / "src_copy" / "a.txt").exists()

        moved = move_local_path(str(src_dir / "b.py"), str(dest_base / "moved_b.py"))
        assert moved["ok"] is True
        assert not (src_dir / "b.py").exists()
        assert (dest_base / "moved_b.py").exists()

        rm = delete_local_path(str(dest_base / "src_copy"), recursive=True)
        assert rm["ok"] is True
        assert not (dest_base / "src_copy").exists()
    finally:
        if prev is None:
            os.environ.pop("FS_ALLOW_ROOTS", None)
        else:
            os.environ["FS_ALLOW_ROOTS"] = prev
        _cleanup_workdir()


def test_find_and_grep():
    work = _fresh_workdir()
    prev = os.environ.get("FS_ALLOW_ROOTS")
    os.environ["FS_ALLOW_ROOTS"] = str(work)
    try:
        docs = work / "docs"
        docs.mkdir()
        (docs / "readme.md").write_text("# Title\nWMS 系统报错处理\n", encoding="utf-8")
        (docs / "app.py").write_text("class WmsError(Exception):\n    pass\n", encoding="utf-8")

        found = find_local_files(str(work), glob_pattern="*.md", name_contains="readme")
        assert found["ok"] is True
        assert found["count"] >= 1
        assert any(m["name"] == "readme.md" for m in found["matches"])

        grep_content = grep_local_files("WMS", path=str(docs), output_mode="content")
        assert grep_content["ok"] is True
        assert grep_content["count"] >= 1
        assert "WMS" in grep_content["matches"][0]["line"]

        grep_files = grep_local_files("WmsError", path=str(docs), glob="*.py", output_mode="files_with_matches")
        assert grep_files["ok"] is True
        assert grep_files["count"] == 1

        grep_count = grep_local_files("Exception", path=str(docs), output_mode="count")
        assert grep_count["ok"] is True
        assert grep_count["count"] >= 1

        rec = list_local_path(str(docs), recursive=True, max_depth=2)
        assert rec["ok"] is True
        assert rec["count"] >= 2
    finally:
        if prev is None:
            os.environ.pop("FS_ALLOW_ROOTS", None)
        else:
            os.environ["FS_ALLOW_ROOTS"] = prev
        _cleanup_workdir()


def test_local_file_ops_rejects_outside_whitelist():
    work = _fresh_workdir()
    prev = os.environ.get("FS_ALLOW_ROOTS")
    allowed = work / "allowed"
    allowed.mkdir()
    outside = work / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.environ["FS_ALLOW_ROOTS"] = str(allowed)
    try:
        for fn, args in (
            (read_local_file, (str(outside),)),
            (write_local_file, (str(outside), "x")),
            (info_local_file, (str(outside),)),
            (delete_local_path, (str(outside),)),
            (move_local_path, (str(outside), str(allowed / "x.txt"))),
            (copy_local_path, (str(outside), str(allowed / "y.txt"))),
            (find_local_files, (str(outside),)),
            (grep_local_files, ("secret", str(outside))),
        ):
            out = fn(*args)
            assert out["ok"] is False
            assert "白名单" in out["error"]
    finally:
        if prev is None:
            os.environ.pop("FS_ALLOW_ROOTS", None)
        else:
            os.environ["FS_ALLOW_ROOTS"] = prev
        _cleanup_workdir()


def test_local_file_tools_invoke_via_registry():
    work = _fresh_workdir()
    prev = os.environ.get("FS_ALLOW_ROOTS")
    os.environ["FS_ALLOW_ROOTS"] = str(work)
    target = work / f"via_tool_{uuid.uuid4().hex[:8]}.txt"
    try:
        write_tool = _tool_by_name("local_file_write")
        read_tool = _tool_by_name("local_file_read")
        grep_tool = _tool_by_name("local_file_grep")
        find_tool = _tool_by_name("local_file_find")
        delete_tool = _tool_by_name("local_file_delete")

        w = json.loads(write_tool.invoke({"path": str(target), "content": "registry grep 测试\n"}))
        assert w["ok"] is True

        r = json.loads(read_tool.invoke({"path": str(target)}))
        assert r["ok"] is True

        g = json.loads(grep_tool.invoke({"pattern": "grep", "path": str(work), "output_mode": "content"}))
        assert g["ok"] is True
        assert g["count"] >= 1

        f = json.loads(find_tool.invoke({"root": str(work), "glob_pattern": "*.txt"}))
        assert f["ok"] is True
        assert f["count"] >= 1

        d = json.loads(delete_tool.invoke({"path": str(target)}))
        assert d["ok"] is True
        assert not target.exists()
    finally:
        if prev is None:
            os.environ.pop("FS_ALLOW_ROOTS", None)
        else:
            os.environ["FS_ALLOW_ROOTS"] = prev
        _cleanup_workdir()
