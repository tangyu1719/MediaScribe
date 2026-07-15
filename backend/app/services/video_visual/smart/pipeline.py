"""方案 C：Probe 路由 + 段内 80% 采样 / 直方图去重 / ROI 字幕（smart）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..common import WORK_DIR, dedupe_text_segments, ocr_image_path
from ..probe import (
    CLASS_CONTINUOUS,
    CLASS_MIXED,
    CLASS_SLIDE_DECK,
    CLASS_SUBTITLE_SPEECH,
    VideoProfile,
    merge_short_segments,
    plan_segment_sample_times,
    probe_video_profile,
    segments_from_cuts,
)
from ..roi_diff.pipeline import extract_visual_segments as roi_extract

_log = logging.getLogger("sba.video_visual.smart")

METHOD_ID = "smart"
METHOD_LABEL = "智能 Probe 路由 + 段内采样"


def _calc_hist_bgr(img: np.ndarray) -> np.ndarray:
    h = cv2.calcHist([img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
    cv2.normalize(h, h)
    return h


def _hist_correl(h1: np.ndarray, h2: np.ndarray) -> float:
    return float(cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL))


def extract_histogram_unique_frames(
    video_path: str,
    *,
    sample_fps: float = 3.0,
    sim_threshold: float = 0.88,
    pixel_diff_threshold: float = 6.0,
    log: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
    """video2ppt + 像素差分：模板相近但文字不同的幻灯片也能检出。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    stride = max(1, int(fps / max(0.5, sample_fps)))
    out_dir = WORK_DIR / f"hist_{Path(video_path).stem}"
    out_dir.mkdir(parents=True, exist_ok=True)

    segments: List[Dict[str, Any]] = []
    last_hist: Optional[np.ndarray] = None
    last_gray: Optional[np.ndarray] = None
    idx = 0
    saved = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride != 0:
            idx += 1
            continue
        t_sec = idx / fps
        h, w = frame.shape[:2]
        scale = 480 / max(w, 1)
        small = cv2.resize(frame, (480, max(1, int(h * scale))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hist = _calc_hist_bgr(small)

        keep = False
        if last_hist is None or last_gray is None:
            keep = True
        else:
            corr = _hist_correl(last_hist, hist)
            pdiff = float(np.mean(cv2.absdiff(last_gray, gray)))
            # 颜色模板相近但文字变化 → 像素差分超阈仍保留
            if corr < sim_threshold or pdiff >= pixel_diff_threshold:
                keep = True

        if keep:
            fp = out_dir / f"uniq_{saved:04d}.jpg"
            cv2.imwrite(str(fp), frame)
            text = ocr_image_path(str(fp))
            if text:
                segments.append(
                    {
                        "time_sec": t_sec,
                        "text": text,
                        "source": "smart_histogram",
                        "frame": str(fp),
                    }
                )
            saved += 1
            last_hist = hist
            last_gray = gray
        idx += 1

    cap.release()
    if log:
        log(f"直方图+差分抽取 {saved} 帧，OCR 有效 {len(segments)} 段", "INFO")
    return dedupe_text_segments(segments)


def _extract_frames_at_times(
    video_path: str,
    times: List[float],
    *,
    tag: str = "seg",
) -> List[Dict[str, Any]]:
    if not times:
        return []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    out_dir = WORK_DIR / f"{tag}_{Path(video_path).stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    segments: List[Dict[str, Any]] = []
    for i, t_sec in enumerate(sorted(times)):
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_sec) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            continue
        fp = out_dir / f"{tag}_{i:04d}.jpg"
        cv2.imwrite(str(fp), frame)
        text = ocr_image_path(str(fp))
        if text:
            segments.append(
                {
                    "time_sec": t_sec,
                    "text": text,
                    "source": "smart_segment",
                    "frame": str(fp),
                }
            )
    cap.release()
    return dedupe_text_segments(segments)


def _extract_slide_deck(
    video_path: str,
    profile: VideoProfile,
    log: Optional[Callable[[str, str], None]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """slidegeist 思路：切点分段 → 每段 80% 位置单帧 OCR。"""
    segments = segments_from_cuts(profile.cut_times, profile.duration_sec)
    if len(segments) <= 1 and profile.duration_sec > 1.0:
        if log:
            log("切点不足，slide_deck 回退直方图+像素差分抽帧", "INFO")
        segs = extract_histogram_unique_frames(
            video_path, sample_fps=3.5, sim_threshold=0.90, pixel_diff_threshold=5.0, log=log
        )
        return segs, "histogram_pixel_fallback"

    times = plan_segment_sample_times(segments, position=0.80)
    if log:
        log(
            f"slide_deck: {len(segments)} 段 → 计划采样 {len(times)} 帧（段内 80%）",
            "INFO",
        )
    segs = _extract_frames_at_times(video_path, times, tag="slide80")
    strategy = "segment_80pct"

    # 覆盖率闭环：OCR 段数明显少于切点数 → 直方图补采
    expected = max(len(segments), profile.cut_count + 1, 1)
    if len(segs) < max(3, int(expected * 0.5)):
        if log:
            log(
                f"覆盖率不足({len(segs)}/{expected})，追加直方图去重",
                "INFO",
            )
        extra = extract_histogram_unique_frames(
            video_path,
            sample_fps=max(2.0, min(4.0, expected / max(profile.duration_sec, 1))),
            sim_threshold=0.90,
            pixel_diff_threshold=5.0,
            log=log,
        )
        combined = dedupe_text_segments(segs + extra)
        return combined, "segment_80pct+histogram"

    return segs, strategy


def run_smart_visual(
    video_path: str,
    log: Optional[Callable[[str, str], None]] = None,
    *,
    profile: Optional[VideoProfile] = None,
) -> Dict[str, Any]:
    """Probe → 分类 → 按类型执行视觉提取。"""
    if profile is None:
        if log:
            log("开始 Probe 快扫（差分曲线 + 类型分类）…", "INFO")
        profile = probe_video_profile(video_path)

    # 文字密度高但规则判成 continuous → 纠正为 slide_deck（全屏课件常见）
    if profile.video_class == CLASS_CONTINUOUS and profile.text_density_score >= 0.04:
        profile.video_class = CLASS_SLIDE_DECK
        profile.video_class_label = "全屏幻灯/文档录屏"
        profile.confidence = max(profile.confidence, 0.55)
        if log:
            log("文字密度高，将 continuous 纠正为 slide_deck", "INFO")

    if log:
        log(
            f"Probe 结果: {profile.video_class_label} "
            f"(置信度 {profile.confidence}, 切点 {profile.cut_count}, "
            f"最短停留 {profile.dwell_min_sec}s)",
            "INFO",
        )

    strategy = profile.video_class
    visual_segments: List[Dict[str, Any]] = []

    if profile.video_class == CLASS_SUBTITLE_SPEECH:
        visual_segments = roi_extract(video_path, log=log)
        strategy = "roi_bottom"

    elif profile.video_class == CLASS_MIXED:
        slide_segs, slide_st = _extract_slide_deck(video_path, profile, log=log)
        roi_segs = roi_extract(video_path, roi=(0.62, 0.0, 1.0, 1.0), log=log)
        visual_segments = dedupe_text_segments(slide_segs + roi_segs)
        strategy = f"mixed:{slide_st}+roi"

    elif profile.video_class == CLASS_SLIDE_DECK:
        visual_segments, strategy = _extract_slide_deck(video_path, profile, log=log)

    else:
        if log:
            log("continuous：直方图相似度驱动抽帧", "INFO")
        visual_segments = extract_histogram_unique_frames(
            video_path, sample_fps=3.5, sim_threshold=0.90, pixel_diff_threshold=4.5, log=log
        )
        strategy = "histogram_pixel"

    # 最终兜底
    if len(visual_segments) < 2 and profile.duration_sec > 2.0:
        if log:
            log("产出过少，最终回退直方图全片扫描", "WARNING")
        visual_segments = extract_histogram_unique_frames(
            video_path, sample_fps=4.0, sim_threshold=0.92, pixel_diff_threshold=4.0, log=log
        )
        strategy = f"{strategy}+final_histogram"

    expected_pages = max(profile.cut_count + 1, 1)
    coverage = round(min(1.0, len(visual_segments) / expected_pages), 3)

    _log.info(
        "[链接沉淀文档-视频视觉提取|smart.run|video|硬编执行|完成] "
        "class=%s; strategy=%s; segments=%s; coverage=%s",
        profile.video_class,
        strategy,
        len(visual_segments),
        coverage,
    )
    return {
        "method": METHOD_ID,
        "visual_segments": visual_segments,
        "segment_count": len(visual_segments),
        "profile": profile.to_dict(),
        "strategy": strategy,
        "coverage_score": coverage,
    }
