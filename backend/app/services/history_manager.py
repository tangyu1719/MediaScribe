"""历史记录管理 —— 统一管理任务历史，确保相同链接只保留最新记录"""
from __future__ import annotations
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from .link_hash import normalize_link_for_hash, url_hash as link_url_hash, links_same_identity, extract_link_fields
from .config import runtime_agent_dir

_RUNTIME_AGENT_DIR = runtime_agent_dir()
_HISTORY_PATH = _RUNTIME_AGENT_DIR / "history.json"

_log = logging.getLogger("sba.history_manager")
_MAX_HISTORY_LOG_LINES = 800


def _db_enabled() -> bool:
    try:
        from . import pipeline_history_store as _phs

        return _phs.is_enabled()
    except Exception:
        return False


def _db_store():
    from . import pipeline_history_store as _phs

    return _phs

_LEGACY_STAGE_LABELS = {
    "download": "下载",
    "transcribe": "转写",
    "ai_analysis": "AI分析",
    "generate_md": "生成MD",
    "feishu_upload": "飞书上传",
}


def _platform_from_link(link: str) -> str:
    low = (link or "").lower()
    if "xiaohongshu" in low or "xhslink" in low:
        return "小红书"
    if "douyin" in low:
        return "抖音"
    if "bilibili" in low or "b23.tv" in low:
        return "B站"
    return "其他"


def _legacy_md_from_task(task: Dict[str, Any]) -> str:
    stages = task.get("stages")
    if not isinstance(stages, dict):
        return ""
    gen = stages.get("generate_md")
    if not isinstance(gen, dict):
        return ""
    res = gen.get("result")
    if isinstance(res, str) and res.strip():
        return res.strip()
    return ""


def _compute_history_progress(task: Dict[str, Any]) -> int:
    try:
        cur = int(task.get("progress") or 0)
    except (TypeError, ValueError):
        cur = 0
    if task.get("status") in ("failed", "cancelled"):
        return max(0, min(100, cur))
    doc_ref = (task.get("doc_path") or task.get("doc_filename") or _legacy_md_from_task(task) or "").strip()
    if doc_ref and _resolve_doc_path(doc_ref).is_file():
        return 100 if task.get("status") == "completed" else max(cur, 90)
    stages = task.get("pipeline_stages") or task.get("stages") or {}
    if not isinstance(stages, dict) or not stages:
        if task.get("status") == "completed":
            return 100 if doc_ref else cur
        return cur
    total = len(stages)
    done = 0
    for row in stages.values():
        if not isinstance(row, dict):
            continue
        st = str(row.get("status") or "").lower()
        if st in ("completed", "ok", "done"):
            done += 1
    if not total:
        return cur
    pct = int(done * 100 / total)
    if task.get("status") == "completed":
        return 100 if done >= total and doc_ref else max(pct, cur)
    return max(cur, pct)


def _history_needs_persist(orig: Dict[str, Any], fixed: Dict[str, Any]) -> bool:
    for k in ("doc_path", "doc_filename", "pipeline_stages", "progress", "link_title", "platform", "html_path", "html_status"):
        if orig.get(k) != fixed.get(k):
            return True
    return False


def normalize_history_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """兼容 legacy history.json（stages 字段）→ 前端与流水线统一字段。"""
    out = dict(task)
    if not out.get("link_title") and out.get("title"):
        out["link_title"] = out["title"]
    if not out.get("doc_title") and out.get("title"):
        out["doc_title"] = out.get("title")
    if not out.get("platform"):
        out["platform"] = _platform_from_link(out.get("link", ""))
    legacy = out.get("stages")
    if isinstance(legacy, dict) and not out.get("pipeline_stages"):
        ps: Dict[str, Dict[str, Any]] = {}
        for sid, row in legacy.items():
            if isinstance(row, dict):
                ps[sid] = {
                    "status": row.get("status", "pending"),
                    "label": _LEGACY_STAGE_LABELS.get(sid, sid),
                }
        out["pipeline_stages"] = ps
    if not (out.get("doc_path") or out.get("doc_filename")):
        md = _legacy_md_from_task(out)
        if md:
            out["doc_path"] = md
            out["doc_filename"] = Path(md).name
    out["progress"] = _compute_history_progress(out)
    return sync_html_artifact_for_task(out)


