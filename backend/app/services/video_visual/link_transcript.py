"""链接沉淀主链路 — 视频转写：仅音频 / 画面 OCR / 混合。"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from ..transcribe_quality import assess_transcript
from .common import merge_transcript_plain, transcribe_audio
from .smart.pipeline import run_smart_visual

_log = logging.getLogger("sba.video_visual.link_transcript")

VALID_MODES = ("audio_only", "visual_frames", "hybrid")
MODE_LABELS = {
    "audio_only": "仅音频转写",
    "visual_frames": "视频画面文字提取",
    "hybrid": "音频+画面混合",
}


def normalize_video_transcript_mode(mode: str) -> str:
    m = (mode or "audio_only").strip().lower()
    aliases = {
        "audio": "audio_only",
        "whisper": "audio_only",
        "visual": "visual_frames",
        "visual_smart": "visual_frames",
        "smart": "visual_frames",
        "frames": "visual_frames",
        "both": "hybrid",
        "audio+visual": "hybrid",
        "audio_visual": "hybrid",
    }
    m = aliases.get(m, m)
    return m if m in VALID_MODES else "audio_only"


def extract_video_transcript_for_pipeline(
    video_path: str,
    mode: str,
    *,
    log_callback: Optional[Callable[[str, str], None]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
    user_prompt: str = "",
    strict_whisper: bool = True,
) -> Dict[str, Any]:
    """
    统一视频→原文入口，返回与 invoke_speech_to_text 兼容的 dict。
    mode: audio_only | visual_frames | hybrid
    """
    mode = normalize_video_transcript_mode(mode)

    def _log(msg: str, level: str = "INFO") -> None:
        if log_callback:
            log_callback(msg, level)

    if mode == "audio_only":
        from ..video_pipeline import invoke_speech_to_text

        return invoke_speech_to_text(
            video_path,
            log_callback=log_callback,
            progress_callback=progress_callback,
            llm_config=llm_config,
            user_prompt=user_prompt,
            strict=strict_whisper,
        )

    audio_text = ""
    audio_meta: Dict[str, Any] = {}
    if mode == "hybrid":
        _log("混合模式：Whisper 音频转写…")
        audio_text, audio_meta = transcribe_audio(video_path)
        if audio_text:
            _log(f"音频轨有效 {len(audio_text)} 字")
        else:
            _log("音频轨无有效正文（可能为纯画面课件）", "WARNING")

    _log(f"{'混合' if mode == 'hybrid' else '画面'}模式：智能 Probe 画面提取…")
    visual_out = run_smart_visual(video_path, log=_log)
    visual_segments = visual_out.get("visual_segments") or []
    plain = merge_transcript_plain(audio_text=audio_text, visual_segments=visual_segments)

    if not (plain or "").strip():
        return {
            "ok": False,
            "error_code": "visual_extract_empty",
            "error_message": "画面文字提取未得到有效正文",
            "video_transcript_mode": mode,
            "visual_meta": visual_out,
        }

    assessment = assess_transcript(
        plain,
        transcribe_source="visual_smart" if mode == "visual_frames" else "hybrid",
    )
    if not assessment.ok and mode == "visual_frames":
        return {
            "ok": False,
            "error_code": assessment.error_code or "visual_text_quality",
            "error_message": assessment.error_message or "画面提取正文未通过质量门禁",
            "full_text": plain,
            "video_transcript_mode": mode,
            "visual_meta": visual_out,
        }

    result: Dict[str, Any] = {
        "ok": True,
        "full_text": plain,
        "transcript": plain,
        "transcribe_source": "hybrid_av" if mode == "hybrid" else "visual_smart",
        "video_transcript_mode": mode,
        "visual_segment_count": len(visual_segments),
        "visual_profile": visual_out.get("profile"),
        "sampling_strategy": visual_out.get("strategy"),
        "coverage_score": visual_out.get("coverage_score"),
    }
    if audio_meta:
        result["audio_meta"] = audio_meta
    _log.info(
        "[链接沉淀文档-视频转写|link_transcript.extract|video|硬编执行|完成] "
        "mode=%s; len=%s; segments=%s",
        mode,
        len(plain),
        len(visual_segments),
    )
    return result