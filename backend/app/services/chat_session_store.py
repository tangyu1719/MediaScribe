"""AI 问答会话 — 本地 JSON 持久化 + 定时同步 Redis"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger("sba.chat_store")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[3]
_CHAT_DIR = _ROOT / "data" / "chat_sessions"
_INDEX_PATH = _CHAT_DIR / "index.json"
_REDIS_PREFIX = "sb:chat:session"
_SYNC_INTERVAL_SEC = 45

_lock = threading.RLock()
_dirty_ids: set = set()
_redis_client: Any = None
_redis_error = ""
_sync_timer: Optional[threading.Timer] = None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _session_path(sid: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sid)
    return _CHAT_DIR / f"{safe}.json"


def _ensure_dir() -> None:
    _CHAT_DIR.mkdir(parents=True, exist_ok=True)


def _read_index() -> Dict[str, Any]:
    _ensure_dir()
    if not _INDEX_PATH.exists():
        return {"sessions": {}}
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}


def _write_index(idx: Dict[str, Any]) -> None:
    _ensure_dir()
    _INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def _init_redis() -> None:
    global _redis_client, _redis_error
    if _redis_client is not None:
        return
    cfg: Dict[str, Any] = {}
    for cp in [_ROOT / "config.json", _ROOT.parent / "src" / "agent" / "config.json"]:
        if cp.exists():
            try:
                cfg = json.loads(cp.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    if not cfg.get("redis_cache_enabled", False):
        _redis_error = "redis_cache_enabled=false"
        return
    url = str(cfg.get("redis_url", "redis://127.0.0.1:6379/0")).strip()
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        client.ping()
        _redis_client = client
        _redis_error = ""
    except Exception as e:
        _redis_client = None
        _redis_error = f"{type(e).__name__}: {e}"


def sync_session_to_redis(sid: str, payload: Optional[Dict[str, Any]] = None) -> bool:
    """将会话 JSON 写入 Redis；无 Redis 时仅记日志。"""
    _init_redis()
    if _redis_client is None:
        return False
    if payload is None:
        p = _session_path(sid)
        if not p.exists():
            return False
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return False
    key = f"{_REDIS_PREFIX}:{sid}"
    try:
        ttl = int(payload.get("redis_ttl_sec") or 7 * 24 * 3600)
        _redis_client.setex(key, ttl, json.dumps(payload, ensure_ascii=False))
        idx_key = f"{_REDIS_PREFIX}:index"
        ts = time.time()
        _redis_client.zadd(idx_key, {sid: ts})
        return True
    except Exception as e:
        _LOG.warning(
            "[AI问答-会话同步|chat_session_store.sync_session_to_redis|session:%s|硬编执行|Redis] 写入失败; error_type=%s; error_message=%s",
            sid,
            type(e).__name__,
            e,
        )
        return False


def sync_all_dirty() -> int:
    """同步所有脏会话到 Redis。"""
    with _lock:
        ids = list(_dirty_ids)
    ok = 0
    for sid in ids:
        if sync_session_to_redis(sid):
            ok += 1
            with _lock:
                _dirty_ids.discard(sid)
    if ids:
        _LOG.info(
            "[AI问答-会话同步|chat_session_store.sync_all_dirty|batch|硬编执行|定时] 完成; total=%s; ok=%s; redis_ready=%s",
            len(ids),
            ok,
            _redis_client is not None,
        )
    return ok


def _schedule_sync_loop() -> None:
    global _sync_timer

    def _tick():
        global _sync_timer
        try:
            sync_all_dirty()
        except Exception:
            _LOG.exception("chat session redis sync tick failed")
        _sync_timer = threading.Timer(_SYNC_INTERVAL_SEC, _tick)
        _sync_timer.daemon = True
        _sync_timer.start()

    _sync_timer = threading.Timer(_SYNC_INTERVAL_SEC, _tick)
    _sync_timer.daemon = True
    _sync_timer.start()


def start_periodic_redis_sync(interval_sec: int = _SYNC_INTERVAL_SEC) -> None:
    global _SYNC_INTERVAL_SEC
    _SYNC_INTERVAL_SEC = max(15, int(interval_sec))
    _init_redis()
    _schedule_sync_loop()
    _LOG.info(
        "[AI问答-会话同步|chat_session_store.start_periodic_redis_sync|scheduler|硬编执行|启动] 已启动; interval_sec=%s; redis_ready=%s; redis_error=%s",
        _SYNC_INTERVAL_SEC,
        _redis_client is not None,
        _redis_error or "none",
    )


def load_all() -> Tuple[Dict[str, Dict], Dict[str, List]]:
    """启动时从本地目录加载全部会话到内存结构。"""
    sessions: Dict[str, Dict] = {}
    messages: Dict[str, List] = {}
    _ensure_dir()
    idx = _read_index()
    meta_map = idx.get("sessions") or {}
    for sid, meta in meta_map.items():
        p = _session_path(sid)
        if not p.exists():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        sm = doc.get("session") or meta
        sm["id"] = sid
        sessions[sid] = sm
        messages[sid] = doc.get("messages") or []
    # 兼容仅有文件、索引缺失
    for p in _CHAT_DIR.glob("*.json"):
        if p.name == "index.json":
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = (doc.get("session") or {}).get("id") or p.stem
        if sid in sessions:
            continue
        sm = doc.get("session") or {"id": sid, "title": "新对话"}
        sm["id"] = sid
        sessions[sid] = sm
        messages[sid] = doc.get("messages") or []
    _LOG.info(
        "[AI问答-会话加载|chat_session_store.load_all|本地目录|硬编执行|启动] 完成; session_count=%s",
        len(sessions),
    )
    return sessions, messages


def persist_session(
    sid: str,
    session_meta: Dict[str, Any],
    messages: List[Any],
    *,
    cur_task: Optional[Dict] = None,
    main_task_history: Optional[List] = None,
    prefs: Optional[Dict] = None,
    memory_meta: Optional[Dict] = None,
    mark_dirty: bool = True,
) -> None:
    """写入本地 JSON 并标记 Redis 同步。"""
    _ensure_dir()
    now = _now_iso()
    meta = dict(session_meta)
    meta["id"] = sid
    meta["updated_at"] = now
    if not meta.get("created_at"):
        meta["created_at"] = now
    # 估算上下文 token（字符/2 粗估）
    ctx_chars = sum(len(str(m.get("content") or "")) for m in messages if isinstance(m, dict))
    meta["context_chars"] = ctx_chars
    summary_tok = 0
    if isinstance(memory_meta, dict):
        summary_tok = int(memory_meta.get("summary_tokens_est") or 0)
    elif prev := get_session_document(sid):
        sm_prev = (prev or {}).get("memory_meta") or {}
        if isinstance(sm_prev, dict):
            summary_tok = int(sm_prev.get("summary_tokens_est") or 0)
    meta["context_tokens_est"] = max(1, ctx_chars // 2 + summary_tok)

    if isinstance(main_task_history, list):
        hist = main_task_history
    else:
        prev = get_session_document(sid)
        hist = (prev or {}).get("main_task_history") if prev else []
        if not isinstance(hist, list):
            hist = []

    mm = memory_meta
    if mm is None:
        prev = get_session_document(sid)
        mm = (prev or {}).get("memory_meta") if prev else {}
    if not isinstance(mm, dict):
        mm = {}

    doc = {
        "session": meta,
        "messages": messages,
        "cur_task": cur_task,
        "main_task_history": hist,
        "prefs": prefs or {},
        "memory_meta": mm,
        "saved_at": now,
    }
    _session_path(sid).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    idx = _read_index()
    sessions = idx.setdefault("sessions", {})
    sessions[sid] = {
        "id": sid,
        "title": meta.get("title") or "新对话",
        "created_at": meta.get("created_at"),
        "updated_at": now,
        "context_tokens_est": meta.get("context_tokens_est", 0),
        "status": meta.get("status", "active"),
    }
    _write_index(idx)

    if mark_dirty:
        with _lock:
            _dirty_ids.add(sid)


def delete_local(sid: str) -> None:
    p = _session_path(sid)
    if p.exists():
        p.unlink(missing_ok=True)
    idx = _read_index()
    sessions = idx.get("sessions") or {}
    sessions.pop(sid, None)
    idx["sessions"] = sessions
    _write_index(idx)
    with _lock:
        _dirty_ids.discard(sid)
    _init_redis()
    if _redis_client:
        try:
            _redis_client.delete(f"{_REDIS_PREFIX}:{sid}")
            _redis_client.zrem(f"{_REDIS_PREFIX}:index", sid)
        except Exception:
            pass


def get_session_document(sid: str) -> Optional[Dict[str, Any]]:
    p = _session_path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def export_markdown(sid: str) -> str:
    doc = get_session_document(sid) or {}
    meta = doc.get("session") or {}
    title = meta.get("title") or sid
    lines = [f"# {title}", "", f"- 会话 ID: `{sid}`", f"- 导出时间: {_now_iso()}", ""]
    for m in doc.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "unknown"
        content = str(m.get("content") or "").strip()
        lines.append(f"## {'用户' if role == 'user' else '助手'}")
        lines.append("")
        lines.append(content or "（空）")
        lines.append("")
        thinking = m.get("thinking") or []
        if thinking:
            lines.append("### 工具与推理链")
            for t in thinking:
                if not isinstance(t, dict):
                    continue
                name = t.get("step_name") or t.get("operation") or "步骤"
                dur = t.get("duration_ms")
                dur_s = f" ({dur}ms)" if dur else ""
                lines.append(f"- **{name}**{dur_s}: {t.get('description') or t.get('result_brief') or ''}")
            lines.append("")
    return "\n".join(lines)