def _resolve_doc_path(md_ref: str) -> Path:
    """将历史中的 doc_path / doc_filename 解析为本地 Path。"""
    ref = (md_ref or "").strip()
    if not ref:
        return Path()
    p = Path(ref)
    if p.is_file():
        return p.resolve()
    try:
        from .task_manager import OUTPUT_DIR

        cand = OUTPUT_DIR / p.name
        if cand.is_file():
            return cand.resolve()
    except Exception:
        pass
    return p


def sync_html_artifact_for_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """若磁盘已有 longpage.html 而历史仍为 async_pending，则回填 html_path。"""
    out = dict(task)
    if (out.get("html_path") or "").strip():
        if out.get("html_status") in ("async_pending", "pending", "running", ""):
            out["html_status"] = "completed"
            out["html_message"] = out.get("html_message") or "OK"
        return out
    md_ref = (out.get("doc_path") or out.get("doc_filename") or "").strip()
    if not md_ref:
        return out
    p = _resolve_doc_path(md_ref)
    if not p.is_file():
        return out
    for suf in (".longpage.html", ".html"):
        cand = p.parent / f"{p.stem}{suf}"
        if cand.is_file():
            out["html_path"] = str(cand.resolve())
            out["html_status"] = "completed"
            out["html_message"] = "OK"
            break
    return out


def _load_history() -> Dict:
    """加载历史记录（MariaDB 优先，否则 history.json）。"""
    if _db_enabled():
        store = _db_store()
        store.get_engine()
        return {"tasks": store.list_tasks(limit=10000)}
    if _HISTORY_PATH.exists():
        try:
            return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tasks": []}


