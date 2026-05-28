"""SKILL 使用流程图：经图例 Agent（diagram_legend_agent）分析源文档 → 药丸流程 JSON，持久化到 output/skill_flows。"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

_LOG = logging.getLogger(__name__)
_BASE = Path(__file__).resolve().parents[3]
_FLOW_DIR = _BASE / "output" / "skill_flows"
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "agents" / "summary" / "AGENT_SKILL_USAGE_FLOW.md"
_lock = threading.Lock()
_jobs: Dict[str, str] = {}  # skill_id -> pending|done|error

# 与长页图例侧翼共用网关路由时可复用 diagram_legend_html 的 endpoint
_TASK_TYPE = "skill_usage_flow"
_AGENT_NAME = "diagram_legend_agent"


def _flow_path(skill_id: str) -> Path:
    sid = re.sub(r"[^a-zA-Z0-9._-]", "_", (skill_id or "").strip())[:80] or "unknown"
    return _FLOW_DIR / f"{sid}.json"


def get_flow_state(skill_id: str) -> Dict[str, Any]:
    p = _flow_path(skill_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    with _lock:
        st = _jobs.get(skill_id)
    if st == "pending":
        return {"status": "pending", "mermaid": "", "flow": None, "error": ""}
    return {"status": "none", "mermaid": "", "flow": None, "error": ""}


def _save_state(skill_id: str, payload: Dict[str, Any]) -> None:
    _FLOW_DIR.mkdir(parents=True, exist_ok=True)
    _flow_path(skill_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_system_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return (
        "你是 SKILL 使用流程图专家。分析 SKILL 源 Markdown，输出仅含 nodes/edges 的 JSON。"
        "禁止把标题逐条当节点；节点为动宾短语的使用步骤；type 为 start/auto/decision/user/done。"
    )


def _context_cap() -> int:
    try:
        from .config import load_config

        cfg = load_config()
        return int(cfg.get("longpage_legend_agent_context_max_chars") or 14000)
    except Exception:
        return 14000


def _truncate_skill_body(body_md: str, cap: int) -> str:
    """保留 SKILL 源文档主体；超长时保留头尾并标注截断（非按行抽取当流程）。"""
    raw = (body_md or "").strip()
    if len(raw) <= cap:
        return raw
    head = max(cap // 2, 4000)
    tail = max(cap - head - 80, 2000)
    return (
        raw[:head]
        + "\n\n…[正文中段已截断，请结合首尾分析主流程，勿把章节标题原样当节点]…\n\n"
        + raw[-tail:]
    )


def _build_user_message(name: str, description: str, body_md: str, command: str = "") -> str:
    cap = _context_cap()
    body = _truncate_skill_body(body_md, cap)
    parts = [
        "请分析下列 SKILL 源文档，输出使用流程图 JSON（nodes + edges）。",
        f"名称: {name or '—'}",
    ]
    if (command or "").strip():
        parts.append(f"命令映射: {command.strip()}")
    if (description or "").strip():
        parts.append(f"摘要: {description.strip()[:800]}")
    parts.append("\n--- SKILL 源文档正文 ---\n")
    parts.append(body)
    return "\n".join(parts)


def _parse_flow_json(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    # 取首个 JSON 对象
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


def _generate_flow_sync(
    name: str,
    description: str,
    body_md: str,
    *,
    command: str = "",
    skill_id: str = "",
) -> Dict[str, Any]:
    from .skill_flow_pill import normalize_pill_flow

    if not (body_md or "").strip():
        return {
            "status": "error",
            "mermaid": "",
            "flow": None,
            "error": "SKILL 正文为空，无法生成流程图",
            "source": "none",
        }

    system = _load_system_prompt()
    user = _build_user_message(name, description, body_md, command)

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
                "[工具-SKILL流程图|skill_flow_service._generate_flow_sync|skill_id=%s|Agent执行|调用] "
                "图例网关失败; ok=false; error=%s",
                skill_id or name,
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
                "[工具-SKILL流程图|skill_flow_service._generate_flow_sync|skill_id=%s|Agent执行|解析] "
                "JSON 解析失败; ok=false",
                skill_id or name,
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
            "[工具-SKILL流程图|skill_flow_service._generate_flow_sync|skill_id=%s|Agent执行|完成] "
            "流程图生成成功; ok=true; nodes=%s",
            skill_id or name,
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
            "[工具-SKILL流程图|skill_flow_service._generate_flow_sync|skill_id=%s|Agent执行|异常] "
            "未捕获异常; error_type=%s",
            skill_id or name,
            type(e).__name__,
        )
        return {
            "status": "error",
            "mermaid": "",
            "flow": None,
            "error": str(e),
            "source": "exception",
        }


def schedule_skill_flow(
    skill_id: str,
    name: str,
    description: str,
    body_md: str,
    *,
    command: str = "",
) -> None:
    """后台生成，不阻塞导入接口。"""
    with _lock:
        if _jobs.get(skill_id) == "pending":
            return
        _jobs[skill_id] = "pending"
    _save_state(skill_id, {"status": "pending", "mermaid": "", "flow": None, "error": ""})

    def _run():
        try:
            result = _generate_flow_sync(
                name,
                description,
                body_md,
                command=command,
                skill_id=skill_id,
            )
            _save_state(skill_id, result)
            with _lock:
                _jobs[skill_id] = result.get("status") or "done"
        except Exception as e:
            _save_state(
                skill_id,
                {
                    "status": "error",
                    "mermaid": "",
                    "flow": None,
                    "error": str(e),
                    "source": "exception",
                },
            )
            with _lock:
                _jobs[skill_id] = "error"

    threading.Thread(target=_run, daemon=True).start()
