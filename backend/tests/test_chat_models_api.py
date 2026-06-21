"""AI 问答页模型列表须来自 api_gateway_nodes，禁止硬编码占位模型。"""
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_OLD_CFG = (
    _BACKEND.parents[1]
    / "src"
    / "agent"
    / "config.json"
).resolve()


def test_list_chat_model_options_from_gateway_nodes():
    os.environ["SBA_AGENT_CONFIG"] = str(_OLD_CFG)
    from app.services.config import list_chat_model_options

    data = list_chat_model_options()
    assert data.get("ok") is True
    models = data.get("models") or []
    assert models and models[0].get("auto") is True
    assert data.get("count", 0) >= 1
    ids = {m.get("id") for m in models if not m.get("auto")}
    assert "hunyuan" not in ids
    assert "gpt-4o" not in ids
    for m in models:
        if m.get("auto"):
            continue
        assert m.get("endpoint_id")
        assert m.get("label")
        assert "api_key" not in m