def _save_history(h: Dict):
    """保存历史记录（MariaDB 时逐条 upsert，并写 JSON 备份）。"""
    tasks = h.get("tasks") or []
    if _db_enabled():
        store = _db_store()
        for t in tasks:
            if isinstance(t, dict) and (t.get("id") or t.get("task_id")):
                store.upsert_task(t)
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_PATH.write_text(
            json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as ex:
        if not _db_enabled():
            raise
        _log.warning(
            "[链接沉淀-历史|history_manager._save_history|history.json|硬编执行|跳过] "
            "json_backup_failed; error=%s",
            ex,
        )


def _find_history_index(tasks: List[Dict], *, link: str = "", url_hash: str = "") -> Optional[int]:
    """按稳定内容 id 查找历史记录（忽略 xsec_token 等差异）。"""
    target_hash = (url_hash or "").strip() or (link_url_hash(link) if link else "")
    for idx, t in enumerate(tasks):
        tlink = t.get("link") or ""
        if target_hash and link_url_hash(tlink) == target_hash:
            return idx
        if link and links_same_identity(tlink, link):
            return idx
    return None


def add_or_update_task_in_history(task_data: Dict[str, Any]):
    """
    添加或更新任务到历史记录。
    相同内容链接（稳定 hash）只保留一条，沿用原 id。
    """
    h = _load_history()
    tasks = h.get("tasks", [])
    
    link = task_data.get("link", "")
    task_id = task_data.get("task_id", "")
    url_hash = task_data.get("url_hash") or link_url_hash(link)
    task_data = dict(task_data)
    task_data["url_hash"] = url_hash
    task_data["normalized_link"] = task_data.get("normalized_link") or normalize_link_for_hash(link)
    
    existing_idx = _find_history_index(tasks, link=link, url_hash=url_hash)
    
    # 准备历史记录数据
    history_entry = {
        "id": task_id,
        "link": link,
        "url_hash": url_hash,
        "normalized_link": task_data.get("normalized_link", ""),
        "platform": task_data.get("platform", ""),
        "status": task_data.get("status", ""),
        "stage": task_data.get("stage", ""),
        "progress": task_data.get("progress", 0),
        "title": task_data.get("doc_title") or task_data.get("link_title") or task_data.get("title", ""),
        "link_title": task_data.get("link_title", ""),
        "doc_title": task_data.get("doc_title", ""),
        "content_type": task_data.get("content_type", ""),
        "cover_url": task_data.get("cover_url", ""),
        "route_type": task_data.get("route_type", ""),
        "pipeline_route": task_data.get("pipeline_route", "") or task_data.get("route_type", ""),
        "pipeline_stages": task_data.get("pipeline_stages") or {},
        "failed_stage": task_data.get("failed_stage", ""),
        "failed_stage_label": task_data.get("failed_stage_label", ""),
        "resume_from": task_data.get("resume_from", ""),
        "resume_context": task_data.get("resume_context") or {},
        "user_prompt": task_data.get("user_prompt", ""),
        "comments": task_data.get("comments"),
        "transcribe_error_code": task_data.get("transcribe_error_code", ""),
        "ops_failure_code": task_data.get("ops_failure_code", ""),
        "ops_failure_summary": task_data.get("ops_failure_summary", ""),
        "ops_failure_category": task_data.get("ops_failure_category", ""),
        "ops_report_id": task_data.get("ops_report_id", ""),
        "ops_report_path": task_data.get("ops_report_path", ""),
        "doc_filename": task_data.get("doc_filename"),
        "doc_path": task_data.get("doc_path"),
        "html_path": task_data.get("html_path"),
        "html_status": task_data.get("html_status", ""),
        "html_message": task_data.get("html_message", ""),
        "feishu_status": task_data.get("feishu_status", ""),
        "feishu_message": task_data.get("feishu_message", ""),
        "feishu_doc_url": task_data.get("feishu_doc_url"),
        "feishu_doc_token": task_data.get("feishu_doc_token"),
        "total_duration_ms": int(task_data.get("total_duration_ms") or 0),
        "total_token_count": int(task_data.get("total_token_count") or 0),
        "article_char_count": int(task_data.get("article_char_count") or 0),
        "summary_char_count": int(task_data.get("summary_char_count") or 0),
        "pipeline_started_at": task_data.get("pipeline_started_at", ""),
        "md_completed_at": task_data.get("md_completed_at", ""),
        "error": task_data.get("error"),
        "logs": _merged_logs_for_history(task_id, task_data),
        "created_at": task_data.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
    }
    history_entry = sync_html_artifact_for_task(history_entry)
    
    if existing_idx is not None:
        # 更新现有记录（保留创建时间与原 id，避免断点恢复换卡片）
        prev = tasks[existing_idx]
        history_entry["created_at"] = prev.get("created_at", history_entry["created_at"])
        history_entry["id"] = prev.get("id") or task_id
        tasks[existing_idx] = history_entry
    else:
        # 添加新记录
        tasks.append(history_entry)
    
    # 按更新时间倒序排列
    tasks.sort(key=lambda x: x.get("updated_at", x.get("created_at", "")), reverse=True)
    
    h["tasks"] = tasks
    _save_history(h)
    if _db_enabled():
        _db_store().upsert_task(history_entry)


def get_task_history(link: str = None, url_hash: str = None) -> Optional[Dict]:
    """获取特定链接的历史记录（按稳定内容 hash；并支持标准字段的宽松匹配）。"""
    if _db_enabled():
        row = _db_store().get_by_link_or_hash(link=link or "", url_hash=url_hash or "")
        return normalize_history_task(row) if row else None
    h = _load_history()
    tasks = h.get("tasks", [])
    target_hash = (url_hash or "").strip() or (link_url_hash(link) if link else "")
    target_fields = extract_link_fields(link or "") if link else {}
    best = None
    for t in tasks:
        tlink = t.get("link") or ""
        if target_hash and link_url_hash(tlink) == target_hash:
            if best is None or (t.get("updated_at") or "") >= (best.get("updated_at") or ""):
                best = t
            continue
        if link and links_same_identity(tlink, link):
            if best is None or (t.get("updated_at") or "") >= (best.get("updated_at") or ""):
                best = t
            continue
        if target_fields:
            t_fields = extract_link_fields(tlink)
            if any(str(t_fields.get(k) or "").strip() == str(v).strip() for k, v in target_fields.items() if v):
                if best is None or (t.get("updated_at") or "") >= (best.get("updated_at") or ""):
                    best = t
    return best


def get_history_task_by_id(task_id: str) -> Optional[Dict]:
    """按 task id 获取单条历史记录（MariaDB 或 history.json）。"""
    tid = (task_id or "").strip()
    if not tid:
        return None
    if _db_enabled():
        row = _db_store().get_by_task_id(tid)
        return normalize_history_task(dict(row)) if row else None
    for t in _load_history().get("tasks", []):
        if (t.get("id") or "") == tid:
            return normalize_history_task(dict(t))
    return None


def consolidate_history_by_url_hash() -> int:
    """历史列表按 url_hash 去重，只保留最近一条（避免同链接多张卡片）。"""
    if _db_enabled():
        return _db_store().consolidate_by_url_hash()
    h = _load_history()
    raw = list(h.get("tasks", []))
    by_hash: Dict[str, List[Dict[str, Any]]] = {}
    orphans: List[Dict[str, Any]] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        link = t.get("link") or ""
        uh = (t.get("url_hash") or "").strip() or link_url_hash(link)
        if not uh:
            orphans.append(t)
            continue
        by_hash.setdefault(uh, []).append(t)
    if all(len(v) <= 1 for v in by_hash.values()) and not orphans:
        return 0
    merged: List[Dict[str, Any]] = list(orphans)
    removed = 0
    for rows in by_hash.values():
        rows.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "")
        merged.append(rows[-1])
        removed += max(0, len(rows) - 1)
    if removed:
        h["tasks"] = merged
        _save_history(h)
    return removed


