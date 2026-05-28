"""运维 Agent Hook —— 对齐 video_gui 日志/飞书/主备切换上报（去重 + 异步）。"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import load_config

_log = logging.getLogger("sba.ops.hooks")

_incident_lock = threading.Lock()
_incident_last_ts: Dict[str, float] = {}
_INCIDENT_COOLDOWN_SEC = 300.0


def _agent_dir() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        cand = p / "src" / "agent"
        if cand.is_dir():
            return cand.resolve()
    return here.parents[2] / "src" / "agent"


def _fingerprint(category: str, text: str) -> str:
    raw = f"{category}|{(text or '')[:500]}"
    return hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()


def _incident_allow(fp: str) -> bool:
    now = time.monotonic()
    with _incident_lock:
        last = _incident_last_ts.get(fp, 0.0)
        if now - last < _INCIDENT_COOLDOWN_SEC:
            return False
        _incident_last_ts[fp] = now
    return True


def ops_async_hook_allowed(hook_name: str) -> bool:
    cfg = load_config()
    if not bool(cfg.get("ops_async_check_enabled", True)):
        return False
    wl = cfg.get("ops_async_hook_whitelist", ["*"])
    if not isinstance(wl, list):
        return True
    items = [str(x).strip() for x in wl if str(x).strip()]
    return (not items) or ("*" in items) or (str(hook_name or "").strip() in set(items))


def _should_forward_log(msg: str, level: str) -> bool:
    if os.environ.get("OPS_LOG_INCIDENT_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return False
    if "[运维Agent]" in (msg or "") or "运维事件已记录" in (msg or ""):
        return False
    return (level or "").upper() in ("ERROR", "EXCEPTION")


def _collect_logs(task_id: Optional[str] = None, max_lines: int = 120) -> List[str]:
    lines: List[str] = []
    if task_id:
        try:
            from .task_manager import get_task

            task = get_task(task_id)
            if task:
                for row in (task.get("logs") or [])[-max_lines:]:
                    ts = row.get("timestamp", "")
                    lvl = row.get("level", "INFO")
                    msg = row.get("message", "")
                    lines.append(f"[{ts}] {lvl}: {msg}")
        except Exception:
            pass
    if lines:
        return lines
    log_path = _agent_dir() / "pipeline.log"
    if not log_path.is_file():
        return []
    try:
        raw = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return raw[-max_lines:] if len(raw) > max_lines else raw
    except Exception:
        return []


def _run_async(fn, *args, **kwargs) -> None:
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


def ops_dispatch_log_incident(msg: str, level: str, *, task_id: Optional[str] = None) -> None:
    """ERROR/EXCEPTION 日志上报运维 Agent（去重冷却）。"""
    if not _should_forward_log(msg, level):
        return
    fp = _fingerprint(f"log_{level}", msg)
    if not _incident_allow(fp):
        return

    def _worker() -> None:
        try:
            from .ops import ops_monitor_task

            logs = _collect_logs(task_id)
            err = {"type": f"Log{level}", "message": (msg or "")[:4000], "traceback": ""}
            tid = task_id or f"log_{fp[:10]}"
            res = ops_monitor_task(
                link="_web_log_",
                task_id=tid,
                status="failed",
                logs=logs,
                error_info=err,
            )
            if res.get("ok") and res.get("report_path"):
                _log.info(
                    "[OPS运维-日志事件|ops_hooks.ops_dispatch_log_incident|log|Agent执行|完成] "
                    "已生成运维报告; task_id=%s; report=%s",
                    tid,
                    str(res.get("report_path", ""))[-60:],
                )
        except Exception as exc:
            _log.warning(
                "[OPS运维-日志事件|ops_hooks.ops_dispatch_log_incident|log|Agent执行|失败] "
                "上报失败; error_type=%s; error_message=%s",
                type(exc).__name__,
                str(exc)[:200],
            )

    _run_async(_worker)


def ops_dispatch_feishu_check(
    *,
    link: str,
    task_id: str,
    doc_url: str,
    verify_result: Dict[str, Any],
) -> None:
    """飞书上传后异步运维检查（非阻塞）。"""
    if not ops_async_hook_allowed("ops_async_review"):
        return

    def _worker() -> None:
        try:
            from .ops import ops_monitor_task

            vr = verify_result or {}
            logs = [
                f"feishu_doc_url={doc_url}",
                f"verify_ok={bool(vr.get('ok'))}",
                f"verify_reason={vr.get('reason', '')}",
                f"remote_length={vr.get('remote_length', 0)}",
                f"expected_length={vr.get('expected_length', 0)}",
            ]
            status = "completed" if bool(vr.get("ok")) else "failed"
            err: Dict[str, Any] = {} if bool(vr.get("ok")) else {"message": str(vr)}
            ops_monitor_task(
                link=link or "",
                task_id=task_id or f"feishu_check_{int(time.time())}",
                status=status,
                logs=logs,
                error_info=err,
            )
            _log.info(
                "[链接沉淀文档-飞书上传-传后校验|ops_hooks.ops_dispatch_feishu_check|feishu|Agent执行|完成] "
                "飞书异步检查已上报; ok=%s; task_id=%s",
                bool(vr.get("ok")),
                task_id,
            )
        except Exception as exc:
            _log.warning(
                "[链接沉淀文档-飞书上传-传后校验|ops_hooks.ops_dispatch_feishu_check|feishu|Agent执行|失败] "
                "飞书异步检查失败; error_message=%s",
                str(exc)[:200],
            )

    _run_async(_worker)


def ops_dispatch_volcengine_degraded(primary_err: str, primary_ep: str, backup_ep: str) -> None:
    """主接入点失败、备用成功时上报运维 Agent。"""
    msg = (
        f"火山主接入点失败但备用成功。主: {primary_ep} 错误: {(primary_err or '')[:800]}；"
        f"已用备: {backup_ep}。建议：检查控制台 Safe Experience/限额，或把 config.json 中 "
        f"ai_chat_model 与 ai_chat_model_backup 对调。"
    )
    fp = _fingerprint("volcengine_degraded", (primary_err or "") + (primary_ep or ""))
    if not _incident_allow(fp):
        return

    def _worker() -> None:
        try:
            from .ops import ops_monitor_task

            err = {
                "type": "VolcenginePrimaryFailedBackupOk",
                "message": msg,
                "traceback": "",
            }
            ops_monitor_task(
                link="_volcengine_",
                task_id=f"ve_{fp[:10]}",
                status="failed",
                logs=_collect_logs(),
                error_info=err,
            )
            _log.info(
                "[OPS运维-主备切换|ops_hooks.ops_dispatch_volcengine_degraded|endpoint|Agent执行|完成] "
                "主备切换事件已记录; primary=%s; backup=%s",
                (primary_ep or "")[:24],
                (backup_ep or "")[:24],
            )
        except Exception as exc:
            _log.warning(
                "[OPS运维-主备切换|ops_hooks.ops_dispatch_volcengine_degraded|endpoint|Agent执行|失败] "
                "上报失败; error_message=%s",
                str(exc)[:200],
            )

    _run_async(_worker)
