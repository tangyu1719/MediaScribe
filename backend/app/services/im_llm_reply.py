"""IM 场景短回复 —— 复用问答链路 LLM 配置。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def generate_im_short_reply(user_text: str, *, scene: str = "feishu") -> str:
    """同步生成适合 IM 窗口的短回复。"""
    user_text = (user_text or "").strip()
    if not user_text:
        return ""
    try:
        here = Path(__file__).resolve()
        for p in here.parents:
            cand = p / "src" / "agent"
            if cand.is_dir() and str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
        from provider_adapters import invoke_unified

        from .ai_chat import load_chat_llm_config, resolve_chat_api_credentials

        cfg = load_chat_llm_config()
        creds = resolve_chat_api_credentials(cfg)
        provider = creds.get("provider") or "ark"
        api_key = creds.get("api_key") or ""
        base_url = creds.get("base_url") or ""
        model = creds.get("model") or ""
        if not api_key or not model:
            return "抱歉，AI 网关未配置，请先在设置中配置 LLM。"

        scene_hint = "飞书群聊" if scene == "feishu" else "IM 群聊"
        system = (
            f"你是 {scene_hint} 助手，回复须简洁、可直接在聊天窗口阅读。"
            "优先给出可执行结论，避免冗长 Markdown 标题。"
            "不要编造未实际调用过的工具结果。"
        )
        result = invoke_unified(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            temperature=0.3,
            max_tokens=600,
            timeout=90.0,
            thinking_enabled=False,
        )
        text = (result or "").strip()
        if not text:
            return "我收到了，但这次没有生成出有效回复。你可以再发一次，或者把问题说得更具体一点。"
        return text[:1800]
    except Exception as exc:
        return f"抱歉，AI 暂时无法回复（{str(exc)[:80]}），请稍后再试。"
