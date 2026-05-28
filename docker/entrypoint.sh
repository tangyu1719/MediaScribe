#!/bin/sh
# 启动前：用服务器环境变量覆盖 runtime config.json（密钥不落镜像）
set -e

CONFIG="${SBA_AGENT_CONFIG:-/app/runtime/agent/config.json}"
TEMPLATE="${SBA_CONFIG_TEMPLATE:-/app/docker/config.template.json}"
mkdir -p "$(dirname "$CONFIG")"

python3 <<'PY'
import json
import os
from pathlib import Path

cfg_path = Path(os.environ.get("SBA_AGENT_CONFIG", "/app/runtime/agent/config.json"))
tpl_path = Path(os.environ.get("SBA_CONFIG_TEMPLATE", "/app/docker/config.template.json"))

data = {}
if cfg_path.is_file():
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
elif tpl_path.is_file():
    data = json.loads(tpl_path.read_text(encoding="utf-8"))

# 火山方舟密钥（compose: VOLC_API_KEY=${VOLC_API_KEY}）
volc = (os.environ.get("VOLC_API_KEY") or os.environ.get("VOLCENGINE_API_KEY") or "").strip()
if volc:
    data["volcengine_api_key"] = volc
    nodes = data.get("api_gateway_nodes")
    if isinstance(nodes, list):
        for n in nodes:
            if isinstance(n, dict) and not (n.get("api_key") or "").strip():
                n["api_key"] = volc

base = (os.environ.get("VOLC_BASE_URL") or os.environ.get("VOLCENGINE_BASE_URL") or "").strip()
if base:
    data["volcengine_base_url"] = base.rstrip("/")

qa = (os.environ.get("LLM_MODEL_QA") or os.environ.get("AI_CHAT_MODEL") or "").strip()
reason = (os.environ.get("LLM_MODEL_REASON") or os.environ.get("LLM_MODEL_SUMMARY") or "").strip()
if qa:
    data["ai_chat_model"] = qa
    route = data.setdefault("gateway_task_type_route", {})
    if isinstance(route, dict):
        route["qa"] = qa
if reason:
    route = data.setdefault("gateway_task_type_route", {})
    if isinstance(route, dict):
        route["summary"] = reason

cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
os.environ["SBA_AGENT_CONFIG"] = str(cfg_path)
print(f"[entrypoint] runtime config -> {cfg_path}; volc_key_set={bool(volc)}")
PY

export PYTHONPATH="/app/backend:/app/src/agent:${PYTHONPATH}"
exec "$@"
