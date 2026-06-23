"""AI 对话本地文件操作（白名单根目录内 CRUD/移动复制/查找/grep，防路径逃逸）。"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from .fs_browse import browse, is_under_allowed_root

_LOG = logging.getLogger("sba.local_file_ops")

_DEFAULT_READ_LIMIT = 50_000
_MAX_READ_LIMIT = 200_000
_MAX_WRITE_BYTES = 512 * 1024
_MAX_FIND_RESULTS = 2000
_MAX_WALK_FILES = 80_000
_MAX_GREP_MATCHES = 500
_MAX_GREP_FILE_BYTES = 2 * 1024 * 1024
_DEFAULT_LIST_RECURSIVE_LIMIT = 500

_SKIP_DIR_NAMES: Set[str] = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    "dist",
    "build",
    ".pytest_cache",
    ".cursor",
    ".mypy_cache",
    ".tox",
    "target",
}


def _resolve_allowed_path(path: str, *, must_exist: bool = True) -> Path:
    raw = (path or "").strip()
    if not raw:
        raise ValueError("缺少 path")
    target = Path(raw).resolve()
    if not is_under_allowed_root(target):
        raise PermissionError("路径不在允许的白名单内")
    if must_exist and not target.exists():
        raise FileNotFoundError(f"路径不存在: {target}")
    return target


def _resolve_allowed_dest(path: str) -> Path:
    """目标路径可以尚不存在，但父目录须在白名单内。"""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("缺少 path")
    target = Path(raw).resolve()
    check = target if target.exists() else target.parent
    if not is_under_allowed_root(check):
        raise PermissionError("路径不在允许的白名单内")
    return target


def _stat_entry(p: Path) -> Dict[str, Any]:
    try:
        st = p.stat()
        kind = "dir" if p.is_dir() else "file"
        return {
            "name": p.name,
            "path": str(p),
            "type": kind,
            "size_bytes": st.st_size if kind == "file" else None,
            "modified_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
    except OSError:
        return {"name": p.name, "path": str(p), "type": "unknown"}


def _should_skip_dir(name: str, *, respect_skip: bool) -> bool:
    if not respect_skip:
        return False
    return name in _SKIP_DIR_NAMES or name.startswith(".")


def _iter_files(
    root: Path,
    *,
    respect_skip_dirs: bool = True,
    max_files: int = _MAX_WALK_FILES,
) -> Iterator[Path]:
    """深度遍历白名单内目录下的文件。"""
    count = 0
    stack: List[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        dirs: List[Path] = []
        for ent in entries:
            name = ent.name
            if name in (".", ".."):
                continue
            try:
                if ent.is_dir(follow_symlinks=False):
                    if _should_skip_dir(name, respect_skip=respect_skip_dirs):
                        continue
                    dirs.append(Path(ent.path))
                elif ent.is_file(follow_symlinks=False):
                    yield Path(ent.path)
                    count += 1
                    if count >= max_files:
                        return
            except OSError:
                continue
        stack.extend(reversed(dirs))


def list_local_path(
    path: str = "",
    recursive: bool = False,
    max_depth: int = 3,
    limit: int = _DEFAULT_LIST_RECURSIVE_LIMIT,
    respect_skip_dirs: bool = True,
) -> Dict[str, Any]:
    """列举白名单根目录或指定目录；recursive=true 时递归（受 max_depth/limit 约束）。"""
    if not recursive:
        out = browse(path)
        _LOG.info(
            "[AI问答-本地文件|local_file_ops.list_local_path|path=%s|工具执行|完成] "
            "列举目录; ok=%s; count=%s; recursive=false",
            path or "(roots)",
            out.get("ok"),
            len(out.get("entries") or []),
        )
        return out

    try:
        if not path or not path.strip():
            roots = browse("")["entries"]
            entries = []
            for r in roots[:20]:
                sub = list_local_path(
                    r["path"],
                    recursive=True,
                    max_depth=max_depth,
                    limit=limit - len(entries),
                    respect_skip_dirs=respect_skip_dirs,
                )
                if sub.get("ok"):
                    entries.extend(sub.get("entries") or [])
                if len(entries) >= limit:
                    break
            return {"ok": True, "entries": entries[:limit], "recursive": True, "truncated": len(entries) >= limit}

        target = _resolve_allowed_path(path, must_exist=True)
        if not target.is_dir():
            return {"ok": False, "error": "不是目录", "path": str(target)}

        depth_lim = max(1, min(int(max_depth or 3), 20))
        lim = max(1, min(int(limit or _DEFAULT_LIST_RECURSIVE_LIMIT), 5000))
        entries: List[Dict[str, Any]] = []
        truncated = False

        def _walk(cur: Path, depth: int) -> None:
            nonlocal truncated
            if truncated or depth > depth_lim:
                return
            try:
                names = sorted(os.listdir(cur))
            except OSError:
                return
            for name in names:
                if truncated:
                    return
                if name in (".", ".."):
                    continue
                p = cur / name
                try:
                    if p.is_dir():
                        if _should_skip_dir(name, respect_skip=respect_skip_dirs):
                            continue
                        entries.append(_stat_entry(p))
                        if len(entries) >= lim:
                            truncated = True
                            return
                        _walk(p, depth + 1)
                    elif p.is_file():
                        entries.append(_stat_entry(p))
                        if len(entries) >= lim:
                            truncated = True
                            return
                except OSError:
                    continue

        _walk(target, 1)
        out = {
            "ok": True,
            "current": str(target),
            "entries": entries,
            "recursive": True,
            "max_depth": depth_lim,
            "truncated": truncated,
            "count": len(entries),
        }
    except (ValueError, PermissionError, FileNotFoundError) as ex:
        out = {"ok": False, "error": str(ex), "entries": []}

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.list_local_path|path=%s|工具执行|完成] "
        "递归列举; ok=%s; count=%s; truncated=%s",
        path or "(roots)",
        out.get("ok"),
        len(out.get("entries") or []),
        out.get("truncated"),
    )
    return out


def read_local_file(path: str, limit: int = _DEFAULT_READ_LIMIT, offset: int = 0) -> Dict[str, Any]:
    """读取白名单内文本文件（UTF-8，支持 offset 行偏移）。"""
    lim = max(1000, min(int(limit or _DEFAULT_READ_LIMIT), _MAX_READ_LIMIT))
    off = max(0, int(offset or 0))
    try:
        target = _resolve_allowed_path(path, must_exist=True)
    except (ValueError, PermissionError, FileNotFoundError) as ex:
        return {"ok": False, "error": str(ex), "path": path}

    if not target.is_file():
        return {"ok": False, "error": "不是文件", "path": str(target)}

    size = target.stat().st_size
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as ex:
        return {"ok": False, "error": str(ex), "path": str(target)}

    lines = text.splitlines(keepends=True)
    if off:
        text = "".join(lines[off:])
    truncated = len(text) > lim
    if truncated:
        text = text[:lim]

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.read_local_file|%s|工具执行|完成] "
        "读取文件; ok=true; offset=%s; returned=%s",
        target.name,
        off,
        len(text),
    )
    return {
        "ok": True,
        "path": str(target),
        "size_bytes": size,
        "text": text,
        "truncated": truncated,
        "offset": off,
        "encoding": "utf-8",
    }


def write_local_file(path: str, content: str, append: bool = False) -> Dict[str, Any]:
    """写入白名单内文本文件（可新建；append 为 True 时追加）。"""
    raw = (path or "").strip()
    if not raw:
        return {"ok": False, "error": "缺少 path"}
    body = content if content is not None else ""
    if len(body.encode("utf-8")) > _MAX_WRITE_BYTES:
        return {"ok": False, "error": f"内容超过 {_MAX_WRITE_BYTES} 字节上限", "path": raw}

    try:
        target = _resolve_allowed_dest(raw)
        if target.exists() and target.is_dir():
            return {"ok": False, "error": "目标是目录，不能写入", "path": str(target)}
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        size = target.stat().st_size
    except (PermissionError, OSError, ValueError) as ex:
        return {"ok": False, "error": str(ex), "path": raw}

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.write_local_file|%s|工具执行|完成] "
        "写入文件; ok=true; append=%s; size=%s",
        target.name,
        append,
        size,
    )
    return {
        "ok": True,
        "path": str(target),
        "size_bytes": size,
        "append": bool(append),
        "created": not existed,
    }


def mkdir_local_path(path: str, parents: bool = True) -> Dict[str, Any]:
    """创建目录（可递归创建父目录）。"""
    try:
        target = _resolve_allowed_dest(path)
        existed = target.exists()
        if existed and not target.is_dir():
            return {"ok": False, "error": "路径已存在且不是目录", "path": str(target)}
        target.mkdir(parents=bool(parents), exist_ok=True)
    except (PermissionError, OSError, ValueError) as ex:
        return {"ok": False, "error": str(ex), "path": path}

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.mkdir_local_path|%s|工具执行|完成] "
        "创建目录; ok=true; created=%s",
        target.name,
        not existed,
    )
    return {"ok": True, "path": str(target), "created": not existed}


def move_local_path(source: str, dest: str, overwrite: bool = False) -> Dict[str, Any]:
    """移动或重命名文件/目录（均在白名单内）。"""
    try:
        src = _resolve_allowed_path(source, must_exist=True)
        dst = _resolve_allowed_dest(dest)
        if dst.exists():
            if not overwrite:
                return {"ok": False, "error": "目标已存在，请设 overwrite=true 或更换目标", "dest": str(dst)}
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        final = shutil.move(str(src), str(dst))
    except (PermissionError, OSError, ValueError, FileNotFoundError) as ex:
        return {"ok": False, "error": str(ex), "source": source, "dest": dest}

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.move_local_path|src->dest|工具执行|完成] "
        "移动; ok=true; from=%s; to=%s",
        src.name,
        Path(final).name,
    )
    return {"ok": True, "source": str(src), "dest": str(final), "moved": True}


def copy_local_path(source: str, dest: str, overwrite: bool = False, recursive: bool = True) -> Dict[str, Any]:
    """复制文件/目录到目标（粘贴语义：copy source -> dest）。"""
    try:
        src = _resolve_allowed_path(source, must_exist=True)
        dst = _resolve_allowed_dest(dest)
        if dst.exists() and not overwrite:
            return {"ok": False, "error": "目标已存在，请设 overwrite=true", "dest": str(dst)}
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)

        if src.is_file():
            shutil.copy2(src, dst)
            copied = 1
        elif src.is_dir():
            if recursive:
                shutil.copytree(src, dst)
                copied = sum(1 for _ in _iter_files(dst, respect_skip_dirs=False, max_files=_MAX_WALK_FILES))
            else:
                dst.mkdir(parents=True, exist_ok=True)
                copied = 0
                for name in os.listdir(src):
                    sp = src / name
                    dp = dst / name
                    if sp.is_file():
                        shutil.copy2(sp, dp)
                        copied += 1
        else:
            return {"ok": False, "error": "源不是文件或目录", "source": str(src)}
    except (PermissionError, OSError, ValueError, FileNotFoundError) as ex:
        return {"ok": False, "error": str(ex), "source": source, "dest": dest}

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.copy_local_path|src->dest|工具执行|完成] "
        "复制; ok=true; recursive=%s; files=%s",
        recursive,
        copied,
    )
    return {
        "ok": True,
        "source": str(src),
        "dest": str(dst),
        "copied": True,
        "recursive": bool(recursive),
        "file_count": copied,
    }


def info_local_file(path: str) -> Dict[str, Any]:
    """查询白名单内文件或目录元信息。"""
    try:
        target = _resolve_allowed_path(path, must_exist=True)
    except (ValueError, PermissionError, FileNotFoundError) as ex:
        return {"ok": False, "error": str(ex), "path": path}

    try:
        st = target.stat()
    except OSError as ex:
        return {"ok": False, "error": str(ex), "path": str(target)}

    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    kind = "dir" if target.is_dir() else "file"
    out: Dict[str, Any] = {
        "ok": True,
        "path": str(target),
        "type": kind,
        "size_bytes": st.st_size if kind == "file" else None,
        "modified_at": mtime,
    }
    if kind == "dir":
        try:
            out["entry_count"] = len(os.listdir(target))
        except OSError:
            out["entry_count"] = None
    return out


def delete_local_path(path: str, recursive: bool = False) -> Dict[str, Any]:
    """删除白名单内文件；recursive=true 时可删非空目录。"""
    try:
        target = _resolve_allowed_path(path, must_exist=True)
    except (ValueError, PermissionError, FileNotFoundError) as ex:
        return {"ok": False, "error": str(ex), "path": path}

    try:
        if target.is_file():
            target.unlink()
            kind = "file"
        elif target.is_dir():
            if not recursive:
                return {
                    "ok": False,
                    "error": "目标是目录，请设 recursive=true 删除目录树",
                    "path": str(target),
                }
            shutil.rmtree(target)
            kind = "dir"
        else:
            return {"ok": False, "error": "未知路径类型", "path": str(target)}
    except OSError as ex:
        return {"ok": False, "error": str(ex), "path": str(target)}

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.delete_local_path|%s|工具执行|完成] "
        "删除; ok=true; type=%s; recursive=%s",
        target.name,
        kind,
        recursive,
    )
    return {"ok": True, "path": str(target), "deleted": True, "type": kind, "recursive": bool(recursive)}


# 兼容旧名
delete_local_file = delete_local_path


def find_local_files(
    root: str,
    glob_pattern: str = "**/*",
    name_contains: str = "",
    min_size_bytes: int = 0,
    max_size_bytes: int = 0,
    modified_after: str = "",
    limit: int = 500,
    respect_skip_dirs: bool = True,
) -> Dict[str, Any]:
    """按 glob/名称/大小/修改时间递归查找文件（大型整理任务入口）。"""
    lim = max(1, min(int(limit or 500), _MAX_FIND_RESULTS))
    try:
        base = _resolve_allowed_path(root, must_exist=True)
    except (ValueError, PermissionError, FileNotFoundError) as ex:
        return {"ok": False, "error": str(ex), "matches": []}

    if not base.is_dir():
        return {"ok": False, "error": "root 须为目录", "path": str(base), "matches": []}

    pat = (glob_pattern or "**/*").strip()
    name_q = (name_contains or "").strip().lower()
    min_sz = max(0, int(min_size_bytes or 0))
    max_sz = max(0, int(max_size_bytes or 0))
    mtime_after: Optional[float] = None
    if modified_after:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                mtime_after = datetime.strptime(modified_after.strip(), fmt).timestamp()
                break
            except ValueError:
                continue

    matches: List[Dict[str, Any]] = []
    scanned = 0
    truncated = False

    for fp in _iter_files(base, respect_skip_dirs=respect_skip_dirs):
        scanned += 1
        rel = fp.relative_to(base).as_posix()
        fname = fp.name
        if pat and pat not in ("*", "**/*"):
            if "**" in pat:
                if not fnmatch.fnmatch(rel, pat) and not fnmatch.fnmatch(fname, pat.lstrip("**/")):
                    continue
            elif not fnmatch.fnmatch(fname, pat):
                continue
        if name_q and name_q not in fname.lower() and name_q not in rel.lower():
            continue
        try:
            st = fp.stat()
        except OSError:
            continue
        if min_sz and st.st_size < min_sz:
            continue
        if max_sz and st.st_size > max_sz:
            continue
        if mtime_after is not None and st.st_mtime < mtime_after:
            continue
        matches.append({
            "path": str(fp),
            "name": fname,
            "relative": rel,
            "size_bytes": st.st_size,
            "modified_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
        if len(matches) >= lim:
            truncated = True
            break

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.find_local_files|root|工具执行|完成] "
        "查找文件; ok=true; scanned=%s; matches=%s; truncated=%s",
        scanned,
        len(matches),
        truncated,
    )
    return {
        "ok": True,
        "root": str(base),
        "glob_pattern": pat,
        "matches": matches,
        "count": len(matches),
        "scanned_files": scanned,
        "truncated": truncated,
    }


def grep_local_files(
    pattern: str,
    path: str = "",
    glob: str = "",
    case_insensitive: bool = False,
    output_mode: str = "content",
    head_limit: int = 200,
    context_before: int = 0,
    context_after: int = 0,
    multiline: bool = False,
    respect_skip_dirs: bool = True,
) -> Dict[str, Any]:
    """
    Cursor 式内容 grep（正则搜索白名单内文本文件）。
    output_mode: content | files_with_matches | count
    """
    pat_raw = (pattern or "").strip()
    if not pat_raw:
        return {"ok": False, "error": "缺少 pattern", "matches": []}

    flags = re.IGNORECASE if case_insensitive else 0
    if multiline:
        flags |= re.MULTILINE | re.DOTALL
    try:
        rx = re.compile(pat_raw, flags)
    except re.error as ex:
        return {"ok": False, "error": f"正则无效: {ex}", "matches": []}

    lim = max(1, min(int(head_limit or 200), _MAX_GREP_MATCHES))
    ctx_b = max(0, min(int(context_before or 0), 10))
    ctx_a = max(0, min(int(context_after or 0), 10))
    mode = (output_mode or "content").strip().lower()
    if mode not in ("content", "files_with_matches", "count"):
        mode = "content"

    search_root: Optional[Path] = None
    single_file: Optional[Path] = None
    if path and path.strip():
        try:
            resolved = _resolve_allowed_path(path, must_exist=True)
        except (ValueError, PermissionError, FileNotFoundError) as ex:
            return {"ok": False, "error": str(ex), "matches": []}
        if resolved.is_file():
            single_file = resolved
        elif resolved.is_dir():
            search_root = resolved
        else:
            return {"ok": False, "error": "path 须为文件或目录", "matches": []}
    else:
        from .fs_browse import allowed_roots

        roots = allowed_roots()
        if not roots:
            return {"ok": False, "error": "无白名单根目录", "matches": []}
        search_root = roots[0]

    glob_pat = (glob or "").strip()
    content_matches: List[Dict[str, Any]] = []
    file_hits: List[str] = []
    total_count = 0
    scanned = 0
    truncated = False

    def _grep_one_file(fp: Path) -> None:
        nonlocal total_count, truncated
        if truncated:
            return
        if glob_pat and not fnmatch.fnmatch(fp.name, glob_pat):
            return
        try:
            sz = fp.stat().st_size
        except OSError:
            return
        if sz > _MAX_GREP_FILE_BYTES:
            return
        try:
            raw = fp.read_bytes()
        except OSError:
            return
        if b"\x00" in raw[:8192]:
            return
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return
        lines = text.splitlines()
        scanned_local = 0
        for i, line in enumerate(lines):
            if truncated:
                return
            if not rx.search(line):
                continue
            total_count += 1
            scanned_local += 1
            if mode == "count":
                continue
            if mode == "files_with_matches":
                if str(fp) not in file_hits:
                    file_hits.append(str(fp))
                    if len(file_hits) >= lim:
                        truncated = True
                continue
            entry: Dict[str, Any] = {
                "path": str(fp),
                "line_number": i + 1,
                "line": line[:2000],
            }
            if ctx_b or ctx_a:
                start = max(0, i - ctx_b)
                end = min(len(lines), i + ctx_a + 1)
                entry["context"] = [
                    {"line_number": j + 1, "line": lines[j][:2000]}
                    for j in range(start, end)
                ]
            content_matches.append(entry)
            if len(content_matches) >= lim:
                truncated = True
                return

    if single_file:
        scanned = 1
        _grep_one_file(single_file)
    elif search_root:
        for fp in _iter_files(search_root, respect_skip_dirs=respect_skip_dirs):
            scanned += 1
            _grep_one_file(fp)
            if truncated:
                break

    _LOG.info(
        "[AI问答-本地文件|local_file_ops.grep_local_files|pattern|工具执行|完成] "
        "grep; ok=true; mode=%s; scanned=%s; total=%s; truncated=%s",
        mode,
        scanned,
        total_count,
        truncated,
    )

    if mode == "count":
        return {
            "ok": True,
            "pattern": pat_raw,
            "output_mode": mode,
            "count": total_count,
            "scanned_files": scanned,
            "truncated": truncated,
        }
    if mode == "files_with_matches":
        return {
            "ok": True,
            "pattern": pat_raw,
            "output_mode": mode,
            "files": file_hits,
            "count": len(file_hits),
            "scanned_files": scanned,
            "truncated": truncated,
        }
    return {
        "ok": True,
        "pattern": pat_raw,
        "output_mode": mode,
        "matches": content_matches,
        "count": len(content_matches),
        "total_match_lines": total_count,
        "scanned_files": scanned,
        "truncated": truncated,
    }
