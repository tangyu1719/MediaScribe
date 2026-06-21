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


def list_chat_model_options() -> Dict[str, Any]:
    """AI 问答页模型下拉：来自 api_gateway_nodes 活跃节点，不含密钥。"""
    cfg = load_config()
    nodes = cfg.get("api_gateway_nodes") or []
    route_map = cfg.get("gateway_task_type_route") if isinstance(cfg.get("gateway_task_type_route"), dict) else {}
    default_ep = str(
        route_map.get("qa") or route_map.get("chat") or cfg.get("ai_chat_model") or ""
    ).strip()

    auto_resolved = ""
    auto_label = "auto（节点池）"
    try:
        from .pipeline_logging import resolve_gateway_models

        routes = resolve_gateway_models(cfg, agent_name="ai_chat", task_type="chat")
        auto_resolved = str(
            routes.get("primary_endpoint") or routes.get("gateway_chosen") or ""
        ).strip()
        if auto_resolved:
            auto_label = f"auto（节点池 → {auto_resolved}）"
    except Exception:
        pass

    options: List[Dict[str, Any]] = [{"id": "", "label": auto_label, "auto": True}]
    seen: set = set()

    for n in nodes:
        if not isinstance(n, dict):
            continue
        status = str(n.get("status") or "active").strip().lower()
        if status not in ("active", ""):
            continue
        ep = str(n.get("endpoint_id") or n.get("model") or "").strip()
        if not ep or ep in seen:
            continue
        seen.add(ep)
        name = str(n.get("name") or n.get("id") or ep).strip()
        model_name = str(n.get("model") or "").strip()
        provider = str(n.get("provider") or cfg.get("gateway_provider") or "ark").strip()
        label_parts = [name]
        if model_name and model_name.lower() != name.lower():
            label_parts.append(model_name)
        label = " · ".join(label_parts) + f"（{provider}）"
        if ep == default_ep:
            label += " · 默认"
        options.append(
            {
                "id": ep,
                "label": label,
                "endpoint_id": ep,
                "node_id": str(n.get("id") or "").strip(),
                "provider": provider,
                "name": name,
                "model": model_name,
                "default": ep == default_ep,
            }
        )

    return {
        "ok": True,
        "models": options,
        "default_id": default_ep,
        "auto_resolved": auto_resolved,
        "count": max(0, len(options) - 1),
    }


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


def get_link_pipeline_prefs() -> Dict:
    """链接文档化页：飞书/HTML 等快捷开关（读写 config.json）。"""
    cfg = load_config()
    return {
        "feishu_sync_enabled": bool(cfg.get("feishu_sync_enabled")),
        "feishu_default_folder_path": str(cfg.get("feishu_default_folder_path") or ""),
        "longpage_html_enabled": bool(cfg.get("longpage_html_enabled", True)),
    }


def save_link_pipeline_prefs(prefs: Dict) -> Dict:
    cfg = load_config()
    if "feishu_sync_enabled" in prefs:
        cfg["feishu_sync_enabled"] = bool(prefs.get("feishu_sync_enabled"))
    if "feishu_default_folder_path" in prefs:
        cfg["feishu_default_folder_path"] = str(prefs.get("feishu_default_folder_path") or "").strip()
    if "longpage_html_enabled" in prefs:
        cfg["longpage_html_enabled"] = bool(prefs.get("longpage_html_enabled"))
    save_config(cfg)
    return {"ok": True, **get_link_pipeline_prefs()}


# ─── Agent 提示词 / 输出控制（按内部 agent_key 白名单读写 config.json）───

_AGENT_PROMPT_FIELDS: Dict[str, List[str]] = {
    "doc_standardize_agent": ["article_polish_prompt", "article_system_prompt", "article_rules"],
    "summary_agent": [
        "summary_prompt",
        "system_prompt",
        "rules",
        "output_template",
        "file_naming_rule",
        "meta_extract_enabled",
        "meta_extract_fields",
        "meta_extract_prompt",
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
    "reader_agent": [
        "reader_system_prompt",
        "reader_role_task",
        "reader_action_framework",
        "reader_standards_must",
        "reader_output_template",
        "reader_no_doing",
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
    # reader_agent：config 空段从 AGENT.md 章节补齐（模块化向量源）
    if agent_key == "reader_agent":
        try:
            from .agent_md_sections import merge_reader_fields_from_md

            md_row = get_agent_md(agent_key)
            result = merge_reader_fields_from_md(result, md_row.get("content") or "")
        except Exception:
            pass
    return {"agent_key": agent_key, "fields": result}


def save_agent_prompt(agent_key: str, fields: Dict) -> Dict:
    cfg = load_config()
    allowed = set(_AGENT_PROMPT_FIELDS.get(agent_key, []))
    for k, v in (fields or {}).items():
        if k in allowed:
            cfg[k] = v
    save_config(cfg)
    # reader_agent：保存字段后回写 AGENT.md 对应章节
    if agent_key == "reader_agent":
        try:
            from .agent_md_sections import sync_reader_agent_md_from_fields

            md_row = get_agent_md(agent_key)
            new_md = sync_reader_agent_md_from_fields(md_row.get("content") or "", fields or {})
            save_agent_md(agent_key, new_md)
        except Exception:
            pass
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
    "reader_agent": ("reader", "AGENT.md"),
}


def _reader_agent_md_path() -> Path:
    """web_rebuild_v2 本地 reader Agent 规范文档。"""
    return (_HERE.parents[2] / "agents" / "reader" / "AGENT.md").resolve()


def get_agent_md(agent_key: str) -> Dict:
    if agent_key == "reader_agent":
        md_path = _reader_agent_md_path()
    else:
        spec = _AGENT_MD_FILES.get(agent_key)
        if not spec:
            md_path = _AGENT_DIR / "agents" / agent_key / "AGENT.md"
        else:
            sub, name = spec
            md_path = _AGENT_DIR / "agents" / sub / name
    content = md_path.read_text(encoding="utf-8", errors="ignore") if md_path.exists() else ""
    return {"agent_key": agent_key, "content": content, "path": str(md_path)}


def save_agent_md(agent_key: str, content: str) -> Dict:
    if agent_key == "reader_agent":
        md_path = _reader_agent_md_path()
    else:
        spec = _AGENT_MD_FILES.get(agent_key)
        if not spec:
            md_path = _AGENT_DIR / "agents" / agent_key / "AGENT.md"
        else:
            sub, name = spec
            md_path = _AGENT_DIR / "agents" / sub / name
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(content or "", encoding="utf-8")
    return {"ok": True, "path": str(md_path)}
