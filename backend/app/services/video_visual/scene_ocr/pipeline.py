"""方案 A：场景变化感知抽帧 + OCR（SceneLens 思路）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..common import (
  WORK_DIR,
  dedupe_text_segments,
  extract_frames_to_dir,
  ocr_image_path,
  probe_video_duration_sec,
)

_log = logging.getLogger("sba.video_visual.scene_ocr")

METHOD_ID = "scene_ocr"
METHOD_LABEL = "场景变化抽帧 + OCR"


def extract_visual_segments(
  video_path: str,
  *,
  scene_threshold: float = 0.28,
  max_frames: int = 80,
  log: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
  """在场景切换处抽帧并 OCR；静态视频回退均匀采样。"""
  duration = probe_video_duration_sec(video_path)
  budget = max_frames
  if duration <= 30:
    budget = min(40, max_frames)
  elif duration <= 180:
    budget = min(60, max_frames)

  out_dir = WORK_DIR / f"scene_{Path(video_path).stem}"
  vf_scene = f"select=gt(scene\\,{scene_threshold}),scale=1280:-1"
  pairs = extract_frames_to_dir(video_path, out_dir, vf_scene, max_frames=budget)

  if len(pairs) < 8 and duration > 3:
    if log:
      log(f"场景帧不足({len(pairs)})，回退固定 fps 采样", "INFO")
    out_dir2 = WORK_DIR / f"scene_fps_{Path(video_path).stem}"
    fps = max(0.5, min(2.0, budget / max(duration, 1.0)))
    vf_fps = f"fps={fps:.3f},scale=1280:-1"
    pairs = extract_frames_to_dir(video_path, out_dir2, vf_fps, max_frames=budget)

  segments: List[Dict[str, Any]] = []
  for t_sec, fp in pairs:
    text = ocr_image_path(str(fp))
    if text:
      segments.append({"time_sec": t_sec, "text": text, "source": "scene_ocr", "frame": str(fp)})
  return dedupe_text_segments(segments)


def run_scene_ocr_visual(video_path: str, log: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
  segs = extract_visual_segments(video_path, log=log)
  _log.info(
    "[链接沉淀文档-视频视觉提取|scene_ocr.run|video|硬编执行|完成] segments=%s",
    len(segs),
  )
  return {"method": METHOD_ID, "visual_segments": segs, "segment_count": len(segs)}
