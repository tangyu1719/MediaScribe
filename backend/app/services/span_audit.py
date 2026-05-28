"""SPAN 审计服务 — 热路径 Redis/本地 JSON；MariaDB 仅异步落盘（禁止同步写库）

数据流：每步 SPAN → 进程内热缓存 → Redis（可用时）或本地 data/span_hot（回退）→ 后台线程 flush MariaDB
绑定链：session_id → task_id → step_id
"""
from __future__ import annotations
import hashlib
import json
import logging
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Literal

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
for _p in _HERE.parents:
    if (_p / "src" / "agent").is_dir():
        _AGENT_DIR = (_p / "src" / "agent").resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

# ── 常量 ──
STEP_TYPES = ("reasoning", "tool_call", "llm_call", "api_call", "retrieval", "summary")
STATUSES = ("created", "pending", "running", "completed", "failed", "timeout", "cancelled", "resumed")
DECISIONS = ("continue", "stop", "replan", "escalate")

# ── 热缓存：内存 + Redis（可选）+ 本地 JSON 回退；MariaDB 仅异步 ──
_log = logging.getLogger("sba.span_audit")
_redis_cache: Dict[str, Dict] = {}
_cache_lock = threading.Lock()
_db_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="span-db")
_db_available = False
_SPAN_REDIS_PREFIX = "sb:span"
_SPAN_REDIS_TTL = 7 * 24 * 3600
_SPAN_LOCAL_ROOT = _HERE.parent / "data" / "span_hot"
_span_redis_probe_ok: Optional[bool] = None

HotStore = Literal["memory", "redis", "local"]


def _span_local_path(key_suffix: str) -> Path:
    safe = key_suffix.replace(":", "/").replace("..", "_")
    return _SPAN_LOCAL_ROOT / f"{safe}.json"


