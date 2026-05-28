"""权威 config.json 须指向含 api_gateway_nodes 的老项目 src/agent。"""
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


def test_resolve_agent_dir_prefers_old_project_config():
    os.environ["SBA_AGENT_CONFIG"] = str(_OLD_CFG)
    from app.services.config import agent_config_path, load_config, resolve_agent_dir

    cfg = load_config()
    assert len(cfg.get("api_gateway_nodes") or []) >= 1
    assert str(cfg.get("volcengine_api_key") or "").strip()
    assert agent_config_path().resolve() == _OLD_CFG
    assert "web_rebuild_v2" not in str(resolve_agent_dir()).lower() or _OLD_CFG.parent != resolve_agent_dir()
