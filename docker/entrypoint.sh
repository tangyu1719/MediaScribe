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
import re
import yaml

cfg_path = Path(os.environ.get("SBA_AGENT_CONFIG", "/app/runtime/agent/config.json"))
tpl_path = Path(os.environ.get("SBA_CONFIG_TEMPLATE", "/app/docker/config.template.json"))
yaml_path = Path(os.environ.get("SBA_CONFIG_YAML", "/app/config.yaml"))

data = {}
if cfg_path.is_file():
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
elif tpl_path.is_file():
    data = json.loads(tpl_path.read_text(encoding="utf-8"))

def expand_env(value):
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        return "" if re.fullmatch(r"\$\{[A-Z0-9_]+\}", expanded) else expanded
    return value

def deep_merge(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value

if yaml_path.is_file():
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    agent = expand_env(raw.get("agent") or {})
    if isinstance(agent, dict):
        deep_merge(data, agent)

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

redis_url = (os.environ.get("REDIS_URL") or "").strip()
if redis_url:
    data["redis_cache_enabled"] = True
    data["redis_url"] = redis_url

feishu_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
feishu_secret = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
if feishu_id:
    data["feishu_app_id"] = feishu_id
if feishu_secret:
    data["feishu_app_secret"] = feishu_secret

cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
os.environ["SBA_AGENT_CONFIG"] = str(cfg_path)
print(f"[entrypoint] runtime config -> {cfg_path}; volc_key_set={bool(volc)}")
PY

export PYTHONPATH="/app/backend:/app/src/agent:${PYTHONPATH}"
exec "$@"
