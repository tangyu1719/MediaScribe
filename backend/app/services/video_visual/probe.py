"""视频 Probe：差分曲线、切点、类型分类与采样计划（借鉴 VideoRouter / slidegeist / slidoc inspect）。"""
from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .common import probe_video_duration_sec

_log = logging.getLogger("sba.video_visual.probe")

# 视频视觉类型
CLASS_SLIDE_DECK = "slide_deck"
CLASS_SUBTITLE_SPEECH = "subtitle_speech"
CLASS_MIXED = "mixed"
CLASS_CONTINUOUS = "continuous"

CLASS_LABELS = {
    CLASS_SLIDE_DECK: "全屏幻灯/文档录屏",
    CLASS_SUBTITLE_SPEECH: "硬字幕口播",
    CLASS_MIXED: "课件+字幕/混合",
    CLASS_CONTINUOUS: "连续画面/运镜",
}


@dataclass
class VideoProfile:
    """Probe 阶段输出的视频画像。"""

    duration_sec: float = 0.0
    fps: float = 25.0
    frame_count: int = 0
    cut_times: List[float] = field(default_factory=list)
    cut_count: int = 0
    dwell_mean_sec: float = 0.0
    dwell_min_sec: float = 0.0
    dwell_std_sec: float = 0.0
    dwell_cv: float = 0.0
    bottom_ratio: float = 0.0
    mean_full_diff: float = 0.0
    mean_bottom_diff: float = 0.0
    text_density_score: float = 0.0
    video_class: str = CLASS_CONTINUOUS
    video_class_label: str = CLASS_LABELS[CLASS_CONTINUOUS]
    confidence: float = 0.0
    probe_sample_count: int = 0
    detector: str = "opencv_diff"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _frame_diff(gray_a: np.ndarray, gray_b: np.ndarray) -> float:
    if gray_a.shape != gray_b.shape:
        gray_b = cv2.resize(gray_b, (gray_a.shape[1], gray_a.shape[0]))
    return float(np.mean(cv2.absdiff(gray_a, gray_b)))


def _roi_bottom(gray: np.ndarray, frac: float = 0.30) -> np.ndarray:
    h = gray.shape[0]
    y0 = int(h * (1.0 - frac))
    return gray[y0:, :]


def _text_density_proxy(gray: np.ndarray) -> float:
    """边缘密度代理文字多少，避免 Probe 阶段全量 OCR。"""
    edges = cv2.Canny(gray, 50, 150)
    return float(np.mean(edges > 0))


def _find_cut_times(
    samples: List[Tuple[float, float, float]],
    *,
    duration: float,
    peak_sigma: float = 1.2,
    min_gap_sec: float = 0.25,
) -> List[float]:
    """从 (t, full_diff, bottom_diff) 序列找切点峰值。"""
    if len(samples) < 3:
        return []
    full_vals = [s[1] for s in samples]
    mu = statistics.mean(full_vals)
    sigma = statistics.pstdev(full_vals) if len(full_vals) > 1 else 0.0
    thr = mu + peak_sigma * sigma
    cuts: List[float] = []
    for i in range(1, len(samples) - 1):
        t, fv, _ = samples[i]
        if fv >= thr and fv >= samples[i - 1][1] and fv >= samples[i + 1][1]:
            if cuts and (t - cuts[-1]) < min_gap_sec:
                # 保留差分更大的切点
                prev_i = next(j for j, s in enumerate(samples) if abs(s[0] - cuts[-1]) < 0.001)
                if fv > samples[prev_i][1]:
                    cuts[-1] = t
            else:
                cuts.append(t)
    # 简化：去重近邻
    merged: List[float] = []
    for t in sorted(cuts):
        if merged and (t - merged[-1]) < min_gap_sec:
            continue
        merged.append(min(t, duration))
    return merged


