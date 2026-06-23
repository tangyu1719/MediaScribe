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
XHS_EXTRACT_EMPTY = "XHS_EXTRACT_EMPTY"
XHS_ASSEMBLE_EMPTY = "XHS_ASSEMBLE_EMPTY"
XHS_CONTENT_JUNK = "XHS_CONTENT_JUNK"
LLM_INPUT_REJECTED = "LLM_INPUT_REJECTED"
LLM_INPUT_TOO_SHORT = "LLM_INPUT_TOO_SHORT"
DELIVERY_REVIEW_FAILED = "DELIVERY_REVIEW_FAILED"


class LLMInputRejectedError(Exception):
    """摘要/整理 Agent 显式 reject 信号。"""

    def __init__(self, error_code: str, message: str, *, reject_reason: str = ""):
        self.error_code = error_code
        self.message = message
        self.reject_reason = reject_reason
        super().__init__(message)

# 小红书正文最低有效字数（低于此且无图片/OCR 视为抓取失败）
_MIN_XHS_BODY_CHARS = 12

# 仅平台名/页脚占位，不得进入 LLM
_JUNK_BODY_EXACT: Tuple[str, ...] = (
    "小红书",
    "# 小红书",
    "## 正文\n小红书",
    "正文：小红书",
    "正文:小红书",
)

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


@dataclass
class ContentAssessment:
    ok: bool
    error_code: str = ""
    error_message: str = ""
    span_stage: str = ""
    text_len: int = 0
    image_links: int = 0
    ocr_text_len: int = 0

    def to_meta(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "span_stage": self.span_stage,
            "text_len": self.text_len,
            "image_links": self.image_links,
            "ocr_text_len": self.ocr_text_len,
        }


def is_junk_body_text(text: str) -> bool:
    """检测仅平台名/标题壳等无效正文占位。"""
    s = (text or "").strip()
    if not s:
        return True
    if s in _JUNK_BODY_EXACT:
        return True
    compact = re.sub(r"\s+", "", s)
    if compact in ("小红书", "#小红书", "正文：小红书", "正文:小红书"):
        return True
    if re.fullmatch(r"#?\s*小红书\s*", s):
        return True
    # 仅 H1 标题行且无实质正文
    if s.startswith("# ") and len(compact) <= 20 and "小红书" in s and "\n" not in s.strip():
        return True
    return len(s) < _MIN_XHS_BODY_CHARS and s in ("小红书",)


def xhs_payload_stats(result: Optional[Dict[str, Any]]) -> Tuple[int, int, int]:
    """返回 (text_len, image_links, ocr_text_len)。"""
    r = result or {}
    text_len = len((r.get("text_content") or "").strip())
    image_links = len(r.get("image_links") or [])
    ocr_text_len = 0
    for img in r.get("image_analysis") or []:
        if isinstance(img, dict):
            ocr_text_len += len((img.get("text") or "").strip())
    return text_len, image_links, ocr_text_len


def assess_xhs_extractor_result(
    result: Optional[Dict[str, Any]],
    *,
    after_ocr: bool = False,
) -> ContentAssessment:
    """
    小红书 LinkAnalyzer 产出前/后校验。
    - 提取后：须 text_len>=阈值 或 image_links>0（可 OCR 补偿）
    - OCR 后：须有效正文或 OCR 文本合计达阈值
    """
    text_len, image_links, ocr_text_len = xhs_payload_stats(result)
    body = ((result or {}).get("text_content") or "").strip()
    stage = "ocr" if after_ocr else "extract"

    if after_ocr:
        combined = body
        if ocr_text_len > 0:
            combined = (combined + "\n" + " ".join(
                (img.get("text") or "").strip()
                for img in ((result or {}).get("image_analysis") or [])
                if isinstance(img, dict)
            )).strip()
        if is_junk_body_text(combined) or len(combined.strip()) < _MIN_XHS_BODY_CHARS:
            if image_links > 0 and ocr_text_len == 0:
                msg = (
                    f"笔记含 {image_links} 张图但 OCR 未识别到文本，"
                    f"且 HTML 正文无效（text_len={text_len}）"
                )
            else:
                msg = (
                    f"抓取结果无有效正文（text_len={text_len}; "
                    f"image_links={image_links}; ocr_text_len={ocr_text_len}）"
                )
            return ContentAssessment(
                ok=False,
                error_code=XHS_EXTRACT_EMPTY if image_links == 0 else XHS_ASSEMBLE_EMPTY,
                error_message=msg,
                span_stage=stage,
                text_len=text_len,
                image_links=image_links,
                ocr_text_len=ocr_text_len,
            )
        return ContentAssessment(
            ok=True,
            text_len=text_len,
            image_links=image_links,
            ocr_text_len=ocr_text_len,
        )

    # 提取阶段：完全空壳直接失败；有图可进 OCR
    if text_len == 0 and image_links == 0:
        return ContentAssessment(
            ok=False,
            error_code=XHS_EXTRACT_EMPTY,
            error_message="HTTP 抓取未得到正文与图片（text_len=0; image_links=0），链接可访问但解析为空",
            span_stage="extract",
            text_len=0,
            image_links=0,
            ocr_text_len=0,
        )
    if text_len > 0 and is_junk_body_text(body) and image_links == 0:
        return ContentAssessment(
            ok=False,
            error_code=XHS_CONTENT_JUNK,
            error_message=f"抓取正文为无效占位（{body[:40]!r}）",
            span_stage="extract",
            text_len=text_len,
            image_links=image_links,
        )
    return ContentAssessment(
        ok=True,
        text_len=text_len,
        image_links=image_links,
        ocr_text_len=ocr_text_len,
    )


def assess_assembled_source_text(source_text: str) -> ContentAssessment:
    """原文装配后自校验。"""
    s = (source_text or "").strip()
    if not s:
        return ContentAssessment(
            ok=False,
            error_code=XHS_ASSEMBLE_EMPTY,
            error_message="原文装配结果为空",
            span_stage="assemble",
        )
    if is_junk_body_text(s):
        return ContentAssessment(
            ok=False,
            error_code=XHS_CONTENT_JUNK,
            error_message=f"原文装配仅为占位/壳文本（len={len(s)}）",
            span_stage="assemble",
            text_len=len(s),
        )
    if len(s) < _MIN_XHS_BODY_CHARS:
        return ContentAssessment(
            ok=False,
            error_code=XHS_ASSEMBLE_EMPTY,
            error_message=f"原文过短（len={len(s)}），不满足最低 {_MIN_XHS_BODY_CHARS} 字",
            span_stage="assemble",
            text_len=len(s),
        )
    return ContentAssessment(ok=True, text_len=len(s))


def assess_consolidation_input(text: str, *, stage_label: str = "") -> ContentAssessment:
    """LLM 沉淀前校验：禁止空/ junk 输入进入摘要 Agent。"""
    s = (text or "").strip()
    if not s or is_junk_body_text(s):
        return ContentAssessment(
            ok=False,
            error_code=PIPE_INVALID_INPUT_EMPTY,
            error_message=f"文档沉淀拒绝空/无效输入（len={len(s)}; stage={stage_label or 'ai_analysis'}）",
            span_stage="ai_analysis",
            text_len=len(s),
        )
    if len(s) < _MIN_XHS_BODY_CHARS:
        return ContentAssessment(
            ok=False,
            error_code=PIPE_INVALID_INPUT_EMPTY,
            error_message=f"文档沉淀输入过短（len={len(s)}）",
            span_stage="ai_analysis",
            text_len=len(s),
        )
    return ContentAssessment(ok=True, text_len=len(s))


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
