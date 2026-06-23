"""链接沉淀 — 可配置元数据 JSON 提取（对齐 HaiChiAgent 结构化处理思路）。"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from .json_llm_output import (
    LLM_JSON_PARSE_FAILED,
    build_json_retry_user_suffix,
    normalize_llm_string_escapes,
    parse_llm_json_object,
)
from .pipeline_logging import invoke_llm_via_gateway, log_llm_done, log_llm_prepare, pipeline_log

_log = logging.getLogger("sba.link_meta_extract")

# 与知识库 metadata JSON / 业务术语表字段对齐的默认提取结构
DEFAULT_META_EXTRACT_FIELDS: List[Dict[str, str]] = [
    {"key": "domain", "label": "领域", "description": "文档所属业务领域（大粒度）"},
    {"key": "module", "label": "模块", "description": "所属功能模块（中粒度）"},
    {"key": "doc_type", "label": "文档类型", "description": "如产品手册/技术文档/FAQ/政策/笔记"},
    {"key": "author_name", "label": "作者", "description": "内容作者/博主昵称（从正文开头提取）"},
    {"key": "keyword1", "label": "关键词1", "description": "核心主题词或实体"},
    {"key": "keyword2", "label": "关键词2", "description": "次要主题词或补充实体"},
]

_KB_FIELD_LABELS = {
    "domain": ("领域", "文档所属业务领域（大粒度）"),
    "module": ("模块", "所属功能模块（中粒度）"),
    "doc_type": ("文档类型", "如产品手册/技术文档/FAQ/政策/笔记"),
    "keyword1": ("关键词1", "核心主题词或实体"),
    "keyword2": ("关键词2", "次要主题词或补充实体"),
    "source": ("来源", "文档来源或出处"),
    "tags": ("标签", "主题标签列表"),
}


def clamp_importance(value: Any, default: int = 5) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(10, n))


def normalize_meta_extract_fields(raw: Any) -> List[Dict[str, str]]:
    """将 config 中的 fields 规范为 [{key,label,description}]。"""
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return list(DEFAULT_META_EXTRACT_FIELDS)
    if isinstance(raw, dict):
        out: List[Dict[str, str]] = []
        for key, val in raw.items():
            k = str(key or "").strip()
            if not k:
                continue
            if isinstance(val, dict):
                out.append({
                    "key": k,
                    "label": str(val.get("label") or k).strip(),
                    "description": str(val.get("description") or "").strip(),
                })
            else:
                lbl, desc = _KB_FIELD_LABELS.get(k, (k, ""))
                out.append({"key": k, "label": lbl, "description": desc})
        return out or list(DEFAULT_META_EXTRACT_FIELDS)
    if isinstance(raw, list):
        out = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            k = str(row.get("key") or "").strip()
            if not k:
                continue
            out.append({
                "key": k,
                "label": str(row.get("label") or k).strip(),
                "description": str(row.get("description") or "").strip(),
            })
        return out or list(DEFAULT_META_EXTRACT_FIELDS)
    return list(DEFAULT_META_EXTRACT_FIELDS)


def fields_from_kb_metadata_json(metadata_json: str) -> List[Dict[str, str]]:
    """将知识库 metadata JSON 模板一键转为提取字段定义。"""
    try:
        obj = json.loads(metadata_json or "{}")
    except json.JSONDecodeError:
        obj = {}
    if not isinstance(obj, dict):
        return list(DEFAULT_META_EXTRACT_FIELDS)
    out: List[Dict[str, str]] = []
    for key in obj.keys():
        k = str(key or "").strip()
        if not k:
            continue
        lbl, desc = _KB_FIELD_LABELS.get(k, (k, f"从正文提取「{k}」相关元数据"))
        out.append({"key": k, "label": lbl, "description": desc})
    return out or list(DEFAULT_META_EXTRACT_FIELDS)


def get_meta_extract_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from .config import load_config

    c = cfg or load_config()
    enabled = bool(c.get("meta_extract_enabled", True))
    fields = normalize_meta_extract_fields(c.get("meta_extract_fields"))
    prompt = str(c.get("meta_extract_prompt") or "").strip()
    return {"enabled": enabled, "fields": fields, "prompt": prompt}


def format_meta_json_block(data: Dict[str, Any], *, fields: Optional[Sequence[Dict[str, str]]] = None) -> str:
    """渲染为 Markdown 代码块，供 output_template 的 {meta_json} 占位。"""
    if not data:
        return ""
    ordered: Dict[str, Any] = {}
    keys = [str(f.get("key") or "") for f in (fields or []) if str(f.get("key") or "").strip()]
    for k in keys:
        if k in data:
            ordered[k] = data[k]
    for k, v in data.items():
        if k not in ordered:
            ordered[k] = v
    body = json.dumps(ordered, ensure_ascii=False, indent=2)
    return f"## 结构化元数据\n\n```json\n{body}\n```\n"


def _build_extract_prompt(
    fields: Sequence[Dict[str, str]],
    *,
    body_text: str,
    summary_text: str = "",
    task_note: str = "",
    task_keywords: str = "",
    custom_prompt: str = "",
) -> str:
    field_lines = []
    for f in fields:
        k = str(f.get("key") or "").strip()
        if not k:
            continue
        label = str(f.get("label") or k).strip()
        desc = str(f.get("description") or "").strip()
        field_lines.append(f'- "{k}" ({label})：{desc or "按字段名理解并提取"}')
    schema_keys = [str(f.get("key") or "").strip() for f in fields if str(f.get("key") or "").strip()]
    schema_hint = ", ".join(f'"{k}": "..."' for k in schema_keys)
    parts = [
        (custom_prompt or "请根据以下正文与摘要，提取文档元数据。").strip(),
        "\n【待提取字段】\n" + "\n".join(field_lines),
    ]
    if task_note.strip():
        parts.append(f"\n【任务备注（可参考）】\n{task_note.strip()}")
    if task_keywords.strip():
        parts.append(f"\n【任务关键词（可参考）】\n{task_keywords.strip()}")
    if summary_text.strip():
        parts.append(f"\n【摘要】\n{summary_text.strip()[:6000]}")
    parts.append(f"\n【正文】\n{(body_text or '').strip()[:12000]}")
    parts.append(
        "\n【输出格式-硬性】仅输出一个 JSON 对象，禁止 JSON 外任何文字。"
        f" 键名必须且仅能包含：{', '.join(schema_keys)}。"
        f" 示例结构：{{{schema_hint}}}"
    )
    return "\n".join(parts).strip()


def extract_link_metadata(
    *,
    body_text: str,
    summary_text: str = "",
    llm_cfg: Dict[str, Any],
    fields: Optional[Sequence[Dict[str, str]]] = None,
    task_note: str = "",
    task_keywords: str = "",
    log_cb: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """真实 LLM 调用：按配置字段提取元数据 JSON。"""
    cfg_fields = list(fields or DEFAULT_META_EXTRACT_FIELDS)
    if not cfg_fields:
        return {}

    task_id = str(llm_cfg.get("_task_id") or "meta_extract")
    chain = str(llm_cfg.get("_log_chain") or "链接沉淀文档-元数据提取")
    log_module = "link_meta_extract.extract_link_metadata"
    log_obj = "元数据提取"

    def log(msg: str, level: str = "INFO"):
        if log_cb:
            log_cb(msg, level)

    meta_cfg = get_meta_extract_config(llm_cfg)
    prompt = _build_extract_prompt(
        cfg_fields,
        body_text=body_text,
        summary_text=summary_text,
        task_note=task_note,
        task_keywords=task_keywords,
        custom_prompt=meta_cfg.get("prompt") or "",
    )
    system_prompt = (
        (llm_cfg.get("system_prompt") or "你是一个专业的内容分析助手。").strip()
        + "\n你只负责按字段说明提取元数据，不得编造正文中不存在的事实。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    routes = log_llm_prepare(
        task_id,
        chain,
        log_module,
        log_obj,
        role="元数据提取Agent",
        text_len=len(body_text or ""),
        cfg=llm_cfg,
        agent_name="summary_agent",
        task_type="summary",
    )
    required = tuple(str(f.get("key") or "").strip() for f in cfg_fields if str(f.get("key") or "").strip())
    last_err = ""
    raw_out = ""

    for attempt in range(3):
        ret = invoke_llm_via_gateway(
            llm_cfg,
            agent_name="summary_agent",
            task_type="summary",
            messages=messages,
            temperature=0.2,
            max_tokens=2048,
            timeout_sec=float(routes.get("timeout_sec") or 90.0),
            retry_index=0,
        )
        raw_out = (ret.get("text") or "").strip()
        if not ret.get("ok") or not raw_out:
            last_err = str(ret.get("error") or "gateway_invoke_failed")
            log(f"元数据提取 LLM 失败: {last_err}", "WARNING")
            break

        parsed = parse_llm_json_object(raw_out, required_keys=required)
        if parsed.ok and isinstance(parsed.data, dict):
            out: Dict[str, Any] = {}
            for k in required:
                val = parsed.data.get(k)
                if val is None:
                    out[k] = ""
                elif isinstance(val, (list, dict)):
                    out[k] = val
                else:
                    out[k] = normalize_llm_string_escapes(str(val).strip())
            log_llm_done(
                task_id, chain, log_module, log_obj,
                role="元数据提取Agent", routes=routes, ok=True, out_len=len(raw_out),
            )
            log(f"元数据提取完成，字段数={len(out)}", "INFO")
            return out

        last_err = parsed.error_code or LLM_JSON_PARSE_FAILED
        if attempt >= 2:
            break
        suffix = build_json_retry_user_suffix(last_err, required)
        messages = messages + [{"role": "user", "content": suffix}]

    log_llm_done(
        task_id, chain, log_module, log_obj,
        role="元数据提取Agent", routes=routes, ok=False, error=last_err[:200],
    )
    pipeline_log(
        task_id, chain, log_module, log_obj, "JSON解析", "Agent执行",
        "元数据提取失败，跳过", "WARNING",
        error_code=last_err, preview=(raw_out or "")[:200],
    )
    return {}
