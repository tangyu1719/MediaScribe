"""OPS 运维服务 —— 导入 src/agent/ops_agent.py，对齐 Tk 版网关注入与可观测性。"""
from __future__ import annotations

import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
for _p in _HERE.parents:
    _candidate = _p / "src" / "agent"
    if _candidate.is_dir():
        _AGENT_DIR = _candidate.resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from ops_agent import OpsAgent, create_ops_agent, OPS_SYSTEM_PROMPT  # noqa: E402
from ops_log_analyzer import OpsLogAnalyzer  # noqa: E402

from .config import load_config

_log = logging.getLogger("sba.ops")

_ops_agent: Optional[OpsAgent] = None
_ops_events: List[Dict[str, Any]] = []
_path_counts: Counter = Counter()
_cost_total_ms: int = 0
_cost_sample_n: int = 0

_ops_overview = {
    "total_calls": 0,
    "success_calls": 0,
    "failed_calls": 0,
    "avg_cost_ms": 0,
    "top_paths": [],
}


def _ops_reports_dir() -> Path:
    return (_AGENT_DIR or Path(".")) / "output" / "ops_reports"


def _ops_reports_search_dirs() -> List[Path]:
    """运维报告目录候选（worktree / 主仓库 / 上级 src/agent）。"""
    seen: set[str] = set()
    out: List[Path] = []
    candidates: List[Path] = []
    if _AGENT_DIR:
        candidates.append(_AGENT_DIR / "output" / "ops_reports")
        for parent in _AGENT_DIR.parents:
            candidates.append(parent / "src" / "agent" / "output" / "ops_reports")
            candidates.append(parent / "output" / "ops_reports")
            candidates.append(parent / "web_rebuild_v2" / "src" / "agent" / "output" / "ops_reports")
            if parent.name in ("SuperBizAgent-AgentFramework", "web_rebuild_v2", "web_rebuild_v2-video-visual"):
                pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "src" / "agent" / "output" / "ops_reports")
        candidates.append(parent / "web_rebuild_v2" / "src" / "agent" / "output" / "ops_reports")
    for p in candidates:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            out.append(p)
    primary = _ops_reports_dir()
    if primary.is_dir() and all(str(primary.resolve()) != str(x.resolve()) for x in out):
        out.insert(0, primary)
    elif not out:
        out.insert(0, primary)
    return out


def _pipeline_log_path() -> Path:
    return (_AGENT_DIR or Path(".")) / "pipeline.log"


def _build_ops_agent_from_config() -> OpsAgent:
    """与 video_gui 一致：从 AI API 配置中心 + 网关 task_type=ops 注入 LLM。"""
    cfg = load_config()
    base_url = ""
    api_key = ""
    primary = ""
    backup = ""
    ops_provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    system_prompt = (cfg.get("ops_system_prompt") or OPS_SYSTEM_PROMPT).strip()

    try:
        from ai_api_config_gui import AIAPIConfigManager, _normalize_openai_base_url  # type: ignore
        from ai_gateway import AIGateway  # type: ignore

        cm = AIAPIConfigManager(runtime_overlay=cfg)
        api_cfg = cm.get_config()
        base_url = _normalize_openai_base_url(api_cfg.get("base_url") or "")
        api_key = (api_cfg.get("api_key") or "").strip()
        primary = (api_cfg.get("endpoint_id") or cfg.get("ai_chat_model") or "").strip()
        backup = (cfg.get("ai_chat_model_backup") or "").strip()
        for b in api_cfg.get("backup_configs") or []:
            ep = (b.get("endpoint_id") or "").strip()
            if ep and ep != primary:
                backup = ep
                break
        try:
            gw = AIGateway.from_runtime_config(cfg)
            chosen = gw.choose_model_for_agent(agent_name="ops_agent", task_type="ops", retry_index=0)
            if chosen and (chosen.endpoint_id or "").strip():
                chosen_id = (chosen.endpoint_id or "").strip()
                if chosen_id != primary:
                    backup = primary or backup
                    primary = chosen_id
                ops_provider = (chosen.provider or ops_provider or "ark").strip().lower()
        except Exception:
            pass
    except Exception:
        import os

        base_url = (os.environ.get("OPS_AI_CHAT_API_URL") or os.environ.get("AI_CHAT_API_URL") or "").strip()
        api_key = (os.environ.get("OPS_AI_CHAT_API_KEY") or os.environ.get("AI_CHAT_API_KEY") or "").strip()
        primary = (os.environ.get("OPS_AI_CHAT_MODEL") or cfg.get("ai_chat_model") or "").strip()
        backup = (os.environ.get("OPS_AI_CHAT_MODEL_BACKUP") or cfg.get("ai_chat_model_backup") or "").strip()

    memory_dir = str(_AGENT_DIR) if _AGENT_DIR else None
    agent = create_ops_agent(
        memory_dir=memory_dir,
        api_provider=ops_provider,
        base_url=base_url,
        api_key=api_key,
        api_model=primary,
        api_model_backup=backup,
        system_prompt=system_prompt,
    )
    _log.info(
        "[OPS运维-Agent初始化|ops._build_ops_agent_from_config|ops_agent|硬编执行|完成] "
        "运维Agent已创建; llm_ready=%s; model=%s",
        bool(agent.api_key and agent.api_model),
        (agent.api_model or "")[:24],
    )
    return agent


