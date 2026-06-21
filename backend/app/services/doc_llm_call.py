"""文档标准化链路：reason 类 LLM 单次调用（OCR+LLM 描述合成）。"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sba.doc_llm_call")


class DocReasonLlm:
    def call(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 900,
        task_type: str = "reason",
    ) -> str:
        cfg = _load_llm_cfg()
        api_key = (cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
        base_url = (
            cfg.get("volcengine_base_url") or cfg.get("openai_base_url") or "https://ark.cn-beijing.volces.com/api/v3"
        ).strip()
        provider = (cfg.get("gateway_provider") or "ark").strip().lower()
        model = (cfg.get("ai_chat_model") or "").strip()
        if not api_key or not model:
            return "【配置错误】未配置 LLM 网关"

        agent_dir = _agent_dir()
        if agent_dir and str(agent_dir) not in sys.path:
            sys.path.insert(0, str(agent_dir))
        try:
            from provider_adapters import invoke_chat_completion_raw, _extract_openai_message_dict
        except ImportError as exc:
            return f"【配置错误】provider_adapters 不可用: {exc}"

        try:
            data = invoke_chat_completion_raw(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=120.0,
                thinking_enabled=False,
                tools=None,
            )
            msg = _extract_openai_message_dict(data)
            content = msg.get("content") or ""
            return content.strip() if isinstance(content, str) else str(content).strip()
        except Exception as exc:
            logger.warning(
                "[RAG-文档标准化|doc_llm_call|call|Agent执行|失败] err=%s",
                str(exc)[:200],
            )
            return ""


_llm: Optional[DocReasonLlm] = None


def get_llm() -> DocReasonLlm:
    global _llm
    if _llm is None:
        _llm = DocReasonLlm()
    return _llm


def _agent_dir() -> Optional[Path]:
    for p in Path(__file__).resolve().parents:
        c = p / "src" / "agent"
        if c.is_dir() and (c / "provider_adapters.py").is_file():
            return c.resolve()
    env = (os.environ.get("SBA_AGENT_CONFIG") or "").strip()
    if env:
        ep = Path(env)
        if ep.is_file():
            return ep.parent.resolve()
        if ep.is_dir():
            return ep.resolve()
    return None


def _load_llm_cfg() -> dict:
    for cp in [
        Path(os.environ.get("SBA_AGENT_CONFIG", "").strip()) if os.environ.get("SBA_AGENT_CONFIG") else None,
        _agent_dir() / "config.json" if _agent_dir() else None,
    ]:
        if not cp or not cp.is_file():
            continue
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}
