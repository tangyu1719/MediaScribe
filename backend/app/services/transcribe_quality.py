"""转写质量门禁：禁止 MOCK 冒充真转写；检测幻听/重复；下载文件最小体积。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# 与 video_downloader.speech_to_text 硬编码 MOCK 文案一致，用于识别静默兜底
MOCK_TRANSCRIPT_SNIPPETS: Tuple[str, ...] = (
    "这是一段模拟的视频转文字结果",
    "这是一个示例文本，用于演示语音转文字功能",
    "视频内容包括产品介绍、使用方法和注意事项",
)

# 生产路径：小于该体积视为坏文件（并发下载易得到 HTML/空壳 mp4）
MIN_VIDEO_FILE_BYTES = 8192

# 过短转写不进入 LLM 沉淀
MIN_TRANSCRIPT_CHARS = 80

# 重复率超过阈值视为 Whisper 幻听/重复
REPETITION_RATIO_THRESHOLD = 0.38
REPEAT_PHRASE_MIN_LEN = 6
REPEAT_PHRASE_MIN_COUNT = 4


@dataclass
class TranscriptAssessment:
    ok: bool
    error_code: str = ""
    error_message: str = ""
    transcribe_degraded: bool = False
    repetition_ratio: float = 0.0
    repeated_samples: Tuple[str, ...] = ()
    is_mock: bool = False
    char_len: int = 0

    def to_meta(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "transcribe_degraded": self.transcribe_degraded,
            "repetition_ratio": round(self.repetition_ratio, 4),
            "repeated_samples": list(self.repeated_samples[:5]),
            "is_mock": self.is_mock,
            "char_len": self.char_len,
        }


def is_mock_transcript(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    return any(m in s for m in MOCK_TRANSCRIPT_SNIPPETS)


def assess_video_file(path: str) -> Tuple[bool, str, str]:
    """校验下载产物是否可用于 Whisper。"""
    p = (path or "").strip()
    if not p or not os.path.isfile(p):
        return False, "video_file_missing", "视频文件不存在"
    try:
        size = os.path.getsize(p)
    except OSError as ex:
        return False, "video_file_stat_failed", f"无法读取视频文件: {ex}"
    if size < MIN_VIDEO_FILE_BYTES:
        return (
            False,
            "video_file_too_small",
            f"视频文件过小（{size} bytes < {MIN_VIDEO_FILE_BYTES}），疑似下载不完整或空壳",
        )
    return True, "", ""


def _normalize_phrase(phrase: str) -> str:
    return re.sub(r"\s+", "", (phrase or "").strip())


def compute_repetition_ratio(text: str) -> Tuple[float, List[str]]:
    """按句/短语及 n-gram 统计重复占比，识别 Whisper 幻听循环。"""
    raw = (text or "").strip()
    if len(raw) < 40:
        return 0.0, []

    compact = re.sub(r"\s+", "", raw)
    samples: List[str] = []
    dup_chars = 0

    parts: List[str] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        for seg in re.split(r"[。！？；;!?]", ln):
            seg = seg.strip()
            if len(seg) >= REPEAT_PHRASE_MIN_LEN:
                parts.append(seg)

    if parts:
        norm_map: Dict[str, str] = {}
        counts: Dict[str, int] = {}
        for p in parts:
            n = _normalize_phrase(p)
            if len(n) < REPEAT_PHRASE_MIN_LEN:
                continue
            norm_map.setdefault(n, p[:48])
            counts[n] = counts.get(n, 0) + 1
        for n, cnt in counts.items():
            if cnt >= REPEAT_PHRASE_MIN_COUNT:
                dup_chars += len(n) * (cnt - 1)
                samples.append(f"{norm_map[n]}×{cnt}")

    # n-gram：捕获无标点连写重复（如「这是什么？」×20 连排）
    win = max(REPEAT_PHRASE_MIN_LEN, min(16, max(8, len(compact) // 12)))
    if len(compact) >= win * 4:
        ng_counts: Dict[str, int] = {}
        for i in range(0, len(compact) - win + 1):
            frag = compact[i : i + win]
            ng_counts[frag] = ng_counts.get(frag, 0) + 1
        for frag, cnt in ng_counts.items():
            if cnt >= REPEAT_PHRASE_MIN_COUNT:
                dup_chars += len(frag) * (cnt - 1)
                if len(samples) < 8:
                    samples.append(f"{frag[:32]}×{cnt}")

    ratio = dup_chars / max(1, len(compact))
    return min(1.0, ratio), samples[:8]


def assess_transcript(
    text: str,
    *,
    transcribe_source: str = "",
    transcript_meta: Optional[Dict[str, Any]] = None,
) -> TranscriptAssessment:
    """生产路径转写门禁。"""
    meta = dict(transcript_meta or {})
    raw = (text or "").strip()
    char_len = len(raw)

    if meta.get("ok") is False or meta.get("error_code"):
        code = str(meta.get("error_code") or "transcribe_failed")
        msg = str(meta.get("error_message") or meta.get("error") or "语音转文字失败")
        return TranscriptAssessment(ok=False, error_code=code, error_message=msg, char_len=char_len)

    if not raw:
        return TranscriptAssessment(
            ok=False,
            error_code="transcript_empty",
            error_message="转写文本为空",
            char_len=0,
        )

    if is_mock_transcript(raw):
        return TranscriptAssessment(
            ok=False,
            error_code="transcript_mock_fallback",
            error_message="检测到 MOCK 演示转写（非真实 Whisper 结果），已阻断沉淀",
            is_mock=True,
            char_len=char_len,
            transcribe_degraded=True,
        )

    if char_len < MIN_TRANSCRIPT_CHARS:
        return TranscriptAssessment(
            ok=False,
            error_code="transcript_too_short",
            error_message=f"转写过短（{char_len} 字 < {MIN_TRANSCRIPT_CHARS}），无法进入原文整理",
            char_len=char_len,
            transcribe_degraded=True,
        )

    ratio, samples = compute_repetition_ratio(raw)
    if ratio >= REPETITION_RATIO_THRESHOLD:
        hint = "；".join(samples[:3]) if samples else "高重复短语"
        return TranscriptAssessment(
            ok=False,
            error_code="transcribe_degraded_repetition",
            error_message=(
                f"转写重复率过高（{ratio:.0%} ≥ {REPETITION_RATIO_THRESHOLD:.0%}），"
                f"疑似 Whisper 幻听/并发劣化；样例：{hint}"
            ),
            transcribe_degraded=True,
            repetition_ratio=ratio,
            repeated_samples=tuple(samples),
            char_len=char_len,
        )

    src = (transcribe_source or meta.get("transcribe_source") or "").strip()
    if src in ("mock", "mock_fallback", "demo"):
        return TranscriptAssessment(
            ok=False,
            error_code="transcript_mock_source",
            error_message=f"转写来源标记为 {src}，生产路径已拒绝",
            is_mock=True,
            char_len=char_len,
            transcribe_degraded=True,
        )

    return TranscriptAssessment(
        ok=True,
        char_len=char_len,
        repetition_ratio=ratio,
        repeated_samples=tuple(samples),
    )
