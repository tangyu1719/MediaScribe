"""用户个人画像：结构化字段 + 生成标准 user.md（与 agent 侧 XML 分离，对话时一并注入）。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple

_HERE = Path(__file__).resolve()
_BASE = _HERE.parents[1]  # backend/app
_OUTPUT = _BASE.parent / "output" / "user_profiles"

# 允许写入的字段（其余忽略）
PORTRAIT_KEYS = (
    "display_name",
    "timezone",
    "occupation",
    "tech_stack",
    "communication_style",
    "interests_projects",
    "notes",
    "language_pref",
)


def _safe_user_dir(user_id: str) -> Path:
    uid = re.sub(r"[^a-zA-Z0-9._-]", "_", (user_id or "").strip())[:80]
    if not uid:
        uid = "unknown"
    return _OUTPUT / uid


def portrait_json_path(user_id: str) -> Path:
    return _safe_user_dir(user_id) / "portrait.json"


def user_md_path(user_id: str) -> Path:
    return _safe_user_dir(user_id) / "user.md"


def default_portrait() -> Dict[str, str]:
    return {k: "" for k in PORTRAIT_KEYS}


def normalize_portrait(raw: Any) -> Dict[str, str]:
    out = default_portrait()
    if isinstance(raw, dict):
        for k in PORTRAIT_KEYS:
            v = raw.get(k)
            out[k] = str(v).strip() if v is not None else ""
    return out


def build_user_md(fields: Dict[str, Any]) -> str:
    """生成供模型读取的标准 Markdown（含 YAML 头）。"""
    f = normalize_portrait(fields)
    lines = [
        "---",
        "document: user-portrait",
        "format: superbizagent-user-v1",
        "role: end-user-profile",
        "---",
        "",
        "# 用户画像（user.md）",
        "",
        "以下信息由用户在「个人信息 → 个人画像」中维护，供对话模型理解终端用户背景；",
        "与 **agent.md**（Agent 个性化 / 系统约束）分文件加载，但处于同一轮对话的 system 上下文中。",
        "",
        "## 基本信息",
    ]

    def add_line(label: str, key: str):
        val = (f.get(key) or "").strip()
        if val:
            lines.append(f"- **{label}**：{val}")

    add_line("称呼 / 姓名", "display_name")
    add_line("时区", "timezone")
    add_line("职业 / 角色", "occupation")
    add_line("技术栈", "tech_stack")
    add_line("沟通偏好", "communication_style")
    add_line("回应语言偏好", "language_pref")

    ip = (f.get("interests_projects") or "").strip()
    if ip:
        lines.extend(["", "## 关心的话题与在做的项目", "", ip, ""])

    notes = (f.get("notes") or "").strip()
    if notes:
        lines.extend(["", "## 备注与补充", "", notes, ""])

    # 若几乎全空，仍保留可解析骨架，避免空文件
    if len(lines) < 18:
        lines.extend(
            [
                "",
                "_（尚未填写详细画像；模型可依据昵称与对话内容推断，勿编造事实。）_",
                "",
            ]
        )

    lines.append("<!-- end user.md -->")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def load_portrait(user_id: str) -> Dict[str, str]:
    p = portrait_json_path(user_id)
    if not p.exists():
        return default_portrait()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return normalize_portrait(data)
    except Exception:
        return default_portrait()


def load_user_md_text(user_id: str) -> str:
    p = user_md_path(user_id)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_portrait(user_id: str, fields: Dict[str, Any]) -> Tuple[Dict[str, str], str]:
    merged = normalize_portrait({**load_portrait(user_id), **(fields or {})})
    md = build_user_md(merged)
    d = _safe_user_dir(user_id)
    d.mkdir(parents=True, exist_ok=True)
    portrait_json_path(user_id).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    user_md_path(user_id).write_text(md, encoding="utf-8")
    return merged, md
