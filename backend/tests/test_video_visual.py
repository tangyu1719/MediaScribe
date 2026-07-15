"""video_visual 子树单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.video_visual.roi_diff.pipeline import _roi_slice
import numpy as np


def test_roi_slice_bounds():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = _roi_slice(frame, (0.6, 0.0, 1.0, 1.0))
    assert crop.shape[0] == 40
    assert crop.shape[1] == 200


def test_whisper_prompt_echo():
    from app.services.video_visual.common import _is_whisper_prompt_echo

    assert _is_whisper_prompt_echo("请使用标准简体中文进行转写，保持语句通顺，不要遗漏任何内容。")
    assert not _is_whisper_prompt_echo("模块 4: 数据集生命周期管理 " * 5)


def test_merge_short_segments():
    from app.services.video_visual.probe import merge_short_segments

    segs = [(0.0, 0.2), (0.2, 1.5), (1.5, 1.7), (1.7, 3.0)]
    merged = merge_short_segments(segs, min_dur_sec=0.35)
    assert merged[0][0] == 0.0
    assert merged[-1][1] == 3.0
    assert len(merged) <= 3


def test_plan_segment_sample_times():
    from app.services.video_visual.probe import plan_segment_sample_times

    times = plan_segment_sample_times([(0.0, 1.0), (1.0, 5.0)], position=0.8)
    assert abs(times[0] - 0.8) < 0.01
    assert abs(times[1] - 4.2) < 0.01


def test_classify_slide_deck():
    from app.services.video_visual.probe import CLASS_SLIDE_DECK, classify_video_profile

    cls, conf = classify_video_profile(
        duration=10.0,
        cut_count=8,
        bottom_ratio=1.0,
        mean_bottom_diff=5.0,
        text_density=0.12,
        dwell_cv=0.4,
    )
    assert cls == CLASS_SLIDE_DECK
    assert conf > 0.3


def test_classify_subtitle():
    from app.services.video_visual.probe import CLASS_SUBTITLE_SPEECH, classify_video_profile

    cls, _ = classify_video_profile(
        duration=60.0,
        cut_count=1,
        bottom_ratio=2.5,
        mean_bottom_diff=12.0,
        text_density=0.02,
        dwell_cv=0.1,
    )
    assert cls == CLASS_SUBTITLE_SPEECH


def test_normalize_video_transcript_mode():
    from app.services.video_visual.link_transcript import normalize_video_transcript_mode

    assert normalize_video_transcript_mode("audio") == "audio_only"
    assert normalize_video_transcript_mode("smart") == "visual_frames"
    assert normalize_video_transcript_mode("hybrid") == "hybrid"
    assert normalize_video_transcript_mode("") == "audio_only"


def test_pipeline_options_video_mode():
    from app.services.pipeline_options_util import video_transcript_mode

    assert video_transcript_mode({"pipeline_options": {"video_transcript_mode": "visual_frames"}}) == "visual_frames"


def test_segments_from_cuts():
    from app.services.video_visual.probe import segments_from_cuts

    segs = segments_from_cuts([1.0, 2.0, 5.0], 10.0)
    assert segs[0][0] == 0.0
    assert segs[-1][1] == 10.0
    assert len(segs) >= 3
