"""链接沉淀流水线 ↔ SPAN 审计桥接（任务粒度 → 阶段 SPAN）。"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from .pipeline_stages import stage_label
from .pipeline_logging import log_span_event

_STAGE_STEP: Dict[str, str] = {}
_LOCK = threading.Lock()

# 阶段 → SPAN step_type
_STAGE_TYPES = {
    "comments": "retrieval",
    "download": "api_call",
    "transcribe": "api_call",
    "extract": "retrieval",
    "ocr": "retrieval",
    "assemble": "retrieval",
    "ai_analysis": "llm_call",
    "generate_md": "summary",
    "feishu_upload": "api_call",
    "html": "llm_call",
}


def session_id_for_platform(platform: str) -> str:
    return f"pipeline:{(platform or 'unknown').strip()}"


def ensure_pipeline_span_task(task_id: str, link: str, platform: str = "") -> None:
    from .span_audit import create_task, get_task, patch_task_snapshot

    if get_task(task_id):
        return
    create_task(session_id_for_platform(platform), (link or "")[:2000], task_id=task_id)
    patch_task_snapshot(
        task_id,
        fixed={"domain": "link_pipeline", "platform": platform, "source_url": link},
        open_layer={"objective": "链接沉淀文档", "current_assessment": "任务已创建", "decision": "continue"},
    )


def on_stage_start(task_id: str, route: str, stage_id: str, *, input_payload: Optional[Dict] = None) -> str:
    from .span_audit import create_step, start_step
    from .task_manager import get_task as _get_pipeline_task

    pt = _get_pipeline_task(task_id) or {}
    ensure_pipeline_span_task(
        task_id,
        str(pt.get("link") or ""),
        str(pt.get("platform") or ""),
    )
    label = stage_label(route, stage_id)
    step_type = _STAGE_TYPES.get(stage_id, "api_call")
    step = create_step(
        task_id,
        session_id_for_platform(str(pt.get("platform") or "")),
        step_type,
        label,
        idempotency_key=f"{task_id}:{stage_id}",
    )
    sid = step["step_id"]
    with _LOCK:
        _STAGE_STEP[f"{task_id}:{stage_id}"] = sid
    start_step(sid, input_payload=input_payload or {"stage_id": stage_id, "route": route})
    log_span_event(
        task_id,
        "链接沉淀文档",
        "pipeline_span_bridge",
        label,
        step_id=sid,
        step_name=label,
        step_type=step_type,
        event="开始",
        status="running",
        stage_id=stage_id,
    )
    return sid


def on_stage_finish(
    task_id: str,
    route: str,
    stage_id: str,
    *,
    status: str = "completed",
    output_payload: Optional[Dict] = None,
    error_code: str = "",
    error_message: str = "",
    token_count: int = 0,
    confidence: float = 0.0,
    elapsed_ms: int = 0,
) -> None:
    from .span_audit import finish_step, update_task

    key = f"{task_id}:{stage_id}"
    with _LOCK:
        sid = _STAGE_STEP.pop(key, "")
    if not sid:
        return
    label = stage_label(route, stage_id)
    open_layer: Dict[str, Any] = {}
    if confidence:
        open_layer["confidence"] = confidence
    if error_message:
        open_layer["stop_reason"] = error_message[:500]
        open_layer["decision"] = "stop"
    finish_step(
        sid,
        status=status,
        output_payload=output_payload or {"stage_id": stage_id},
        error_code=error_code,
        error_message=error_message,
        token_count=int(token_count or 0),
        open_layer=open_layer or None,
    )
    log_span_event(
        task_id,
        "链接沉淀文档",
        "pipeline_span_bridge",
        label,
        step_id=sid,
        step_name=label,
        step_type=_STAGE_TYPES.get(stage_id, "api_call"),
        event="结束",
        status=status,
        token_count=token_count,
        confidence=confidence,
        elapsed_ms=elapsed_ms,
        error_code=error_code or "",
    )
    if token_count:
        try:
            task = __import__(".span_audit", fromlist=["get_task"]).get_task(task_id)
            if task:
                update_task(task_id, total_token_count=(task.get("total_token_count") or 0) + token_count)
        except Exception:
            pass


def finish_llm_span(
    task_id: str,
    stage_id: str,
    *,
    ok: bool,
    token_count: int = 0,
    elapsed_ms: int = 0,
    confidence: float = 0.0,
    error: str = "",
    output_preview: str = "",
) -> None:
    """LLM 阶段结束：写入 token / 耗时 / 置信度。"""
    route = "video"
    status = "completed" if ok else "failed"
    on_stage_finish(
        task_id,
        route,
        stage_id,
        status=status,
        output_payload={
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "token_count": token_count,
            "preview": (output_preview or "")[:300],
        },
        error_message=error or "",
        token_count=token_count,
        confidence=confidence,
        elapsed_ms=elapsed_ms,
    )
