"""任务管理服务 —— 纯内存存储，无外部依赖"""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .link_hash import normalize_link_for_hash, url_hash as link_url_hash

_HERE = Path(__file__).resolve()
# 向上找到 web_rebuild_v2 根目录（包含 frontend/ 的那个目录）
_OUTPUT_ROOT = None
for _p in _HERE.parents:
    if (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _OUTPUT_ROOT = _p
        break
if _OUTPUT_ROOT is None:
    _OUTPUT_ROOT = _HERE.parents[3]
OUTPUT_DIR = _OUTPUT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
_OUTPUT_OVERRIDE = _OUTPUT_ROOT / ".web_output_dir.json"

_task_store: Dict[str, Dict] = {}

# 仍在执行中的状态（同 url_hash 禁止再开新卡片）
_PIPELINE_ACTIVE_STATUSES = frozenset({
    "pending", "running", "started",
    "downloading", "transcribing", "consolidating", "generating", "generating_html",
    "extracting", "ocr", "comments", "assembling", "feishu_upload",
})
# 真正跑流水线中的状态（pending 仅排队，允许在同卡片上断点恢复/重跑）
_PIPELINE_RUNNING_STATUSES = _PIPELINE_ACTIVE_STATUSES - {"pending"}


def get_output_dir() -> Path:
    """输出根目录：默认 `output/`，若存在 `.web_output_dir.json` 且 path 为绝对路径则覆盖。"""
    try:
        if _OUTPUT_OVERRIDE.exists():
            data = json.loads(_OUTPUT_OVERRIDE.read_text(encoding="utf-8"))
            raw = (data.get("path") or "").strip()
            if raw:
                p = Path(raw)
                if p.is_absolute():
                    p.mkdir(parents=True, exist_ok=True)
                    return p
    except Exception:
        pass
    return OUTPUT_DIR


def set_output_dir(path: str) -> Dict:
    """写入覆盖配置（绝对路径）。"""
    p = Path(path.strip())
    if not p.is_absolute():
        return {"ok": False, "error": "须为绝对路径"}
    p.mkdir(parents=True, exist_ok=True)
    _OUTPUT_OVERRIDE.write_text(
        json.dumps({"path": str(p)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "path": str(p)}


class TaskStore:
    """任务状态容器"""
    @staticmethod
    def create(platform: str, link: str) -> str:
        return create_task(platform, link)

    @staticmethod
    def get(task_id: str) -> Optional[Dict]:
        return get_task(task_id)

    @staticmethod
    def list_all() -> list:
        return list_tasks()

    @staticmethod
    def delete(task_id: str) -> bool:
        return delete_task(task_id)


def create_task(
    platform: str,
    link: str,
    user_prompt: str = "",
    comments: Optional[Dict] = None,
    *,
    resume_from: Optional[str] = None,
    resume_context: Optional[Dict] = None,
    pipeline_stages: Optional[Dict] = None,
    pipeline_route: Optional[str] = None,
) -> str:
    norm = normalize_link_for_hash(link)
    uh = link_url_hash(link)

    # 同链接禁止新建卡片：内存中已有则复用 task_id 重启入队
    existing = find_task_by_url_hash(uh)
    if existing and (existing.get("task_id") or "").strip():
        return restart_existing_task(
            existing["task_id"],
            platform=platform,
            link=link,
            user_prompt=user_prompt,
            comments=comments,
            resume_from=resume_from,
            resume_context=resume_context,
            pipeline_stages=pipeline_stages,
            pipeline_route=pipeline_route,
            full_rerun=False,
        )

    task_id = uuid.uuid4().hex[:12]

    # 首层标题：入队时即从链接页尝试提取标题/封面/路由类型
    link_title_boot = None
    cover_boot = None
    content_type_boot = None
    route_type_boot = None
    try:
        from .file_naming import bootstrap_link_meta
        meta = bootstrap_link_meta(link, platform)
        if meta.get("link_title"):
            link_title_boot = meta["link_title"]
        if meta.get("cover_url"):
            cover_boot = meta["cover_url"]
        if meta.get("content_type"):
            content_type_boot = meta["content_type"]
        if meta.get("route_type"):
            route_type_boot = meta["route_type"]
    except Exception:
        pass

    # 计算优先级：新任务默认优先级为当前最高优先级+1
    existing_tasks = list_tasks()
    max_priority = max([t.get("priority", 0) for t in existing_tasks], default=0)
    
    _task_store[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "platform": platform,
        "link": link,
        "normalized_link": norm,
        "url_hash": uh,
        "progress": 0,
        "stage": "等待开始",
        "link_title": link_title_boot,
        "doc_title": None,
        "content_type": content_type_boot,
        "cover_url": cover_boot,
        "route_type": route_type_boot,
        "doc_filename": None,
        "doc_path": None,
        "html_path": None,
        "html_status": "",
        "html_message": "",
        "feishu_doc_url": None,
        "feishu_doc_token": None,
        "feishu_status": "",
        "feishu_message": "",
        "pipeline_started_at": "",
        "total_duration_ms": 0,
        "total_token_count": 0,
        "error": None,
        "user_prompt": user_prompt,
        "comments": comments or {"enabled": False, "count": 10, "sort": "hot"},
        "pipeline_route": pipeline_route or route_type_boot or "",
        "pipeline_stages": pipeline_stages or {},
        "failed_stage": "",
        "failed_stage_label": "",
        "resume_from": resume_from or "",
        "resume_context": resume_context or {},
        "priority": max_priority + 1,  # 新任务默认最高优先级
        "created_at": datetime.now().isoformat(),
        "logs": [],
    }
    return task_id


def get_task(task_id: str) -> Optional[Dict]:
    return _task_store.get(task_id)


def add_log(task_id: str, message: str, level: str = "INFO"):
    """日志格式与原项目 append_log 一致：[HH:MM:SS] LEVEL: message"""
    task = _task_store.get(task_id)
    if task:
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = {"timestamp": timestamp, "level": level, "message": str(message)}
        task.setdefault("logs", []).append(entry)
        try:
            from .pipeline_task_logs import append_persistent_log

            append_persistent_log(task_id, timestamp=timestamp, level=level, message=str(message))
        except Exception:
            pass
        if len(task["logs"]) > 800:
            task["logs"] = task["logs"][-800:]
    lvl = (level or "INFO").upper()
    if lvl in ("ERROR", "EXCEPTION"):
        try:
            from .ops_hooks import ops_dispatch_log_incident

            ops_dispatch_log_incident(str(message), lvl, task_id=task_id)
        except Exception:
            pass


def update_task(task_id: str, **kwargs):
    task = _task_store.get(task_id)
    if task:
        task.update(kwargs)


def list_tasks() -> list:
    """返回任务列表，按优先级和创建时间排序"""
    tasks = list(_task_store.values())
    # 按优先级降序，然后按创建时间降序
    return sorted(tasks, key=lambda t: (t.get("priority", 0), t.get("created_at", "")), reverse=True)


# 终态任务在队列中保留可见的时长（小时）
_QUEUE_TERMINAL_VISIBLE_HOURS = 72


def _parse_iso_dt(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return None


def _history_recent_enough(hist: Dict[str, Any], *, hours: int = _QUEUE_TERMINAL_VISIBLE_HOURS) -> bool:
    for key in ("updated_at", "created_at"):
        dt = _parse_iso_dt(str(hist.get(key) or ""))
        if dt and datetime.now() - dt.replace(tzinfo=None) <= timedelta(hours=hours):
            return True
    return False


def _list_history_rows_for_queue(limit: int) -> List[Dict[str, Any]]:
    """读取历史用于队列回填（不触发 consolidate / 写回）。"""
    from .history_manager import normalize_history_task

    try:
        from . import pipeline_history_store as store

        if store.is_enabled():
            raw = store.list_tasks(limit=max(limit, 1))
            return [normalize_history_task(t) for t in raw if isinstance(t, dict)]
    except Exception:
        pass
    try:
        from .config import runtime_agent_dir

        path = runtime_agent_dir() / "history.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = list(data.get("tasks") or [])[:limit]
            return [normalize_history_task(t) for t in raw if isinstance(t, dict)]
    except Exception:
        pass
    return []


def import_history_task_to_queue(hist: Dict[str, Any]) -> Optional[str]:
    """将历史记录还原为内存队列卡片（保留 status，不自动重新入队）。"""
    task_id = (hist.get("id") or hist.get("task_id") or "").strip()
    link = (hist.get("link") or "").strip()
    if not task_id or not link:
        return None
    if task_id in _task_store:
        return None
    uh = (hist.get("url_hash") or "").strip() or link_url_hash(link)
    if find_task_by_url_hash(uh):
        return None

    status = (hist.get("status") or "completed").strip()
    stage = hist.get("stage") or ""
    error = hist.get("error")
    if status in _PIPELINE_RUNNING_STATUSES:
        status = "failed"
        stage = stage or "服务重启后中断"
        error = error or "后端重启或热重载导致任务中断，请点「重新执行」"

    norm = hist.get("normalized_link") or normalize_link_for_hash(link)
    existing_tasks = list_tasks()
    max_priority = max([t.get("priority", 0) for t in existing_tasks], default=0)

    _task_store[task_id] = {
        "task_id": task_id,
        "status": status,
        "platform": hist.get("platform") or "",
        "link": link,
        "normalized_link": norm,
        "url_hash": uh,
        "progress": int(hist.get("progress") or 0),
        "stage": stage,
        "link_title": hist.get("link_title") or hist.get("title"),
        "doc_title": hist.get("doc_title"),
        "content_type": hist.get("content_type"),
        "cover_url": hist.get("cover_url"),
        "route_type": hist.get("route_type"),
        "doc_filename": hist.get("doc_filename"),
        "doc_path": hist.get("doc_path"),
        "html_path": hist.get("html_path"),
        "html_status": hist.get("html_status") or "",
        "html_message": hist.get("html_message") or "",
        "feishu_doc_url": hist.get("feishu_doc_url"),
        "feishu_doc_token": hist.get("feishu_doc_token"),
        "error": error,
        "user_prompt": hist.get("user_prompt") or "",
        "comments": hist.get("comments") or {"enabled": False, "count": 10, "sort": "hot"},
        "pipeline_route": hist.get("pipeline_route") or hist.get("route_type") or "",
        "pipeline_stages": hist.get("pipeline_stages") or {},
        "failed_stage": hist.get("failed_stage") or "",
        "failed_stage_label": hist.get("failed_stage_label") or "",
        "resume_from": hist.get("resume_from") or "",
        "resume_context": hist.get("resume_context") or {},
        "priority": max_priority + 1,
        "created_at": hist.get("created_at") or datetime.now().isoformat(),
        "logs": list(hist.get("logs") or [])[-400:],
    }
    return task_id


def merge_history_into_queue(*, limit: int = 80) -> int:
    """从历史/MariaDB 回填内存队列展示（只增不删）。"""
    before = len(_task_store)
    for hist in _list_history_rows_for_queue(limit):
        st = (hist.get("status") or "").strip()
        if st in ("completed", "failed", "cancelled"):
            if not _history_recent_enough(hist):
                continue
        elif st not in _PIPELINE_ACTIVE_STATUSES:
            continue
        import_history_task_to_queue(hist)
    return len(_task_store) - before


def init_queue_from_history() -> int:
    """启动时恢复队列可见任务。"""
    n = merge_history_into_queue(limit=100)
    consolidate_queue_by_url_hash()
    return n


def list_queue_tasks() -> list:
    """供 /api/process/queue 使用：先回填历史再返回内存列表。"""
    merge_history_into_queue(limit=80)
    return list_tasks()


def list_pending_tasks() -> list:
    """返回待处理的任务列表"""
    return [t for t in list_tasks() if t.get("status") == "pending"]


def list_running_tasks() -> list:
    """返回运行中的任务列表"""
    return [t for t in list_tasks() if t.get("status") in ("started", "running")]


def delete_task(task_id: str) -> bool:
    return _task_store.pop(task_id, None) is not None


def cancel_task(task_id: str) -> bool:
    """取消任务（将状态设为 cancelled）"""
    task = _task_store.get(task_id)
    if task and task.get("status") not in ("completed", "failed"):
        task["status"] = "cancelled"
        task["stage"] = "已取消"
        task["error"] = "用户取消"
        return True
    return False


def move_task_priority(task_id: str, direction: str) -> bool:
    """
    移动任务优先级（仅对 pending 状态的任务有效）
    direction: 'up' 或 'down'
    """
    task = _task_store.get(task_id)
    if not task or task.get("status") != "pending":
        return False
    
    pending = list_pending_tasks()
    if len(pending) < 2:
        return False
    
    # 找到当前任务在 pending 列表中的位置
    idx = None
    for i, t in enumerate(pending):
        if t.get("task_id") == task_id:
            idx = i
            break
    
    if idx is None:
        return False
    
    # 调整优先级
    current_priority = task.get("priority", 0)
    if direction == "up" and idx > 0:
        # 与前面的任务交换优先级
        other = pending[idx - 1]
        other_priority = other.get("priority", 0)
        task["priority"] = other_priority + 1
        return True
    elif direction == "down" and idx < len(pending) - 1:
        # 与后面的任务交换优先级
        other = pending[idx + 1]
        other_priority = other.get("priority", 0)
        task["priority"] = other_priority - 1
        return True
    
    return False


def get_task_by_link(link: str) -> Optional[Dict]:
    """通过链接查找任务（按稳定 url_hash，忽略 xsec_token 等差异）。"""
    uh = link_url_hash(link)
    for task in _task_store.values():
        if link_url_hash(task.get("link") or "") == uh:
            return task
    return None


def find_task_by_url_hash(url_hash: str) -> Optional[Dict]:
    """内存队列中同内容最近一条任务（优先非执行中）。"""
    uh = (url_hash or "").strip()
    if not uh:
        return None
    rows = [t for t in _task_store.values() if link_url_hash(t.get("link") or "") == uh]
    if not rows:
        return None

    def _key(t: Dict) -> Tuple[int, str]:
        active = 1 if (t.get("status") or "") in _PIPELINE_ACTIVE_STATUSES else 0
        return (active, t.get("created_at") or "")

    rows.sort(key=_key)
    return rows[-1]


def remove_duplicate_tasks(url_hash: str, keep_task_id: str) -> int:
    """同链接只保留一张卡片：移除内存中其它 task_id（按稳定 hash 比对）。"""
    uh = (url_hash or "").strip()
    if not uh:
        return 0
    removed = 0
    for tid in list(_task_store.keys()):
        if tid == keep_task_id:
            continue
        if link_url_hash((_task_store.get(tid) or {}).get("link") or "") == uh:
            del _task_store[tid]
            removed += 1
    return removed


def _sync_link_identity_fields(task: Dict, link: str) -> None:
    """写入/刷新 normalized_link 与 url_hash，保证判重字段一致。"""
    norm = normalize_link_for_hash(link)
    uh = link_url_hash(link)
    task["link"] = link
    task["normalized_link"] = norm
    task["url_hash"] = uh


def _append_restart_log(task: Dict, message: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    task.setdefault("logs", []).append({"timestamp": ts, "level": level, "message": message})
    try:
        from .pipeline_task_logs import append_persistent_log

        append_persistent_log(task["task_id"], timestamp=ts, level=level, message=message)
    except Exception:
        pass


def restart_existing_task(
    task_id: str,
    *,
    platform: str,
    link: str,
    user_prompt: str = "",
    comments: Optional[Dict] = None,
    resume_from: Optional[str] = None,
    resume_context: Optional[Dict] = None,
    pipeline_stages: Optional[Dict] = None,
    pipeline_route: Optional[str] = None,
    full_rerun: bool = False,
) -> str:
    """在同一 task_id / 卡片上重新入队（断点恢复或全量重跑）。"""
    task = _task_store.get(task_id)
    if not task:
        raise KeyError(f"任务不存在: {task_id}")
    if (task.get("status") or "") in _PIPELINE_RUNNING_STATUSES:
        raise RuntimeError("任务正在执行中，请稍候或先停止")

    norm = normalize_link_for_hash(link)
    uh = link_url_hash(link)
    if full_rerun:
        resume_from = ""
        resume_context = {}
        pipeline_stages = {}
        failed_stage = ""
        failed_stage_label = ""
    else:
        rf = (resume_from if resume_from is not None else task.get("resume_from") or task.get("failed_stage") or "")
        resume_from = str(rf).strip()
        resume_context = resume_context if resume_context is not None else (task.get("resume_context") or {})
        pipeline_stages = pipeline_stages if pipeline_stages is not None else (task.get("pipeline_stages") or {})
        failed_stage = resume_from
        failed_stage_label = task.get("failed_stage_label") or ""

    task.update({
        "platform": platform or task.get("platform"),
        "user_prompt": user_prompt if user_prompt is not None else task.get("user_prompt", ""),
        "comments": comments if comments is not None else task.get("comments"),
        "status": "pending",
        "stage": "等待开始",
        "progress": 0,
        "error": None,
        "pipeline_route": pipeline_route or task.get("pipeline_route") or "",
        "pipeline_stages": pipeline_stages if pipeline_stages is not None else task.get("pipeline_stages") or {},
        "failed_stage": failed_stage if not full_rerun else "",
        "failed_stage_label": failed_stage_label if not full_rerun else "",
        "resume_from": (resume_from or "") if not full_rerun else "",
        "resume_context": (resume_context or {}) if not full_rerun else {},
        "html_status": "",
        "html_message": "",
    })
    _sync_link_identity_fields(task, link)
    mode = "全量重跑" if full_rerun else ("断点恢复" if task.get("resume_from") else "重新执行")
    _append_restart_log(task, f"[{mode}] 复用本卡片 task_id={task_id}")
    remove_duplicate_tasks(uh, task_id)
    return task_id


def rehydrate_task_from_history(
    hist: Dict[str, Any],
    *,
    platform: str,
    link: str,
    user_prompt: str = "",
    comments: Optional[Dict] = None,
    resume_from: Optional[str] = None,
    resume_context: Optional[Dict] = None,
    pipeline_stages: Optional[Dict] = None,
    pipeline_route: Optional[str] = None,
    full_rerun: bool = False,
) -> str:
    """历史记录写回内存队列，沿用原 id，不新建卡片。"""
    task_id = (hist.get("id") or hist.get("task_id") or "").strip() or uuid.uuid4().hex[:12]
    norm = normalize_link_for_hash(link)
    uh = hist.get("url_hash") or link_url_hash(link)
    if task_id in _task_store:
        return restart_existing_task(
            task_id,
            platform=platform,
            link=link,
            user_prompt=user_prompt,
            comments=comments,
            resume_from=resume_from,
            resume_context=resume_context,
            pipeline_stages=pipeline_stages,
            pipeline_route=pipeline_route,
            full_rerun=full_rerun,
        )

    existing = list_tasks()
    max_priority = max([t.get("priority", 0) for t in existing], default=0)
    _task_store[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "platform": platform or hist.get("platform", ""),
        "link": link,
        "normalized_link": norm,
        "url_hash": uh,
        "progress": 0,
        "stage": "等待开始",
        "link_title": hist.get("link_title") or hist.get("title"),
        "doc_title": hist.get("doc_title"),
        "content_type": hist.get("content_type"),
        "cover_url": hist.get("cover_url"),
        "route_type": hist.get("route_type"),
        "doc_filename": hist.get("doc_filename"),
        "doc_path": hist.get("doc_path"),
        "html_path": hist.get("html_path"),
        "html_status": "",
        "html_message": "",
        "feishu_doc_url": hist.get("feishu_doc_url"),
        "feishu_doc_token": hist.get("feishu_doc_token"),
        "error": None,
        "user_prompt": user_prompt or hist.get("user_prompt") or "",
        "comments": comments if comments is not None else hist.get("comments"),
        "pipeline_route": pipeline_route or hist.get("pipeline_route") or hist.get("route_type") or "",
        "pipeline_stages": {} if full_rerun else (pipeline_stages or hist.get("pipeline_stages") or {}),
        "failed_stage": "" if full_rerun else (hist.get("failed_stage") or ""),
        "failed_stage_label": "" if full_rerun else (hist.get("failed_stage_label") or ""),
        "resume_from": "" if full_rerun else (resume_from or hist.get("resume_from") or ""),
        "resume_context": {} if full_rerun else (resume_context or hist.get("resume_context") or {}),
        "priority": max_priority + 1,
        "created_at": hist.get("created_at") or datetime.now().isoformat(),
        "logs": list(hist.get("logs") or [])[-400:],
    }
    return restart_existing_task(
        task_id,
        platform=platform,
        link=link,
        user_prompt=user_prompt,
        comments=comments,
        resume_from=resume_from,
        resume_context=resume_context,
        pipeline_stages=pipeline_stages,
        pipeline_route=pipeline_route,
        full_rerun=full_rerun,
    )


def reuse_or_enqueue_task(
    platform: str,
    link: str,
    user_prompt: str = "",
    comments: Optional[Dict] = None,
    *,
    task_id: Optional[str] = None,
    resume_from: Optional[str] = None,
    resume_context: Optional[Dict] = None,
    pipeline_stages: Optional[Dict] = None,
    pipeline_route: Optional[str] = None,
    action: str = "start",
) -> Tuple[str, bool]:
    """
    同链接复用同一卡片：返回 (task_id, reused)。
    action: resume/start → 断点；rerun → 全量。
    """
    from .history_manager import get_task_history

    full_rerun = (action or "").strip().lower() == "rerun"
    uh = link_url_hash(link)
    tid = (task_id or "").strip()

    if tid and tid in _task_store:
        st = (_task_store[tid].get("status") or "")
        if st in _PIPELINE_ACTIVE_STATUSES:
            return tid, True
        restart_existing_task(
            tid,
            platform=platform,
            link=link,
            user_prompt=user_prompt,
            comments=comments,
            resume_from=resume_from,
            resume_context=resume_context,
            pipeline_stages=pipeline_stages,
            pipeline_route=pipeline_route,
            full_rerun=full_rerun,
        )
        return tid, True

    mem = find_task_by_url_hash(uh)
    if mem:
        mtid = mem["task_id"]
        if (mem.get("status") or "") in _PIPELINE_ACTIVE_STATUSES:
            return mtid, True
        restart_existing_task(
            mtid,
            platform=platform,
            link=link,
            user_prompt=user_prompt,
            comments=comments,
            resume_from=resume_from,
            resume_context=resume_context,
            pipeline_stages=pipeline_stages,
            pipeline_route=pipeline_route,
            full_rerun=full_rerun,
        )
        return mtid, True

    hist = get_task_history(link=link, url_hash=uh)
    if hist:
        new_id = rehydrate_task_from_history(
            hist,
            platform=platform,
            link=link,
            user_prompt=user_prompt,
            comments=comments,
            resume_from=resume_from,
            resume_context=resume_context,
            pipeline_stages=pipeline_stages,
            pipeline_route=pipeline_route,
            full_rerun=full_rerun,
        )
        return new_id, True

    new_id = create_task(
        platform,
        link,
        user_prompt,
        comments,
        resume_from=resume_from,
        resume_context=resume_context,
        pipeline_stages=pipeline_stages,
        pipeline_route=pipeline_route,
    )
    remove_duplicate_tasks(uh, new_id)
    return new_id, False


def consolidate_queue_by_url_hash() -> int:
    """同内容链接只保留一张卡片：优先执行中 > 待处理 > 最近更新（勿在 GET 轮询里调用）。"""
    by_hash: Dict[str, List[Dict]] = {}
    for t in list_tasks():
        link = t.get("link") or ""
        uh = link_url_hash(link)
        if uh != (t.get("url_hash") or ""):
            t["url_hash"] = uh
            t["normalized_link"] = normalize_link_for_hash(link)
        if not uh:
            continue
        by_hash.setdefault(uh, []).append(t)
    removed = 0

    def _rank(row: Dict) -> Tuple[int, str]:
        st = (row.get("status") or "").strip()
        if st in _PIPELINE_RUNNING_STATUSES:
            tier = 3
        elif st == "pending":
            tier = 2
        elif st in ("failed", "cancelled"):
            tier = 1
        else:
            tier = 0
        ts = row.get("updated_at") or row.get("created_at") or ""
        return (tier, ts)

    for uh, rows in by_hash.items():
        if len(rows) < 2:
            continue
        rows.sort(key=_rank)
        keep_id = rows[-1]["task_id"]
        removed += remove_duplicate_tasks(uh, keep_id)
    return removed


def cleanup_completed_tasks() -> int:
    """清理已完成的任务（从内存中移除）"""
    to_remove = []
    for task_id, task in _task_store.items():
        if task.get("status") in ("completed", "failed", "cancelled"):
            to_remove.append(task_id)
    
    for task_id in to_remove:
        del _task_store[task_id]
    
    return len(to_remove)
