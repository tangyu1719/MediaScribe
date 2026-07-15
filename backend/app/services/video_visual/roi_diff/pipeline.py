"""方案 B：字幕/画面 ROI 帧差动态抽帧 + OCR。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..common import WORK_DIR, dedupe_text_segments, ocr_image_path, probe_video_duration_sec

_log = logging.getLogger("sba.video_visual.roi_diff")

METHOD_ID = "roi_diff"
METHOD_LABEL = "ROI 帧差动态抽帧 + OCR"


def _roi_slice(frame: np.ndarray, roi: Tuple[float, float, float, float]) -> np.ndarray:
  h, w = frame.shape[:2]
  y0 = int(h * roi[0])
  y1 = int(h * roi[2])
  x0 = int(w * roi[1])
  x1 = int(w * roi[3])
  y0, y1 = max(0, y0), min(h, y1)
  x0, x1 = max(0, x0), min(w, x1)
  if y1 <= y0 or x1 <= x0:
    return frame
  return frame[y0:y1, x0:x1]


def extract_visual_segments(
  video_path: str,
  *,
  roi: Tuple[float, float, float, float] = (0.68, 0.0, 1.0, 1.0),
  diff_threshold: float = 10.0,
  sample_stride: int = 2,
  log: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
  """仅在 ROI 像素变化超过阈值时抽关键帧并 OCR。"""
  cap = cv2.VideoCapture(video_path)
  if not cap.isOpened():
    return []

  fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
  out_dir = WORK_DIR / f"roi_{Path(video_path).stem}"
  out_dir.mkdir(parents=True, exist_ok=True)

  prev_gray: Optional[np.ndarray] = None
  segments: List[Dict[str, Any]] = []
  frame_idx = 0
  saved = 0

  while True:
    ok, frame = cap.read()
    if not ok:
      break
    if frame_idx % max(1, sample_stride) != 0:
      frame_idx += 1
      continue

    roi_img = _roi_slice(frame, roi)
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    t_sec = frame_idx / fps

    take = False
    score = 0.0
    if prev_gray is None:
      take = True
    else:
      if gray.shape != prev_gray.shape:
        prev_gray = cv2.resize(prev_gray, (gray.shape[1], gray.shape[0]))
      diff = cv2.absdiff(gray, prev_gray)
      score = float(np.mean(diff))
      if score >= diff_threshold:
        take = True

    if take:
      fp = out_dir / f"kf_{saved:04d}.jpg"
      cv2.imwrite(str(fp), frame)
      text = ocr_image_path(str(fp))
      if text:
        segments.append(
          {
            "time_sec": t_sec,
            "text": text,
            "source": "roi_diff",
            "frame": str(fp),
            "diff_score": score if prev_gray is not None else 0.0,
          }
        )
      saved += 1
      prev_gray = gray

    frame_idx += 1

  cap.release()
  if log:
    log(f"ROI 帧差抽取 {saved} 帧，OCR 有效 {len(segments)} 段", "INFO")
  return dedupe_text_segments(segments)


def run_roi_diff_visual(video_path: str, log: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
  duration = probe_video_duration_sec(video_path)
  # 短视频字幕区偏底部；极短视频略扩 ROI
  roi = (0.62, 0.0, 1.0, 1.0) if duration > 15 else (0.55, 0.0, 1.0, 1.0)
  segs = extract_visual_segments(video_path, roi=roi, log=log)
  strategy = "roi_bottom"
  if len(segs) < 3:
    if log:
      log("ROI 产出不足，回退全画面帧差采样", "INFO")
    segs = extract_visual_segments(
      video_path,
      roi=(0.0, 0.0, 1.0, 1.0),
      diff_threshold=8.0,
      sample_stride=3,
      log=log,
    )
    strategy = "full_frame_fallback"
  _log.info(
    "[链接沉淀文档-视频视觉提取|roi_diff.run|video|硬编执行|完成] segments=%s; strategy=%s",
    len(segs),
    strategy,
  )
  return {
    "method": METHOD_ID,
    "visual_segments": segs,
    "segment_count": len(segs),
    "roi": roi,
    "strategy": strategy,
  }