def _dwell_stats(cut_times: List[float], duration: float) -> Tuple[float, float, float, float]:
    if duration <= 0:
        return 0.0, 0.0, 0.0, 0.0
    bounds = [0.0] + sorted(cut_times) + [duration]
    durs = [max(0.0, bounds[i + 1] - bounds[i]) for i in range(len(bounds) - 1)]
    durs = [d for d in durs if d > 0.01]
    if not durs:
        return duration, duration, 0.0, 0.0
    mean_d = statistics.mean(durs)
    min_d = min(durs)
    std_d = statistics.pstdev(durs) if len(durs) > 1 else 0.0
    cv = std_d / mean_d if mean_d > 0 else 0.0
    return mean_d, min_d, std_d, cv


def classify_video_profile(
    *,
    duration: float,
    cut_count: int,
    bottom_ratio: float,
    mean_bottom_diff: float,
    text_density: float,
    dwell_cv: float,
) -> Tuple[str, float]:
    """规则分类（可解释）；返回 (class_id, confidence)。"""
    scores = {
        CLASS_SLIDE_DECK: 0.0,
        CLASS_SUBTITLE_SPEECH: 0.0,
        CLASS_MIXED: 0.0,
        CLASS_CONTINUOUS: 0.0,
    }
    cut_rate = cut_count / max(duration, 0.5)

    if bottom_ratio > 1.5 and mean_bottom_diff > 4.0:
        scores[CLASS_SUBTITLE_SPEECH] += 2.0
        if cut_count >= 2:
            scores[CLASS_MIXED] += 2.5
        if text_density > 0.08:
            scores[CLASS_MIXED] += 1.0

    if cut_count >= 2 or cut_rate >= 0.15:
        scores[CLASS_SLIDE_DECK] += 2.0
    if text_density > 0.06:
        scores[CLASS_SLIDE_DECK] += 2.5
    if text_density > 0.10:
        scores[CLASS_SLIDE_DECK] += 2.0
    if duration <= 120 and text_density > 0.05:
        scores[CLASS_SLIDE_DECK] += 1.5

    if cut_count <= 1 and bottom_ratio < 1.2 and text_density < 0.04:
        scores[CLASS_CONTINUOUS] += 2.0
    elif cut_count <= 1 and text_density >= 0.05:
        # 全屏课件：切点少但文字密度高 → 仍是 slide_deck
        scores[CLASS_SLIDE_DECK] += 2.0
        scores[CLASS_CONTINUOUS] -= 1.0
    if dwell_cv > 0.8 and cut_count >= 2:
        scores[CLASS_SLIDE_DECK] += 0.5

    best = max(scores, key=scores.get)
    total = sum(scores.values()) or 1.0
    conf = scores[best] / total
    if scores[best] < 0.5:
        best = CLASS_SLIDE_DECK if text_density > 0.05 else CLASS_CONTINUOUS
        conf = 0.4
    return best, round(conf, 3)


def merge_short_segments(
    segments: List[Tuple[float, float]],
    *,
    min_dur_sec: float = 0.35,
) -> List[Tuple[float, float]]:
    """合并过短段（过渡闪烁），保留幻灯主体段。"""
    if not segments:
        return segments
    merged: List[List[float]] = [[segments[0][0], segments[0][1]]]
    for start, end in segments[1:]:
        dur = end - start
        if dur < min_dur_sec:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    out = [(a, b) for a, b in merged if b - a > 0.02]
    return out or segments


def segments_from_cuts(cut_times: List[float], duration: float) -> List[Tuple[float, float]]:
    bounds = [0.0] + sorted(cut_times) + [duration]
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    return merge_short_segments(segs)


def plan_segment_sample_times(
    segments: List[Tuple[float, float]],
    *,
    position: float = 0.80,
) -> List[float]:
    """slidegeist 思路：每段在 position（默认 80%）处采 1 帧，与页停留长短无关。"""
    times: List[float] = []
    for start, end in segments:
        dur = end - start
        if dur < 0.05:
            continue
        t = start + dur * max(0.1, min(0.95, position))
        times.append(t)
    return times