def list_history_tasks(limit: int = 100, *, repair_html: bool = True, persist_normalize: bool = True) -> List[Dict]:
    """列出历史记录任务；修复 legacy 字段、HTML 产物，必要时写回库表 / history.json。"""
    consolidate_history_by_url_hash()
    if _db_enabled():
        raw = _db_store().list_tasks(limit=max(limit, 400))
        tasks: List[Dict[str, Any]] = []
        dirty = False
        for t in raw:
            if not isinstance(t, dict):
                continue
            fixed = normalize_history_task(t) if repair_html else dict(t)
            if persist_normalize and _history_needs_persist(t, fixed):
                dirty = True
            tasks.append(fixed)
        if dirty and persist_normalize:
            for t in tasks:
                _db_store().upsert_task(t)
        return tasks[:limit]
    h = _load_history()
    raw = list(h.get("tasks", []))
    tasks: List[Dict[str, Any]] = []
    dirty = False
    for t in raw:
        if not isinstance(t, dict):
            continue
        fixed = normalize_history_task(t) if repair_html else dict(t)
        if persist_normalize and _history_needs_persist(t, fixed):
            dirty = True
        tasks.append(fixed)
    if dirty and persist_normalize:
        h["tasks"] = tasks
        _save_history(h)
    return tasks[:limit]


