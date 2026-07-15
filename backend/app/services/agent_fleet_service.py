"""多 Agent 舰队管理 — 真实 CLI 探测与派发、作用域锁、状态机、日志持久化。"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple

_log = logging.getLogger("sba.agent_fleet")

# ── 路径 ──
_WEB_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _WEB_ROOT / "backend" / "data" / "agent_fleet"
_STORE_PATH = _DATA_DIR / "store.json"
_LOG_DIR = _DATA_DIR / "logs"

_store_lock = threading.RLock()
_run_lock = threading.RLock()
_active_procs: Dict[str, subprocess.Popen] = {}

# ── Harness 注册表（Codex / CC / Cursor 等）──
HARNESS_SPECS: Dict[str, Dict[str, Any]] = {
    "claude_code": {
        "label": "Claude Code",
        "binaries": ["claude", "claude.exe"],
        "build_cmd": lambda b, prompt: [b, "-p", prompt, "--print"],
    },
    "codex": {
        "label": "Codex CLI",
        "binaries": ["codex", "codex.exe"],
        "build_cmd": lambda b, prompt: [b, "exec", "-p", prompt],
    },
    "cursor": {
        "label": "Cursor Agent",
        "binaries": ["cursor-agent", "agent", "cursor"],
        "build_cmd": lambda b, prompt: [b, "-p", prompt, "--print"],
    },
    "opencode": {
        "label": "OpenCode",
        "binaries": ["opencode", "opencode.exe"],
        "build_cmd": lambda b, prompt: [b, "run", prompt],
    },
    "shell": {
        "label": "Shell（验证用）",
        "binaries": ["cmd", "powershell", "bash", "sh"],
        "build_cmd": None,
    },
}

SESSION_STATUSES = ("pending", "running", "review", "done", "failed", "cancelled")
SESSION_ROLES = ("planner", "implementer", "reviewer", "explorer", "orchestrator")
STATUS_LABELS = {
    "pending": "待派发",
    "running": "运行中",
    "review": "待审查",
    "done": "已完成",
    "failed": "已失败",
    "cancelled": "已取消",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dirs() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


def _default_store() -> Dict[str, Any]:
    return {"projects": [], "sessions": [], "ownership": [], "updated_at": _utc_now()}


def _load_store() -> Dict[str, Any]:
    _ensure_dirs()
    if not _STORE_PATH.is_file():
        return _default_store()
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_store()
        data.setdefault("projects", [])
        data.setdefault("sessions", [])
        data.setdefault("ownership", [])
        return data
    except Exception as ex:
        _log.warning(
            "[多Agent管理-持久化|agent_fleet_service._load_store|store.json|硬编执行|读取] "
            "失败; error_type=%s; error_message=%s",
            type(ex).__name__,
            str(ex)[:120],
        )
        return _default_store()


def _save_store(store: Dict[str, Any]) -> None:
    _ensure_dirs()
    store["updated_at"] = _utc_now()
    tmp = _STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STORE_PATH)


def _find_binary(candidates: Iterable[str]) -> Optional[str]:
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def probe_harnesses() -> List[Dict[str, Any]]:
    """探测本机已安装的 Agent CLI。"""
    out: List[Dict[str, Any]] = []
    for hid, spec in HARNESS_SPECS.items():
        binary = _find_binary(spec.get("binaries") or [])
        out.append(
            {
                "harness_id": hid,
                "label": spec.get("label") or hid,
                "available": bool(binary),
                "binary_path": binary or "",
                "binaries": list(spec.get("binaries") or []),
            }
        )
    return out


def list_projects() -> List[Dict[str, Any]]:
    store = _load_store()
    return list(store.get("projects") or [])


def add_project(*, name: str, workspace_path: str, default_harness: str = "claude_code") -> Dict[str, Any]:
    name = (name or "").strip()
    workspace_path = str(Path((workspace_path or "").strip()).resolve())
    if not name:
        raise ValueError("项目名称不能为空")
    if not Path(workspace_path).is_dir():
        raise ValueError(f"工作区路径不存在: {workspace_path}")
    if default_harness not in HARNESS_SPECS:
        raise ValueError(f"未知 harness: {default_harness}")

    with _store_lock:
        store = _load_store()
        for p in store.get("projects") or []:
            if str(p.get("workspace_path") or "") == workspace_path:
                raise ValueError("该工作区已注册")
        proj = {
            "project_id": f"proj_{uuid.uuid4().hex[:10]}",
            "name": name,
            "workspace_path": workspace_path,
            "default_harness": default_harness,
            "created_at": _utc_now(),
        }
        store.setdefault("projects", []).append(proj)
        _save_store(store)
    _log.info(
        "[多Agent管理-项目|agent_fleet_service.add_project|project|硬编执行|创建] "
        "完成; project_id=%s; workspace=%s; ok=true",
        proj["project_id"],
        workspace_path,
    )
    return proj


def delete_project(project_id: str) -> bool:
    pid = (project_id or "").strip()
    with _store_lock:
        store = _load_store()
        before = len(store.get("projects") or [])
        store["projects"] = [p for p in (store.get("projects") or []) if str(p.get("project_id")) != pid]
        if len(store["projects"]) == before:
            return False
        # 释放关联 ownership
        store["ownership"] = [
            o for o in (store.get("ownership") or []) if str(o.get("project_id")) != pid
        ]
        _save_store(store)
    return True


def list_sessions(*, project_id: str = "", status: str = "") -> List[Dict[str, Any]]:
    store = _load_store()
    rows = list(store.get("sessions") or [])
    if project_id:
        rows = [s for s in rows if str(s.get("project_id") or "") == project_id.strip()]
    if status:
        rows = [s for s in rows if str(s.get("status") or "") == status.strip()]
    rows.sort(key=lambda s: str(s.get("updated_at") or s.get("created_at") or ""), reverse=True)
    return rows


def _get_project(store: Dict[str, Any], project_id: str) -> Optional[Dict[str, Any]]:
    for p in store.get("projects") or []:
        if str(p.get("project_id") or "") == project_id:
            return p
    return None


def _get_session(store: Dict[str, Any], session_id: str) -> Optional[Dict[str, Any]]:
    for s in store.get("sessions") or []:
        if str(s.get("session_id") or "") == session_id:
            return s
    return None


def _scope_conflicts(store: Dict[str, Any], project_id: str, scope_paths: List[str], exclude_session: str = "") -> List[str]:
    """检查作用域是否与活跃 ownership 冲突。"""
    active_status = {"pending", "running", "review"}
    conflicts: List[str] = []
    norm_scopes = [_norm_scope(s) for s in scope_paths if s]
    for own in store.get("ownership") or []:
        if str(own.get("project_id") or "") != project_id:
            continue
        sid = str(own.get("session_id") or "")
        if sid == exclude_session:
            continue
        sess = _get_session(store, sid)
        if not sess or str(sess.get("status") or "") not in active_status:
            continue
        for sp in own.get("scope_paths") or []:
            nsp = _norm_scope(str(sp))
            for req in norm_scopes:
                if _scopes_overlap(req, nsp):
                    conflicts.append(f"{sid}:{nsp}")
    return conflicts


def _norm_scope(path: str) -> str:
    p = str(path or "").strip().replace("\\", "/")
    if not p:
        return ""
    if not p.startswith("/") and ":" not in p[:3]:
        return p.rstrip("/")
    return p.rstrip("/")


def _scopes_overlap(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def create_session(
    *,
    project_id: str,
    harness_id: str,
    role: str,
    prompt: str,
    scope_paths: Optional[List[str]] = None,
    parent_session_id: str = "",
    title: str = "",
) -> Dict[str, Any]:
    pid = (project_id or "").strip()
    hid = (harness_id or "").strip()
    role = (role or "implementer").strip()
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("任务提示词不能为空")
    if hid not in HARNESS_SPECS:
        raise ValueError(f"未知 harness: {hid}")
    if role not in SESSION_ROLES:
        raise ValueError(f"未知角色: {role}")

    scopes = [str(s).strip() for s in (scope_paths or []) if str(s).strip()]
    with _store_lock:
        store = _load_store()
        proj = _get_project(store, pid)
        if not proj:
            raise ValueError("项目不存在")
        conflicts = _scope_conflicts(store, pid, scopes)
        if conflicts:
            raise ValueError(f"作用域与活跃会话冲突: {', '.join(conflicts[:5])}")

        sid = f"fleet_{uuid.uuid4().hex[:12]}"
        sess = {
            "session_id": sid,
            "task_id": sid,
            "task_kind": "fleet",
            "project_id": pid,
            "project_name": proj.get("name") or "",
            "workspace_path": proj.get("workspace_path") or "",
            "harness_id": hid,
            "harness_label": HARNESS_SPECS[hid].get("label") or hid,
            "role": role,
            "title": (title or prompt[:80]).strip(),
            "prompt": prompt,
            "scope_paths": scopes,
            "parent_session_id": (parent_session_id or "").strip(),
            "status": "pending",
            "exit_code": None,
            "error_message": "",
            "binary_path": "",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "started_at": "",
            "finished_at": "",
        }
        store.setdefault("sessions", []).append(sess)
        if scopes:
            store.setdefault("ownership", []).append(
                {
                    "session_id": sid,
                    "project_id": pid,
                    "harness_id": hid,
                    "scope_paths": scopes,
                    "claimed_at": _utc_now(),
                }
            )
        _save_store(store)
    _log.info(
        "[多Agent管理-会话|agent_fleet_service.create_session|session|硬编执行|创建] "
        "完成; session_id=%s; harness=%s; role=%s; ok=true",
        sid,
        hid,
        role,
    )
    return sess


def create_orchestration_plan(
    *,
    project_id: str,
    goal: str,
    implement_harness: str = "codex",
    review_harness: str = "claude_code",
    scope_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """三角色编排：Planner(CC) → Implementer → Reviewer（跨厂商）。"""
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("目标描述不能为空")
    scopes = scope_paths or ["src/", "backend/", "frontend/"]
    planner = create_session(
        project_id=project_id,
        harness_id="claude_code",
        role="planner",
        title=f"规划: {goal[:40]}",
        prompt=f"你是技术总监。请为以下目标输出结构化实施计划（步骤、验收标准、风险），不要直接改代码：\n\n{goal}",
        scope_paths=[],
    )
    impl = create_session(
        project_id=project_id,
        harness_id=implement_harness,
        role="implementer",
        title=f"实现: {goal[:40]}",
        prompt=f"按计划实现以下目标。先阅读项目 AGENTS.md / README。目标：\n\n{goal}",
        scope_paths=scopes,
        parent_session_id=planner["session_id"],
    )
    review = create_session(
        project_id=project_id,
        harness_id=review_harness,
        role="reviewer",
        title=f"审查: {goal[:40]}",
        prompt=(
            f"审查 implementer 会话 {impl['session_id']} 的产出。"
            f"对照目标做 cross-review，只报告问题不改代码。目标：\n\n{goal}"
        ),
        scope_paths=scopes,
        parent_session_id=impl["session_id"],
    )
    return {
        "plan_id": f"plan_{uuid.uuid4().hex[:10]}",
        "goal": goal,
        "sessions": [planner, impl, review],
    }


def _append_log(session_id: str, line: str, *, stream: str = "stdout") -> None:
    _ensure_dirs()
    path = _LOG_DIR / f"{session_id}.jsonl"
    rec = {"ts": _utc_now(), "stream": stream, "line": line.rstrip("\n")[:8000]}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _update_session(session_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    with _store_lock:
        store = _load_store()
        sess = _get_session(store, session_id)
        if not sess:
            return None
        for k, v in fields.items():
            sess[k] = v
        sess["updated_at"] = _utc_now()
        _save_store(store)
        return dict(sess)


def _release_ownership(session_id: str) -> None:
    with _store_lock:
        store = _load_store()
        store["ownership"] = [
            o for o in (store.get("ownership") or []) if str(o.get("session_id")) != session_id
        ]
        _save_store(store)


def _build_dispatch_cmd(harness_id: str, binary: str, prompt: str) -> List[str]:
    spec = HARNESS_SPECS.get(harness_id) or {}
    builder = spec.get("build_cmd")
    if harness_id == "shell":
        # Windows 验证路径：echo 任务摘要
        if os.name == "nt":
            return ["cmd", "/c", f"echo [SBA-FLEET] {prompt[:200]}"]
        return ["sh", "-c", f"echo '[SBA-FLEET] {prompt[:200]}'"]
    if not callable(builder):
        raise ValueError(f"Harness {harness_id} 不支持 CLI 派发")
    return builder(binary, prompt)


def _run_session_thread(session_id: str) -> None:
    store = _load_store()
    sess = _get_session(store, session_id)
    if not sess:
        return
    hid = str(sess.get("harness_id") or "")
    spec = HARNESS_SPECS.get(hid) or {}
    workspace = str(sess.get("workspace_path") or "")
    prompt = str(sess.get("prompt") or "")

    binary = _find_binary(spec.get("binaries") or [])
    if not binary and hid != "shell":
        _update_session(
            session_id,
            status="failed",
            error_message=f"未找到 {spec.get('label') or hid} CLI，请先安装并加入 PATH",
            finished_at=_utc_now(),
        )
        _release_ownership(session_id)
        _append_log(session_id, f"ERROR: CLI not found for {hid}", stream="stderr")
        return

    try:
        cmd = _build_dispatch_cmd(hid, binary or "", prompt)
    except Exception as ex:
        _update_session(session_id, status="failed", error_message=str(ex), finished_at=_utc_now())
        _release_ownership(session_id)
        return

    _update_session(
        session_id,
        status="running",
        binary_path=binary or cmd[0],
        started_at=_utc_now(),
        error_message="",
    )
    _append_log(session_id, f"CMD: {' '.join(cmd[:6])}{'...' if len(cmd) > 6 else ''}", stream="meta")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=workspace if Path(workspace).is_dir() else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "SBA_FLEET_SESSION": session_id},
        )
    except Exception as ex:
        _update_session(
            session_id,
            status="failed",
            error_message=str(ex),
            finished_at=_utc_now(),
            exit_code=-1,
        )
        _release_ownership(session_id)
        _append_log(session_id, f"SPAWN ERROR: {ex}", stream="stderr")
        return

    with _run_lock:
        _active_procs[session_id] = proc

    stdout_data, stderr_data = proc.communicate()
    exit_code = proc.returncode
    if stdout_data:
        for line in stdout_data.splitlines():
            _append_log(session_id, line, stream="stdout")
    if stderr_data:
        for line in stderr_data.splitlines():
            _append_log(session_id, line, stream="stderr")

    with _run_lock:
        _active_procs.pop(session_id, None)

    role = str(sess.get("role") or "")
    if exit_code == 0:
        new_status = "review" if role in ("implementer", "explorer") else "done"
    else:
        new_status = "failed"
    _update_session(
        session_id,
        status=new_status,
        exit_code=exit_code,
        finished_at=_utc_now(),
        error_message="" if exit_code == 0 else f"进程退出码 {exit_code}",
    )
    if new_status in ("done", "failed", "cancelled"):
        _release_ownership(session_id)
    _log.info(
        "[多Agent管理-派发|agent_fleet_service._run_session_thread|session|Agent执行|结束] "
        "完成; session_id=%s; exit_code=%s; status=%s; ok=%s",
        session_id,
        exit_code,
        new_status,
        exit_code == 0,
    )


def dispatch_session(session_id: str) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    with _store_lock:
        store = _load_store()
        sess = _get_session(store, sid)
        if not sess:
            raise ValueError("会话不存在")
        if str(sess.get("status") or "") != "pending":
            raise ValueError(f"仅 pending 状态可派发，当前: {sess.get('status')}")

    t = threading.Thread(target=_run_session_thread, args=(sid,), daemon=True, name=f"fleet-{sid}")
    t.start()
    return {"ok": True, "session_id": sid, "message": "已启动真实 CLI 派发"}


def dispatch_plan_sessions(session_ids: List[str]) -> Dict[str, Any]:
    results = []
    for sid in session_ids:
        try:
            results.append({"session_id": sid, **dispatch_session(sid)})
        except Exception as ex:
            results.append({"session_id": sid, "ok": False, "error": str(ex)})
    return {"ok": True, "results": results}


def cancel_session(session_id: str) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    with _run_lock:
        proc = _active_procs.get(sid)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        _active_procs.pop(sid, None)
    sess = _update_session(sid, status="cancelled", finished_at=_utc_now(), error_message="用户取消")
    if not sess:
        raise ValueError("会话不存在")
    _release_ownership(sid)
    _append_log(sid, "CANCELLED by user", stream="meta")
    return {"ok": True, "session": sess}


def mark_session_review_done(session_id: str, *, approved: bool) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    store = _load_store()
    sess = _get_session(store, sid)
    if not sess:
        raise ValueError("会话不存在")
    if str(sess.get("status") or "") != "review":
        raise ValueError("仅 review 状态可确认")
    new_status = "done" if approved else "failed"
    out = _update_session(
        sid,
        status=new_status,
        finished_at=_utc_now(),
        error_message="" if approved else "审查未通过",
    )
    _release_ownership(sid)
    return {"ok": True, "session": out}


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    return _get_session(_load_store(), (session_id or "").strip())


def list_ownership(*, project_id: str = "") -> List[Dict[str, Any]]:
    store = _load_store()
    rows = list(store.get("ownership") or [])
    if project_id:
        rows = [o for o in rows if str(o.get("project_id") or "") == project_id.strip()]
    return rows


def read_session_logs(session_id: str, *, tail: int = 200) -> List[Dict[str, Any]]:
    path = _LOG_DIR / f"{(session_id or '').strip()}.jsonl"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-max(1, tail) :]:
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"ts": "", "stream": "raw", "line": line})
    return out


def iter_session_log_sse(session_id: str, *, poll_sec: float = 0.8, idle_rounds: int = 30) -> Generator[str, None, None]:
    """SSE：追读会话日志直到终态或空闲。"""
    sid = (session_id or "").strip()
    path = _LOG_DIR / f"{sid}.jsonl"
    offset = 0
    idle = 0
    terminal = {"done", "failed", "cancelled"}

    def _emit(event: str, data: Dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield _emit("stream_open", {"session_id": sid})

    while idle < idle_rounds:
        sess = get_session(sid)
        if sess:
            yield _emit("session_status", {"status": sess.get("status"), "session": sess})

        if path.is_file():
            raw = path.read_text(encoding="utf-8", errors="replace")
            if len(raw) > offset:
                chunk = raw[offset:]
                offset = len(raw)
                idle = 0
                for line in chunk.splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        rec = {"line": line}
                    yield _emit("log_line", rec)
            else:
                idle += 1
        else:
            idle += 1

        if sess and str(sess.get("status") or "") in terminal:
            yield _emit("session_done", {"status": sess.get("status"), "session": sess})
            break
        time.sleep(poll_sec)

    yield _emit("stream_end", {"session_id": sid})


def collect_fleet_registry_rows(*, limit: int = 200) -> List[Dict[str, Any]]:
    """供 task_registry 聚合的 fleet 任务行。"""
    rows: List[Dict[str, Any]] = []
    for s in list_sessions()[:limit]:
        sid = str(s.get("session_id") or "")
        rows.append(
            {
                "task_id": sid,
                "task_kind": "fleet",
                "session_id": sid,
                "user_query": str(s.get("prompt") or "")[:500],
                "query_summary": str(s.get("title") or sid)[:80],
                "status": str(s.get("status") or ""),
                "updated_at": str(s.get("updated_at") or s.get("created_at") or ""),
                "created_at": str(s.get("created_at") or ""),
                "harness_id": s.get("harness_id"),
                "harness_label": s.get("harness_label"),
                "role": s.get("role"),
                "project_name": s.get("project_name"),
                "redis_present": False,
                "mysql_synced": False,
                "mysql_table": "agent_fleet_store",
            }
        )
    return rows


def get_fleet_summary() -> Dict[str, Any]:
    harnesses = probe_harnesses()
    sessions = list_sessions()
    by_status: Dict[str, int] = {}
    for s in sessions:
        st = str(s.get("status") or "unknown")
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "harnesses": harnesses,
        "projects_count": len(list_projects()),
        "sessions_count": len(sessions),
        "sessions_by_status": by_status,
        "active_runs": len(_active_procs),
        "data_dir": str(_DATA_DIR),
    }