def _get_ops_agent(*, force_reload: bool = False) -> Optional[OpsAgent]:
    global _ops_agent
    if _ops_agent is not None and not force_reload:
        return _ops_agent
    try:
        _ops_agent = _build_ops_agent_from_config()
        return _ops_agent
    except Exception as exc:
        _log.warning(
            "[OPS运维-Agent初始化|ops._get_ops_agent|ops_agent|硬编执行|失败] "
            "初始化失败; error_type=%s; error_message=%s",
            type(exc).__name__,
            str(exc)[:200],
        )
        return None


def _refresh_top_paths() -> None:
    _ops_overview["top_paths"] = [
        {"path": p, "count": c}
        for p, c in _path_counts.most_common(15)
    ]


def ops_get_overview() -> Dict:
    return {"ok": True, "data": dict(_ops_overview)}


def ops_get_events(limit: int = 120) -> Dict:
    evts = _ops_events[-limit:] if len(_ops_events) > limit else _ops_events
    return {"ok": True, "data": {"events": list(reversed(evts))}}


def ops_add_event(
    method: str,
    path: str,
    status_code: int,
    cost_ms: int,
    *,
    query: str = "",
    error_detail: str = "",
    request_brief: str = "",
    response_brief: str = "",
) -> Dict:
    global _cost_total_ms, _cost_sample_n
    evt = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": method,
        "path": path,
        "status_code": status_code,
        "cost_ms": cost_ms,
        "query": (query or "")[:500],
        "error_detail": (error_detail or "")[:2000],
        "request_brief": (request_brief or "")[:1500],
        "response_brief": (response_brief or "")[:3000],
    }
    _ops_events.append(evt)
    if len(_ops_events) > 10000:
        _ops_events[:] = _ops_events[-5000:]

    _ops_overview["total_calls"] += 1
    if status_code < 400:
        _ops_overview["success_calls"] += 1
    else:
        _ops_overview["failed_calls"] += 1

    if cost_ms > 0:
        _cost_total_ms += cost_ms
        _cost_sample_n += 1
        _ops_overview["avg_cost_ms"] = int(_cost_total_ms / max(_cost_sample_n, 1))

    norm_path = path.split("?")[0] if path else "/"
    _path_counts[norm_path] += 1
    _refresh_top_paths()

    return {"ok": True}


