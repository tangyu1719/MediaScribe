"""内置 Tool Call 执行流程图：经图例 Agent 分析源码 → 药丸流程 JSON，持久化到 output/tool_flows。"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .tool_schema_service import (
    _find_builtin_meta,
    read_tool_source_bundle,
    resolve_invoke_name,
)

_LOG = logging.getLogger(__name__)
_BASE = Path(__file__).resolve().parents[3]
_FLOW_DIR = _BASE / "output" / "tool_flows"
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "agents" / "summary" / "AGENT_TOOL_EXECUTION_FLOW.md"
_lock = threading.Lock()
_jobs: Dict[str, str] = {}

_TASK_TYPE = "tool_execution_flow"
_AGENT_NAME = "diagram_legend_agent"


def _flow_path(tool_id: str) -> Path:
    tid = re.sub(r"[^a-zA-Z0-9._-]", "_", (tool_id or "").strip())[:80] or "unknown"
    return _FLOW_DIR / f"{tid}.json"


def get_flow_state(tool_id: str) -> Dict[str, Any]:
    p = _flow_path(tool_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    with _lock:
        st = _jobs.get(tool_id)
    if st == "pending":
        return {"status": "pending", "mermaid": "", "flow": None, "error": ""}
    return {"status": "none", "mermaid": "", "flow": None, "error": ""}


def _save_state(tool_id: str, payload: Dict[str, Any]) -> None:
    _FLOW_DIR.mkdir(parents=True, exist_ok=True)
    _flow_path(tool_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_system_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return (
        "你是 Tool Call 执行流程图专家。分析工具源码，输出仅含 nodes/edges 的 JSON。"
        "节点为服务端执行步骤；type 为 start/auto/decision/user/done。"
    )


def _context_cap() -> int:
    try:
        from .config import load_config

        cfg = load_config()
        return int(cfg.get("longpage_legend_agent_context_max_chars") or 14000)
    except Exception:
        return 14000


def _build_user_message(tool_id: str, name: str, description: str, invoke: str) -> str:
    cap = _context_cap()
    source = read_tool_source_bundle(tool_id, max_chars=cap)
    parts = [
        "请分析下列内置 Tool Call 源码，输出执行流程图 JSON（nodes + edges）。",
        f"工具页 ID: {tool_id}",
        f"调用名: {invoke}",
        f"名称: {name or '—'}",
    ]
    if (description or "").strip():
        parts.append(f"说明: {description.strip()[:800]}")
    parts.append("\n--- 工具源码 ---\n")
    parts.append(source)
    return "\n".join(parts)


def _parse_flow_json(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    if isinstance(obj, dict) and isinstance(obj.get("nodes"), list):
                        return obj
                except json.JSONDecodeError:
                    pass
                break
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _generate_flow_sync(tool_id: str) -> Dict[str, Any]:
    from .skill_flow_pill import normalize_pill_flow

    meta = _find_builtin_meta(tool_id)
    if not meta:
        return {
            "status": "error",
            "mermaid": "",
            "flow": None,
            "error": f"未知内置工具: {tool_id}",
            "source": "none",
        }
    name = str(meta.get("name") or tool_id)
    description = str(meta.get("description") or "")
    invoke = resolve_invoke_name(tool_id)
    source = read_tool_source_bundle(tool_id, max_chars=500)
    if not source.strip():
        return {
            "status": "error",
            "mermaid": "",
            "flow": None,
            "error": "无法读取工具源码，无法生成流程图",
            "source": "none",
        }

    system = _load_system_prompt()
    user = _build_user_message(tool_id, name, description, invoke)

    try:
        from .config import load_config
        from .pipeline_logging import enrich_pipeline_llm_cfg, invoke_llm_via_gateway

        cfg = enrich_pipeline_llm_cfg(load_config())
        if not bool(cfg.get("longpage_legend_agent_enabled", True)):
            return {
                "status": "error",
                "mermaid": "",
                "flow": None,
                "error": "图例 Agent 已关闭（longpage_legend_agent_enabled=false）",
                "source": "legend_agent_disabled",
            }

        timeout = float(cfg.get("longpage_legend_agent_timeout_sec") or 120.0)
        gw = invoke_llm_via_gateway(
            cfg,
            agent_name=_AGENT_NAME,
            task_type=_TASK_TYPE,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.12,
            max_tokens=2200,
            timeout_sec=timeout,
        )
        if not gw.get("ok"):
            err = str(gw.get("error") or gw.get("hint") or "gateway_failed")
            _LOG.warning(
                "[工具-ToolCall流程图|tool_flow_service._generate_flow_sync|tool_id=%s|Agent执行|调用] "
                "图例网关失败; ok=false; error=%s",
                tool_id,
                err[:200],
            )
            return {
                "status": "error",
                "mermaid": "",
                "flow": None,
                "error": f"图例 Agent 调用失败: {err}",
                "source": "legend_agent_error",
            }

        parsed = _parse_flow_json(str(gw.get("text") or ""))
        if not parsed:
            _LOG.warning(
                "[工具-ToolCall流程图|tool_flow_service._generate_flow_sync|tool_id=%s|Agent执行|解析] "
                "JSON 解析失败; ok=false",
                tool_id,
            )
            return {
                "status": "error",
                "mermaid": "",
                "flow": None,
                "error": "图例 Agent 返回无法解析为流程 JSON",
                "source": "legend_agent_parse_error",
            }

        flow = normalize_pill_flow(parsed, name, description)
        _LOG.info(
            "[工具-ToolCall流程图|tool_flow_service._generate_flow_sync|tool_id=%s|Agent执行|完成] "
            "流程图生成成功; ok=true; nodes=%s",
            tool_id,
            len(flow.get("nodes") or []),
        )
        return {
            "status": "done",
            "mermaid": "",
            "flow": flow,
            "error": "",
            "source": "legend_agent",
        }
    except Exception as e:
        _LOG.exception(
            "[工具-ToolCall流程图|tool_flow_service._generate_flow_sync|tool_id=%s|Agent执行|异常] "
            "未捕获异常; error_type=%s",
            tool_id,
            type(e).__name__,
        )
        return {
            "status": "error",
            "mermaid": "",
            "flow": None,
            "error": str(e),
            "source": "exception",
        }


def schedule_tool_flow(tool_id: str) -> None:
    """后台生成，不阻塞详情接口。"""
    tid = (tool_id or "").strip()
    if not tid:
        return
    with _lock:
        if _jobs.get(tid) == "pending":
            return
        _jobs[tid] = "pending"
    _save_state(tid, {"status": "pending", "mermaid": "", "flow": None, "error": ""})

    def _run():
        try:
            result = _generate_flow_sync(tid)
            _save_state(tid, result)
            with _lock:
                _jobs[tid] = result.get("status") or "done"
        except Exception as e:
            _save_state(
                tid,
                {
                    "status": "error",
                    "mermaid": "",
                    "flow": None,
                    "error": str(e),
                    "source": "exception",
                },
            )
            with _lock:
                _jobs[tid] = "error"

    threading.Thread(target=_run, daemon=True).start()
