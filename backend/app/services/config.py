"""配置管理服务 —— 直读/写 src/agent/config.json，与原版 video_gui.py 完全一致"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List

_HERE = Path(__file__).resolve()


def resolve_agent_dir() -> Path:
    """
    解析权威 Agent 目录（含 config.json / link_analyzer / ai_gateway）。

    优先级：
    1. 环境变量 SBA_AGENT_CONFIG（文件或目录）
    2. 含完整 LLM 配置的 src/agent（老项目 SuperBizAgent-AgentFramework/src/agent）
    3. 任意 src/agent 目录（如 web_rebuild_v2/src/agent 仅放 history/pipeline 运行时数据）
    """
    env = (os.environ.get("SBA_AGENT_CONFIG") or os.environ.get("SBA_CONFIG_PATH") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p.parent.resolve()
        if p.is_dir():
            return p.resolve()

    candidates: List[Path] = []
    seen: set = set()
    for parent in _HERE.parents:
        cand = (parent / "src" / "agent").resolve()
        key = str(cand).lower()
        if cand.is_dir() and key not in seen:
            seen.add(key)
            candidates.append(cand)

    def _score(agent_dir: Path) -> int:
        cp = agent_dir / "config.json"
        if not cp.is_file():
            return 0
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            return 1
        score = 2
        if str(data.get("volcengine_api_key") or "").strip():
            score += 10
        nodes = data.get("api_gateway_nodes")
        if isinstance(nodes, list) and nodes:
            score += 20
        if str(data.get("ai_chat_model") or "").strip():
            score += 5
        return score

    if candidates:
        return max(candidates, key=_score)
    return (_HERE.parents[2] / "src" / "agent").resolve()


_AGENT_DIR = resolve_agent_dir()
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

_CONFIG_PATH = _AGENT_DIR / "config.json"

# 运行时产物（history / pipeline_logs）仍落在 web 工程侧 src/agent
_RUNTIME_AGENT_DIR = None
for _p in _HERE.parents:
    _wr = (_p / "src" / "agent").resolve()
    if _wr.is_dir() and "web_rebuild_v2" in str(_wr).lower():
        _RUNTIME_AGENT_DIR = _wr
        break
if _RUNTIME_AGENT_DIR is None:
    _RUNTIME_AGENT_DIR = _AGENT_DIR


def load_config() -> Dict:
    """加载 config.json，与 video_gui.py load_config() 一致"""
    if not _CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def agent_config_path() -> Path:
    """权威 config.json 路径（供诊断日志）。"""
    return _CONFIG_PATH


def runtime_agent_dir() -> Path:
    """流水线 history.json / pipeline_logs 等运行时目录。"""
    return _RUNTIME_AGENT_DIR or _AGENT_DIR


def load_pipeline_config() -> Dict:
    """链接沉淀流水线配置：与「内部 Agent 配置 / IAG」同源，只读 src/agent/config.json。"""
    return load_config()


def save_config(cfg: Dict) -> Dict:
    """保存 config.json，与 video_gui.py save_config() 一致"""
    _CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ─── 网关节点 CRUD ───

def get_gateway_nodes() -> List[Dict]:
    cfg = load_config()
    return cfg.get("api_gateway_nodes", [])


def upsert_gateway_node(node: Dict) -> Dict:
    cfg = load_config()
    nodes = list(cfg.get("api_gateway_nodes", []))
    nid = node.get("id", "").strip()
    found = False
    for i, n in enumerate(nodes):
        if n.get("id") == nid:
            nodes[i] = node
            found = True
            break
    if not found:
        nodes.append(node)
    nodes = sorted(nodes, key=lambda x: int(x.get("priority", 9999)))
    cfg["api_gateway_nodes"] = nodes
    save_config(cfg)
    return {"ok": True, "nodes": nodes}


def delete_gateway_node(node_id: str) -> Dict:
    cfg = load_config()
    nodes = [n for n in cfg.get("api_gateway_nodes", []) if n.get("id") != node_id]
    cfg["api_gateway_nodes"] = nodes
    save_config(cfg)
    return {"ok": True}


# ─── Agent 路由 ───

def get_agent_routing() -> Dict:
    cfg = load_config()
    return cfg.get("agent_route_rules", {})


def save_agent_routing(rules: Dict) -> Dict:
    cfg = load_config()
    cfg["agent_route_rules"] = rules
    save_config(cfg)
    return {"ok": True}


# ─── Agent 提示词 / 输出控制（按内部 agent_key 白名单读写 config.json）───

_AGENT_PROMPT_FIELDS: Dict[str, List[str]] = {
    "doc_standardize_agent": ["article_polish_prompt", "article_system_prompt", "article_rules"],
    "summary_agent": [
        "summary_prompt",
        "system_prompt",
        "rules",
        "output_template",
        "file_naming_rule",
    ],
    "qa_orchestrator_agent": [
        "ai_chat_system_prompt",
        "framework_business_layer",
        "framework_rules_layer",
        "framework_constraints_layer",
        "framework_response_format_layer",
        "framework_optimization_layer",
    ],
    "ops_agent": ["ops_system_prompt"],
    "longpage_html_assembler_agent": [
        "longpage_html_enabled",
        "longpage_html_max_bytes",
        "longpage_html_timeout_sec",
        "longpage_html_async_diagram_pipeline",
        "longpage_html_async_timeout_sec",
        "longpage_multiphase_enabled",
        "longpage_s3_html_assembler_enabled",
        "longpage_s3_html_assembler_timeout_sec",
        "longpage_diagram_max_parallel",
        "longpage_diagram_plan_max_chars",
        "longpage_diagram_draw_context_max_chars",
        "longpage_diagram_extraction_timeout",
        "longpage_diagram_draw_timeout",
    ],
    "longpage_diagram_legend_agent": [
        "longpage_legend_agent_enabled",
        "longpage_legend_llm_required",
        "longpage_legend_agent_timeout_sec",
        "longpage_legend_agent_context_max_chars",
        "longpage_analysis_inline_diagrams_enabled",
        "diagram_style_mermaid_json",
        "diagram_style_legend_suite_json",
        "diagram_style_diag_slot_json",
        "diagram_style_er_json",
        "diagram_style_tool_flow_pill_json",
    ],
}


def get_agent_prompt(agent_key: str) -> Dict:
    cfg = load_config()
    keys = _AGENT_PROMPT_FIELDS.get(agent_key, [])
    result: Dict[str, Any] = {}
    for fk in keys:
        if fk not in cfg:
            result[fk] = ""
            continue
        result[fk] = cfg.get(fk)
    return {"agent_key": agent_key, "fields": result}


def save_agent_prompt(agent_key: str, fields: Dict) -> Dict:
    cfg = load_config()
    allowed = set(_AGENT_PROMPT_FIELDS.get(agent_key, []))
    for k, v in (fields or {}).items():
        if k in allowed:
            cfg[k] = v
    save_config(cfg)
    return {"ok": True}


# ─── Agent Markdown（AGENT.md 或指定规范文档）───

# (agents 子目录名, 文件名)
_AGENT_MD_FILES: Dict[str, tuple[str, str]] = {
    "doc_standardize_agent": ("doc_standardize", "AGENT.md"),
    "summary_agent": ("summary", "AGENT.md"),
    "qa_orchestrator_agent": ("qa_orchestrator", "AGENT.md"),
    "ops_agent": ("ops", "AGENT.md"),
    "longpage_html_assembler_agent": ("summary", "AGENT_LONGPAGE_HTML_ASSEMBLER_V1.md"),
    "longpage_diagram_legend_agent": ("summary", "AGENT_DIAGRAM_ORCHESTRATION.md"),
    "skill_usage_flow_agent": ("summary", "AGENT_SKILL_USAGE_FLOW.md"),
}


def get_agent_md(agent_key: str) -> Dict:
    spec = _AGENT_MD_FILES.get(agent_key)
    if not spec:
        md_path = _AGENT_DIR / "agents" / agent_key / "AGENT.md"
    else:
        sub, name = spec
        md_path = _AGENT_DIR / "agents" / sub / name
    content = md_path.read_text(encoding="utf-8", errors="ignore") if md_path.exists() else ""
    return {"agent_key": agent_key, "content": content, "path": str(md_path)}


def save_agent_md(agent_key: str, content: str) -> Dict:
    spec = _AGENT_MD_FILES.get(agent_key)
    if not spec:
        md_path = _AGENT_DIR / "agents" / agent_key / "AGENT.md"
    else:
        sub, name = spec
        md_path = _AGENT_DIR / "agents" / sub / name
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(content or "", encoding="utf-8")
    return {"ok": True, "path": str(md_path)}
