"""视频画面转文字附加入口：统一调度下载、音频转写、视觉子树。"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .common import (
  download_video_for_visual,
  merge_transcript_plain,
  transcribe_audio,
)
from .roi_diff.pipeline import METHOD_ID as ROI_ID, METHOD_LABEL as ROI_LABEL, run_roi_diff_visual
from .scene_ocr.pipeline import METHOD_ID as SCENE_ID, METHOD_LABEL as SCENE_LABEL, run_scene_ocr_visual
from .smart.pipeline import METHOD_ID as SMART_ID, METHOD_LABEL as SMART_LABEL, run_smart_visual

_log = logging.getLogger("sba.video_visual.entry")

_METHODS = {
  SMART_ID: {"label": SMART_LABEL, "runner": run_smart_visual},
  SCENE_ID: {"label": SCENE_LABEL, "runner": run_scene_ocr_visual},
  ROI_ID: {"label": ROI_LABEL, "runner": run_roi_diff_visual},
}

# auto 与 smart 同义：Probe 后自动选策略
_METHOD_ALIASES = {"auto": SMART_ID, "probe": SMART_ID}


def list_video_visual_methods() -> List[Dict[str, str]]:
  return [{"id": k, "label": v["label"]} for k, v in _METHODS.items()]


def run_video_visual_extract(
  url: str,
  *,
  method: str = SMART_ID,
  include_audio: bool = True,
  title: str = "",
  log: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
  """
  附加入口：返回 plain_text（原文生文，非摘要）。
  method: smart | auto | scene_ocr | roi_diff
  """
  started = time.time()
  raw_method = (method or SMART_ID).strip().lower()
  method = _METHOD_ALIASES.get(raw_method, raw_method)
  if method not in _METHODS:
    return {"ok": False, "error_code": "invalid_method", "error_message": f"未知方法: {method}"}

  def _emit(msg: str, level: str = "INFO") -> None:
    if log:
      log(msg, level)
    _log.info(
      "[链接沉淀文档-视频视觉提取|entry.run_video_visual_extract|url|硬编执行|进度] %s",
      msg,
    )

  video_path = download_video_for_visual(url, log=_emit)
  if not video_path:
    return {
      "ok": False,
      "error_code": "download_failed",
      "error_message": "视频下载失败",
      "url": url,
      "method": method,
    }

  audio_text = ""
  audio_meta: Dict[str, Any] = {}
  if include_audio:
    _emit("开始 Whisper 音频转写…")
    audio_text, audio_meta = transcribe_audio(video_path)

  _emit(f"开始视觉提取: {_METHODS[method]['label']}")
  visual_out = _METHODS[method]["runner"](video_path, log=_emit)
  visual_segments = visual_out.get("visual_segments") or []

  plain_text = merge_transcript_plain(
    audio_text=audio_text,
    visual_segments=visual_segments,
    title=title,
  )

  ok = bool((plain_text or "").strip())
  elapsed = round(time.time() - started, 2)
  result: Dict[str, Any] = {
    "ok": ok,
    "method": method,
    "method_label": _METHODS[method]["label"],
    "url": url,
    "video_path": video_path,
    "plain_text": plain_text,
    "audio_text": audio_text,
    "visual_segment_count": len(visual_segments),
    "visual_segments": visual_segments[:30],
    "audio_meta": {k: audio_meta.get(k) for k in ("transcribe_source", "error_code", "error_message") if k in audio_meta},
    "elapsed_sec": elapsed,
    "error_code": None if ok else "empty_output",
    "error_message": None if ok else "未提取到有效文本（检查下载/OCR/音频轨）",
  }
  if method == SMART_ID:
    result["video_profile"] = visual_out.get("profile")
    result["sampling_strategy"] = visual_out.get("strategy")
    result["coverage_score"] = visual_out.get("coverage_score")
  return result
