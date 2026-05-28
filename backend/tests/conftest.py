"""pytest 公共 fixture。"""
from __future__ import annotations
import os
import sys
from pathlib import Path
import pytest

# LangGraph HITL 在回归测试中自动确认，避免 graph_interrupt 截断 SSE 序列
os.environ.setdefault("CHAT_GRAPH_AUTO_HITL", "1")
from fastapi.testclient import TestClient
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.main import app
client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def _seed_auth_policies():
    """确保 Casbin 默认策略存在（pytest 独立进程可能未走完整 startup）。"""
    try:
        from app.auth.init_admin import ensure_auth_ready

        ensure_auth_ready()
    except Exception:
        pass
    yield


@pytest.fixture
def api_client():
    return client


@pytest.fixture(scope="session")
def admin_token():
    r = client.post("/api/auth/login", json={"identifier": "admin", "credential": "admin", "login_type": "password"})
    assert r.status_code == 200, r.text
    t = r.json().get("access_token")
    assert t
    return t
@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}
