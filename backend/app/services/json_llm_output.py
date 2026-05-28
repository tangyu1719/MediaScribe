"""LLM 结构化 JSON 输出：解析、语法修补、校验与错误码。"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger("sba.json_llm_output")

# 定制错误码（流水线日志 / ops 可识别）
LLM_JSON_PARSE_FAILED = "LLM_JSON_PARSE_FAILED"
LLM_JSON_REPAIR_FAILED = "LLM_JSON_REPAIR_FAILED"
LLM_JSON_SCHEMA_INVALID = "LLM_JSON_SCHEMA_INVALID"


@dataclass
class JsonParseResult:
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    repaired: bool = False
    raw_preview: str = ""


def normalize_llm_string_escapes(text: str) -> str:
    """
    将 JSON 字段内残留的字面量转义（\\n、\\t）还原为真实换行/制表符。
    模型按「字符串内换行须转义」输出时，常写成 \\\\n，json.loads 后仍为两字符 \\n。
    """
    if not text or "\\" not in text:
        return text
    out = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return out


def _normalize_parsed_values(obj: Any) -> Any:
    if isinstance(obj, str):
        return normalize_llm_string_escapes(obj)
    if isinstance(obj, dict):
        return {k: _normalize_parsed_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_parsed_values(v) for v in obj]
    return obj


def _preview(text: str, n: int = 240) -> str:
    s = (text or "").strip().replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def _strip_code_fence(text: str) -> str:
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    return m.group(1).strip() if m else s


def _extract_balanced_object(text: str) -> Optional[str]:
    """按括号深度截取最外层 JSON 对象子串。"""
    s = _strip_code_fence(text)
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def repair_json_text(text: str) -> Tuple[str, List[str]]:
    """
    智能修补常见 LLM JSON 瑕疵（非严格 JSON5，偏保守）。
    返回 (修补后文本, 已应用修补项说明列表)。
    """
    notes: List[str] = []
    chunk = _extract_balanced_object(text) or _strip_code_fence(text)
    if not chunk:
        return "", notes

    s = chunk
    if re.search(r"\bundefined\b", s):
        s = re.sub(r"\bundefined\b", "null", s)
        notes.append("undefined→null")
    # 去掉对象/数组末尾多余逗号
    s2 = re.sub(r",\s*}", "}", s)
    s2 = re.sub(r",\s*]", "]", s2)
    if s2 != s:
        notes.append("trailing_comma")
        s = s2
    # 全角引号 → 半角
    if "\u201c" in s or "\u201d" in s:
        s = s.replace("\u201c", '"').replace("\u201d", '"')
        notes.append("fullwidth_quotes")
    return s, notes


def _validate_schema(obj: Dict[str, Any], required_keys: Sequence[str]) -> Optional[str]:
    for k in required_keys:
        if k not in obj:
            return f"缺少必填字段: {k}"
        val = obj.get(k)
        if val is None or (isinstance(val, str) and not val.strip()):
            return f"字段为空: {k}"
    return None


def parse_llm_json_object(
    text: str,
    *,
    required_keys: Sequence[str],
    allow_repair: bool = True,
) -> JsonParseResult:
    """
    解析 LLM 返回的 JSON 对象；失败时尝试语法修补后再解析。
    """
    raw = text or ""
    preview = _preview(raw)

    # 1) 直接解析整段
    for candidate in (raw, _strip_code_fence(raw), _extract_balanced_object(raw) or ""):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                err = _validate_schema(obj, required_keys)
                if err:
                    return JsonParseResult(
                        ok=False,
                        error_code=LLM_JSON_SCHEMA_INVALID,
                        error_message=err,
                        raw_preview=preview,
                    )
                return JsonParseResult(
                    ok=True, data=_normalize_parsed_values(obj), raw_preview=preview
                )
        except json.JSONDecodeError:
            continue

    if not allow_repair:
        return JsonParseResult(
            ok=False,
            error_code=LLM_JSON_PARSE_FAILED,
            error_message="JSON 解析失败",
            raw_preview=preview,
        )

    # 2) 修补后再解析
    repaired_text, repair_notes = repair_json_text(raw)
    if repaired_text:
        try:
            obj = json.loads(repaired_text)
            if isinstance(obj, dict):
                err = _validate_schema(obj, required_keys)
                if err:
                    return JsonParseResult(
                        ok=False,
                        error_code=LLM_JSON_SCHEMA_INVALID,
                        error_message=err,
                        repaired=True,
                        raw_preview=preview,
                    )
                _log.info(
                    "[LLM-JSON|json_llm_output.parse_llm_json_object|响应|硬编执行|修补成功] "
                    "修补项=%s",
                    ",".join(repair_notes) or "none",
                )
                return JsonParseResult(
                    ok=True, data=_normalize_parsed_values(obj), repaired=True, raw_preview=preview
                )
        except json.JSONDecodeError as ex:
            return JsonParseResult(
                ok=False,
                error_code=LLM_JSON_REPAIR_FAILED,
                error_message=f"修补后仍无法解析: {ex}",
                repaired=True,
                raw_preview=preview,
            )

    return JsonParseResult(
        ok=False,
        error_code=LLM_JSON_PARSE_FAILED,
        error_message="无法从模型输出中提取合法 JSON 对象",
        raw_preview=preview,
    )


def build_json_retry_user_suffix(error_code: str, required_keys: Sequence[str]) -> str:
    keys = ", ".join(f'"{k}"' for k in required_keys)
    return (
        "\n\n【重要-重试】上次输出不是合法 JSON，错误码="
        f"{error_code}。请仅输出一个 JSON 对象（可用 ```json 包裹），"
        f"必须包含字段: {keys}。禁止输出 JSON 以外的任何说明文字。"
    )
