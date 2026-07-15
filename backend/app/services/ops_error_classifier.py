"""运维 Agent 失败类型标准化：委托 error_code_registry（模块字母 + 四位序号，如 T1001）。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_AGENT_DIR = None
for _p in Path(__file__).resolve().parents:
    _candidate = _p / "src" / "agent"
    if _candidate.is_dir():
        _AGENT_DIR = _candidate.resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from error_code_registry import (  # noqa: E402
    classify_by_message,
    extract_error_code_from_text,
    get_error_entry,
    get_error_remediation,
    list_errors,
    list_modules,
    resolve_error_code,
)


def extract_error_code(message: str) -> str:
    """从报错文本提取标准 error_code（如 T1001）。"""
    return extract_error_code_from_text(message)


def classify_task_failure(
    *,
    error_message: str = "",
    error_info: Any = None,
    stage: str = "",
    task: Optional[Dict[str, Any]] = None,
    error_code: str = "",
) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    if isinstance(error_info, dict):
        info = error_info
    elif error_info:
        info = {"message": str(error_info)}

    hint_parts = [
        error_code,
        str(info.get("error_code") or ""),
        str((task or {}).get("transcribe_error_code") or ""),
        str((task or {}).get("error_code") or ""),
        str((task or {}).get("ops_failure_code") or ""),
    ]
    hint_code = ""
    for p in hint_parts:
        resolved = resolve_error_code(str(p or "").strip())
        if resolved:
            hint_code = resolved
            break

    merged_msg = "\n".join(
        x
        for x in (
            str(info.get("message") or ""),
            str(error_message or ""),
            str((task or {}).get("error") or ""),
            str(info.get("type") or ""),
        )
        if x
    )
    stage_hint = (stage or info.get("stage") or info.get("step_name") or "").strip()
    if not stage_hint and task:
        stage_hint = str(task.get("failed_stage") or task.get("stage") or "")

    cls = classify_by_message(merged_msg, stage=stage_hint, hint_code=hint_code)
    return cls


def build_failure_summary_block(classification: Dict[str, Any]) -> str:
    if not classification:
        return ""
    lines = [
        "## 错误类型总结（规则提取）",
        "",
        f"- **error_code**: `{classification.get('error_code', '')}`",
        f"- **module**: {classification.get('module', '')} ({classification.get('module_name', '')})",
        f"- **error_message**: {classification.get('error_message', '')}",
        f"- **category**: {classification.get('category', '')}",
        f"- **severity**: {classification.get('severity', '')}",
        f"- **title**: {classification.get('title', '')}",
        f"- **stage**: {classification.get('stage', '')}",
        f"- **match_source**: {classification.get('match_source', '')}",
        "",
    ]
    return "\n".join(lines)


def normalize_error_info(error_info: Any, **kwargs: Any) -> Dict[str, Any]:
    base: Dict[str, Any]
    if isinstance(error_info, dict):
        base = dict(error_info)
    elif error_info:
        base = {"message": str(error_info)}
    else:
        base = {}
    cls = classify_task_failure(error_message=kwargs.get("error_message", ""), error_info=base, **kwargs)
    std_code = cls["error_code"]
    remedy = get_error_remediation(std_code)
    base["error_code"] = std_code
    base["error_message"] = cls["error_message"]
    base["failure_category"] = cls["category"]
    base["failure_module"] = cls.get("module", "")
    base["failure_module_name"] = cls.get("module_name", "")
    base["failure_severity"] = cls["severity"]
    base["failure_title"] = cls["title"]
    base["failure_stage"] = cls["stage"]
    base["failure_match_source"] = cls["match_source"]
    base["failure_explanation"] = remedy.get("explanation") or ""
    base["failure_remediation"] = remedy.get("remediation") or []
    base["failure_remediation_md"] = remedy.get("remediation_md") or ""
    base["failure_summary"] = f"{std_code}: {cls['error_message']}" if std_code else str(cls.get("error_message") or "")
    return base
