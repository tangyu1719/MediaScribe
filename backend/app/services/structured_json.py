"""大模型结构化 JSON：Greedy 解码 + Pydantic 校验（预处理 / 意图纠偏）。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger("sba.structured_json")

GREEDY_DECODE_PARAMS: dict[str, float] = {"temperature": 0.0, "top_p": 1.0}

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")

# SuperBizAgent 领域意图（与 chat_feedback.INTENT_LABELS 对齐）
_VALID_DOMAIN_INTENTS = frozenset(
    {"kb", "doc_process", "devops", "business", "social", "general", "chitchat"}
)

DOMAIN_INTENT_LABELS: dict[str, str] = {
    "kb": "知识库",
    "doc_process": "资料处理",
    "devops": "研发运维",
    "business": "业务系统",
    "social": "社媒分析",
    "general": "通用",
    "chitchat": "简单问答",
}


class PreprocessJsonOutput(BaseModel):
    intent: str = "general"
    rewritten_query: str
    query_keywords: list[str] = Field(default_factory=list)
    retrieval_terms: list[str] = Field(default_factory=list)

    @field_validator("intent")
    @classmethod
    def _intent_enum(cls, v: str) -> str:
        code = (v or "general").strip()
        return code if code in _VALID_DOMAIN_INTENTS else "general"

    @field_validator("rewritten_query")
    @classmethod
    def _rewrite_nonempty(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("rewritten_query 不能为空")
        return text


class IntentSuggestItem(BaseModel):
    code: str
    label: str
    summary: str = ""

    @field_validator("label")
    @classmethod
    def _label_trim(cls, v: str) -> str:
        text = (v or "").strip()[:24]
        if not text:
            raise ValueError("label 不能为空")
        return text


T = TypeVar("T", bound=BaseModel)


def strip_markdown_fence(text: str) -> str:
    raw = (text or "").strip()
    if "```" not in raw:
        return raw
    for block in raw.split("```"):
        block = block.strip()
        if block.lower().startswith("json"):
            block = block[4:].strip()
        if block.startswith("{") or block.startswith("["):
            return block
    return raw


def repair_json_text(text: str) -> str:
    s = strip_markdown_fence(text).strip()
    if not (s.startswith("{") or s.startswith("[")):
        mobj = _JSON_OBJECT_RE.search(s) or _JSON_ARRAY_RE.search(s)
        if mobj:
            s = mobj.group()
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return s.replace("'", '"')


def extract_json_candidate(raw: str, *, kind: Literal["object", "array"] = "object") -> str | None:
    text = strip_markdown_fence(raw)
    if not text:
        return None
    if kind == "object":
        if text.startswith("{"):
            return text
        m = _JSON_OBJECT_RE.search(text)
        return m.group() if m else None
    if text.startswith("["):
        return text
    m = _JSON_ARRAY_RE.search(text)
    return m.group() if m else None


def loads_json(raw: str, *, kind: Literal["object", "array"] = "object") -> Any | None:
    candidate = extract_json_candidate(raw, kind=kind)
    if not candidate:
        return None
    for attempt_text in (candidate, repair_json_text(candidate)):
        try:
            return json.loads(attempt_text)
        except json.JSONDecodeError:
            continue
    return None


def validate_model(data: Any, model: type[T]) -> T | None:
    if data is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "[AI问答-结构化JSON|structured_json|Schema校验|硬编执行|失败] model=%s; error=%s",
            model.__name__,
            str(exc)[:200],
        )
        return None


def parse_preprocess_output(raw: str) -> dict | None:
    data = loads_json(raw, kind="object")
    if not isinstance(data, dict):
        return None
    validated = validate_model(data, PreprocessJsonOutput)
    return validated.model_dump() if validated else None


def parse_intent_suggest_items(raw: str, *, max_items: int = 2) -> list[dict]:
    data = loads_json(raw, kind="array")
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data[:max_items]:
        if not isinstance(item, dict):
            continue
        validated = validate_model(item, IntentSuggestItem)
        if validated:
            out.append(validated.model_dump())
    return out


def build_repair_prompt(original_prompt: str, bad_output: str) -> str:
    snippet = (bad_output or "")[:800]
    return (
        f"{original_prompt}\n\n"
        "你上一次输出不是合法 JSON 或字段不合规。请仅输出修正后的 JSON，不要解释。\n"
        f"错误输出片段：\n{snippet}"
    )


def openai_json_response_format() -> dict[str, Any]:
    return {"type": "json_object"}
