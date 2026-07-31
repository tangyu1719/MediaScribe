"""问答凭据解析不能泄露密钥，LLM_MODEL_QA 可显式切换主测模型。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_chat import resolve_chat_api_credentials


def test_resolve_credentials_from_ark_environment(monkeypatch):
    monkeypatch.setenv("VOLC_API_KEY", "test-secret-not-for-logs")
    monkeypatch.setenv("LLM_MODEL_QA", "ep-deepseek-v4-pro")
    monkeypatch.setenv("ARK_BASE_URL", "https://example.invalid/api/v3")
    creds = resolve_chat_api_credentials({})
    assert creds == {
        "provider": "ark",
        "api_key": "test-secret-not-for-logs",
        "base_url": "https://example.invalid/api/v3",
        "model": "ep-deepseek-v4-pro",
    }


def test_explicit_key_and_url_win_but_llm_model_qa_overrides_model(monkeypatch):
    monkeypatch.setenv("VOLC_API_KEY", "env-secret")
    monkeypatch.setenv("LLM_MODEL_QA", "env-model")
    creds = resolve_chat_api_credentials(
        {
            "volcengine_api_key": "config-secret",
            "ai_chat_model": "config-model",
            "volcengine_base_url": "https://config.invalid/api/v3",
        }
    )
    assert creds["api_key"] == "config-secret"
    assert creds["model"] == "env-model"
    assert creds["base_url"] == "https://config.invalid/api/v3"
