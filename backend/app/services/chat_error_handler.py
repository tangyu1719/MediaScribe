"""聊天链路错误处理：禁止原始异常直出，必须经 LLM 分析后再对用户展示。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator, Dict, Optional, Union

_LOG = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chat_err")

_RAW_TECH_PATTERNS = re.compile(
    r"(Traceback|Error:|Exception:|UnboundLocalError|NameError|TypeError|"
    r"AttributeError|ImportError|ModuleNotFoundError|SyntaxError|KeyError|"
    r"cannot access local variable|is not defined|not associated with a value|"
    r"HTTP \d{3}|stack trace|File \"|line \d+)",
    re.I,
)


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _new_trace(prefix: str = "trace_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def is_raw_technical_error(message: str) -> bool:
    """判断是否为需经 LLM 转译的原始技术报错。"""
    text = (message or "").strip()
    if not text:
        return False
    if _RAW_TECH_PATTERNS.search(text):
        return True
    # 英文占比高且含典型异常词
    if len(text) > 20 and sum(1 for c in text if ord(c) < 128) / len(text) > 0.65:
        low = text.lower()
        if any(k in low for k in ("error", "exception", "failed", "invalid", "timeout")):
            return True
    return False


def fallback_user_message(*, stage: str = "") -> str:
    stage_cn = (stage or "任务处理").strip()
    return (
        f"**{stage_cn}时遇到问题**\n\n"
        "系统未能完成本次请求，建议您：\n"
        "1. 稍后重试相同问题\n"
        "2. 简化或拆分问题后重新发送\n"
        "3. 若反复出现，请联系管理员并说明操作步骤（无需提供技术报错原文）"
    )


async def llm_analyze_error_for_user(
    *,
    error_type: str,
    error_message: str,
    stage: str = "",
    user_message: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """调用 LLM 将内部异常转写为用户可读说明；失败时返回通用兜底文案（不含原始报错）。"""
    from .ai_chat import load_chat_llm_config, resolve_chat_api_credentials

    cfg = load_chat_llm_config()
    creds = resolve_chat_api_credentials(cfg)
    api_key = creds.get("api_key") or ""
    model = creds.get("model") or ""
    provider = creds.get("provider") or "ark"
    base_url = creds.get("base_url") or ""

    if not api_key or not model:
        _LOG.warning(
            "[AI问答-错误处理|chat_error_handler.llm_analyze_error_for_user|LLM|Agent执行|跳过] "
            "凭证未就绪; stage=%s; error_type=%s",
            stage,
            error_type,
        )
        return fallback_user_message(stage=stage)

    ctx_bits = []
    if user_message:
        ctx_bits.append(f"用户问题：{user_message[:300]}")
    if stage:
        ctx_bits.append(f"发生阶段：{stage}")
    if context:
        try:
            ctx_bits.append(f"上下文：{json.dumps(context, ensure_ascii=False)[:400]}")
        except Exception:
            pass

    system_prompt = (
        "你是 SuperBizAgent 的系统异常分析助手。"
        "你会收到系统内部错误信息，必须用中文向用户说明，格式：\n"
        "## 发生了什么\n（一句话）\n"
        "## 可能原因\n（1-3 条要点）\n"
        "## 建议操作\n（1-3 条可执行步骤）\n"
        "硬性规则：禁止原文粘贴 Python/C/Java 异常、Traceback、堆栈、英文类名作为主要说明；"
        "禁止输出 UnboundLocalError、NameError 等术语；语气专业简洁，总字数 120-220 字。"
    )
    user_prompt = "\n".join(
        [
            "请分析以下系统内部错误并生成用户说明：",
            *ctx_bits,
            f"错误类型（内部）：{error_type}",
            f"错误详情（内部，勿原文复述）：{error_message[:600]}",
        ]
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    loop = asyncio.get_running_loop()

    def _call() -> str:
        from provider_adapters import invoke_unified

        return (
            invoke_unified(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=600,
                timeout=45.0,
            )
            or ""
        ).strip()

    try:
        text = await loop.run_in_executor(_executor, _call)
        if text and not is_raw_technical_error(text):
            return text
    except Exception as ex:
        _LOG.warning(
            "[AI问答-错误处理|chat_error_handler.llm_analyze_error_for_user|LLM|Agent执行|失败] "
            "分析调用失败; error_type=%s; error_message=%s",
            type(ex).__name__,
            str(ex)[:200],
        )
    return fallback_user_message(stage=stage)


async def _stream_text_as_answer(
    text: str,
    *,
    session_id: str,
    trace_id: str,
    task_id: str,
    error_analyzed: bool,
) -> AsyncIterator[str]:
    body = (text or "").strip() or fallback_user_message()
    pos = 0
    chunk = 48
    while pos < len(body):
        piece = body[pos : pos + chunk]
        pos += chunk
        yield _sse(
            "answer_delta",
            {
                "session_id": session_id,
                "trace_id": trace_id,
                "task_id": task_id,
                "content": piece,
                "kind": "body",
                "stream_mode": "token",
                "error_analyzed": error_analyzed,
            },
        )
    yield _sse(
        "answer_end",
        {
            "session_id": session_id,
            "trace_id": trace_id,
            "task_id": task_id,
            "error_analyzed": error_analyzed,
            "content": body,
        },
    )


async def stream_user_error_sse(
    message_or_exc: Union[str, BaseException],
    *,
    session_id: str,
    trace_id: str = "",
    task_id: str = "",
    stage: str = "",
    user_message: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[str]:
    """
    统一错误 SSE 出口：原始异常 / 技术报错经 LLM 分析后以 answer 事件输出；
    已是中文可读的业务提示则直接输出，仍禁止带 Traceback 的原文。
    """
    trace_id = trace_id or _new_trace()

    if isinstance(message_or_exc, BaseException):
        error_type = type(message_or_exc).__name__
        error_message = str(message_or_exc)[:800]
        need_llm = True
    else:
        error_message = str(message_or_exc or "").strip()
        error_type = "UserFacingError"
        need_llm = is_raw_technical_error(error_message)

    _LOG.error(
        "[AI问答-错误处理|chat_error_handler.stream_user_error_sse|session:%s|Agent执行|捕获] "
        "error_type=%s; stage=%s; need_llm=%s; error_message=%s",
        session_id,
        error_type,
        stage or "unknown",
        need_llm,
        error_message[:300],
    )

    yield _sse(
        "pipeline_progress",
        {
            "session_id": session_id,
            "trace_id": trace_id,
            "stage": "正在分析异常原因" if need_llm else "正在整理说明",
            "progress": 95,
            "detail": "系统正在生成可读说明",
        },
    )
    yield _sse(
        "answer_start",
        {
            "session_id": session_id,
            "trace_id": trace_id,
            "task_id": task_id,
            "error_analyzed": True,
            "stream_mode": "token",
        },
    )

    if need_llm:
        body = await llm_analyze_error_for_user(
            error_type=error_type,
            error_message=error_message,
            stage=stage,
            user_message=user_message,
            context=context,
        )
    else:
        body = error_message or fallback_user_message(stage=stage)

    async for line in _stream_text_as_answer(
        body,
        session_id=session_id,
        trace_id=trace_id,
        task_id=task_id,
        error_analyzed=True,
    ):
        yield line