def _merged_logs_for_history(task_id: str, task_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """任务写入历史时即固化一份可展示日志，优先供历史查看直取。"""
    try:
        from .pipeline_task_logs import merge_task_logs

        return merge_task_logs(
            task_id,
            memory_logs=task_data.get("logs"),
            history_logs=None,
        )[-_MAX_HISTORY_LOG_LINES:]
    except Exception:
        return list((task_data.get("logs") or [])[-_MAX_HISTORY_LOG_LINES:])


def get_history_logs(task_id: str) -> Optional[List[Dict[str, Any]]]:
    """按 task id 读取已持久化的历史日志。优先直取固化日志，缺失时再做多源合并。"""
    tid = (task_id or "").strip()
    if not tid:
        return None
    hist_rows = None
    status = ""
    if _db_enabled():
        row = _db_store().get_by_task_id(tid)
        if row:
            hist_rows = list(row.get("logs") or [])
            status = str(row.get("status") or "")
    if hist_rows is None:
        for t in _load_history().get("tasks", []):
            if t.get("id") == tid:
                hist_rows = list(t.get("logs") or [])
                status = str(t.get("status") or "")
                break
    if hist_rows is not None and status in ("completed", "failed", "cancelled"):
        return hist_rows
    try:
        from .pipeline_task_logs import merge_task_logs

        merged = merge_task_logs(tid, history_logs=hist_rows)
        return merged if merged or hist_rows is not None else None
    except Exception:
        return hist_rows


def build_task_log_bundle(task_id: str) -> Dict[str, Any]:
    """历史/OPS 共用的任务日志包：操作日志 + SPAN + 异常。"""
    tid = (task_id or "").strip()
    from .task_manager import get_task as _mem_task
    from .pipeline_task_logs import merge_task_logs, extract_error_logs

    mem = _mem_task(tid)
    hist = None
    link = ""
    title = ""
    platform = ""
    status = ""
    if _db_enabled():
        hist = _db_store().get_by_task_id(tid)
        if hist:
            link = hist.get("link") or ""
            title = hist.get("link_title") or hist.get("title") or ""
            platform = hist.get("platform") or ""
            status = hist.get("status") or ""
    if not hist:
        for t in _load_history().get("tasks", []):
            if t.get("id") == tid:
                hist = t
                link = t.get("link") or ""
                title = t.get("link_title") or t.get("title") or ""
                platform = t.get("platform") or ""
                status = t.get("status") or ""
                break
    if mem:
        link = link or mem.get("link") or ""
        title = title or mem.get("link_title") or mem.get("doc_title") or ""
        platform = platform or mem.get("platform") or ""
        status = status or mem.get("status") or ""

    history_logs = (hist or {}).get("logs") if hist else None
    terminal_status = {"completed", "failed", "cancelled"}
    if history_logs and status in terminal_status and not mem:
        text_logs = list(history_logs)
    else:
        text_logs = merge_task_logs(
            tid,
            memory_logs=(mem or {}).get("logs"),
            history_logs=history_logs,
        )
    errors = extract_error_logs(text_logs)

    span_task = None
    spans: List[Dict[str, Any]] = []
    try:
        from .span_audit import get_task as _span_get, list_exception_steps_for_task

        span_task = _span_get(tid)
        if span_task:
            spans = list(span_task.get("steps") or [])
        errors_span = list_exception_steps_for_task(tid)
        for s in errors_span:
            errors.append(
                {
                    "timestamp": (s.get("ended_at") or s.get("updated_at") or "")[-8:],
                    "level": "ERROR",
                    "message": f"[SPAN] {s.get('step_name')}: {s.get('error_message') or s.get('status')}",
                    "extra": {"step_id": s.get("step_id"), "step_type": s.get("step_type")},
                }
            )
    except Exception:
        pass

    source = "memory" if mem else ("history" if hist else "file")
    if mem and (hist or text_logs):
        source = "merged"
    found = bool(mem or hist or text_logs or span_task)

    return {
        "ok": found,
        "task_id": tid,
        "source": source,
        "link": link,
        "title": title,
        "platform": platform,
        "status": status,
        "text_logs": text_logs,
        "errors": errors,
        "span_task": _public_span_task(span_task) if span_task else None,
        "spans": [_public_span_step(s) for s in spans],
        "log_count": len(text_logs),
    }


def _public_span_task(t: Dict[str, Any]) -> Dict[str, Any]:
    if not t:
        return {}
    return {
        "task_id": t.get("task_id"),
        "session_id": t.get("session_id"),
        "user_query": (t.get("user_query") or "")[:500],
        "status": t.get("status"),
        "total_duration_ms": t.get("total_duration_ms"),
        "total_token_count": t.get("total_token_count"),
        "total_steps": t.get("total_steps"),
        "completed_steps": t.get("completed_steps"),
        "failed_steps": t.get("failed_steps"),
        "started_at": t.get("started_at"),
        "ended_at": t.get("ended_at"),
    }


def _public_span_step(s: Dict[str, Any]) -> Dict[str, Any]:
    inp = s.get("input_payload")
    out = s.get("output_payload")
    tool_io = s.get("tool_io_brief")
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except Exception:
            pass
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except Exception:
            pass
    if isinstance(tool_io, str):
        try:
            tool_io = json.loads(tool_io)
        except Exception:
            pass
    return {
        "task_id": s.get("task_id"),
        "step_id": s.get("step_id"),
        "step_type": s.get("step_type"),
        "step_name": s.get("step_name"),
        "status": s.get("status"),
        "started_at": s.get("started_at"),
        "ended_at": s.get("ended_at"),
        "duration_ms": s.get("duration_ms"),
        "token_count": s.get("token_count"),
        "confidence": s.get("confidence"),
        "error_code": s.get("error_code"),
        "error_message": s.get("error_message"),
        "input_payload": inp,
        "output_payload": out,
        "tool_io_brief": tool_io if isinstance(tool_io, dict) else {},
        "decision": s.get("decision"),
        "session_id": s.get("session_id"),
    }


def delete_history_task(link: str = None, url_hash: str = None) -> bool:
    """删除历史记录中的任务，同时标记为已 dismiss 防止重启后回填。"""
    from .task_manager import dismiss_queue_task_by_url_hash

    uh = (url_hash or "").strip()
    if not uh and link:
        from .link_hash import link_url_hash
        uh = link_url_hash(link)
    if uh:
        try:
            dismiss_queue_task_by_url_hash(uh)
        except Exception:
            pass

    if _db_enabled():
        return _db_store().delete_by_link_or_hash(link=link or "", url_hash=url_hash or "")
    h = _load_history()
    tasks = h.get("tasks", [])

    original_len = len(tasks)
    tasks = [
        t
        for t in tasks
        if not (
            (link and links_same_identity(t.get("link") or "", link))
            or (url_hash and t.get("url_hash") == url_hash)
        )
    ]

    if len(tasks) < original_len:
        h["tasks"] = tasks
        _save_history(h)
        return True
    return False


def clear_completed_history() -> int:
    """清理所有已完成的历史记录"""
    if _db_enabled():
        return _db_store().clear_completed()
    h = _load_history()
    tasks = h.get("tasks", [])

    original_len = len(tasks)
    tasks = [t for t in tasks if t.get("status") != "completed"]

    removed = original_len - len(tasks)
    if removed > 0:
        h["tasks"] = tasks
        _save_history(h)
    return removed


def init_history_persistence() -> Dict[str, Any]:
    """启动时建表并从 history.json 导入（库为空时）。"""
    if not _db_enabled():
        return {"enabled": False, "driver": "json", "path": str(_HISTORY_PATH)}
    store = _db_store()
    store.get_engine()
    imported = store.ensure_migrated_from_json_file(_HISTORY_PATH)
    return {
        "enabled": True,
        "table": "pipeline_task_history",
        "imported_from_json": imported,
        "count": store.count_tasks(),
    }


def _task_merge_key(task: Dict[str, Any]) -> str:
    from .link_hash import normalize_link_for_hash

    link = (task.get("link") or "").strip()
    if link:
        return "link:" + normalize_link_for_hash(link)
    uh = (task.get("url_hash") or "").strip()
    if uh:
        return "hash:" + uh
    tid = (task.get("id") or task.get("task_id") or "").strip()
    return "id:" + tid if tid else ""


def _task_merge_rank(task: Dict[str, Any]) -> tuple:
    """合并时择优：有产物 > 更新时间 > 字段完整度。"""
    md = bool(
        (task.get("doc_path") or task.get("doc_filename") or _legacy_md_from_task(task) or "").strip()
    )
    html = bool((task.get("html_path") or "").strip())
    updated = str(task.get("updated_at") or task.get("created_at") or "")
    fields = sum(
        1
        for k in ("link", "title", "stages", "pipeline_stages", "logs", "platform")
        if task.get(k)
    )
    return (1 if md else 0, 1 if html else 0, updated, fields)


def _pick_richer_task(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    return b if _task_merge_rank(b) > _task_merge_rank(a) else a


def discover_history_backup_paths() -> List[Path]:
    """扫描仓库内可合并的历史备份（不含当前主 history.json）。"""
    found: List[Path] = []
    seen: set = set()
    main = _HISTORY_PATH.resolve()

    def _add(p: Path) -> None:
        try:
            rp = p.resolve()
        except Exception:
            return
        if not rp.is_file() or rp in seen or rp == main:
            return
        seen.add(rp)
        found.append(rp)

    if _AGENT_DIR:
        agent_root = _AGENT_DIR.parents[2] if len(_AGENT_DIR.parents) > 2 else _AGENT_DIR.parent
        demo_root = _AGENT_DIR.parents[3] if len(_AGENT_DIR.parents) > 3 else None
        candidates = [
            agent_root / "web_migration" / "backend" / "agent" / "history.json",
            agent_root / "history.json",
        ]
        if demo_root:
            candidates.insert(0, demo_root / "history.json")
        if demo_root:
            candidates.insert(0, demo_root / "history.json")
        for c in candidates:
            if c:
                _add(c)
    return found


def restore_history_from_backups(
    *,
    extra_paths: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    从仓库内备份 history.json 合并恢复主历史（按链接去重，保留更完整记录）。
    会先备份当前主文件为 history.json.bak.<时间戳>。
    """
    import shutil

    before = _load_history()
    before_tasks = list(before.get("tasks") or [])
    before_n = len(before_tasks)

    sources: List[Path] = discover_history_backup_paths()
    for raw in extra_paths or []:
        p = Path(str(raw).strip())
        if p.is_file():
            sources.append(p.resolve())

    merged: Dict[str, Dict[str, Any]] = {}
    for t in before_tasks:
        if not isinstance(t, dict):
            continue
        key = _task_merge_key(t)
        if key:
            merged[key] = dict(t)

    per_source: List[Dict[str, Any]] = []
    imported = 0
    updated = 0
    for src in sources:
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as ex:
            per_source.append({"path": str(src), "ok": False, "error": str(ex)[:200]})
            continue
        rows = [x for x in (data.get("tasks") or []) if isinstance(x, dict)]
        add_n = 0
        upd_n = 0
        for row in rows:
            key = _task_merge_key(row)
            if not key:
                continue
            if key in merged:
                prev = merged[key]
                richer = _pick_richer_task(prev, row)
                if richer is not prev:
                    merged[key] = dict(richer)
                    upd_n += 1
                else:
                    merged[key] = dict(prev)
            else:
                merged[key] = dict(row)
                add_n += 1
        imported += add_n
        updated += upd_n
        per_source.append(
            {
                "path": str(src),
                "ok": True,
                "tasks_in_file": len(rows),
                "added": add_n,
                "updated": upd_n,
            }
        )

    out_tasks = [normalize_history_task(t) for t in merged.values()]
    out_tasks.sort(key=lambda x: x.get("updated_at", x.get("created_at", "")), reverse=True)

    result = {
        "ok": True,
        "dry_run": dry_run,
        "main_path": str(_HISTORY_PATH),
        "before_count": before_n,
        "after_count": len(out_tasks),
        "added": max(0, len(out_tasks) - before_n),
        "merged_updates": updated,
        "sources": per_source,
    }

    if dry_run:
        return result

    if _HISTORY_PATH.exists() and before_n > 0:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = _HISTORY_PATH.with_name(f"history.json.bak.{stamp}")
        shutil.copy2(_HISTORY_PATH, bak)
        result["backup_path"] = str(bak)

    _save_history({"tasks": out_tasks})
    result["restored"] = True
    _log.info(
        "[链接沉淀-历史恢复|history_manager.restore_history_from_backups|history.json|硬编执行|完成] "
        "before=%s; after=%s; added=%s; updated=%s",
        before_n,
        len(out_tasks),
        result.get("added"),
        updated,
    )
    return result
