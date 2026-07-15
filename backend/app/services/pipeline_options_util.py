"""流水线任务选项 — UP 画像等子链路复用。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def pipeline_options(task: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not task:
        return {}
    raw = task.get("pipeline_options")
    return dict(raw) if isinstance(raw, dict) else {}


def is_article_only(task: Optional[Dict[str, Any]]) -> bool:
    return bool(pipeline_options(task).get("article_only"))


def skip_html(task: Optional[Dict[str, Any]]) -> bool:
    opts = pipeline_options(task)
    return bool(opts.get("skip_html") or opts.get("article_only"))


def skip_feishu(task: Optional[Dict[str, Any]]) -> bool:
    opts = pipeline_options(task)
    return bool(opts.get("skip_feishu") or opts.get("article_only"))


def whisper_pool(task: Optional[Dict[str, Any]]) -> str:
    return str(pipeline_options(task).get("whisper_pool") or "").strip()


def task_source(task: Optional[Dict[str, Any]]) -> str:
    return str(pipeline_options(task).get("source") or "").strip()


def video_transcript_mode(task: Optional[Dict[str, Any]]) -> str:
    """audio_only | visual_frames | hybrid"""
    from .video_visual.link_transcript import normalize_video_transcript_mode

    raw = str(pipeline_options(task).get("video_transcript_mode") or "audio_only")
    return normalize_video_transcript_mode(raw)
