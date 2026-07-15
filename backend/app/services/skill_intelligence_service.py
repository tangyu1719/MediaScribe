"""SKILL 智能分析：经图例 Agent 分析源文档 → 结构化 JSON，持久化到 output/skill_intelligence。"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)
_BASE = Path(__file__).resolve().parents[3]
_INTEL_DIR = _BASE / "output" / "skill_intelligence"
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "agents" / "summary" / "AGENT_SKILL_INTELLIGENCE.md"
_lock = threading.Lock()
_jobs: Dict[str, str] = {}

_TASK_TYPE = "skill_intelligence"
_AGENT_NAME = "diagram_legend_agent"


def _intel_path(skill_id: str) -> Path:
    sid = re.sub(r"[^a-zA-Z0-9._-]", "_", (skill_id or "").strip())[:80] or "unknown"
    return _INTEL_DIR / f"{sid}.json"


def get_intelligence_state(skill_id: str) -> Dict[str, Any]:
    p = _intel_path(skill_id)
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
        return {"status": "pending", "analysis": None, "error": ""}
    return {"status": "none", "analysis": None, "error": ""}


def _save_state(skill_id: str, payload: Dict[str, Any]) -> None:
    _INTEL_DIR.mkdir(parents=True, exist_ok=True)
    _intel_path(skill_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_system_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "你是 SKILL 智能分析专家。分析 SKILL Markdown，输出 JSON（focus/meaning/scenarios/example/triggers/desc_zh 等）。"


def _context_cap() -> int:
    try:
        from .config import load_config

        cfg = load_config()
        return int(cfg.get("longpage_legend_agent_context_max_chars") or 14000)
    except Exception:
        return 14000


def _truncate_skill_body(body_md: str, cap: int) -> str:
    raw = (body_md or "").strip()
    if len(raw) <= cap:
        return raw
    head = max(cap // 2, 4000)
    tail = max(cap - head - 80, 2000)
    return (
        raw[:head]
        + "\n\n…[正文中段已截断，请结合首尾分析，勿把章节标题原样当场景]…\n\n"
        + raw[-tail:]
    )


def _build_user_message(name: str, description: str, body_md: str, command: str = "") -> str:
    cap = _context_cap()
    body = _truncate_skill_body(body_md, cap)
    parts = [
        "请分析下列 SKILL 源文档，输出智能分析 JSON。",
        f"名称: {name or '—'}",
    ]
    if (command or "").strip():
        parts.append(f"命令映射: {command.strip()}")
    if (description or "").strip():
        parts.append(f"摘要: {description.strip()[:800]}")
    parts.append("\n--- SKILL 源文档正文 ---\n")
    parts.append(body)
    return "\n".join(parts)


def _parse_intel_json(raw: str) -> Optional[Dict[str, Any]]:
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
                    if isinstance(obj, dict) and obj.get("focus"):
                        return obj
                except json.JSONDecodeError:
                    pass
                break
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_scenarios(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:6]:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()[:32]
            detail = str(item.get("detail") or item.get("desc") or "").strip()[:200]
            if title or detail:
                out.append({"title": title or "场景", "detail": detail})
        elif isinstance(item, str) and item.strip():
            out.append({"title": "场景", "detail": item.strip()[:200]})
    return out


def normalize_intelligence(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """规范化 LLM 输出供前端展示。"""
    ex = parsed.get("example") if isinstance(parsed.get("example"), dict) else {}
    return {
        "focus": str(parsed.get("focus") or "").strip()[:300],
        "meaning": str(parsed.get("meaning") or "").strip()[:300],
        "primary_scenarios": _normalize_scenarios(parsed.get("primary_scenarios")),
        "other_scenarios": _normalize_scenarios(parsed.get("other_scenarios")),
        "how_others_use": [
            str(x).strip()[:200]
            for x in (parsed.get("how_others_use") or [])
            if str(x).strip()
        ][:4],
        "example": {
            "user_says": str(ex.get("user_says") or "").strip()[:120],
            "agent_does": str(ex.get("agent_does") or "").strip()[:120],
        },
        "triggers": [str(x).strip()[:40] for x in (parsed.get("triggers") or []) if str(x).strip()][:12],
        "desc_zh": str(parsed.get("desc_zh") or "").strip()[:600],
        "desc_en": str(parsed.get("desc_en") or "").strip()[:600],
        "cautions": [str(x).strip()[:200] for x in (parsed.get("cautions") or []) if str(x).strip()][:5],
    }


def _sync_desc_zh_to_registry(skill_id: str, desc_zh: str) -> None:
    """将智能分析产出的 desc_zh 写回 skills_registry.display（若原为空）。"""
    if not skill_id or not desc_zh:
        return
    try:
        from .skill_registry import _load_raw, _save_raw, get_skill

        sk = get_skill(skill_id)
        if not sk:
            return
        disp = sk.get("display") if isinstance(sk.get("display"), dict) else {}
        if str(disp.get("desc_zh") or "").strip():
            return
        data = _load_raw()
        skills = list(data.get("skills") or [])
        for i, row in enumerate(skills):
            if row.get("id") != skill_id:
                continue
            d2 = dict(row.get("display") or {})
            d2["desc_zh"] = desc_zh
            d2["desc_zh_source"] = "ai"
            if not d2.get("desc_en"):
                d2["desc_en"] = str(row.get("description") or "")[:600]
                d2["desc_en_source"] = "file"
            row["display"] = d2
            skills[i] = row
            break
        data["skills"] = skills
        _save_raw(data)
    except Exception as e:
        _LOG.warning(
            "[工具-SKILL智能分析|skill_intelligence_service._sync_desc_zh|skill_id=%s|硬编执行|写回] "
            "desc_zh 写回失败; error_type=%s",
            skill_id,
            type(e).__name__,
        )


def _generate_intel_sync(
    name: str,
    description: str,
    body_md: str,
    *,
    command: str = "",
    skill_id: str = "",
) -> Dict[str, Any]:
    if not (body_md or "").strip():
        return {
            "status": "error",
            "analysis": None,
            "error": "SKILL 正文为空，无法生成智能分析",
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
                "analysis": None,
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
            temperature=0.15,
            max_tokens=2800,
            timeout_sec=timeout,
        )
        if not gw.get("ok"):
            err = str(gw.get("error") or gw.get("hint") or "gateway_failed")
            _LOG.warning(
                "[工具-SKILL智能分析|skill_intelligence_service._generate_intel_sync|skill_id=%s|Agent执行|调用] "
                "网关失败; ok=false; error=%s",
                skill_id or name,
                err[:200],
            )
            return {
                "status": "error",
                "analysis": None,
                "error": f"图例 Agent 调用失败: {err}",
                "source": "legend_agent_error",
            }

        parsed = _parse_intel_json(str(gw.get("text") or ""))
        if not parsed:
            return {
                "status": "error",
                "analysis": None,
                "error": "图例 Agent 返回无法解析为智能分析 JSON",
                "source": "legend_agent_parse_error",
            }

        analysis = normalize_intelligence(parsed)
        if skill_id and analysis.get("desc_zh"):
            _sync_desc_zh_to_registry(skill_id, analysis["desc_zh"])

        _LOG.info(
            "[工具-SKILL智能分析|skill_intelligence_service._generate_intel_sync|skill_id=%s|Agent执行|完成] "
            "智能分析生成成功; ok=true",
            skill_id or name,
        )
        return {
            "status": "done",
            "analysis": analysis,
            "error": "",
            "source": "legend_agent",
        }
    except Exception as e:
        _LOG.exception(
            "[工具-SKILL智能分析|skill_intelligence_service._generate_intel_sync|skill_id=%s|Agent执行|异常] "
            "未捕获异常; error_type=%s",
            skill_id or name,
            type(e).__name__,
        )
        return {
            "status": "error",
            "analysis": None,
            "error": str(e),
            "source": "exception",
        }


def schedule_skill_intelligence(
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
    _save_state(skill_id, {"status": "pending", "analysis": None, "error": ""})

    def _run():
        try:
            result = _generate_intel_sync(
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
                    "analysis": None,
                    "error": str(e),
                    "source": "exception",
                },
            )
            with _lock:
                _jobs[skill_id] = "error"

    threading.Thread(target=_run, daemon=True).start()


def schedule_skill_assets(
    skill_id: str,
    name: str,
    description: str,
    body_md: str,
    *,
    command: str = "",
) -> None:
    """导入/更新后同时调度流程图与智能分析。"""
    from .skill_flow_service import schedule_skill_flow

    schedule_skill_flow(skill_id, name, description, body_md, command=command)
    schedule_skill_intelligence(skill_id, name, description, body_md, command=command)