def ops_get_status() -> Dict:
    """运维 Agent 运行态：LLM 是否就绪、模型、记忆条数、报告目录。"""
    agent = _get_ops_agent()
    cfg = load_config()
    llm_ready = False
    model = ""
    backup = ""
    memory_count = 0
    if agent is not None:
        llm_ready = bool(agent.api_key and agent.api_model)
        model = agent.api_model or ""
        backup = agent.api_model_backup or ""
        try:
            memory_count = len(agent.get_memory(limit=1000))
        except Exception:
            memory_count = 0
    reports_dir = _ops_reports_dir()
    report_count = len(list(reports_dir.glob("ops_*.md"))) if reports_dir.is_dir() else 0
    log_exists = _pipeline_log_path().is_file()
    return {
        "ok": True,
        "data": {
            "llm_ready": llm_ready,
            "model": model,
            "model_backup": backup,
            "memory_count": memory_count,
            "report_count": report_count,
            "pipeline_log_exists": log_exists,
            "ops_async_check_enabled": bool(cfg.get("ops_async_check_enabled", False)),
            "agent_md_path": str((_AGENT_DIR / "agents" / "ops" / "AGENT.md") if _AGENT_DIR else ""),
        },
    }


def ops_get_memory(limit: int = 30) -> Dict:
    agent = _get_ops_agent()
    if agent is None:
        return {"ok": False, "error": "OpsAgent 未初始化"}
    items = agent.get_memory(limit=limit)
    return {"ok": True, "data": {"items": list(reversed(items))}}


def ops_list_reports(limit: int = 40) -> Dict:
    files: List[Path] = []
    seen_names: set[str] = set()
    for reports_dir in _ops_reports_search_dirs():
        if not reports_dir.is_dir():
            continue
        for fp in reports_dir.glob("ops_*.md"):
            if fp.name in seen_names:
                continue
            seen_names.add(fp.name)
            files.append(fp)
    if not files:
        return {"ok": True, "data": {"reports": []}}
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[Dict[str, Any]] = []
    for fp in files[:limit]:
        try:
            st = fp.stat()
            name = fp.name
            # ops_{task_id}_{timestamp}.md
            parts = name.replace(".md", "").split("_")
            task_hint = "_".join(parts[1:-1]) if len(parts) > 2 else name
            out.append(
                {
                    "id": name,
                    "task_id": task_hint,
                    "filename": name,
                    "created_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "size_bytes": st.st_size,
                }
            )
        except Exception:
            continue
    return {"ok": True, "data": {"reports": out}}