def _write_span_local(key_suffix: str, payload: Dict[str, Any]) -> bool:
    try:
        path = _span_local_path(key_suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as ex:
        _log.warning(
            "[AI问答-SPAN审计|span_audit._write_span_local|%s|硬编执行|失败] "
            "error_type=%s; error_message=%s",
            key_suffix,
            type(ex).__name__,
            str(ex)[:200],
        )
        return False


def _span_redis_client():
    try:
        from . import chat_session_store as _cs

        _cs._init_redis()
        return _cs._redis_client
    except Exception:
        return None


def _span_redis_put(key_suffix: str, payload: Dict[str, Any]) -> bool:
    """写入 Redis 热快照；失败则回退本地，禁止同步 MariaDB。"""
    global _span_redis_probe_ok
    client = _span_redis_client()
    if client is None:
        _span_redis_probe_ok = False
        return _write_span_local(key_suffix, payload)
    try:
        key = f"{_SPAN_REDIS_PREFIX}:{key_suffix}"
        client.setex(key, _SPAN_REDIS_TTL, json.dumps(payload, ensure_ascii=False, default=str))
        _span_redis_probe_ok = True
        return True
    except Exception as ex:
        _span_redis_probe_ok = False
        _log.info(
            "[AI问答-SPAN审计|span_audit._span_redis_put|%s|硬编执行|回退本地] "
            "error_type=%s; error_message=%s",
            key_suffix,
            type(ex).__name__,
            str(ex)[:120],
        )
        return _write_span_local(key_suffix, payload)


def _persist_span_hot(key_suffix: str, payload: Dict[str, Any]) -> HotStore:
    """同步热写：内存已在调用方更新；此处写 Redis 或本地文件（毫秒级）。"""
    if _span_redis_put(key_suffix, payload):
        return "redis" if _span_redis_probe_ok else "local"
    return "local"


def _load_span_local(key_suffix: str) -> Optional[Dict[str, Any]]:
    path = _span_local_path(key_suffix)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _sync_task_hot(task: Dict[str, Any]) -> HotStore:
    tid = task.get("task_id")
    if not tid:
        return "memory"
    return _persist_span_hot(f"task:{tid}", task)


def _sync_step_hot(step: Dict[str, Any]) -> HotStore:
    sid = step.get("step_id")
    if not sid:
        return "memory"
    store = _persist_span_hot(f"step:{sid}", step)
    tid = step.get("task_id")
    if tid:
        task = _redis_cache.get(f"task:{tid}")
        if task:
            _persist_span_hot(f"task:{tid}", task)
    return store


def _init_db():
    """初始化 MariaDB 连接并建表"""
    global _db_available
    try:
        import db as _db
        from db_models import CREATE_SPAN_TASKS_SQL, CREATE_SPAN_STEPS_SQL
        _db.execute_update(CREATE_SPAN_TASKS_SQL)
        _db.execute_update(CREATE_SPAN_STEPS_SQL)
        _db_available = True
        return True
    except Exception:
        _db_available = False
        return False


def _new_id(prefix: str = "step") -> str:
    ts = int(time.time() * 1000)
    rnd = uuid.uuid4().hex[:4]
    return f"{prefix}_{ts}_{rnd}"


def _now_dt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# ═══════════════════════════════════════════════════════
# 主任务 CRUD
# ═══════════════════════════════════════════════════════

def create_task(session_id: str, user_query: str, *, task_id: str = None) -> Dict:
    """创建主任务记录"""
    tid = task_id or _new_id("task")
    now = _now_dt()
    task = {
        "task_id": tid, "session_id": session_id,
        "user_query": user_query, "rewritten_query": "", "query_summary": "",
        "intent": "", "needs_multi_path": 0, "status": "created",
        "started_at": now, "ended_at": None,
        "total_duration_ms": 0, "total_token_count": 0,
        "total_steps": 0, "completed_steps": 0, "failed_steps": 0,
        "created_at": now, "updated_at": now,
        "steps": [],  # 子任务/工具步骤（内存树）
        "sub_plans": [],  # [{sub_plan_id, sub_index, status, summary, tools:[]}]
        "snapshot_json": {},  # 父任务双层快照（固定+开放层合并 JSON）
        "summary_history": [],  # [{from,to,at}] 摘要覆盖轨迹
        "tool_outputs": [],  # 统一工具输出轨迹（真实 JSON）
    }
    with _cache_lock:
        _redis_cache[f"task:{tid}"] = task
    _sync_task_hot(task)
    _enqueue_db_flush_task(task)
    return task


def update_task(task_id: str, **kwargs) -> Optional[Dict]:
    """更新主任务字段"""
    task = get_task(task_id)
    if not task:
        return None
    task.update(kwargs)
    task["updated_at"] = _now_dt()
    with _cache_lock:
        _redis_cache[f"task:{task_id}"] = task
    _sync_task_hot(task)
    _enqueue_db_flush_task(task)
    return task


def get_task(task_id: str) -> Optional[Dict]:
    """获取主任务（含所有步骤）：内存 → 本地 SPAN → MariaDB（只读，不同步写）。"""
    task = _redis_cache.get(f"task:{task_id}")
    if task:
        return task
    task = _load_span_local(f"task:{task_id}")
    if task:
        with _cache_lock:
            _redis_cache[f"task:{task_id}"] = task
        return task
    return _load_task_from_db(task_id)


def list_tasks(session_id: str) -> List[Dict]:
    """列出某会话下的所有主任务（DB 行与进程内热缓存 steps 合并）。"""
    mem_by_id: Dict[str, Dict] = {}
    with _cache_lock:
        for k, t in _redis_cache.items():
            if k.startswith("task:") and t.get("session_id") == session_id:
                mem_by_id[t.get("task_id") or ""] = t

    def _attach_steps(task: Dict) -> Dict:
        tid = task.get("task_id") or ""
        hot = mem_by_id.get(tid)
        if hot and hot.get("steps"):
            task = dict(task)
            task["steps"] = list(hot.get("steps") or [])
            task["total_steps"] = len(task["steps"])
        elif tid and not task.get("steps"):
            task = dict(task)
            task["steps"] = _load_steps_from_db(tid)
            task["total_steps"] = len(task["steps"])
        return task

    if _db_available:
        try:
            import db as _db
            rows = _db.execute_query(
                "SELECT * FROM span_tasks WHERE session_id=%s ORDER BY created_at DESC LIMIT 50",
                (session_id,),
            )
            out = [_attach_steps(_row_to_task(r)) for r in rows]
            seen = {t.get("task_id") for t in out}
            for tid, hot in mem_by_id.items():
                if tid and tid not in seen:
                    out.insert(0, hot)
            return out
        except Exception:
            pass
    return list(mem_by_id.values())


# ═══════════════════════════════════════════════════════
# 步骤 CRUD
# ═══════════════════════════════════════════════════════

def create_step(task_id: str, session_id: str, step_type: str, step_name: str = "",
                parent_step_id: str = None, idempotency_key: str = None) -> Dict:
    """创建步骤审计记录"""
    sid = _new_id("step")
    now = _now_dt()
    step = {
        "step_id": sid, "task_id": task_id, "parent_step_id": parent_step_id or "",
        "session_id": session_id, "step_type": step_type, "step_name": step_name,
        "status": "created", "started_at": None, "ended_at": None,
        "duration_ms": 0, "retry_count": 0, "idempotency_key": idempotency_key or sid,
        "input_payload": {}, "output_payload": {}, "error_code": "", "error_message": "",
        "token_count": 0, "resume_from": "",
        # 开放层
        "objective": "", "current_assessment": "", "progress_percent": 0,
        "next_actions": [], "risk_flags": [], "tool_io_brief": {},
        "context_summary": "", "tool_result_analysis": "",
        "decision": "continue", "confidence": 0.0, "stop_reason": "",
        "created_at": now, "updated_at": now,
    }
    # 关联到主任务
    task = _redis_cache.get(f"task:{task_id}")
    if task:
        task.setdefault("steps", []).append(step)
        task["total_steps"] = len(task["steps"])
    # Redis
    with _cache_lock:
        _redis_cache[f"step:{sid}"] = step
    _sync_step_hot(step)
    return step


def start_step(step_id: str, input_payload: Dict = None) -> Optional[Dict]:
    """标记步骤开始执行"""
    step = _redis_cache.get(f"step:{step_id}")
    if not step: return None
    step["status"] = "running"
    step["started_at"] = _now_dt()
    if input_payload: step["input_payload"] = input_payload
    step["updated_at"] = _now_dt()
    _sync_step_hot(step)
    return step


def finish_step(step_id: str, status: str = "completed", output_payload: Dict = None,
                error_code: str = "", error_message: str = "", token_count: int = 0,
                open_layer: Dict = None) -> Optional[Dict]:
    """标记步骤结束"""
    step = _redis_cache.get(f"step:{step_id}")
    if not step: return None
    now = _now_dt()
    step["status"] = status
    step["ended_at"] = now
    step["updated_at"] = now
    if step.get("started_at"):
        try:
            started = datetime.strptime(step["started_at"], "%Y-%m-%d %H:%M:%S.%f")
            ended = datetime.strptime(now, "%Y-%m-%d %H:%M:%S.%f")
            step["duration_ms"] = int((ended - started).total_seconds() * 1000)
        except Exception:
            pass
    if output_payload: step["output_payload"] = output_payload
    if output_payload is not None:
        task = _redis_cache.get(f"task:{step['task_id']}")
        if task is not None:
            record = _tool_output_record_from_payload(
                step_id, step, output_payload, status=status, timestamp=now,
            )
            if record:
                task.setdefault("tool_outputs", []).append(record)
                snap = task.get("snapshot_json")
                if not isinstance(snap, dict):
                    snap = {}
                    task["snapshot_json"] = snap
                snap.setdefault("tool_outputs", []).append(record)
                task["updated_at"] = now
    step["error_code"] = error_code
    step["error_message"] = error_message
    if token_count: step["token_count"] = token_count
    if open_layer:
        for k in ("objective", "current_assessment", "progress_percent", "next_actions",
                  "risk_flags", "tool_io_brief", "context_summary", "tool_result_analysis",
                  "decision", "confidence", "stop_reason"):
            if k in open_layer: step[k] = open_layer[k]
    # 更新主任务统计
    task = _redis_cache.get(f"task:{step['task_id']}")
    if task:
        task["total_duration_ms"] = sum(s.get("duration_ms", 0) for s in task.get("steps", []))
        task["total_token_count"] = sum(s.get("token_count", 0) for s in task.get("steps", []))
        if status == "completed": task["completed_steps"] = task.get("completed_steps", 0) + 1
        if status == "failed": task["failed_steps"] = task.get("failed_steps", 0) + 1
    _sync_step_hot(step)
    _enqueue_db_flush_step(step)
    return step


def get_step(step_id: str) -> Optional[Dict]:
    step = _redis_cache.get(f"step:{step_id}")
    if step:
        return step
    step = _load_span_local(f"step:{step_id}")
    if step:
        with _cache_lock:
            _redis_cache[f"step:{step_id}"] = step
        return step
    if _db_available:
        try:
            import db as _db

            rows = _db.execute_query("SELECT * FROM span_steps WHERE step_id=%s", (step_id,))
            if rows:
                step = _row_to_step(rows[0])
                with _cache_lock:
                    _redis_cache[f"step:{step_id}"] = step
                return step
        except Exception:
            pass
    return None


def patch_task_snapshot(
    task_id: str,
    *,
    fixed: Optional[Dict[str, Any]] = None,
    open_layer: Optional[Dict[str, Any]] = None,
) -> Optional[Dict]:
    """合并主任务双层快照（fixed / open）。"""
    task = get_task(task_id)
    if not task:
        return None
    snap = task.get("snapshot_json")
    if not isinstance(snap, dict):
        snap = {}
        task["snapshot_json"] = snap
    if fixed:
        snap.setdefault("fixed", {}).update(fixed)
    if open_layer:
        snap.setdefault("open", {}).update(open_layer)
    task["updated_at"] = _now_dt()
    with _cache_lock:
        _redis_cache[f"task:{task_id}"] = task
    _sync_task_hot(task)
    _enqueue_db_flush_task(task)
    return task


def _tool_output_record_from_payload(
    step_id: str,
    step: Dict,
    output_payload: Dict,
    *,
    status: str,
    timestamp: str,
) -> Optional[Dict]:
    """从统一 schema 或 tool_call 步骤生成可持久化记录。"""
    if not isinstance(output_payload, dict):
        return None
    is_tool = output_payload.get("tool_call") is True or step.get("step_type") == "tool_call"
    if not is_tool:
        return None
    return {
        "step_id": step_id,
        "step_name": step.get("step_name", ""),
        "tool_name": output_payload.get("tool_name", ""),
        "tool_args": output_payload.get("tool_args", {}),
        "tool_result": output_payload.get("tool_result"),
        "error": output_payload.get("error"),
        "cost_ms": output_payload.get("cost_ms", step.get("duration_ms", 0)),
        "status": status,
        "timestamp": timestamp,
        "schema_version": output_payload.get("schema_version", 1),
    }


# ═══════════════════════════════════════════════════════
# 内部实现
# ═══════════════════════════════════════════════════════

def _flush_task_to_db(task: Dict):
    if not _db_available: return
    try:
        import db as _db
        _db.execute_update("""
            INSERT INTO span_tasks (task_id, session_id, user_query, rewritten_query, query_summary,
                intent, needs_multi_path, status, started_at, ended_at, total_duration_ms,
                total_token_count, total_steps, completed_steps, failed_steps, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                status=VALUES(status), ended_at=VALUES(ended_at),
                total_duration_ms=VALUES(total_duration_ms), total_token_count=VALUES(total_token_count),
                total_steps=VALUES(total_steps), completed_steps=VALUES(completed_steps),
                failed_steps=VALUES(failed_steps), updated_at=VALUES(updated_at),
                rewritten_query=VALUES(rewritten_query), query_summary=VALUES(query_summary),
                intent=VALUES(intent), needs_multi_path=VALUES(needs_multi_path)
        """, (
            task["task_id"], task["session_id"], task["user_query"],
            task.get("rewritten_query", ""), task.get("query_summary", ""),
            task.get("intent", ""), task.get("needs_multi_path", 0), task.get("status", "created"),
            task.get("started_at"), task.get("ended_at"), task.get("total_duration_ms", 0),
            task.get("total_token_count", 0), task.get("total_steps", 0),
            task.get("completed_steps", 0), task.get("failed_steps", 0),
            task.get("created_at"), task.get("updated_at"),
        ))
    except Exception:
        pass


def _flush_step_to_db(step: Dict):
    if not _db_available: return
    try:
        import db as _db
        _db.execute_update("""
            INSERT INTO span_steps (step_id, task_id, parent_step_id, session_id, step_type, step_name,
                status, started_at, ended_at, duration_ms, retry_count, idempotency_key,
                input_payload, output_payload, error_code, error_message, token_count, resume_from,
                objective, current_assessment, progress_percent, next_actions, risk_flags,
                tool_io_brief, context_summary, tool_result_analysis, decision, confidence, stop_reason,
                created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                status=VALUES(status), ended_at=VALUES(ended_at), duration_ms=VALUES(duration_ms),
                output_payload=VALUES(output_payload), error_code=VALUES(error_code),
                error_message=VALUES(error_message), token_count=VALUES(token_count),
                objective=VALUES(objective), current_assessment=VALUES(current_assessment),
                progress_percent=VALUES(progress_percent), next_actions=VALUES(next_actions),
                risk_flags=VALUES(risk_flags), tool_io_brief=VALUES(tool_io_brief),
                context_summary=VALUES(context_summary), tool_result_analysis=VALUES(tool_result_analysis),
                decision=VALUES(decision), confidence=VALUES(confidence),
                stop_reason=VALUES(stop_reason), updated_at=VALUES(updated_at)
        """, (
            step["step_id"], step["task_id"], step.get("parent_step_id") or "", step["session_id"],
            step["step_type"], step.get("step_name", ""), step["status"],
            step.get("started_at"), step.get("ended_at"), step.get("duration_ms", 0),
            step.get("retry_count", 0), step.get("idempotency_key", ""),
            json.dumps(step.get("input_payload", {}), ensure_ascii=False),
            json.dumps(step.get("output_payload", {}), ensure_ascii=False),
            step.get("error_code", ""), step.get("error_message", ""),
            step.get("token_count", 0), step.get("resume_from", ""),
            step.get("objective", ""), step.get("current_assessment", ""),
            step.get("progress_percent", 0),
            json.dumps(step.get("next_actions", []), ensure_ascii=False),
            json.dumps(step.get("risk_flags", []), ensure_ascii=False),
            json.dumps(step.get("tool_io_brief", {}), ensure_ascii=False),
            step.get("context_summary", ""), step.get("tool_result_analysis", ""),
            step.get("decision", "continue"), step.get("confidence", 0.0),
            step.get("stop_reason", ""), step.get("created_at", ""), step.get("updated_at", ""),
        ))
    except Exception:
        pass


def _load_task_from_db(task_id: str) -> Optional[Dict]:
    if not _db_available: return None
    try:
        import db as _db
        rows = _db.execute_query("SELECT * FROM span_tasks WHERE task_id=%s", (task_id,))
        if rows:
            task = _row_to_task(rows[0])
            task["steps"] = _load_steps_from_db(task_id)
            with _cache_lock:
                _redis_cache[f"task:{task_id}"] = task
            return task
    except Exception:
        pass
    return None


def _load_steps_from_db(task_id: str) -> List[Dict]:
    steps: List[Dict] = []
    local_dir = _SPAN_LOCAL_ROOT / "step"
    if local_dir.is_dir():
        prefix = f'"task_id": "{task_id}"'
        try:
            for fp in local_dir.glob("*.json"):
                try:
                    raw = fp.read_text(encoding="utf-8")
                    if prefix not in raw:
                        continue
                    s = json.loads(raw)
                    if isinstance(s, dict) and s.get("task_id") == task_id:
                        steps.append(s)
                except Exception:
                    continue
            if steps:
                steps.sort(key=lambda x: x.get("created_at") or "")
                return steps
        except Exception:
            pass
    if not _db_available:
        return []
    try:
        import db as _db
        rows = _db.execute_query(
            "SELECT * FROM span_steps WHERE task_id=%s ORDER BY created_at", (task_id,))
        return [_row_to_step(r) for r in rows]
    except Exception:
        return []


def _row_to_task(row: Dict) -> Dict:
    t = dict(row)
    for k in ("started_at", "ended_at", "created_at", "updated_at"):
        if t.get(k) and hasattr(t[k], "strftime"):
            t[k] = t[k].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return {**t, "steps": []}


def _row_to_step(row: Dict) -> Dict:
    s = dict(row)
    for k in ("started_at", "ended_at", "created_at", "updated_at"):
        if s.get(k) and hasattr(s[k], "strftime"):
            s[k] = s[k].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    for json_k in ("input_payload", "output_payload", "next_actions", "risk_flags", "tool_io_brief"):
        if isinstance(s.get(json_k), str):
            try: s[json_k] = json.loads(s[json_k])
            except: pass
    return s


def list_pipeline_span_tasks(*, limit: int = 80) -> List[Dict]:
    """列出链接沉淀类 SPAN 主任务（session_id 以 pipeline: 开头）。"""
    limit = max(1, min(int(limit or 80), 200))
    rows: List[Dict] = []
    with _cache_lock:
        for k, t in _redis_cache.items():
            if not k.startswith("task:"):
                continue
            sid = str(t.get("session_id") or "")
            if sid.startswith("pipeline:"):
                rows.append(dict(t))
    if _db_available:
        try:
            import db as _db

            db_rows = _db.execute_query(
                "SELECT * FROM span_tasks WHERE session_id LIKE 'pipeline:%' "
                "ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
            seen = {r.get("task_id") for r in rows}
            for r in db_rows:
                tid = r.get("task_id")
                if tid in seen:
                    continue
                task = _row_to_task(r)
                task["steps"] = _load_steps_from_db(tid)
                rows.append(task)
        except Exception:
            pass
    rows.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    return rows[:limit]


def list_exception_steps(*, limit: int = 100, task_id: str = "") -> List[Dict]:
    """异常 SPAN 步骤（failed / timeout）。"""
    limit = max(1, min(int(limit or 100), 300))
    tid = (task_id or "").strip()
    if tid:
        return [s for s in list_exception_steps_for_task(tid)]

    out: List[Dict] = []
    if _db_available:
        try:
            import db as _db

            rows = _db.execute_query(
                "SELECT * FROM span_steps WHERE status IN ('failed','timeout') "
                "ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
            out = [_row_to_step(r) for r in rows]
        except Exception:
            pass
    if not out:
        with _cache_lock:
            for k, s in _redis_cache.items():
                if k.startswith("step:") and s.get("status") in ("failed", "timeout"):
                    out.append(dict(s))
        out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        out = out[:limit]
    return out


def list_exception_steps_for_task(task_id: str) -> List[Dict]:
    steps = _load_steps_from_db(task_id) if _db_available else []
    if not steps:
        task = get_task(task_id)
        steps = list((task or {}).get("steps") or [])
    return [s for s in steps if s.get("status") in ("failed", "timeout")]


# MariaDB 表结构延后到首次异步 flush 时初始化，避免阻塞 import/SSE
def _ensure_db_async_ready() -> None:
    global _db_available
    if _db_available:
        return
    _init_db()


def _enqueue_db_flush_task(task: Dict[str, Any]) -> None:
    if not task:
        return
    _ensure_db_async_ready()
    if not _db_available:
        return
    snap = dict(task)

    def _run() -> None:
        _flush_task_to_db(snap)

    try:
        _db_executor.submit(_run)
    except Exception:
        pass


def _enqueue_db_flush_step(step: Dict[str, Any]) -> None:
    if not step:
        return
    _ensure_db_async_ready()
    if not _db_available:
        return
    snap = dict(step)

    def _run() -> None:
        _flush_step_to_db(snap)

    try:
        _db_executor.submit(_run)
    except Exception:
        pass
