"""视频画面转文字增强（附加入口）：smart / scene_ocr / roi_diff。"""

from .entry import run_video_visual_extract, list_video_visual_methods
from .link_transcript import extract_video_transcript_for_pipeline, normalize_video_transcript_mode

__all__ = [
    "run_video_visual_extract",
    "list_video_visual_methods",
    "extract_video_transcript_for_pipeline",
    "normalize_video_transcript_mode",
]
