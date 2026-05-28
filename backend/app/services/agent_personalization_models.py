"""Agent 个性化：分层 Prompt 模型（代码内默认值 + XML 渲染）。

分层与必填（*）字段在 validate_layers / normalize_layers 中统一约束。
XML 根块使用：<context> <document> <example> <instructions>（与产品约定一致）。
"""
from __future__ import annotations

import html
import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

# ── 代码内默认：内置三类 + 通用字段结构 ─────────────────────────────

DEFAULT_LAYER0 = {
    "display_name": "这鱼",
    "reply_style": "专业、清晰，贴合用户所在领域（技术、设计、产品、办公等）",
    "user_relationship": "多领域协作助手，以 user.md 中的用户背景为准",
}

DEFAULT_LAYER1_BASE = {
    "role": "通用任务助手，按用户领域理解问题并给出可执行方案",
    "task_requirements": "先澄清目标与约束，再分步回答；需要工具时说明用途与预期结果",
    "action_content": "理解问题 → 必要时调用工具或检索 → 结构化输出结论与下一步建议",
    "execution_framework": "COT",
    "tools_scope": "内置 Tool Call、已配置 MCP、SKILL 命令（若可用）",
}

DEFAULT_LAYER2_BASE = {
    "standards_must": "禁止编造数据；region 等标识使用连字符格式；连续 3 次失败必须停止并说明原因",
    "output_template": "执行阶段：结构化 JSON；完成阶段：Markdown 报告（含标题、步骤、结论）",
    "few_shots": "告警清单→根因分析→处理方案→结论",
    "no_doing": "禁止用文档之外的背景知识进行回答；不得泄露密钥与内部路径",
}

BUILTIN_LAYER_PRESETS: Dict[str, Dict[str, Any]] = {
    "default": {
        "layer0": deepcopy(DEFAULT_LAYER0),
        "layer1": {
            **deepcopy(DEFAULT_LAYER1_BASE),
        },
        "layer2": deepcopy(DEFAULT_LAYER2_BASE),
    },
    "doc": {
        "layer0": {
            "display_name": "文档助手",
            "reply_style": "结构化输出、引用与改写规范、列表与标题层级清晰",
            "user_relationship": "文档整理与多模态内容结构化专家",
        },
        "layer1": {
            **deepcopy(DEFAULT_LAYER1_BASE),
            "role": "文档分析与结构化输出专家（Planner+Replanner）",
            "task_requirements": "阅读用户材料，抽取要点，生成可交付的 Markdown/要点列表",
            "tools_scope": "文档解析、RAG（若挂载）、格式化输出",
        },
        "layer2": {
            **deepcopy(DEFAULT_LAYER2_BASE),
            "no_doing": DEFAULT_LAYER2_BASE["no_doing"] + "；不得凭空捏造原文中不存在的引文页码",
        },
    },
    "ops": {
        "layer0": {
            "display_name": "运维助手",
            "reply_style": "可执行的排障步骤、风险分级与回滚提示",
            "user_relationship": "线上稳定性与可观测性协作伙伴",
        },
        "layer1": {
            **deepcopy(DEFAULT_LAYER1_BASE),
            "role": "运维排障编排（Planner+Replanner）",
            "task_requirements": "基于日志/指标/告警定位根因并给出可验证的下一步",
            "tools_scope": "日志与指标解读、变更窗口建议、脚本级操作说明（不直接代执行高危命令）",
        },
        "layer2": {
            **deepcopy(DEFAULT_LAYER2_BASE),
            "standards_must": DEFAULT_LAYER2_BASE["standards_must"] + "；高危操作须显式风险提示",
        },
    },
}


def builtin_template_key(agent_id: str) -> str:
    aid = (agent_id or "default").strip().lower()
    if aid.startswith("c_"):
        return f"custom:{aid}"
    return f"builtin:{aid if aid in ('doc', 'ops', 'default') else 'default'}"


def agent_id_from_template_key(template_key: str) -> str:
    tk = (template_key or "").strip()
    if tk.startswith("custom:"):
        return tk.split(":", 1)[1]
    if tk.startswith("builtin:"):
        return tk.split(":", 1)[1]
    return "default"


