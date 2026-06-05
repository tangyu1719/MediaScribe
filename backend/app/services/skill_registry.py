"""SKILL 轻量注册表（薄适配层）：持久化 JSON + 列表/导入/按命令检索。

后续可接 LangChain Tool 或自研执行器，对外保持 list / import / resolve_command。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Iterable

# 附件单文件上限（字节），避免 skills_registry.json 膨胀
_MAX_ATTACHMENT_BYTES = 200_000
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        "docs",
        "fixtures",
    }
)
_SKIP_ATTACHMENT_DIR_NAMES = frozenset(
    {
        "tests",
        "test",
        "__pycache__",
        "node_modules",
        ".git",
        "fixtures",
        ".pytest_cache",
    }
)
_STANDALONE_MD_SKIP = frozenset(
    {
        "README.md",
        "readme.md",
        "SKILLS_QUICK_REFERENCE.md",
        "CHANGELOG.md",
        "LICENSE.md",
    }
)
# 默认批量导入根目录（含项目内 + 用户 local_skills）
_DEFAULT_EXTRA_SKILL_ROOTS = (
    Path(r"F:\AI\local_skills"),
)
_TEXT_EXT_META: Dict[str, Tuple[str, str]] = {
    ".py": ("script", "python"),
    ".pyi": ("script", "python"),
    ".js": ("script", "javascript"),
    ".mjs": ("script", "javascript"),
    ".ts": ("script", "typescript"),
    ".tsx": ("script", "typescript"),
    ".jsx": ("script", "javascript"),
    ".sh": ("script", "shell"),
    ".ps1": ("script", "powershell"),
    ".bat": ("script", "batch"),
    ".json": ("data", "json"),
    ".yaml": ("config", "yaml"),
    ".yml": ("config", "yaml"),
    ".toml": ("config", "toml"),
    ".ini": ("config", "ini"),
    ".cfg": ("config", "ini"),
    ".env": ("config", "dotenv"),
    ".txt": ("doc", "text"),
    ".md": ("doc", "markdown"),
    ".html": ("doc", "html"),
    ".css": ("style", "css"),
    ".xml": ("data", "xml"),
    ".sql": ("script", "sql"),
    ".csv": ("data", "csv"),
}
_SKILL_MD_NAMES = ("SKILL.md", "skill.md", "SKILL.MD")

_HERE = Path(__file__).resolve()
_BASE: Path | None = None
for _p in _HERE.parents:
    if (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _BASE = _p
        break
if _BASE is None:
    _BASE = _HERE.parents[3]
_SKILLS_FILE = _BASE / "skills_registry.json"


def _normalize_command(cmd: str) -> str:
    c = (cmd or "").strip()
    if not c:
        return ""
    if not c.startswith("/"):
        c = "/" + c
    return c.lower()


def _parse_frontmatter(md: str) -> Tuple[Dict[str, str], str]:
    """解析 SKILL.md 顶部 YAML 风格 frontmatter（宽松，含无开头 --- 的变体）。"""
    meta: Dict[str, str] = {}
    body = (md or "").strip()
    fm_text = ""
    if body.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", body, re.DOTALL)
        if not m:
            return meta, body
        fm_text, body = m.group(1), m.group(2).strip()
    else:
        sep = re.search(r"\n---\s*\n", body)
        if sep and re.match(r"^[A-Za-z0-9_.-]+\s*:", body):
            fm_text, body = body[: sep.start()], body[sep.end() :].strip()
        else:
            return meta, body

    def _parse_fm_block(fm: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        lines = fm.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip() or line.strip().startswith("#"):
                i += 1
                continue
            if ":" not in line:
                i += 1
                continue
            k, v = line.split(":", 1)
            key = k.strip().lower()
            val = v.strip()
            if val in (">-", ">", "|", "|-") or (not val and i + 1 < len(lines)):
                block: List[str] = []
                fold = val in (">-", ">")
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if not nxt.strip():
                        i += 1
                        break
                    if re.match(r"^[A-Za-z0-9_.-]+\s*:", nxt) and not nxt.startswith(" "):
                        break
                    block.append(nxt.strip() if fold else nxt.rstrip())
                    i += 1
                out[key] = (" ".join(block) if fold else "\n".join(block)).strip()
                continue
            out[key] = val.strip().strip('"').strip("'")
            i += 1
        return out

    meta = _parse_fm_block(fm_text)
    return meta, body


def _load_raw() -> Dict[str, Any]:
    if not _SKILLS_FILE.exists():
        return {"skills": []}
    try:
        return json.loads(_SKILLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"skills": []}


def _save_raw(data: Dict[str, Any]) -> None:
    _SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SKILLS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _attachment_kind(ext: str) -> Tuple[str, str]:
    ext_l = (ext or "").lower()
    if ext_l in _TEXT_EXT_META:
        return _TEXT_EXT_META[ext_l]
    if ext_l in (".requirements",):
        return "config", "text"
    name = ext_l.lstrip(".") or "text"
    if "require" in name:
        return "config", "text"
    return "other", name


def _read_text_file(path: Path) -> Optional[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > _MAX_ATTACHMENT_BYTES:
        return None
    if b"\x00" in raw[:8000]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None


def _collect_attachments(skill_dir: Path, skill_md: Path) -> List[Dict[str, Any]]:
    """扫描 skill 目录内除主 SKILL.md 外的可读文本附件。"""
    out: List[Dict[str, Any]] = []
    skill_dir = skill_dir.resolve()
    skill_md = skill_md.resolve()
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.resolve() == skill_md:
            continue
        rel = p.relative_to(skill_dir).as_posix()
        parts = rel.split("/")
        if any(seg in _SKIP_DIR_NAMES or seg in _SKIP_ATTACHMENT_DIR_NAMES or seg.startswith(".") for seg in parts[:-1]):
            continue
        if p.name.startswith(".") and p.name not in (".env.example",):
            continue
        ext = p.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".woff", ".woff2"):
            continue
        text = _read_text_file(p)
        if text is None:
            continue
        kind, language = _attachment_kind(ext if ext else p.name.lower())
        out.append(
            {
                "path": rel,
                "name": p.name,
                "kind": kind,
                "language": language,
                "size": len(text.encode("utf-8")),
                "text": text,
            }
        )
    return out


def _find_skill_md(skill_dir: Path) -> Optional[Path]:
    for name in _SKILL_MD_NAMES:
        p = skill_dir / name
        if p.is_file():
            return p
    return None


def _is_skippable_scan_dir(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or name in _SKIP_DIR_NAMES:
        return True
    return False


def discover_skill_dirs(root: Path, *, recursive: bool = True) -> List[Path]:
    """发现含 SKILL.md 的目录；recursive=True 时递归子树（batch-*/garden-skills 等）。"""
    entries = discover_skill_entries(root, recursive=recursive)
    return [d for d, _ in entries]


def discover_skill_entries(root: Path, *, recursive: bool = True) -> List[Tuple[Path, Path]]:
    """返回 (skill_dir, skill_md_path) 列表；含递归 SKILL.md 与根目录独立 *.md。"""
    root = root.resolve()
    if not root.is_dir():
        return []
    out: List[Tuple[Path, Path]] = []
    seen: set[str] = set()

    def add(skill_dir: Path, skill_md: Path) -> None:
        key = str(skill_md.resolve())
        if key in seen:
            return
        seen.add(key)
        out.append((skill_dir.resolve(), skill_md.resolve()))

    if recursive:
        for pattern in ("SKILL.md", "skill.md", "SKILL.MD"):
            for md in sorted(root.rglob(pattern)):
                if any(_is_skippable_scan_dir(p) for p in md.parent.relative_to(root).parents):
                    continue
                if any(part in _SKIP_DIR_NAMES for part in md.relative_to(root).parts[:-1]):
                    continue
                add(md.parent, md)
    else:
        if md := _find_skill_md(root):
            add(root, md)
        for child in sorted(root.iterdir()):
            if not child.is_dir() or _is_skippable_scan_dir(child):
                continue
            if md := _find_skill_md(child):
                add(child, md)

    # 根目录独立 frontmatter .md（如 aigc-down-skill.md）
    for md in sorted(root.glob("*.md")):
        if md.name in _STANDALONE_MD_SKIP:
            continue
        if md.name.lower() in ("skill.md",):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, _ = _parse_frontmatter(text)
        if not (meta.get("name") or "").strip() or not (meta.get("description") or "").strip():
            continue
        add(md.parent, md)

    out.sort(key=lambda x: str(x[1]).lower())
    return out


def list_skills() -> List[Dict[str, Any]]:
    data = _load_raw()
    skills = data.get("skills") or []
    out: List[Dict[str, Any]] = []
    for s in skills:
        body = (s.get("body_md") or "").strip()
        preview = " ".join(body.split())[:240]
        if len(body) > 240:
            preview += "…"
        atts = s.get("attachments") or []
        board = s.get("board") if isinstance(s.get("board"), dict) else None
        display = s.get("display") if isinstance(s.get("display"), dict) else None
        out.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "description": s.get("description"),
                "command": s.get("command"),
                "created_at": s.get("created_at"),
                "source": s.get("source"),
                "version": (s.get("version") or "1.0.0"),
                "preview": preview,
                "attachment_count": len(atts) if isinstance(atts, list) else 0,
                "board": board,
                "display": display,
            }
        )
    return out


def get_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    for s in _load_raw().get("skills") or []:
        if s.get("id") == skill_id:
            return dict(s)
    return None


def find_skill_by_name(name: str) -> Optional[Dict[str, Any]]:
    want = (name or "").strip().lower()
    if not want:
        return None
    for s in _load_raw().get("skills") or []:
        if (s.get("name") or "").strip().lower() == want:
            return dict(s)
    return None


_COMMAND_OVERRIDES: Dict[str, str] = {
    "longpage-html-3uds": "/3uds",
    "impeccable": "/impeccable",
    "ui-ux-pro-max": "/ui-ux",
    "htet-gui-macro-regression-sop": "/htet",
    "light-diagram-html-suite": "/diagram",
}


def _default_command_for_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    low = n.lower()
    if low in _COMMAND_OVERRIDES:
        return _normalize_command(_COMMAND_OVERRIDES[low])
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return _normalize_command("/" + slug) if slug else ""


def _update_skill_row(
    skill_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    body_md: Optional[str] = None,
    command: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    source: Optional[str] = None,
    version: Optional[str] = None,
    board: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    data = _load_raw()
    skills: List[Dict[str, Any]] = list(data.get("skills") or [])
    idx = next((i for i, s in enumerate(skills) if s.get("id") == skill_id), -1)
    if idx < 0:
        return None, "SKILL 不存在"
    row = dict(skills[idx])
    if name is not None and str(name).strip():
        row["name"] = str(name).strip()
    if description is not None and str(description).strip():
        row["description"] = str(description).strip()
    if body_md is not None:
        row["body_md"] = str(body_md).strip()
    if attachments is not None:
        row["attachments"] = attachments
    if source is not None:
        row["source"] = source
    if version is not None:
        row["version"] = version
    if board is not None:
        row["board"] = board
    if command is not None:
        cmd = _normalize_command(str(command)) if str(command).strip() else ""
        for j, other in enumerate(skills):
            if j == idx:
                continue
            oc = (other.get("command") or "").strip().lower()
            if cmd and oc == cmd:
                return None, f"命令 {cmd} 已被 SKILL「{other.get('name')}」占用"
        row["command"] = cmd
    skills[idx] = row
    data["skills"] = skills
    _save_raw(data)
    return get_skill(skill_id), None


def delete_skill(skill_id: str) -> bool:
    data = _load_raw()
    skills = data.get("skills") or []
    new_sk = [s for s in skills if s.get("id") != skill_id]
    if len(new_sk) == len(skills):
        return False
    data["skills"] = new_sk
    _save_raw(data)
    return True


def patch_skill(skill_id: str, patch: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """部分更新 SKILL（至少一项）。command 会规范化并校验唯一性。"""
    if not (skill_id or "").strip():
        return None, "skill_id 无效"
    if not isinstance(patch, dict) or not patch:
        return None, "body 须为非空 JSON 对象"
    data = _load_raw()
    skills: List[Dict[str, Any]] = list(data.get("skills") or [])
    idx = next((i for i, s in enumerate(skills) if s.get("id") == skill_id), -1)
    if idx < 0:
        return None, "SKILL 不存在"
    row = dict(skills[idx])

    if "command" in patch:
        cmd = _normalize_command(str(patch.get("command") or ""))
        for j, other in enumerate(skills):
            if j == idx:
                continue
            oc = (other.get("command") or "").strip().lower()
            if cmd and oc == cmd:
                return None, f"命令 {cmd} 已被 SKILL「{other.get('name')}」占用"
        row["command"] = cmd

    if "name" in patch:
        n = str(patch.get("name") or "").strip()
        if n:
            row["name"] = n

    if "description" in patch:
        d = str(patch.get("description") or "").strip()
        if d:
            row["description"] = d

    if "body_md" in patch:
        row["body_md"] = str(patch.get("body_md") or "").strip()

    prev = dict(skills[idx])
    skills[idx] = row
    data["skills"] = skills
    _save_raw(data)
    body_changed = "body_md" in patch and str(patch.get("body_md") or "") != str(
        prev.get("body_md") or ""
    )
    meta_changed = any(
        k in patch
        for k in ("body_md", "description", "name")
    )
    if meta_changed:
        try:
            from .skill_version_service import ensure_initial_version, record_version

            ensure_initial_version(row)
            if body_changed or len(patch) > 1:
                ver = record_version(
                    skill_id,
                    row,
                    message="更新 SKILL",
                    bump=body_changed,
                )
                row["version"] = ver
                skills[idx] = row
                data["skills"] = skills
                _save_raw(data)
        except Exception:
            pass
    return get_skill(skill_id), None


def import_skill(
    *,
    name: str,
    description: str,
    body_md: str = "",
    command: str = "",
    source: str = "manual",
    attachments: Optional[List[Dict[str, Any]]] = None,
    upsert_by_name: bool = False,
    tag_board: bool = True,
) -> Dict[str, Any]:
    name = (name or "").strip()
    description = (description or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    if not description:
        raise ValueError("description 不能为空")
    cmd = _normalize_command(command) if command else _default_command_for_name(name)
    data = _load_raw()
    skills: List[Dict[str, Any]] = list(data.get("skills") or [])
    existing = find_skill_by_name(name) if upsert_by_name else None
    if existing:
        sid = existing.get("id")
        if cmd:
            for s in skills:
                if s.get("id") == sid:
                    continue
                if (s.get("command") or "").lower() == cmd:
                    raise ValueError(f"命令 {cmd} 已被 SKILL「{s.get('name')}」占用")
        row, err = _update_skill_row(
            sid,
            name=name,
            description=description,
            body_md=body_md,
            command=cmd,
            attachments=attachments if attachments is not None else existing.get("attachments"),
            source=source,
        )
        if err:
            raise ValueError(err)
        return row or existing
    if cmd:
        for s in skills:
            if (s.get("command") or "").lower() == cmd:
                raise ValueError(f"命令 {cmd} 已被 SKILL「{s.get('name')}」占用")
    sid = uuid.uuid4().hex[:10]
    row = {
        "id": sid,
        "name": name,
        "description": description,
        "command": cmd,
        "body_md": (body_md or "").strip(),
        "source": source,
        "created_at": datetime.now().isoformat(),
        "version": "1.0.0",
        "attachments": list(attachments or []),
    }
    skills.append(row)
    data["skills"] = skills
    _save_raw(data)
    try:
        from .skill_version_service import ensure_initial_version

        ensure_initial_version(row)
    except Exception:
        pass
    if tag_board:
        try:
            from .skill_board_tagger import tag_and_persist_skill

            row = tag_and_persist_skill(row)
        except Exception:
            pass
    return row


def import_from_markdown(
    text: str,
    source: str = "file",
    *,
    attachments: Optional[List[Dict[str, Any]]] = None,
    upsert_by_name: bool = False,
    tag_board: bool = True,
) -> Dict[str, Any]:
    meta, body = _parse_frontmatter(text)
    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    command = meta.get("command", "") or meta.get("绑定命令", "")
    version = (meta.get("version") or "").strip() or "1.0.0"
    if not name or not description:
        raise ValueError("SKILL.md 须在 frontmatter 中提供 name 与 description（均不可为空）")
    row = import_skill(
        name=name,
        description=description,
        body_md=body,
        command=command,
        source=source,
        attachments=attachments,
        upsert_by_name=upsert_by_name,
        tag_board=tag_board,
    )
    row["version"] = version
    data = _load_raw()
    skills = data.get("skills") or []
    for i, s in enumerate(skills):
        if s.get("id") == row.get("id"):
            skills[i]["version"] = version
            break
    data["skills"] = skills
    _save_raw(data)
    return row


def import_from_skill_directory(
    skill_dir: Path,
    *,
    skill_md: Optional[Path] = None,
    source: str = "dir",
    upsert_by_name: bool = True,
    tag_board: bool = True,
) -> Dict[str, Any]:
    skill_dir = Path(skill_dir).resolve()
    skill_md = Path(skill_md).resolve() if skill_md else _find_skill_md(skill_dir)
    if not skill_md or not skill_md.is_file():
        raise ValueError(f"目录无 SKILL.md: {skill_dir}")
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    # 独立 .md 在共享根目录时，附件仅限同名前缀子目录，避免扫入兄弟 skill
    attach_root = skill_dir
    if skill_md.name.lower() != "skill.md":
        stem = skill_md.stem
        sub = skill_dir / stem
        if sub.is_dir():
            attach_root = sub
        else:
            attachments = []
            return import_from_markdown(
                text,
                source=source,
                attachments=attachments,
                upsert_by_name=upsert_by_name,
                tag_board=tag_board,
            )
    attachments = _collect_attachments(attach_root, skill_md)
    return import_from_markdown(
        text,
        source=source,
        attachments=attachments,
        upsert_by_name=upsert_by_name,
        tag_board=tag_board,
    )


def default_project_skill_roots() -> List[Path]:
    """批量导入默认根：项目内两目录 + F:\\AI\\local_skills（存在则加入）。"""
    roots: List[Path] = []
    if _BASE is not None:
        parent = _BASE.parent
        for rel in (".cursor/skills", "web_migration/skills_downloaded"):
            p = (parent / rel).resolve()
            if p.is_dir():
                roots.append(p)
    for p in _DEFAULT_EXTRA_SKILL_ROOTS:
        try:
            rp = Path(p).resolve()
            if rp.is_dir() and rp not in roots:
                roots.append(rp)
        except OSError:
            pass
    import os

    extra = (os.environ.get("SKILL_IMPORT_ROOTS") or "").strip()
    if extra:
        for part in extra.split(";"):
            part = part.strip()
            if not part:
                continue
            try:
                rp = Path(part).resolve()
                if rp.is_dir() and rp not in roots:
                    roots.append(rp)
            except OSError:
                pass
    return roots


def import_batch_from_roots(
    roots: Optional[Iterable[str | Path]] = None,
    *,
    upsert_by_name: bool = True,
) -> Dict[str, Any]:
    """批量导入多个根目录下的 SKILL 文件夹。"""
    use_roots: List[Path] = []
    if roots:
        for r in roots:
            p = Path(r).resolve()
            if p.is_dir():
                use_roots.append(p)
    else:
        use_roots = default_project_skill_roots()
    imported: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    seen_mds: set[str] = set()
    for root in use_roots:
        for skill_dir, skill_md in discover_skill_entries(root, recursive=True):
            key = str(skill_md.resolve())
            if key in seen_mds:
                continue
            seen_mds.add(key)
            try:
                row = import_from_skill_directory(
                    skill_dir,
                    skill_md=skill_md,
                    source=f"batch:{root.name}",
                    upsert_by_name=upsert_by_name,
                    tag_board=False,
                )
                imported.append(
                    {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "dir": str(skill_dir),
                        "md": str(skill_md),
                        "attachment_count": len(row.get("attachments") or []),
                    }
                )
            except Exception as e:
                errors.append({"dir": str(skill_dir), "error": str(e)})
    board_tag: Dict[str, Any] = {"tagged": 0, "skipped": 0, "errors": []}
    ids = [x.get("id") for x in imported if x.get("id")]
    if ids:
        try:
            from .skill_board_tagger import tag_skills_by_ids

            board_tag = tag_skills_by_ids(ids, force=True)
        except Exception as e:
            board_tag = {"tagged": 0, "skipped": 0, "errors": [{"error": str(e)[:200]}]}
    return {
        "ok": len(errors) == 0,
        "roots": [str(p) for p in use_roots],
        "imported": imported,
        "errors": errors,
        "count": len(imported),
        "board_tag": board_tag,
    }


def import_skill_bundle(items: List[Dict[str, Any]], *, upsert_by_name: bool = True) -> Dict[str, Any]:
    """浏览器文件夹上传：每项含 markdown 与 attachments 列表。"""
    imported: List[str] = []
    errors: List[Dict[str, str]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        raw = (it.get("markdown") or it.get("content") or "").strip()
        if not raw:
            errors.append({"name": it.get("name", ""), "error": "markdown 为空"})
            continue
        atts_in = it.get("attachments") or []
        atts: List[Dict[str, Any]] = []
        if isinstance(atts_in, list):
            for a in atts_in:
                if not isinstance(a, dict):
                    continue
                path = (a.get("path") or a.get("name") or "").strip()
                text = a.get("text") or a.get("content") or ""
                if not path or not str(text).strip():
                    continue
                ext = Path(path).suffix.lower()
                kind, language = _attachment_kind(ext if ext else path.lower())
                atts.append(
                    {
                        "path": path.replace("\\", "/"),
                        "name": Path(path).name,
                        "kind": a.get("kind") or kind,
                        "language": a.get("language") or language,
                        "size": len(str(text).encode("utf-8")),
                        "text": str(text),
                    }
                )
        try:
            row = import_from_markdown(
                raw,
                source="folder-upload",
                attachments=atts,
                upsert_by_name=upsert_by_name,
                tag_board=False,
            )
            imported.append(row.get("name") or row.get("id") or "")
        except Exception as e:
            errors.append({"name": it.get("name", ""), "error": str(e)})
    board_tag: Dict[str, Any] = {"tagged": 0, "skipped": 0, "errors": []}
    if imported:
        try:
            from .skill_board_tagger import tag_skills_by_ids

            ids = []
            for nm in imported:
                sk = find_skill_by_name(str(nm))
                if sk and sk.get("id"):
                    ids.append(sk["id"])
            if ids:
                board_tag = tag_skills_by_ids(ids, force=True)
        except Exception as e:
            board_tag = {"tagged": 0, "skipped": 0, "errors": [{"error": str(e)[:200]}]}
    return {
        "ok": not errors,
        "imported": imported,
        "errors": errors,
        "count": len(imported),
        "board_tag": board_tag,
    }


def commit_skill_commands(commands: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """提交工具页未保存的命令映射；commands 为 {skill_id: /cmd}，缺省则仅为空命令补默认斜杠。"""
    data = _load_raw()
    skills: List[Dict[str, Any]] = list(data.get("skills") or [])
    saved: List[str] = []
    errors: List[Dict[str, str]] = []
    cmd_map = commands if isinstance(commands, dict) else {}
    for s in skills:
        sid = s.get("id") or ""
        if not sid:
            continue
        want = cmd_map.get(sid)
        if want is None:
            cur = (s.get("command") or "").strip()
            if cur:
                continue
            want = _default_command_for_name(s.get("name") or "")
        else:
            want = _normalize_command(str(want)) if str(want).strip() else ""
        row, err = _update_skill_row(sid, command=want)
        if err:
            errors.append({"id": sid, "name": s.get("name", ""), "error": err})
        else:
            saved.append(sid)
    return {"ok": not errors, "saved": saved, "errors": errors, "count": len(saved)}


def find_by_slash_command(message_first_token: str) -> Optional[Dict[str, Any]]:
    """message_first_token 形如 '/foo' 或 '/foo-bar'。"""
    want = _normalize_command(message_first_token.split()[0] if message_first_token else "")
    if not want or want == "/":
        return None
    for s in _load_raw().get("skills") or []:
        c = (s.get("command") or "").strip().lower()
        if c and c == want:
            return s
    return None


SLASH_SUGGEST_LIMIT = 12


def _slash_match_score(prefix: str, cmd: str, name: str, desc: str) -> int:
    """命令前缀 > 名称前缀 > 名称首字母 > 子串匹配。"""
    raw = (prefix or "").strip().lower()
    if not raw.startswith("/"):
        raw = "/" + raw.lstrip("/")
    bare = raw.lstrip("/")
    c = (cmd or "").strip().lower()
    n = (name or "").strip().lower()
    d = (desc or "").strip().lower()
    if not bare:
        return 1000
    if c == raw:
        return 2000
    if c.startswith(raw) or c.lstrip("/").startswith(bare):
        return 1800 - len(bare)
    if n.startswith(bare):
        return 1500
    initials = "".join(w[0] for w in re.split(r"[\s\-_/]+", n) if w and w[0].isalnum())
    if initials and initials.startswith(bare):
        return 1300
    if bare in c.lstrip("/"):
        return 1100
    if bare in n:
        return 900
    if bare in d:
        return 700
    return 0


def slash_suggestions(prefix: str, limit: int = 12) -> List[Dict[str, str]]:
    """prefix 为 '/xx' 或 '/'；按匹配度排序，返回 SKILL 命令摘要。"""
    return slash_suggestions_with_total(prefix, limit=limit).get("suggestions") or []


def slash_suggestions_with_total(prefix: str, limit: int = 12) -> Dict[str, Any]:
    """带 total 的 slash 建议（供 API 分页提示）。"""
    p = (prefix or "").strip().lower()
    if not p.startswith("/"):
        p = "/" + p.lstrip("/")
    all_rows: List[Dict[str, str]] = []
    for s in _load_raw().get("skills") or []:
        cmd = (s.get("command") or "").strip().lower()
        if not cmd:
            continue
        name = s.get("name", "")
        desc = (s.get("description") or "")[:160]
        score = _slash_match_score(p, cmd, name, desc)
        if score <= 0 and p != "/":
            continue
        all_rows.append({"command": cmd, "name": name, "description": desc, "_score": score})
    all_rows.sort(key=lambda x: (-int(x.get("_score") or 0), x["command"]))
    for row in all_rows:
        row.pop("_score", None)
    cap = max(1, min(int(limit or 12), 30))
    return {"suggestions": all_rows[:cap], "total": len(all_rows)}


def expand_message_with_skill_meta(user_message: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """若首词为已注册 /command，将 SKILL 正文注入；并返回挂载元数据供审计日志。"""
    raw = (user_message or "").strip()
    if not raw.startswith("/"):
        return user_message, None
    parts = raw.split(None, 1)
    token = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    sk = find_by_slash_command(token)
    if not sk:
        return user_message, None
    body = (sk.get("body_md") or "").strip()
    name = sk.get("name", "")
    cmd = sk.get("command", token)
    block = (
        f"【系统：已挂载 SKILL「{name}」，命令 {cmd}。请遵循下列说明处理用户请求。】\n{body}\n【/SKILL】"
        if body
        else f"【系统：已挂载 SKILL「{name}」（{cmd}），正文为空，请按名称与描述尽力执行。】"
    )
    user_line = f"{cmd} {tail}".strip() if tail else cmd
    expanded = f"{block}\n\n【用户】\n{user_line}"
    meta = {"skill_id": sk.get("id"), "command": cmd, "name": name}
    return expanded, meta


def expand_message_with_skill(user_message: str) -> str:
    expanded, _ = expand_message_with_skill_meta(user_message)
    return expanded
