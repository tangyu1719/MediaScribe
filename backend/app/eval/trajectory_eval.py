"""Agent 轨迹 eval：封装 agentevals（工具调用层 L2）。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger("sba.eval.trajectory")

_VALID_MODES = frozenset({"strict", "unordered", "subset", "superset"})


def evaluate_trajectory(
    outputs: List[Dict[str, Any]],
    reference_outputs: List[Dict[str, Any]],
    *,
    mode: str = "strict",
) -> Dict[str, Any]:
    """
    轨迹匹配评测。mode: strict | unordered | subset | superset
    未安装 agentevals 时返回 skipped。
    """
    m = (mode or "strict").strip().lower()
    if m not in _VALID_MODES:
        return {"ok": False, "error": f"不支持的 mode: {mode}"}
    try:
        from agentevals.trajectory.match import create_trajectory_match_evaluator
    except ImportError as ex:
        _log.warning(
            "[Eval-轨迹|eval.trajectory_eval.evaluate_trajectory|agentevals|硬编执行|导入] 失败; error=%s",
            ex,
        )
        return {"ok": False, "skipped": True, "error": "agentevals 未安装", "mode": m}

    evaluator = create_trajectory_match_evaluator(trajectory_match_mode=m)
    result = evaluator(inputs={}, outputs=outputs, reference_outputs=reference_outputs)
    score = getattr(result, "score", None)
    key = getattr(result, "key", None)
    comment = getattr(result, "comment", None)
    if isinstance(result, dict):
        score = result.get("score", score)
        key = result.get("key", key)
        comment = result.get("comment", comment)
    return {
        "ok": True,
        "skipped": False,
        "mode": m,
        "score": bool(score),
        "key": key,
        "comment": comment,
        "detail": result if isinstance(result, dict) else getattr(result, "__dict__", str(result)),
    }


def evaluate_trajectory_strict(
    outputs: List[Dict[str, Any]],
    reference_outputs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """向后兼容 strict API。"""
    return evaluate_trajectory(outputs, reference_outputs, mode="strict")


def _tool_args_json(payload: Any) -> str:
    if payload is None:
        return "{}"
    if isinstance(payload, str):
        return payload if payload.strip().startswith("{") else json.dumps({"raw": payload}, ensure_ascii=False)
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return "{}"


def messages_from_span_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 span_audit 的 step 列表转为 OpenAI 风格消息序列（供 agentevals）。"""
    msgs: List[Dict[str, Any]] = []
    for st in steps or []:
        stype = (st.get("step_type") or "").strip()
        name = (st.get("step_name") or st.get("tool_name") or st.get("name") or "tool").strip()
        step_id = (st.get("step_id") or f"call_{len(msgs)}").strip()

        if stype in ("tool_call", "retrieval", "mcp_call"):
            inp = st.get("input_payload") or st.get("tool_io_brief") or {}
            tool_name = name
            if isinstance(inp, dict) and inp.get("tool"):
                tool_name = str(inp.get("tool"))
            msgs.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": step_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": _tool_args_json(inp),
                            },
                        }
                    ],
                }
            )
            out_txt = ""
            out_payload = st.get("output_payload") or {}
            if isinstance(out_payload, dict):
                out_txt = str(out_payload.get("summary") or out_payload.get("result") or "")[:2000]
            if not out_txt:
                out_txt = (st.get("output_summary") or st.get("tool_result_analysis") or "")[:2000]
            if out_txt:
                msgs.append({"role": "tool", "tool_call_id": step_id, "content": out_txt})
        elif stype in ("llm_call", "summary", "reasoning", "orchestration"):
            txt = (
                st.get("output_summary")
                or st.get("current_assessment")
                or st.get("context_summary")
                or ""
            )
            if not txt and isinstance(st.get("output_payload"), dict):
                txt = str(st["output_payload"].get("text") or st["output_payload"].get("content") or "")
            txt = (txt or "")[:2000]
            if txt:
                msgs.append({"role": "assistant", "content": txt})
    return msgs
