"""LLM Agent 结构化信号：ok / reject（输入不足时拒答，禁止编造）。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# 拒答码（摘要/整理 Agent 输出 status=reject 时使用）
LLM_INPUT_REJECTED = "LLM_INPUT_REJECTED"
LLM_INPUT_TOO_SHORT = "LLM_INPUT_TOO_SHORT"
LLM_INPUT_UNREADABLE = "LLM_INPUT_UNREADABLE"

_LLM_SIGNAL_RULE = (
    "\n【拒答信号-硬性】仅输出一个 JSON 对象。"
    " 若输入正文有效信息不足以忠实整理/摘要（例如仅平台名、空壳、乱码、过短无法概括），"
    " 必须拒答，禁止编造百科/平台概述/虚构内容。"
    ' 成功: {"status":"ok", ...业务字段...}'
    ' 拒答: {"status":"reject","reject_code":"INPUT_TOO_SHORT|INPUT_UNREADABLE|INPUT_EMPTY","reject_reason":"20字内说明"}'
    " 不得 JSON 外任何文字。"
)

_JSON_OUTPUT_RULE_SUMMARY_WITH_SIGNAL = (
    "\n【输出格式-硬性】"
    ' 成功时: {"status":"ok","title":"不超过20字标题","summary":"摘要正文"}；'
    ' 拒答时: {"status":"reject","reject_code":"...","reject_reason":"..."}。'
    "title 不要含 #；summary 可含 Markdown 目录与要点。"
    + _LLM_SIGNAL_RULE
)

_JSON_OUTPUT_RULE_ARTICLE_WITH_SIGNAL = (
    "\n【输出格式-硬性】"
    ' 成功时: {"status":"ok","article":"整理后的正文"}；'
    ' 拒答时: {"status":"reject","reject_code":"...","reject_reason":"..."}。'
    "article 段落用 \\n\\n 分隔。"
    + _LLM_SIGNAL_RULE
)


def input_stats_block(char_len: int, *, label: str = "输入正文") -> str:
    """注入用户消息，供 LLM 判断是否应 reject。"""
    return (
        f"\n【输入统计】{label}约 {int(char_len or 0)} 字符。"
        "若不足以忠实整理/摘要，必须输出 status=reject，不得编造。"
    )


def parse_agent_status(data: Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """解析 status 字段；无 status 时兼容旧格式视为 ok。"""
    d = data or {}
    status = str(d.get("status") or "ok").strip().lower()
    if status == "reject":
        code = str(d.get("reject_code") or LLM_INPUT_REJECTED).strip().upper()
        if code == "INPUT_TOO_SHORT":
            code = LLM_INPUT_TOO_SHORT
        elif code == "INPUT_UNREADABLE":
            code = LLM_INPUT_UNREADABLE
        elif not code.startswith("LLM_"):
            code = LLM_INPUT_REJECTED
        return "reject", {
            "reject_code": code,
            "reject_reason": str(d.get("reject_reason") or "输入不足以生成有效输出").strip(),
        }
    return "ok", d
