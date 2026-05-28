"""链接沉淀 —— 评论文本解析与摘要/MD 拼装。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def resolve_comments_text(
    *,
    comments_data: Any = None,
    comments_text: str = "",
    comments_file_path: str = "",
    max_chars: int = 12000,
) -> str:
    if (comments_text or "").strip():
        return (comments_text or "").strip()[:max_chars]

    if comments_data is not None:
        try:
            from .comment_scraper import CommentResult, format_comments_as_text

            if isinstance(comments_data, CommentResult):
                txt = format_comments_as_text(comments_data).strip()
                return txt[:max_chars] if txt else ""
        except Exception:
            pass

    fp = (comments_file_path or "").strip()
    if fp and Path(fp).is_file():
        try:
            raw = Path(fp).read_text(encoding="utf-8", errors="ignore").strip()
            if raw:
                return raw[:max_chars]
        except Exception:
            pass
    return ""


def compose_summary_input(article_text: str, comments_text: str = "") -> str:
    """整理后正文 + 评论区，送入摘要 Agent。"""
    article = (article_text or "").strip()
    comments = (comments_text or "").strip()
    if not comments:
        return article
    block = f"## 评论区\n\n{comments}"
    if article:
        return f"{article}\n\n{block}".strip()
    return block


def format_comments_file_link(comments_file_path: str) -> str:
    fp = (comments_file_path or "").strip()
    if not fp:
        return ""
    name = Path(fp).name
    return f"评论已单独保存，请查看: [{name}](./{name})" if name else ""
