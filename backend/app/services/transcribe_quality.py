"""转写质量门禁：禁止 MOCK 冒充真转写；检测幻听/重复；下载文件最小体积。"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

for _p in Path(__file__).resolve().parents:
    _cand = _p / "src" / "agent"
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand.resolve()))
        break

from error_code_registry import (  # noqa: E402
    T1002,
    T1003,
    T1005,
    T1006,
    T1007,
    T1010,
    V1001,
    V1002,
    resolve_error_code,
)

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
        return False, V1001, "视频文件不存在"
    try:
        size = os.path.getsize(p)
    except OSError as ex:
        return False, T1003, f"无法读取视频文件: {ex}"
    if size < MIN_VIDEO_FILE_BYTES:
        return (
            False,
            V1002,
            f"视频文件过小（{size} bytes < {MIN_VIDEO_FILE_BYTES}），疑似下载不完整或空壳",
        )
    return True, "", ""


def _normalize_phrase(phrase: str) -> str:
    return re.sub(r"\s+", "", (phrase or "").strip())


def _compact_index_to_raw_prefix(raw: str, compact_keep: int) -> str:
    """将紧凑文本保留长度映射回原始字符串前缀。"""
    if compact_keep <= 0:
        return ""
    kept = 0
    out: List[str] = []
    for ch in raw:
        if not ch.isspace():
            if kept >= compact_keep:
                break
            kept += 1
        if kept < compact_keep:
            out.append(ch)
    cleaned = "".join(out).strip()
    compact = _normalize_phrase(raw)
    if len(cleaned) < MIN_TRANSCRIPT_CHARS and len(compact[:compact_keep]) >= MIN_TRANSCRIPT_CHARS:
        return compact[:compact_keep]
    return cleaned


def _find_longest_clean_prefix_cut(compact: str) -> int:
    """从尾部收缩，找重复率低于阈值的最长前缀（处理滑动错位幻听）。"""
    n = len(compact)
    if n < MIN_TRANSCRIPT_CHARS:
        return n
    min_keep = max(MIN_TRANSCRIPT_CHARS, int(n * 0.15))
    step = max(8, n // 80)
    cut = n
    while cut >= min_keep:
        ratio, _ = compute_repetition_ratio(compact[:cut])
        if ratio < REPETITION_RATIO_THRESHOLD:
            return cut
        cut -= step
    ratio, _ = compute_repetition_ratio(compact[:min_keep])
    if ratio < REPETITION_RATIO_THRESHOLD:
        return min_keep
    return n


def strip_whisper_hallucination_tail(text: str) -> Tuple[str, int]:
    """
    剔除 Whisper 尾部短语循环幻听（如「小伙伴们」连排），保留前文有效转写。
    返回 (清洗后文本, 剔除的紧凑字符数)。
    """
    raw = (text or "").strip()
    if not raw:
        return raw, 0
    compact = _normalize_phrase(raw)
    n = len(compact)
    if n < 60:
        return raw, 0

    full_ratio, _ = compute_repetition_ratio(compact)
    if full_ratio < REPETITION_RATIO_THRESHOLD:
        return raw, 0

    best_cut = n
    max_period = min(40, max(REPEAT_PHRASE_MIN_LEN, n // REPEAT_PHRASE_MIN_COUNT))
    for period in range(REPEAT_PHRASE_MIN_LEN, max_period + 1):
        unit = compact[n - period : n]
        if len(unit) < REPEAT_PHRASE_MIN_LEN:
            continue
        pos = n - period
        cnt = 1
        while pos >= period:
            if compact[pos - period : pos] == unit:
                cnt += 1
                pos -= period
            else:
                break
        if cnt >= REPEAT_PHRASE_MIN_COUNT and pos < best_cut:
            best_cut = pos

    prefix_cut = _find_longest_clean_prefix_cut(compact)
    best_cut = min(best_cut, prefix_cut)

    if best_cut >= n:
        return raw, 0

    stripped = n - best_cut
    cleaned = _compact_index_to_raw_prefix(raw, best_cut)
    return cleaned, stripped


def sanitize_transcript_for_pipeline(text: str) -> Tuple[str, int]:
    """生产路径：先剔除尾部幻听再进入门禁与 LLM 沉淀。"""
    return strip_whisper_hallucination_tail(text)


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
    stripped_tail = 0
    cleaned, stripped_tail = sanitize_transcript_for_pipeline(raw)
    if stripped_tail > 0 and cleaned:
        raw = cleaned
    char_len = len(raw)

    if meta.get("ok") is False or meta.get("error_code"):
        code = resolve_error_code(str(meta.get("error_code") or T1003)) or T1003
        msg = str(meta.get("error_message") or meta.get("error") or "语音转文字失败")
        return TranscriptAssessment(ok=False, error_code=code, error_message=msg, char_len=char_len)

    if not raw:
        return TranscriptAssessment(
            ok=False,
            error_code=T1005,
            error_message="转写文本为空",
            char_len=0,
        )

    if is_mock_transcript(raw):
        return TranscriptAssessment(
            ok=False,
            error_code=T1007,
            error_message="检测到 MOCK 演示转写（非真实 Whisper 结果），已阻断沉淀",
            is_mock=True,
            char_len=char_len,
            transcribe_degraded=True,
        )

    if char_len < MIN_TRANSCRIPT_CHARS:
        return TranscriptAssessment(
            ok=False,
            error_code=T1006,
            error_message=f"转写过短（{char_len} 字 < {MIN_TRANSCRIPT_CHARS}），无法进入原文整理",
            char_len=char_len,
            transcribe_degraded=True,
        )

    ratio, samples = compute_repetition_ratio(raw)
    if ratio >= REPETITION_RATIO_THRESHOLD:
        hint = "；".join(samples[:3]) if samples else "高重复短语"
        return TranscriptAssessment(
            ok=False,
            error_code=T1002,
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
            error_code=T1007,
            error_message=f"转写来源标记为 {src}，生产路径已拒绝",
            is_mock=True,
            char_len=char_len,
            transcribe_degraded=True,
        )

    degraded = bool(stripped_tail > 0 and ratio < REPETITION_RATIO_THRESHOLD)
    return TranscriptAssessment(
        ok=True,
        char_len=char_len,
        repetition_ratio=ratio,
        repeated_samples=tuple(samples),
        transcribe_degraded=degraded,
    )
