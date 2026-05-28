"""工具详情页：调用项目已配置的网关模型生成补充说明 HTML（与 AI 问答同源 adapter）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

_BASE_DIR = Path(__file__).resolve().parents[2]
_AGENT_DIR = (_BASE_DIR.parent / "src" / "agent").resolve()
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


def _load_llm_cfg() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    for cp in [_BASE_DIR / "config.json", _AGENT_DIR / "config.json"]:
        if cp.exists():
            try:
                cfg = json.loads(cp.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    return cfg


def generate_tool_detail_html(*, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """返回 {ok, html?, error?}。html 为可直接 v-html 的安全子集（由模型生成，前端仍建议仅作展示）。"""
    cfg = _load_llm_cfg()
    api_key = (cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
    base_url = (cfg.get("volcengine_base_url") or cfg.get("openai_base_url") or "https://ark.cn-beijing.volces.com/api/v3").strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    model = (cfg.get("ai_chat_model") or "").strip()
    if not api_key or not model:
        return {
            "ok": False,
            "error": "未配置网关：请在 src/agent/config.json 中填写 volcengine_api_key（或 openai_api_key）与 ai_chat_model。",
        }

    system = (
        "你是企业内文档助手。根据用户给出的工具元数据（JSON），用中文生成一段简洁的 HTML 片段，"
        "只使用以下标签：section、h4、p、ul、li、code、pre、strong。"
        "结构必须包含：<section><h4>说明</h4>...</section>"
        "<section><h4>输入要点</h4>...</section><section><h4>输出要点</h4>...</section>。"
        "不要写 script、不要外链、不要编造该工具不具备的能力。"
        "总字数控制在 600 字以内。"
    )
    user = f"类型: {kind}\n元数据:\n{json.dumps(payload, ensure_ascii=False, default=str)[:12000]}"

    try:
        from provider_adapters import invoke_chat_completion_raw, _extract_openai_message_dict
    except ImportError as e:
        return {"ok": False, "error": f"无法加载 provider_adapters: {e}"}

    try:
        data = invoke_chat_completion_raw(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=900,
            timeout=90.0,
            thinking_enabled=False,
            tools=None,
        )
        msg = _extract_openai_message_dict(data)
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        return {"ok": True, "html": content.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