def normalize_layers(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """合并缺省字段；不覆盖已有非空字符串。"""
    base = deepcopy(BUILTIN_LAYER_PRESETS["default"])
    if not raw or not isinstance(raw, dict):
        return base
    for lk in ("layer0", "layer1", "layer2"):
        if isinstance(raw.get(lk), dict):
            base.setdefault(lk, {})
            for k, v in raw[lk].items():
                if v is None:
                    continue
                if isinstance(v, str) and not v.strip() and k in (base.get(lk) or {}):
                    continue
                base[lk][k] = v
    return base


def validate_layers(layers: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    l0 = layers.get("layer0") or {}
    l1 = layers.get("layer1") or {}
    l2 = layers.get("layer2") or {}
    if not str(l0.get("display_name") or "").strip():
        errs.append("layer0.display_name * 不能为空")
    if not str(l1.get("role") or "").strip():
        errs.append("layer1.role * 不能为空")
    if not str(l1.get("execution_framework") or "").strip():
        errs.append("layer1.execution_framework * 不能为空")
    return (len(errs) == 0, errs)


def render_layers_to_xml(layers: Dict[str, Any]) -> str:
    """将三层配置渲染为单一 system 附加块（自定义 XML 标签）。"""
    l0 = layers.get("layer0") or {}
    l1 = layers.get("layer1") or {}
    l2 = layers.get("layer2") or {}

    def esc(x: Any) -> str:
        return html.escape(str(x or ""), quote=False)

    ctx = "\n".join(
        [
            f"<display_name>{esc(l0.get('display_name'))}</display_name>",
            f"<reply_style>{esc(l0.get('reply_style'))}</reply_style>",
            f"<user_relationship>{esc(l0.get('user_relationship'))}</user_relationship>",
        ]
    )
    doc = "\n".join(
        [
            f"<role>{esc(l1.get('role'))}</role>",
            f"<task_requirements>{esc(l1.get('task_requirements'))}</task_requirements>",
            f"<action_content>{esc(l1.get('action_content'))}</action_content>",
            f"<execution_framework>{esc(l1.get('execution_framework'))}</execution_framework>",
            f"<tools_scope>{esc(l1.get('tools_scope'))}</tools_scope>",
        ]
    )
    inst = "\n".join(
        [
            f"<must_do>{esc(l2.get('standards_must'))}</must_do>",
            f"<output_format>{esc(l2.get('output_template'))}</output_format>",
            f"<no_doing>{esc(l2.get('no_doing'))}</no_doing>",
        ]
    )
    ex = f"<few_shots>{esc(l2.get('few_shots'))}</few_shots>"

    return (
        "<agent_personalization>\n"
        f"<context>\n{ctx}\n</context>\n"
        f"<document>\n{doc}\n</document>\n"
        f"<example>\n{ex}\n</example>\n"
        f"<instructions>\n{inst}\n</instructions>\n"
        "</agent_personalization>"
    )


def layers_from_legacy_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """将旧版扁平 agent_profile 映射到分层结构（仅作兼容）。"""
    layers = normalize_layers({})
    l0 = layers["layer0"]
    l1 = layers["layer1"]
    l2 = layers["layer2"]
    nm = str(profile.get("name") or "").strip()
    if nm:
        l0["display_name"] = nm
    desc = str(profile.get("description") or "").strip()
    if desc:
        l1["task_requirements"] = desc
    fw = str(profile.get("framework") or "").strip()
    if fw:
        l1["execution_framework"] = fw.upper() if fw.lower() in ("cot", "react") else fw
    tools = str(profile.get("tools_scope") or "").strip()
    if tools:
        l1["tools_scope"] = tools
    bounds = str(profile.get("boundaries") or "").strip()
    if bounds:
        l2["no_doing"] = (l2.get("no_doing") or "") + "\n（用户自定义边界）" + bounds
    return layers


def layers_to_json(layers: Dict[str, Any]) -> str:
    return json.dumps(layers, ensure_ascii=False, indent=2)


def layers_from_json(s: str) -> Dict[str, Any]:
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return normalize_layers(o)
    except Exception:
        pass
    return normalize_layers({})
