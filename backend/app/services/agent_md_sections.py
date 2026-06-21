"""从 AGENT.md 按章节截取模块化变量，与 config.json 字段双向同步。"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

READER_FIELD_KEYS = [
    "reader_system_prompt",
    "reader_role_task",
    "reader_action_framework",
    "reader_standards_must",
    "reader_output_template",
    "reader_no_doing",
]


def _split_md_sections(content: str) -> Dict[str, str]:
    """按 ## 标题切分；__preamble__ 为首个 ## 之前的内容。"""
    parts: Dict[str, str] = {}
    current_title = ""
    buf: List[str] = []
    for line in (content or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if m:
            if current_title:
                parts[current_title.strip()] = "\n".join(buf).strip()
            elif buf:
                parts["__preamble__"] = "\n".join(buf).strip()
            current_title = m.group(1).strip()
            buf = []
            continue
        buf.append(line)
    if current_title:
        parts[current_title.strip()] = "\n".join(buf).strip()
    elif buf:
        parts["__preamble__"] = "\n".join(buf).strip()
    return parts


def _section_by_prefix(sections: Dict[str, str], prefix: str) -> str:
    for title, body in sections.items():
        if title.startswith(prefix):
            return body
    return ""


def parse_reader_agent_md(content: str) -> Dict[str, str]:
    """从 AGENT.md 解析 reader_* 模块化字段（向量/配置各载一段）。"""
    sec = _split_md_sections(content)
    pre = sec.get("__preamble__", "")
    pre_lines = [ln for ln in pre.splitlines() if not re.match(r"^#\s+", ln.strip())]
    role = _section_by_prefix(sec, "1. 角色")
    task = _section_by_prefix(sec, "2. 任务")
    role_task = role
    if task:
        role_task = (role + "\n\n" + task).strip() if role else task
    tpl = _section_by_prefix(sec, "6. 输出格式")
    tpl = re.sub(r"^```\s*\n?", "", tpl)
    tpl = re.sub(r"\n?```\s*$", "", tpl)
    return {
        "reader_system_prompt": "\n".join(pre_lines).strip(),
        "reader_role_task": role_task,
        "reader_action_framework": _section_by_prefix(sec, "3. 动作框架"),
        "reader_standards_must": _section_by_prefix(sec, "4. 规范"),
        "reader_no_doing": _section_by_prefix(sec, "5. 禁止"),
        "reader_output_template": tpl.strip(),
    }


def _replace_section_body(content: str, title_prefix: str, new_body: str) -> str:
    lines = (content or "").splitlines()
    out: List[str] = []
    i = 0
    found = False
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if m and m.group(1).strip().startswith(title_prefix):
            found = True
            out.append(line)
            if (new_body or "").strip():
                out.append("")
                out.extend(new_body.splitlines())
            i += 1
            while i < len(lines) and not re.match(r"^##\s+", lines[i].strip()):
                i += 1
            continue
        out.append(line)
        i += 1
    if not found and (new_body or "").strip():
        out.extend(["", f"## {title_prefix}", "", *new_body.splitlines()])
    return "\n".join(out)


def sync_reader_agent_md_from_fields(content: str, fields: Dict[str, str]) -> str:
    """用模块化 config 字段回写 AGENT.md 各 ## 章节。"""
    md = content or ""
    sys_p = str(fields.get("reader_system_prompt") or "").strip()
    if sys_p:
        title_m = re.search(r"^#\s+.+$", md, re.M)
        head = (title_m.group(0) + "\n\n") if title_m else ""
        first_h2 = md.find("\n## ")
        tail = md[first_h2 + 1 :] if first_h2 >= 0 else ""
        md = head + sys_p + ("\n" + tail if tail else "")

    role_task = str(fields.get("reader_role_task") or "").strip()
    if "\n## " in role_task or "\n2." in role_task:
        chunks = re.split(r"\n(?=\d+\.\s)", role_task)
        role_body = chunks[0].strip() if chunks else role_task
        task_body = chunks[1].strip() if len(chunks) > 1 else ""
        task_body = re.sub(r"^2\.\s*任务\s*\n?", "", task_body).strip()
        md = _replace_section_body(md, "1. 角色", role_body)
        if task_body:
            md = _replace_section_body(md, "2. 任务", task_body)
    elif role_task:
        md = _replace_section_body(md, "1. 角色", role_task)

    md = _replace_section_body(md, "3. 动作框架", str(fields.get("reader_action_framework") or "").strip())
    md = _replace_section_body(md, "4. 规范", str(fields.get("reader_standards_must") or "").strip())
    md = _replace_section_body(md, "5. 禁止", str(fields.get("reader_no_doing") or "").strip())

    tpl = str(fields.get("reader_output_template") or "").strip()
    if tpl and not tpl.startswith("```"):
        tpl = "```\n" + tpl + "\n```"
    md = _replace_section_body(md, "6. 输出格式", tpl)
    return md.strip() + "\n"


def merge_reader_fields_from_md(fields: Dict[str, str], md_content: str) -> Dict[str, str]:
    """config 某段为空时，从 AGENT.md 对应章节补齐。"""
    parsed = parse_reader_agent_md(md_content)
    merged = dict(fields or {})
    for k in READER_FIELD_KEYS:
        if not str(merged.get(k) or "").strip() and parsed.get(k):
            merged[k] = parsed[k]
    return merged
