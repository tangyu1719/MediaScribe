"""LLM Agent reject JSON 检测与降级防护（避免 reject 占位污染正文/标题/摘要）。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

_REJECT_STATUS_RE = re.compile(r'"status"\s*:\s*"reject"', re.I)
_REJECT_CODE_RE = re.compile(r'"reject_code"\s*:\s*"([^"]+)"', re.I)


def looks_like_llm_reject_text(text: str) -> bool:
    """判断字符串是否为 LLM 拒答 JSON 或含 reject 信号的片段。"""
    s = (text or "").strip()
    if not s:
        return False
    if _REJECT_STATUS_RE.search(s):
        return True
    if "reject_code" in s and s.startswith("{"):
        try:
            data = json.loads(s)
            if isinstance(data, dict) and str(data.get("status") or "").lower() == "reject":
                return True
        except json.JSONDecodeError:
            pass
    # 截断/损坏的 reject JSON（如标题提取误截）
    if "statusreject" in s.replace(" ", "").lower() or s.startswith("{status"):
        return True
    return False


def parse_reject_payload(text: str) -> Optional[Dict[str, Any]]:
    """解析拒答 JSON；非 reject 或无法解析时返回 None。"""
    s = (text or "").strip()
    if not looks_like_llm_reject_text(s):
        return None
    if s.startswith("{"):
        try:
            data = json.loads(s)
            if isinstance(data, dict) and str(data.get("status") or "").lower() == "reject":
                return data
        except json.JSONDecodeError:
            pass
    m = _REJECT_CODE_RE.search(s)
    if m:
        return {
            "status": "reject",
            "reject_code": m.group(1),
            "reject_reason": "拒答 JSON 片段",
        }
    return {"status": "reject", "reject_code": "LLM_INPUT_REJECTED", "reject_reason": "拒答占位"}


def reject_error_from_text(text: str) -> Tuple[str, str]:
    """从拒答文本提取 (error_code, error_message)。"""
    payload = parse_reject_payload(text) or {}
    code = str(payload.get("reject_code") or "LLM_INPUT_REJECTED").strip().upper()
    if code == "INPUT_TOO_SHORT":
        from .pipeline_output_quality import LLM_INPUT_TOO_SHORT

        code = LLM_INPUT_TOO_SHORT
    reason = str(payload.get("reject_reason") or "LLM 拒答：输入不足以生成有效输出").strip()
    return code, reason


def safe_plain_text_fallback(
    raw_out: str,
    *,
    fallback_fn,
) -> str:
    """
    JSON 解析失败后的纯文本降级：reject JSON 不得当作正文，回退 fallback_fn('')。
    """
    if looks_like_llm_reject_text(raw_out):
        return fallback_fn("")
    return fallback_fn(raw_out or "")