def ops_find_report_for_task(task_id: str) -> Optional[Dict[str, Any]]:
    """按 task_id 查找最新运维报告（兼容未写入任务字段的历史失败）。"""
    tid = (task_id or "").strip()
    if not tid:
        return None
    safe = tid.replace("/", "_").replace("\\", "_")
    pattern = f"ops_{safe}_*.md"
    best: Optional[Path] = None
    for reports_dir in _ops_reports_search_dirs():
        if not reports_dir.is_dir():
            continue
        files = sorted(reports_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if files and (best is None or files[0].stat().st_mtime > best.stat().st_mtime):
            best = files[0]
    if not best:
        return None
    fp = best
    try:
        st = fp.stat()
        return {
            "id": fp.name,
            "task_id": tid,
            "filename": fp.name,
            "report_path": str(fp),
            "created_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "size_bytes": st.st_size,
        }
    except Exception:
        return None


def ops_get_report(report_id: str) -> Dict:
    safe = Path(report_id).name
    if not safe.endswith(".md") or ".." in safe:
        return {"ok": False, "error": "无效报告 ID"}
    fp: Optional[Path] = None
    for reports_dir in _ops_reports_search_dirs():
        candidate = reports_dir / safe
        if candidate.is_file():
            fp = candidate
            break
    if fp is None:
        fp = _ops_reports_dir() / safe
    if not fp.is_file():
        return {"ok": False, "error": "报告不存在"}
    try:
        content = fp.read_text(encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "data": {"id": safe, "content": content}}


def ops_get_daily_stats() -> Dict:
    """规则化 pipeline.log 统计（不调用 LLM）。"""
    analyzer = OpsLogAnalyzer(log_dir=str(_AGENT_DIR) if _AGENT_DIR else None)
    entries = analyzer.parse_log(str(_pipeline_log_path()))
    if not entries:
        return {
            "ok": True,
            "data": {
                "entry_count": 0,
                "error_by_type": {},
                "error_by_stage": {},
                "timeout_levels": [],
                "top_failed": [],
            },
        }
    error_counts = analyzer.count_errors_by_type(entries)
    stage_counts = analyzer.count_errors_by_stage(entries)
    timeout_levels = analyzer.analyze_timeout_levels(entries)
    top_failed = analyzer.get_top_failed_tasks(entries, limit=5)
    return {
        "ok": True,
        "data": {
            "entry_count": len(entries),
            "error_by_type": error_counts,
            "error_by_stage": stage_counts,
            "timeout_levels": timeout_levels,
            "top_failed": top_failed,
        },
    }


def ops_get_suggestions() -> Dict:
    """优化建议：记忆 + 日志统计 + 可观测概览。"""
    suggestions: List[str] = []
    ov = _ops_overview
    if ov["failed_calls"] > 0 and ov["total_calls"] > 0:
        rate = ov["failed_calls"] / max(ov["total_calls"], 1)
        suggestions.append(f"近期 API 失败率约 {rate:.1%}，建议检查网关节点与健康检查")
    for row in (ov.get("top_paths") or [])[:3]:
        suggestions.append(f"高频接口 {row.get('path')}（{row.get('count')} 次）可重点监控延时")

    stats = ops_get_daily_stats()
    if stats.get("ok") and stats.get("data", {}).get("entry_count"):
        data = stats["data"]
        by_type = data.get("error_by_type") or {}
        if by_type:
            top_type = max(by_type.items(), key=lambda x: x[1])
            suggestions.append(f"pipeline.log 主要错误类型：{top_type[0]}（{top_type[1]} 次）")
        top_failed = data.get("top_failed") or []
        if top_failed:
            t0 = top_failed[0]
            suggestions.append(
                f"失败最多任务：{t0.get('task_id', '')[:48]}…（{t0.get('error_count')} 次错误）"
            )

    agent = _get_ops_agent()
    if agent is not None:
        try:
            memories = agent.get_memory(limit=5)
            for m in memories:
                action = m.get("action", "")
                status = m.get("status", "")
                tid = (m.get("task_id") or "")[:32]
                if action == "monitor_task_completion" and status == "failed":
                    suggestions.append(f"最近运维记录：任务 {tid} 失败，见报告 {m.get('report', '')[-40:]}")
        except Exception:
            pass

    if not suggestions:
        suggestions.append("暂无显著异常；可在「日志 LLM 分析」对 pipeline.log 做深度诊断")
    return {"ok": True, "data": {"suggestions": suggestions[:12]}}


def ops_analyze_logs(use_llm: bool = True) -> Dict:
    """解析 pipeline.log；use_llm 时调用真实 OpsAgent.analyze_logs。"""
    analyzer = OpsLogAnalyzer(log_dir=str(_AGENT_DIR) if _AGENT_DIR else None)
    entries = analyzer.parse_log(str(_pipeline_log_path()))
    stats = ops_get_daily_stats().get("data") or {}
    if not entries:
        return {"ok": False, "error": "pipeline.log 无有效条目或文件不存在", "stats": stats}

    if not use_llm:
        try:
            report = analyzer.generate_daily_report()
        except Exception:
            report = "规则统计已生成，详见 daily_stats"
        return {"ok": True, "llm_powered": False, "analysis": report, "stats": stats}

    agent = _get_ops_agent()
    if agent is None:
        return {"ok": False, "error": "OpsAgent 未初始化", "stats": stats}
    if not (agent.api_key and agent.api_model):
        return {
            "ok": False,
            "error": "运维 LLM 未配置（请在设置中配置 API Key 与 ops 路由节点）",
            "stats": stats,
            "llm_powered": False,
        }
    analysis = agent.analyze_logs(analyzer)
    return {"ok": True, "llm_powered": True, "analysis": analysis, "stats": stats}


def ops_monitor_task(
    link: str,
    task_id: str,
    status: str,
    logs: Optional[list] = None,
    error_info: Any = None,
) -> Dict:
    """监控任务完成/失败 —— 与 video_gui._call_ops_agent_for_error 对齐。"""
    agent = _get_ops_agent()
    if agent is None:
        return {"ok": False, "error": "OpsAgent 未初始化", "llm_powered": False}
    llm_powered = bool(agent.api_key and agent.api_model) and (status or "").lower() != "completed"
    err_obj: Dict[str, Any]
    if isinstance(error_info, dict):
        err_obj = error_info
    elif error_info:
        err_obj = {"message": str(error_info)}
    else:
        err_obj = {}
    task_snap: Dict[str, Any] = {}
    tid = (task_id or "").strip()
    if tid and tid not in ("_web_log_", "_span_failure_") and not tid.startswith("log_"):
        try:
            from .task_manager import get_task

            task_snap = get_task(tid) or {}
        except Exception:
            task_snap = {}
    try:
        from .ops_error_classifier import classify_task_failure, normalize_error_info

        err_obj = normalize_error_info(
            err_obj,
            error_message=str(err_obj.get("message") or err_obj.get("error_message") or ""),
            stage=str(err_obj.get("failure_stage") or err_obj.get("step_name") or ""),
            task=task_snap,
        )
        failure_cls = classify_task_failure(error_info=err_obj, task=task_snap)
    except Exception:
        failure_cls = {}
    report_path = agent.monitor_task_completion(
        link=link,
        task_id=task_id,
        status=status,
        logs=logs or [],
        error_info=err_obj,
        failure_classification=failure_cls or None,
    )
    report_id = ""
    if report_path:
        try:
            report_id = Path(str(report_path)).name
        except Exception:
            report_id = ""
    tid = (task_id or "").strip()
    if report_id and tid and tid not in ("_web_log_", "_span_failure_") and not tid.startswith("log_"):
        try:
            from .task_manager import get_task, update_task

            if get_task(tid):
                patch: Dict[str, Any] = {
                    "ops_report_id": report_id,
                    "ops_report_path": str(report_path),
                }
                if failure_cls:
                    patch["ops_failure_code"] = failure_cls.get("error_code") or ""
                    patch["ops_failure_summary"] = (
                        f"{failure_cls.get('error_code')}: {failure_cls.get('error_message')}"
                        if failure_cls.get("error_code")
                        else str(failure_cls.get("error_message") or "")
                    )
                    patch["ops_failure_category"] = failure_cls.get("category") or ""
                update_task(tid, **patch)
                snap = get_task(tid) or {}
                if (snap.get("status") or "").lower() == "failed":
                    from .history_manager import add_or_update_task_in_history

                    add_or_update_task_in_history(dict(snap))
        except Exception as exc:
            _log.warning(
                "[OPS运维-任务报告|ops.ops_monitor_task|task:%s|Agent执行|回写] "
                "报告字段写入失败; error_type=%s; error_message=%s",
                tid[:12],
                type(exc).__name__,
                str(exc)[:200],
            )
    return {
        "ok": True,
        "llm_powered": llm_powered,
        "report_path": report_path,
        "report_id": report_id,
        "degraded": llm_powered and not report_path,
        "failure_classification": failure_cls or {},
    }


def ops_route_action(action: str, payload: Optional[Dict] = None) -> Dict:
    payload = payload or {}
    node_id = (payload.get("node_id") or "").strip()
    path = f"/api/ops/route/{action}"
    ops_add_event("POST", path, 200, 0)
    agent = _get_ops_agent()
    if agent is not None:
        try:
            agent.add_memory(
                {
                    "action": f"route_{action}",
                    "node_id": node_id,
                    "payload": payload,
                }
            )
        except Exception:
            pass
    messages = {
        "mark-failed": "已记录故障标记；请在「设置 → 网关与路由」调整 ops 节点或切换 endpoint",
        "reconfigure": "已记录重配请求；Web 端请通过设置页修改 api_gateway_nodes / agent_route_rules",
        "rollback-last": "已记录回滚请求；请手动恢复上一版 config.json 或网关节点优先级",
    }
    return {"ok": True, "message": messages.get(action, "已记录"), "recorded": True}


def ops_list_span_tasks(*, limit: int = 80) -> Dict[str, Any]:
    from .span_audit import list_pipeline_span_tasks
    from .history_manager import _public_span_task

    tasks = list_pipeline_span_tasks(limit=limit)
    return {"ok": True, "tasks": [_public_span_task(t) for t in tasks], "total": len(tasks)}


def ops_get_span_task_detail(task_id: str) -> Dict[str, Any]:
    from .history_manager import build_task_log_bundle

    return build_task_log_bundle(task_id)


def ops_list_span_exceptions(*, limit: int = 100) -> Dict[str, Any]:
    from .span_audit import list_exception_steps
    from .history_manager import _public_span_step

    items = list_exception_steps(limit=limit)
    return {"ok": True, "items": [_public_span_step(s) for s in items], "total": len(items)}


def ops_get_dashboard() -> Dict:
    """OPS 聚合面板：Agent 状态 + 可观测 + 日志统计 + 平台健康快照 + Eval。"""
    status = ops_get_status().get("data") or {}
    overview = ops_get_overview().get("data") or {}
    daily = ops_get_daily_stats().get("data") or {}
    suggestions = ops_get_suggestions().get("data", {}).get("suggestions") or []
    hooks = {
        "ops_async_check_enabled": bool(load_config().get("ops_async_check_enabled", True)),
        "log_incident_enabled": os.environ.get("OPS_LOG_INCIDENT_DISABLE", "").strip().lower()
        not in ("1", "true", "yes"),
    }
    platform_health: Dict[str, Any] = {"ready": False}
    try:
        from .platform_health import get_platform_health_snapshot

        platform_health = get_platform_health_snapshot() or {"ready": False}
    except Exception:
        pass
    eval_block: Dict[str, Any] = {}
    try:
        from app.eval.ops_service import eval_get_overview

        ov = eval_get_overview()
        if ov.get("ok"):
            eval_block = ov.get("data") or {}
    except Exception:
        try:
            from app.eval.tracing import eval_tracing_status

            eval_block = eval_tracing_status()
        except Exception:
            eval_block = {"error": "eval 模块不可用"}
    return {
        "ok": True,
        "data": {
            "status": status,
            "overview": overview,
            "daily": daily,
            "suggestions": suggestions,
            "hooks": hooks,
            "platform_health": platform_health,
            "eval": eval_block,
        },
    }


_scheduled_job_events: List[Dict[str, Any]] = []


def ops_add_scheduled_job_event(
    *,
    job_key: str,
    run_id: str,
    trigger: str,
    status: str,
    summary: str,
    duration_ms: int,
    error_message: str = "",
) -> Dict[str, Any]:
    evt = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "job_key": job_key,
        "run_id": run_id,
        "trigger": trigger,
        "status": status,
        "summary": (summary or "")[:500],
        "duration_ms": int(duration_ms or 0),
        "error_message": (error_message or "")[:2000],
    }
    _scheduled_job_events.append(evt)
    if len(_scheduled_job_events) > 2000:
        _scheduled_job_events[:] = _scheduled_job_events[-1000:]
    ops_add_event(
        "JOB",
        f"/internal/scheduled-job/{job_key}",
        200 if status == "completed" else 500,
        duration_ms,
        error_detail=error_message or "",
        response_brief=summary,
    )
    return evt


def ops_get_scheduled_job_events(limit: int = 100) -> Dict[str, Any]:
    lim = max(1, min(500, int(limit or 100)))
    items = list(reversed(_scheduled_job_events[-lim:]))
    return {"ok": True, "items": items, "count": len(items)}
