"""流水线产出质量门禁：标题/摘要空输入与拒答式占位检测，输出运维 Agent 可解析的 PIPE_* 报错码。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# ── 结构化报错码（运维 Agent 按码定位 SPAN 环节）──
PIPE_INVALID_INPUT_EMPTY = "PIPE_INVALID_INPUT_EMPTY"
PIPE_SUMMARY_REJECTED = "PIPE_SUMMARY_REJECTED"
PIPE_LINK_NOT_FOUND = "PIPE_LINK_NOT_FOUND"
PIPE_TITLE_REJECTED = "PIPE_TITLE_REJECTED"
PIPE_TITLE_HIGH_REPETITION = "PIPE_TITLE_HIGH_REPETITION"
PIPE_ASR_EMPTY = "PIPE_ASR_EMPTY"

# 标题/摘要中的拒答式占位片段（子串匹配）
_REJECT_TITLE_MARKERS: Tuple[str, ...] = (
    "未提供有效待整理文本内容",
    "未提供有效",
    "无有效待整理",
    "无有效技术内容",
    "无有效内容输入",
    "无有效输入",
    "空输入内容",
    "空输入技术",
    "空待整理",
    "空输入",
    "待处理输入内容",
    "待处理文本",
    "待处理内容",
    "待分析内容",
    "输入占位内容",
    "输入内容为空",
    "单字符待处理",
    "单字符输入",
    "单字符0输入",
    "单数字零输入",
    "单个数字0输入",
    "单数字0输入",
    "页面访问异常",
    "你访问的页面不见了",
    "页面不见了",
    "内容分析",  # extract_title_from_summary 兜底标题
)

_LINK_NOT_FOUND_MARKERS: Tuple[str, ...] = (
    "页面不见了",
    "页面访问异常",
    "访问的页面",
    "404",
)

_EMPTY_INPUT_MARKERS: Tuple[str, ...] = (
    "未提供有效",
    "无有效",
    "空输入",
    "空待整理",
    "输入内容为空",
    "输入占位",
    "单字符",
    "单数字",
    "单个数字",
    "待处理输入",
    "待处理文本",
    "待分析内容",
)


class PipelineOutputQualityError(Exception):
    """标题/摘要质量门禁失败。"""

    def __init__(self, error_code: str, message: str, *, span_stage: str = ""):
        self.error_code = error_code
        self.message = message
        self.span_stage = span_stage
        super().__init__(message)


@dataclass
class TitleAssessment:
    ok: bool
    error_code: str = ""
    error_message: str = ""
    span_stage: str = ""
    title: str = ""

    def to_meta(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "span_stage": self.span_stage,
            "title": self.title,
        }


def _title_repetition_ratio(title: str) -> float:
    """短标题字符级重复率（检测「整理整理整理」类异常）。"""
    s = re.sub(r"\s+", "", (title or "").strip())
    if len(s) < 8:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    dup = sum(c - 1 for c in counts.values() if c > 1)
    return dup / max(1, len(s))


def _match_any(text: str, markers: Tuple[str, ...]) -> Optional[str]:
    t = (text or "").strip()
    for m in markers:
        if m and m in t:
            return m
    return None


def assess_extracted_title(
    title: str,
    *,
    link: str = "",
    link_title: str = "",
    source_text_len: int = 0,
) -> TitleAssessment:
    """
    标题提取后质量审核。
    span_stage 供运维 Agent 定位：extract / assemble / ai_analysis / transcribe / download
    """
    t = (title or "").strip()
    lt = (link_title or "").strip()
    if not t or t in ("内容分析", "未知标题", "未命名文档"):
        code = PIPE_INVALID_INPUT_EMPTY
        if _match_any(lt, _LINK_NOT_FOUND_MARKERS) or "不见了" in lt:
            code = PIPE_LINK_NOT_FOUND
        return TitleAssessment(
            ok=False,
            error_code=code,
            error_message=f"标题提取失败：无效占位标题「{t or '(空)'}」",
            span_stage="extract" if code == PIPE_LINK_NOT_FOUND else "assemble",
            title=t,
        )

    hit = _match_any(t, _REJECT_TITLE_MARKERS)
    if hit:
        if _match_any(t, _LINK_NOT_FOUND_MARKERS) or _match_any(lt, _LINK_NOT_FOUND_MARKERS):
            return TitleAssessment(
                ok=False,
                error_code=PIPE_LINK_NOT_FOUND,
                error_message=f"链接无效或笔记不存在（标题含「{hit}」）",
                span_stage="extract",
                title=t,
            )
        if _match_any(t, _EMPTY_INPUT_MARKERS):
            stage = "assemble"
            if source_text_len <= 0:
                stage = "extract"
            return TitleAssessment(
                ok=False,
                error_code=PIPE_INVALID_INPUT_EMPTY,
                error_message=f"上游无有效正文（标题拒答式占位：「{hit}」）",
                span_stage=stage,
                title=t,
            )
        return TitleAssessment(
            ok=False,
            error_code=PIPE_TITLE_REJECTED,
            error_message=f"标题为拒答式占位（命中「{hit}」）",
            span_stage="ai_analysis",
            title=t,
        )

    rep = _title_repetition_ratio(t)
    if rep >= 0.55:
        return TitleAssessment(
            ok=False,
            error_code=PIPE_TITLE_HIGH_REPETITION,
            error_message=f"标题重复率过高（{rep:.0%}）：{t[:40]}",
            span_stage="ai_analysis",
            title=t,
        )

    return TitleAssessment(ok=True, title=t)


def validate_extracted_title(
    title: str,
    *,
    link: str = "",
    link_title: str = "",
    source_text_len: int = 0,
) -> str:
    """通过则返回标题；失败抛 PipelineOutputQualityError。"""
    gate = assess_extracted_title(
        title,
        link=link,
        link_title=link_title,
        source_text_len=source_text_len,
    )
    if gate.ok:
        return gate.title
    raise PipelineOutputQualityError(
        gate.error_code,
        gate.error_message,
        span_stage=gate.span_stage,
    )
