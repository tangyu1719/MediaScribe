"""知识库标准化产物目录（对齐 output/kb_assets 布局）。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from app.services.task_manager import get_output_dir


def kb_assets_dir(tenant_id: str | int, doc_id: int | str) -> Path:
    p = get_output_dir() / "kb_assets" / str(tenant_id) / str(doc_id)
    p.mkdir(parents=True, exist_ok=True)
    (p / "images").mkdir(parents=True, exist_ok=True)
    return p


def abs_path_to_public_url(abs_path: str | Path) -> str:
    p = Path(abs_path).resolve()
    root = get_output_dir().resolve()
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:
        return ""
    return "/output/" + quote(rel, safe="/")