def _try_pyscenedetect_segments(video_path: str) -> List[Tuple[float, float]]:
    try:
        from scenedetect import AdaptiveDetector, detect

        scenes = detect(video_path, AdaptiveDetector(min_content_val=12.0, adaptive_threshold=3.0))
        segs = [(s[0].get_seconds(), s[1].get_seconds()) for s in scenes]
        if segs:
            return segs
    except Exception as exc:
        _log.debug("PySceneDetect 不可用或失败: %s", exc)
    return []


def probe_video_profile(
    video_path: str,
    *,
    scan_stride_frames: int = 2,
    max_probe_frames: int = 800,
) -> VideoProfile:
    """低分辨率快扫：差分曲线 + 切点 + 类型分类。"""
    duration = probe_video_duration_sec(video_path)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return VideoProfile(duration_sec=duration)

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    prev_full: Optional[np.ndarray] = None
    prev_bottom: Optional[np.ndarray] = None
    samples: List[Tuple[float, float, float]] = []
    text_scores: List[float] = []
    idx = 0

    while idx < max_probe_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % max(1, scan_stride_frames) != 0:
            idx += 1
            continue
        t_sec = idx / fps if fps > 0 else 0.0
        h, w = frame.shape[:2]
        scale = 320 / max(w, 1)
        small = cv2.resize(frame, (320, max(1, int(h * scale))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        bottom = _roi_bottom(gray)

        if prev_full is not None:
            fd = _frame_diff(prev_full, gray)
            bd = _frame_diff(prev_bottom, bottom) if prev_bottom is not None else fd
            samples.append((t_sec, fd, bd))

        if len(text_scores) < 5 and idx % max(1, int(fps * 2)) == 0:
            text_scores.append(_text_density_proxy(gray))

        prev_full = gray
        prev_bottom = bottom
        idx += 1

    cap.release()

    cut_times = _find_cut_times(samples, duration=duration)
    pyscene_segs = _try_pyscenedetect_segments(video_path)
    detector = "opencv_diff"
    if pyscene_segs:
        detector = "pyscenedetect+opencv"
        pyscene_cuts = [s[0] for s in pyscene_segs[1:]]
        if len(pyscene_cuts) >= len(cut_times):
            cut_times = pyscene_cuts

    cut_count = len(cut_times)
    mean_full = statistics.mean([s[1] for s in samples]) if samples else 0.0
    mean_bottom = statistics.mean([s[2] for s in samples]) if samples else 0.0
    bottom_ratio = mean_bottom / mean_full if mean_full > 1e-6 else 1.0
    text_density = statistics.mean(text_scores) if text_scores else 0.0
    dwell_mean, dwell_min, dwell_std, dwell_cv = _dwell_stats(cut_times, duration)

    video_class, confidence = classify_video_profile(
        duration=duration,
        cut_count=cut_count,
        bottom_ratio=bottom_ratio,
        mean_bottom_diff=mean_bottom,
        text_density=text_density,
        dwell_cv=dwell_cv,
    )

    profile = VideoProfile(
        duration_sec=round(duration, 3),
        fps=round(fps, 2),
        frame_count=frame_count,
        cut_times=[round(t, 3) for t in cut_times],
        cut_count=cut_count,
        dwell_mean_sec=round(dwell_mean, 3),
        dwell_min_sec=round(dwell_min, 3),
        dwell_std_sec=round(dwell_std, 3),
        dwell_cv=round(dwell_cv, 3),
        bottom_ratio=round(bottom_ratio, 3),
        mean_full_diff=round(mean_full, 3),
        mean_bottom_diff=round(mean_bottom, 3),
        text_density_score=round(text_density, 4),
        video_class=video_class,
        video_class_label=CLASS_LABELS.get(video_class, video_class),
        confidence=confidence,
        probe_sample_count=len(samples),
        detector=detector,
    )
    _log.info(
        "[链接沉淀文档-视频视觉提取|probe.probe_video_profile|video|硬编执行|完成] "
        "class=%s; cuts=%s; dwell_min=%s; bottom_ratio=%s; detector=%s",
        video_class,
        cut_count,
        dwell_min,
        bottom_ratio,
        detector,
    )
    return profile
