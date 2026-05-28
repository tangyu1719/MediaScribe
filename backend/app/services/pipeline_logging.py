"""链接沉淀流水线结构化日志 —— 对齐原 video_gui.append_log / _flog 与全局 logging-spec。"""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
_PIPELINE_LOG_LOCK = threading.Lock()
for _p in _HERE.parents:
    if (_p / "src" / "agent").is_dir():
        _AGENT_DIR = (_p / "src" / "agent").resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

_PIPELINE_LOG_PATH = (_AGENT_DIR / "pipeline.log") if _AGENT_DIR else (_HERE.parents[2] / "pipeline.log")


def thread_name() -> str:
    return threading.current_thread().name or "MainThread"


def llm_timeout_for_text_len(text_len: int) -> Tuple[float, str]:
    """与原 video_gui.summarize_with_volcengine 动态超时一致。"""
    n = int(text_len or 0)
    if n < 5000:
        return 90.0, "1.5 分钟"
    if n < 20000:
        return 150.0, "2.5 分钟"
    if n < 50000:
        return 210.0, "3.5 分钟"
    return 300.0, "5 分钟"


def resolve_gateway_models(
    cfg: Dict[str, Any],
    *,
    agent_name: str = "summary_agent",
    task_type: str = "summary",
) -> Dict[str, Any]:
    """解析摘要/原文整理使用的 endpoint（含网关路由）。"""
    primary = (cfg.get("ai_chat_model") or cfg.get("model") or "").strip()
    backup = (cfg.get("ai_chat_model_backup") or "").strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    route_mode = (cfg.get("gateway_route_mode") or "").strip()
    route_task_ep = ""
    tr = cfg.get("gateway_task_type_route") or {}
    if isinstance(tr, dict):
        route_task_ep = (tr.get(task_type) or "").strip()

    chosen_id = ""
    gw_err = ""
    try:
        from ai_gateway import AIGateway

        gw = AIGateway.from_runtime_config(cfg)
        route_mode = route_mode or getattr(gw, "route_mode", "") or ""
        chosen = gw.choose_model_for_agent(agent_name=agent_name, task_type=task_type, retry_index=0)
        if chosen and (getattr(chosen, "endpoint_id", None) or "").strip():
            chosen_id = (chosen.endpoint_id or "").strip()
            if chosen_id and chosen_id != primary:
                backup = primary or backup
                primary = chosen_id
    except Exception as e:
        gw_err = str(e)

    if route_task_ep and not chosen_id:
        primary = route_task_ep

    return {
        "agent_name": agent_name,
        "task_type": task_type,
        "primary_endpoint": primary,
        "backup_endpoint": backup,
        "provider": provider,
        "route_mode": route_mode,
        "task_type_route_ep": route_task_ep,
        "gateway_chosen": chosen_id,
        "gateway_error": gw_err,
    }


def _write_pipeline_file(line: str) -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        th = thread_name()
        with _PIPELINE_LOG_LOCK:
            _PIPELINE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_PIPELINE_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [{th}] {line}\n")
    except Exception:
        pass


def pipeline_log(
    task_id: str,
    chain: str,
    module: str,
    obj: str,
    phase: str,
    category: str,
    action: str,
    level: str = "INFO",
    *,
    also_task_log: bool = True,
    log_cb: Optional[Callable[[str, str], None]] = None,
    **kwargs: Any,
) -> str:
    """
    全局规范：[链|模块|对象|类别|阶段] 动作; k=v
    also_task_log=True 时同步写入任务内存日志（SSE）。
    """
    payload = "; ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None and str(v) != "")
    head = f"[{chain}|{module}|{obj}|{category}|{phase}]"
    msg = f"{head} {action}" + (f"; {payload}" if payload else "")
    _write_pipeline_file(msg)
    if also_task_log:
        from .task_manager import add_log

        tid = thread_name()
        add_log(task_id, f"[{tid}] {msg}", level)
    if log_cb:
        log_cb(msg, level)
    return msg


def log_span_event(
    task_id: str,
    chain: str,
    module: str,
    obj: str,
    *,
    step_id: str,
    step_name: str,
    step_type: str,
    event: str,
    status: str = "",
    level: str = "INFO",
    **kwargs: Any,
) -> None:
    """每个节点 SPAN 生命周期日志（创建/开始/结束）。"""
    pipeline_log(
        task_id,
        chain,
        module,
        obj,
        event,
        "Agent执行",
        f"SPAN {step_name}({step_type}) step_id={step_id} status={status or '-'}",
        level,
        step_id=step_id,
        step_type=step_type,
        **kwargs,
    )


