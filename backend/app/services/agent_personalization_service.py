"""Agent 个性化：解析对话用 system 附加块 + HTTP 层服务函数。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .agent_personalization_models import (
    BUILTIN_LAYER_PRESETS,
    agent_id_from_template_key,
    layers_from_json,
    layers_from_legacy_profile,
    normalize_layers,
    render_layers_to_xml,
)
from .agent_personalization_template import AgentPromptTemplate
from . import agent_personalization_db as _db


def builtin_agent_ids() -> List[str]:
    return ["default", "doc", "ops"]


def resolve_layers_for_agent(
    agent_id: Optional[str],
    *,
    legacy_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """返回合并后的三层 JSON（用于渲染与可选落库）。"""
    aid = (agent_id or "default").strip().lower()
    if aid.startswith("c_"):
        tk = f"custom:{aid}"
        latest = _db.get_latest_revision(tk)
        if latest:
            return normalize_layers(layers_from_json(latest["layers_json"]))
        # 尚无 DB 记录：用代码默认并带上 display_name 占位
        base = normalize_layers({})
        base["layer0"]["display_name"] = base["layer0"].get("display_name") or "自定义 Agent"
        return base

    preset_key = aid if aid in BUILTIN_LAYER_PRESETS else "default"
    code_layers = normalize_layers(BUILTIN_LAYER_PRESETS[preset_key])
    tk = f"builtin:{preset_key}"
    latest = _db.get_latest_revision(tk)
    if latest:
        merged = normalize_layers(layers_from_json(latest["layers_json"]))
        # 保证内置身份不被完全清空：用代码预设填回空必填
        for lk in ("layer0", "layer1", "layer2"):
            for k, v in (code_layers.get(lk) or {}).items():
                cur = (merged.get(lk) or {}).get(k)
                if cur is None or (isinstance(cur, str) and not str(cur).strip()):
                    merged.setdefault(lk, {})
                    merged[lk][k] = v
        return merged

    if legacy_profile and isinstance(legacy_profile, dict) and any(
        legacy_profile.get(x) for x in ("name", "description", "boundaries", "tools_scope", "framework")
    ):
        return normalize_layers(layers_from_legacy_profile(legacy_profile))

    return code_layers


def build_system_prompt_extension(
    agent_id: Optional[str],
    *,
    legacy_profile: Optional[Dict[str, Any]] = None,
) -> str:
    layers = resolve_layers_for_agent(agent_id, legacy_profile=legacy_profile)
    tpl = AgentPromptTemplate.from_dict(layers)
    ok, _ = tpl.validate()
    if not ok:
        tpl = AgentPromptTemplate.builtin_preset("default")
    return tpl.to_xml()


def save_layers(template_key: str, layers: Dict[str, Any]) -> Dict[str, Any]:
    tpl = AgentPromptTemplate.from_dict(layers)
    ok, errs = tpl.validate()
    if not ok:
        return {"ok": False, "errors": errs}
    layers_n = tpl.layers
    rendered = tpl.to_xml()
    kind = "custom" if template_key.startswith("custom:") else "builtin"
    label = str((layers_n.get("layer0") or {}).get("display_name") or "").strip() or template_key
    ver = _db.insert_revision(template_key, tpl.to_json(), rendered, kind=kind, display_label=label)
    return {"ok": True, "version": ver, "rendered_system": rendered, "layers": layers_n}


def get_current_payload(template_key: str) -> Dict[str, Any]:
    aid = agent_id_from_template_key(template_key)
    preset = aid if aid in BUILTIN_LAYER_PRESETS else "default"
    code = (
        normalize_layers(BUILTIN_LAYER_PRESETS[preset])
        if template_key.startswith("builtin:")
        else normalize_layers({})
    )

    latest = _db.get_latest_revision(template_key)
    if latest:
        merged = normalize_layers(layers_from_json(latest["layers_json"]))
        for lk in ("layer0", "layer1", "layer2"):
            for k, v in (code.get(lk) or {}).items():
                cur = (merged.get(lk) or {}).get(k)
                if cur is None or (isinstance(cur, str) and not str(cur).strip()):
                    merged.setdefault(lk, {})
                    merged[lk][k] = v
        layers = merged
        version = latest["version"]
        rendered = latest["rendered_system"] or render_layers_to_xml(layers)
    else:
        if template_key.startswith("builtin:"):
            layers = normalize_layers(BUILTIN_LAYER_PRESETS[preset])
        else:
            layers = resolve_layers_for_agent(aid)
        version = 0
        rendered = render_layers_to_xml(layers)

    return {
        "template_key": template_key,
        "version": version,
        "layers": layers,
        "rendered_system": rendered,
    }


def catalog(*, user_facing: bool = True) -> Dict[str, Any]:
    """返回全部内置模板（default / doc / ops）。"""
    builtins = []
    ids = builtin_agent_ids()
    for bid in ids:
        tk = f"builtin:{bid}"
        latest = _db.get_latest_revision(tk)
        preset = normalize_layers(BUILTIN_LAYER_PRESETS[bid if bid in BUILTIN_LAYER_PRESETS else "default"])
        builtins.append(
            {
                "template_key": tk,
                "agent_id": bid,
                "label": preset["layer0"].get("display_name") or bid,
                "version": latest["version"] if latest else 0,
            }
        )
    customs = []
    for row in _db.list_active_custom_keys():
        aid = agent_id_from_template_key(row["template_key"])
        customs.append(
            {
                "template_key": row["template_key"],
                "agent_id": aid,
                "label": row.get("label") or aid,
                "version": row.get("version") or 0,
            }
        )
    return {"builtins": builtins, "customs": customs, "db": _db.db_status()}


def create_custom_template(layers: Dict[str, Any]) -> Dict[str, Any]:
    import uuid

    aid = "c_" + uuid.uuid4().hex[:10]
    tk = f"custom:{aid}"
    r = save_layers(tk, layers)
    if not r.get("ok"):
        return r
    return {"ok": True, "agent_id": aid, "template_key": tk, "version": r.get("version")}
