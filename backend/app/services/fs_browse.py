"""服务端路径浏览（白名单根目录下一级或多级列举，防路径逃逸）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _web_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "frontend").is_dir() and (p / "backend").is_dir():
            return p
    return here.parents[3]


def allowed_roots() -> List[Path]:
    raw = os.environ.get("FS_ALLOW_ROOTS", "").strip()
    out: List[Path] = []
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(Path(part).resolve())
            except Exception:
                continue
    if not out:
        root = _web_root()
        out.append(root)
        outp = root / "output"
        if outp.exists() or True:
            try:
                outp.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            out.append(outp.resolve())
    seen = set()
    uniq: List[Path] = []
    for p in out:
        rp = str(p)
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def _is_under(child: Path, root: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _which_root(path: Path) -> Optional[Path]:
    rp = path.resolve()
    for root in allowed_roots():
        if _is_under(rp, root) or rp == root.resolve():
            return root
    return None


def is_under_allowed_root(abs_path: Path) -> bool:
    """路径是否在 FS 白名单根目录下。"""
    return _which_root(abs_path.resolve()) is not None


def resolve_allowed_directory(path: str) -> Path:
    """解析并确保白名单内目录存在（用于另存为选文件夹）。"""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("缺少目录路径")
    target = Path(raw).resolve()
    root = _which_root(target)
    if root is None:
        raise PermissionError("目录不在允许的白名单内")
    if target.exists() and not target.is_dir():
        raise ValueError("不是目录")
    target.mkdir(parents=True, exist_ok=True)
    return target


def browse(path: str = "") -> Dict[str, Any]:
    """
    path 为空：返回白名单根目录列表。
    path 为某根下绝对路径：返回该目录下一层子项（目录与文件）。
    """
    roots = allowed_roots()
    if not path or not path.strip():
        entries = []
        for r in roots:
            try:
                entries.append(
                    {
                        "name": r.name or str(r),
                        "path": str(r),
                        "type": "dir",
                    }
                )
            except Exception:
                continue
        return {"ok": True, "entries": entries, "roots": [str(x) for x in roots]}

    target = Path(path.strip()).resolve()
    root = _which_root(target)
    if root is None:
        return {"ok": False, "error": "路径不在允许的白名单内", "entries": []}

    if not target.exists():
        return {"ok": False, "error": "路径不存在", "entries": []}
    if not target.is_dir():
        return {"ok": False, "error": "不是目录", "entries": []}

    entries: List[Dict[str, Any]] = []
    try:
        for name in sorted(os.listdir(target)):
            if name in (".", ".."):
                continue
            p = target / name
            try:
                is_dir = p.is_dir()
            except OSError:
                continue
            entries.append(
                {
                    "name": name,
                    "path": str(p.resolve()),
                    "type": "dir" if is_dir else "file",
                }
            )
    except PermissionError:
        return {"ok": False, "error": "无权限读取目录", "entries": []}

    return {"ok": True, "entries": entries, "current": str(target), "root": str(root)}