def log_llm_prepare(
    task_id: str,
    chain: str,
    module: str,
    stage: str,
    *,
    role: str,
    text_len: int,
    cfg: Dict[str, Any],
    agent_name: str = "summary_agent",
    task_type: str = "summary",
) -> Dict[str, Any]:
    """摘要 Agent / 原文整理 Agent 调用前：model、超时、预计耗时说明。"""
    routes = resolve_gateway_models(cfg, agent_name=agent_name, task_type=task_type)
    timeout_sec, timeout_desc = llm_timeout_for_text_len(text_len)
    routes["timeout_sec"] = timeout_sec
    routes["timeout_desc"] = timeout_desc
    routes["text_len"] = text_len
    routes["stage"] = stage
    routes["role"] = role
    pipeline_log(
        task_id,
        chain,
        module,
        stage,
        "调用前",
        "Agent执行",
        f"{role} LLM 即将调用",
        primary_endpoint=routes.get("primary_endpoint"),
        backup_endpoint=routes.get("backup_endpoint"),
        provider=routes.get("provider"),
        route_mode=routes.get("route_mode"),
        agent_name=agent_name,
        task_type=task_type,
        text_len=text_len,
        timeout_sec=timeout_sec,
        timeout_desc=timeout_desc,
        gateway_chosen=routes.get("gateway_chosen") or "",
        gateway_error=routes.get("gateway_error") or "",
    )
    return routes


def enrich_pipeline_llm_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """合并权威 config.json 与 api_gateway_nodes 凭证（与 AI 问答 / 老项目 video_gui 同源）。"""
    try:
        from .config import load_config

        base = load_config()
    except Exception:
        base = {}
    out = {**base, **(cfg or {})}
    try:
        from .ai_chat import resolve_chat_api_credentials

        creds = resolve_chat_api_credentials(out)
        if creds.get("api_key") and not str(out.get("volcengine_api_key") or "").strip():
            out["volcengine_api_key"] = creds["api_key"]
        if creds.get("base_url") and not str(out.get("volcengine_base_url") or "").strip():
            out["volcengine_base_url"] = creds["base_url"]
        if creds.get("model") and not str(out.get("ai_chat_model") or "").strip():
            out["ai_chat_model"] = creds["model"]
        if creds.get("provider"):
            out.setdefault("gateway_provider", creds["provider"])
    except Exception:
        pass
    return out


def invoke_llm_via_gateway(
    cfg: Dict[str, Any],
    *,
    agent_name: str,
    task_type: str,
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout_sec: float = 150.0,
    retry_index: int = 0,
) -> Dict[str, Any]:
    """经统一 AIGateway 调用 LLM（节点池 api_key / endpoint / provider）。"""
    try:
        from ai_gateway import AIGateway

        merged = enrich_pipeline_llm_cfg(cfg)
        gw = AIGateway.from_runtime_config(merged)
        if not gw.models:
            return {
                "ok": False,
                "error": "no_active_model",
                "text": "",
                "hint": "api_gateway_nodes 为空；请确认 SBA_AGENT_CONFIG 指向老项目 src/agent/config.json",
            }
        return gw.invoke_for_agent(
            agent_name=agent_name,
            task_type=task_type,
            messages=messages,
            retry_index=retry_index,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout_sec,
        )
    except Exception as ex:
        return {"ok": False, "error": str(ex), "text": ""}


def log_llm_done(
    task_id: str,
    chain: str,
    module: str,
    stage: str,
    *,
    role: str,
    routes: Dict[str, Any],
    ok: bool,
    out_len: int = 0,
    elapsed_ms: int = 0,
    error: str = "",
    token_count: int = 0,
    confidence: float = 0.0,
) -> None:
    est_tokens = int(token_count or 0) or (max(1, int(out_len) // 2) if ok and out_len else 0)
    conf = float(confidence or 0.0)
    if ok and conf <= 0:
        conf = 0.82
    pipeline_log(
        task_id,
        chain,
        module,
        stage,
        "调用后",
        "Agent执行",
        f"{role} LLM 调用{'完成' if ok else '失败'}",
        "INFO" if ok else "WARNING",
        ok=ok,
        out_len=out_len,
        elapsed_ms=elapsed_ms,
        token_count=est_tokens,
        confidence=round(conf, 3) if conf else "",
        primary_endpoint=routes.get("primary_endpoint"),
        timeout_sec=routes.get("timeout_sec"),
        error_message=(error or "")[:200],
    )
