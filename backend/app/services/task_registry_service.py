"""全局任务中心 — Redis/热缓存优先，MySQL（span_tasks / pipeline_task_history）补全。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("sba.task_registry")

_HEX_TASK_RE = re.compile(r"^[0-9a-f]{12}$")


def _pipeline_db_enabled() -> bool:
    try:
        from . import pipeline_history_store as phs

        return phs.is_enabled()
    except Exception:
        return False


def _span_db_enabled() -> bool:
    try:
        from . import span_audit as sa

        sa._ensure_db_async_ready()
        return bool(sa._db_available)
    except Exception:
        return False


def _mysql_info() -> Dict[str, bool]:
    return {
        "span_tasks": _span_db_enabled(),
        "pipeline_task_history": _pipeline_db_enabled(),
    }


def _classify_kind(task_id: str, task: Dict[str, Any]) -> str:
    tid = (task_id or "").strip()
    if tid.startswith("task_"):
        return "main"
    try:
        from .span_audit import _is_pipeline_span_task

        if _is_pipeline_span_task(task):
            return "pipeline"
    except Exception:
        pass
    if (task.get("link") or "").strip():
        return "pipeline"
    if _HEX_TASK_RE.match(tid):
        return "pipeline"
    return "main"


def _ts_key(row: Dict[str, Any]) -> str:
    return str(row.get("updated_at") or row.get("created_at") or "")


def _merge_row(prev: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    if not prev:
        return dict(new)
    out = dict(prev)
    for k, v in new.items():
        if v is None or v == "":
            continue
        if k in ("redis_present", "mysql_synced"):
            out[k] = bool(prev.get(k)) or bool(v)
            continue
        if k == "redis_present" and v:
            out[k] = True
            continue
        if not prev.get(k) or k in ("status", "updated_at", "user_query", "query_summary", "progress", "link"):
            out[k] = v
    out["redis_present"] = bool(prev.get("redis_present")) or bool(new.get("redis_present"))
    out["mysql_synced"] = bool(prev.get("mysql_synced")) or bool(new.get("mysql_synced"))
    return out


def _main_row(
    task: Dict[str, Any],
    *,
    redis_present: bool,
    mysql_synced: bool,
    session_id: str = "",
) -> Dict[str, Any]:
    tid = str(task.get("task_id") or "").strip()
    uq = str(task.get("user_query") or "").strip()
    qs = str(task.get("query_summary") or uq or tid).strip()[:80]
    return {
        "task_id": tid,
        "task_kind": "main",
        "session_id": session_id or str(task.get("session_id") or ""),
        "user_query": uq,
        "query_summary": qs,
        "status": str(task.get("status") or ""),
        "updated_at": _ts_key(task),
        "created_at": str(task.get("created_at") or ""),
        "total_duration_ms": task.get("total_duration_ms"),
        "total_token_count": task.get("total_token_count"),
        "redis_present": redis_present,
        "mysql_synced": mysql_synced,
        "mysql_table": "span_tasks",
    }


def _pipeline_row(
    task: Dict[str, Any],
    *,
    redis_present: bool,
    mysql_synced: bool,
) -> Dict[str, Any]:
    tid = str(task.get("task_id") or task.get("id") or "").strip()
    link = str(task.get("link") or task.get("user_query") or "").strip()
    title = str(task.get("link_title") or task.get("title") or link or tid).strip()
    return {
        "task_id": tid,
        "task_kind": "pipeline",
        "session_id": str(task.get("session_id") or ""),
        "user_query": str(task.get("user_prompt") or task.get("user_query") or link),
        "query_summary": title[:80],
        "link": link,
        "platform": str(task.get("platform") or ""),
        "status": str(task.get("status") or ""),
        "progress": task.get("progress"),
        "updated_at": _ts_key(task),
        "created_at": str(task.get("created_at") or ""),
        "redis_present": redis_present,
        "mysql_synced": mysql_synced,
        "mysql_table": "pipeline_task_history",
    }


def _collect_hot_span_tasks(*, limit: int = 400) -> List[Dict[str, Any]]:
    from . import span_audit as sa

    by_id: Dict[str, Dict[str, Any]] = {}

    def _put(task: Optional[Dict[str, Any]], *, redis_present: bool) -> None:
        if not task or not task.get("task_id"):
            return
        tid = str(task["task_id"])
        try:
            from .span_audit import is_task_in_span_db

            mysql_ok = is_task_in_span_db(tid)
        except Exception:
            mysql_ok = False
        kind = _classify_kind(tid, task)
        if kind == "pipeline":
            row = _pipeline_row(task, redis_present=redis_present, mysql_synced=mysql_ok)
        else:
            row = _main_row(task, redis_present=redis_present, mysql_synced=mysql_ok)
        by_id[tid] = _merge_row(by_id.get(tid), row)

    with sa._cache_lock:
        for k, t in sa._redis_cache.items():
            if k.startswith("task:"):
                _put(t, redis_present=True)

    client = sa._span_redis_client()
    if client is not None:
        try:
            n = 0
            for raw_key in client.scan_iter(match=f"{sa._SPAN_REDIS_PREFIX}:task:*", count=100):
                suffix = str(raw_key).split(":task:", 1)[-1]
                t = sa._span_redis_get(f"task:{suffix}")
                _put(t, redis_present=True)
                n += 1
                if n >= limit * 2:
                    break
        except Exception as ex:
            _log.info(
                "[任务中心-查询|task_registry_service._collect_hot_span_tasks|redis|硬编执行|扫描] "
                "失败; error_type=%s; error_message=%s",
                type(ex).__name__,
                str(ex)[:120],
            )

    for t in sa._collect_span_tasks_from_local(max_files=limit * 2):
        _put(t, redis_present=True)

    return list(by_id.values())


def _collect_session_main_tasks() -> List[Dict[str, Any]]:
    from .chat_session_store import get_session_document, _read_index, _init_redis, _redis_client, _REDIS_PREFIX

    rows: List[Dict[str, Any]] = []
    idx = _read_index()
    sids = list((idx.get("sessions") or {}).keys())
    _init_redis()

    for sid in sids:
        doc = None
        redis_hit = False
        if _redis_client is not None:
            try:
                raw = _redis_client.get(f"{_REDIS_PREFIX}:{sid}")
                if raw:
                    doc = json.loads(raw)
                    redis_hit = True
            except Exception:
                doc = None
        if doc is None:
            doc = get_session_document(sid)
        if not doc:
            continue
        hist = doc.get("main_task_history") or []
        if not isinstance(hist, list):
            continue
        for h in hist:
            if not isinstance(h, dict):
                continue
            tid = str(h.get("task_id") or "").strip()
            if not tid.startswith("task_"):
                continue
            try:
                from .span_audit import is_task_in_span_db

                mysql_ok = is_task_in_span_db(tid)
            except Exception:
                mysql_ok = False
            merged = {**h, "session_id": sid}
            rows.append(
                _main_row(
                    merged,
                    redis_present=redis_hit,
                    mysql_synced=mysql_ok,
                    session_id=sid,
                )
            )
    return rows


def _collect_pipeline_runtime(*, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        from .task_manager import list_tasks as mem_list

        for t in mem_list() or []:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("task_id") or t.get("id") or "").strip()
            if not tid:
                continue
            mysql_ok = False
            if _pipeline_db_enabled():
                try:
                    from . import pipeline_history_store as phs

                    mysql_ok = phs.get_by_task_id(tid) is not None
                except Exception:
                    mysql_ok = False
            rows.append(_pipeline_row(t, redis_present=True, mysql_synced=mysql_ok))
            if len(rows) >= limit:
                break
    except Exception:
        pass
    return rows


def _collect_mysql_supplement(*, limit: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    mains: List[Dict[str, Any]] = []
    pipes: List[Dict[str, Any]] = []

    if _span_db_enabled():
        try:
            import db as _db

            db_rows = _db.execute_query(
                "SELECT * FROM span_tasks ORDER BY updated_at DESC LIMIT %s",
                (limit * 2,),
            )
            for r in db_rows or []:
                task = dict(r)
                for k in ("started_at", "ended_at", "created_at", "updated_at"):
                    if task.get(k) is not None and hasattr(task[k], "strftime"):
                        task[k] = task[k].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                tid = str(task.get("task_id") or "")
                kind = _classify_kind(tid, task)
                if kind == "pipeline":
                    pipes.append(_pipeline_row(task, redis_present=False, mysql_synced=True))
                else:
                    mains.append(_main_row(task, redis_present=False, mysql_synced=True))
        except Exception as ex:
            _log.warning(
                "[任务中心-查询|task_registry_service._collect_mysql_supplement|span_tasks|硬编执行|读取] "
                "失败; error_type=%s; error_message=%s",
                type(ex).__name__,
                str(ex)[:160],
            )

    if _pipeline_db_enabled():
        try:
            from . import pipeline_history_store as phs

            for t in phs.list_tasks(limit=limit * 2):
                if not isinstance(t, dict):
                    continue
                tid = str(t.get("task_id") or t.get("id") or "").strip()
                if not tid:
                    continue
                pipes.append(_pipeline_row(t, redis_present=False, mysql_synced=True))
        except Exception as ex:
            _log.warning(
                "[任务中心-查询|task_registry_service._collect_mysql_supplement|pipeline_task_history|硬编执行|读取] "
                "失败; error_type=%s; error_message=%s",
                type(ex).__name__,
                str(ex)[:160],
            )

    return mains, pipes


def _apply_filters(
    rows: List[Dict[str, Any]],
    *,
    session_id: str,
    task_id: str,
    status: str,
    task_kind: str,
    name: str,
) -> List[Dict[str, Any]]:
    sid_f = (session_id or "").strip()
    tid_f = (task_id or "").strip().lower()
    st_f = (status or "").strip().lower()
    kind_f = (task_kind or "all").strip().lower()
    name_f = (name or "").strip().lower()

    out: List[Dict[str, Any]] = []
    for r in rows:
        if kind_f in ("main", "pipeline") and r.get("task_kind") != kind_f:
            continue
        if sid_f and str(r.get("session_id") or "") != sid_f:
            continue
        if tid_f and tid_f not in str(r.get("task_id") or "").lower():
            continue
        if st_f and st_f != str(r.get("status") or "").lower():
            continue
        if name_f:
            hay = " ".join(
                [
                    str(r.get("user_query") or ""),
                    str(r.get("query_summary") or ""),
                    str(r.get("link") or ""),
                    str(r.get("session_id") or ""),
                ]
            ).lower()
            if name_f not in hay:
                continue
        out.append(r)
    return out


def _sort_rows(rows: List[Dict[str, Any]], sort: str) -> List[Dict[str, Any]]:
    sk = (sort or "time_desc").strip()
    arr = list(rows)
    if sk == "id_asc":
        return sorted(arr, key=lambda x: str(x.get("task_id") or ""))
    if sk == "id_desc":
        return sorted(arr, key=lambda x: str(x.get("task_id") or ""), reverse=True)
    if sk == "name_asc":
        return sorted(
            arr,
            key=lambda x: str(x.get("user_query") or x.get("query_summary") or ""),
        )
    if sk == "name_desc":
        return sorted(
            arr,
            key=lambda x: str(x.get("user_query") or x.get("query_summary") or ""),
            reverse=True,
        )
    if sk == "time_asc":
        return sorted(arr, key=_ts_key)
    return sorted(arr, key=_ts_key, reverse=True)


def query_task_registry(
    *,
    session_id: str = "",
    task_id: str = "",
    status: str = "",
    task_kind: str = "all",
    name: str = "",
    sort: str = "time_desc",
    limit: int = 200,
) -> Dict[str, Any]:
    """Redis/热缓存优先聚合，MySQL 补全未出现在热层的单据。"""
    limit = max(1, min(int(limit or 200), 500))
    by_id: Dict[str, Dict[str, Any]] = {}

    def _ingest(row: Dict[str, Any]) -> None:
        tid = str(row.get("task_id") or "").strip()
        if not tid:
            return
        key = f"{tid}:{row.get('task_kind') or 'main'}"
        by_id[key] = _merge_row(by_id.get(key), row)

    # 1) Redis / 内存 / 本地 span_hot（权威热数据）
    for row in _collect_hot_span_tasks(limit=limit):
        _ingest(row)

    # 2) 会话级 main_task_history（AI 主任务，可能尚未写入 span）
    for row in _collect_session_main_tasks():
        _ingest(row)

    # 3) 流水线内存队列
    for row in _collect_pipeline_runtime(limit=limit):
        _ingest(row)

    # 4) MySQL 补全（仅补充热层没有的 task_id）
    mysql_mains, mysql_pipes = _collect_mysql_supplement(limit=limit)
    for row in mysql_mains + mysql_pipes:
        tid = str(row.get("task_id") or "")
        kind = row.get("task_kind") or "main"
        key = f"{tid}:{kind}"
        if key not in by_id:
            _ingest(row)
        else:
            prev = by_id[key]
            by_id[key] = _merge_row(prev, {"mysql_synced": True, "mysql_table": row.get("mysql_table")})

    rows = list(by_id.values())
    rows = _apply_filters(
        rows,
        session_id=session_id,
        task_id=task_id,
        status=status,
        task_kind=task_kind,
        name=name,
    )
    rows = _sort_rows(rows, sort)[:limit]

    redis_n = sum(1 for r in rows if r.get("redis_present"))
    mysql_n = sum(1 for r in rows if r.get("mysql_synced"))
    _log.info(
        "[任务中心-查询|task_registry_service.query_task_registry|registry|硬编执行|完成] "
        "total=%s; redis_present=%s; mysql_synced=%s",
        len(rows),
        redis_n,
        mysql_n,
    )
    return {
        "tasks": rows,
        "total": len(rows),
        "stats": {
            "main": sum(1 for r in rows if r.get("task_kind") == "main"),
            "pipeline": sum(1 for r in rows if r.get("task_kind") == "pipeline"),
            "redis_present_count": redis_n,
            "mysql_synced_count": mysql_n,
        },
        "mysql": _mysql_info(),
    }


def _kv_rows(obj: Any, *, prefix: str = "", max_depth: int = 3) -> List[Dict[str, str]]:
    """将 dict 展平为键值行（供前端双快照子表渲染）。"""
    rows: List[Dict[str, str]] = []
    if obj is None:
        return rows
    if not isinstance(obj, dict):
        return [{"key": prefix or "value", "value": str(obj)[:2000]}]
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict) and max_depth > 0:
            rows.extend(_kv_rows(v, prefix=key, max_depth=max_depth - 1))
        elif isinstance(v, list):
            try:
                val = json.dumps(v, ensure_ascii=False)
            except Exception:
                val = str(v)
            rows.append({"key": key, "value": val[:2000]})
        else:
            rows.append({"key": key, "value": "" if v is None else str(v)[:2000]})
    return rows


def _parse_snapshot_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


_OPEN_LAYER_KEYS = (
    "objective",
    "current_assessment",
    "progress_percent",
    "next_actions",
    "risk_flags",
    "context_summary",
    "tool_result_analysis",
    "decision",
    "confidence",
    "stop_reason",
    "metadata",
    "keywords",
    "needs_rag",
    "rewrite_state",
    "rewrite_confidence",
    "tool_io_brief",
)


def _aggregate_open_from_steps(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    open_layer: Dict[str, Any] = {}
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        for k in _OPEN_LAYER_KEYS:
            v = s.get(k)
            if v not in (None, "", [], {}):
                open_layer[k] = v
    return open_layer


def _tool_outputs_from_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from .span_audit import _tool_output_record_from_payload

    records: List[Dict[str, Any]] = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        out = s.get("output_payload")
        if not isinstance(out, dict):
            continue
        is_tool = out.get("tool_call") is True or str(s.get("step_type") or "") == "tool_call"
        if not is_tool and not out.get("tool_name"):
            continue
        rec = _tool_output_record_from_payload(
            str(s.get("step_id") or ""),
            s,
            out,
            status=str(s.get("status") or "completed"),
            timestamp=str(s.get("ended_at") or s.get("updated_at") or s.get("created_at") or ""),
        )
        if rec:
            if s.get("sub_plan_id"):
                rec["sub_plan_id"] = s.get("sub_plan_id")
            if s.get("react_round") is not None:
                rec["react_round"] = s.get("react_round")
            records.append(rec)
    return records


def _thinking_to_step(th: Dict[str, Any], *, task_id: str) -> Dict[str, Any]:
    phase = str(th.get("phase") or th.get("step_type") or th.get("type") or "").lower()
    node_kind = str(th.get("node_kind") or "").lower()
    step_type = phase or node_kind or "reasoning"
    if node_kind == "tool_call" or phase == "tool" or th.get("tool_name"):
        step_type = "tool_call"
    out_payload: Dict[str, Any] = {}
    if isinstance(th.get("output_payload"), dict):
        out_payload = dict(th["output_payload"])
    else:
        raw_out = th.get("output_text") or th.get("output")
        if isinstance(raw_out, str) and raw_out.strip().startswith("{"):
            try:
                parsed = json.loads(raw_out)
                if isinstance(parsed, dict):
                    out_payload = parsed
            except Exception:
                pass
        elif isinstance(raw_out, dict):
            out_payload = dict(raw_out)
    if (not out_payload or not out_payload.get("tool_name")) and (
        node_kind == "tool_call" or phase == "tool" or th.get("tool_name")
    ):
        tool_name = th.get("tool_name") or ""
        if not tool_name and th.get("step_name"):
            sn = str(th.get("step_name"))
            if ":" in sn:
                tool_name = sn.split(":", 1)[-1].strip()
            else:
                tool_name = sn.strip()
        inp_raw = th.get("input_text") or th.get("input")
        tool_args: Any = {}
        if isinstance(inp_raw, str) and inp_raw.strip().startswith("{"):
            try:
                tool_args = json.loads(inp_raw)
            except Exception:
                tool_args = {"raw": inp_raw[:500]}
        elif isinstance(inp_raw, dict):
            tool_args = inp_raw
        out_payload = {
            "tool_call": True,
            "tool_name": tool_name or "tool",
            "tool_args": tool_args,
            "tool_result": th.get("tool_result") or th.get("output_text") or th.get("result"),
        }
    return {
        "step_id": str(th.get("step_id") or th.get("id") or ""),
        "task_id": task_id,
        "step_type": step_type,
        "step_name": str(th.get("step_name") or th.get("description") or th.get("title") or phase or "步骤"),
        "status": str(th.get("status") or "completed"),
        "duration_ms": th.get("duration_ms") or th.get("cost_ms") or 0,
        "token_count": th.get("token_count") or 0,
        "decision": th.get("decision") or "",
        "objective": th.get("objective") or th.get("description") or "",
        "current_assessment": th.get("current_assessment") or th.get("think_text") or "",
        "output_payload": out_payload,
        "input_payload": th.get("input_payload") if isinstance(th.get("input_payload"), dict) else {},
        "ended_at": th.get("ended_at") or th.get("at") or "",
        "react_round": th.get("react_round"),
        "sub_plan_id": th.get("sub_plan_id") or "",
    }


def _session_task_fallback(task_id: str, session_id: str = "") -> Dict[str, Any]:
    """会话 JSON 回填：main_task_history + 消息 thinking 链。"""
    from .chat_session_store import get_session_document, _read_index

    sid = (session_id or "").strip()
    hist_entry: Dict[str, Any] = {}
    if not sid:
        try:
            idx = _read_index()
            for candidate in (idx.get("sessions") or {}):
                doc = get_session_document(candidate) or {}
                for h in doc.get("main_task_history") or []:
                    if isinstance(h, dict) and str(h.get("task_id") or "") == task_id:
                        sid = candidate
                        hist_entry = dict(h)
                        break
                if sid:
                    break
        except Exception:
            sid = ""
    doc = get_session_document(sid) if sid else None
    if doc and not hist_entry:
        for h in doc.get("main_task_history") or []:
            if isinstance(h, dict) and str(h.get("task_id") or "") == task_id:
                hist_entry = dict(h)
                break
    steps: List[Dict[str, Any]] = []
    if doc:
        msgs = doc.get("messages") or []
        idx_raw = hist_entry.get("result_msg_index")
        if idx_raw is not None:
            try:
                idx = int(idx_raw)
                if 0 <= idx < len(msgs) and isinstance(msgs[idx], dict):
                    thinking = msgs[idx].get("thinking") or []
                    if isinstance(thinking, list):
                        for th in thinking:
                            if isinstance(th, dict):
                                steps.append(_thinking_to_step(th, task_id=task_id))
            except (TypeError, ValueError):
                pass
        if not steps:
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                mtid = str(m.get("task_id") or "").strip()
                if mtid and mtid != task_id:
                    continue
                if not mtid and m.get("role") != "assistant":
                    continue
                thinking = m.get("thinking") or []
                if not isinstance(thinking, list):
                    continue
                for th in thinking:
                    if isinstance(th, dict):
                        steps.append(_thinking_to_step(th, task_id=task_id))
    open_layer = _aggregate_open_from_steps(steps)
    if hist_entry.get("rewrite_snapshot") and isinstance(hist_entry["rewrite_snapshot"], dict):
        rs = hist_entry["rewrite_snapshot"]
        for k in ("metadata", "keywords", "needs_rag", "rewrite_state", "rewrite_confidence"):
            if rs.get(k) not in (None, "", [], {}):
                open_layer.setdefault(k, rs.get(k))
        if rs.get("rewritten_query"):
            open_layer.setdefault("objective", rs.get("rewritten_query"))
    fixed_extra = {
        k: hist_entry.get(k)
        for k in (
            "user_query",
            "query_summary",
            "status",
            "task_kind",
            "async_pipeline_pending",
            "pipeline_task_ids",
            "result_msg_index",
            "result_status",
        )
        if hist_entry.get(k) not in (None, "", [], {})
    }
    return {
        "session_id": sid,
        "hist_entry": hist_entry,
        "steps": steps,
        "tool_outputs": _tool_outputs_from_steps(steps),
        "open_layer": open_layer,
        "fixed_extra": fixed_extra,
    }


def _rebuild_fixed_layer(task: Dict[str, Any], *, task_id: str, fixed_extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fixed: Dict[str, Any] = {}
    if isinstance(fixed_extra, dict):
        fixed.update(fixed_extra)
    for k in (
        "task_id",
        "session_id",
        "user_query",
        "rewritten_query",
        "query_summary",
        "intent",
        "status",
        "needs_multi_path",
        "started_at",
        "ended_at",
        "total_duration_ms",
        "total_token_count",
        "total_steps",
        "completed_steps",
        "failed_steps",
    ):
        v = task.get(k)
        if v not in (None, "", [], {}):
            fixed[k] = v
    if "task_id" not in fixed:
        fixed["task_id"] = task_id
    try:
        from .chat_context_memory import resolve_task_group_seq

        fixed.setdefault("group_seq", resolve_task_group_seq(task_id))
    except Exception:
        pass
    subs = task.get("sub_plans")
    if isinstance(subs, list) and subs:
        fixed["sub_plans_count"] = len(subs)
    return fixed


def _hydrate_main_task(task_id: str, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """补全主任务双快照 / 工具链 / SPAN 步骤（MySQL 与热缓存缺字段时回填）。"""
    from .span_audit import get_task, _load_steps_from_db

    base = dict(task or get_task(task_id) or {"task_id": task_id})
    steps = list(base.get("steps") or [])
    if not steps:
        steps = _load_steps_from_db(task_id)
    sess_fb = _session_task_fallback(task_id, str(base.get("session_id") or ""))
    if not base.get("session_id") and sess_fb.get("session_id"):
        base["session_id"] = sess_fb["session_id"]
    if not steps and sess_fb.get("steps"):
        steps = list(sess_fb["steps"])
    base["steps"] = steps

    snap = _parse_snapshot_json(base.get("snapshot_json"))
    fixed = snap.get("fixed") if isinstance(snap.get("fixed"), dict) else {}
    open_layer = snap.get("open") if isinstance(snap.get("open"), dict) else {}
    if not fixed:
        fixed = _rebuild_fixed_layer(base, task_id=task_id, fixed_extra=sess_fb.get("fixed_extra"))
    if not open_layer:
        open_layer = _aggregate_open_from_steps(steps)
    if not open_layer and isinstance(sess_fb.get("open_layer"), dict):
        open_layer = dict(sess_fb["open_layer"])

    tool_outputs_raw = base.get("tool_outputs") or snap.get("tool_outputs") or []
    if not isinstance(tool_outputs_raw, list):
        tool_outputs_raw = []
    if not tool_outputs_raw:
        tool_outputs_raw = _tool_outputs_from_steps(steps)
    if not tool_outputs_raw and sess_fb.get("tool_outputs"):
        tool_outputs_raw = list(sess_fb["tool_outputs"])

    hist = sess_fb.get("hist_entry") if isinstance(sess_fb.get("hist_entry"), dict) else {}
    for k in ("user_query", "query_summary", "status"):
        if not base.get(k) and hist.get(k):
            base[k] = hist.get(k)

    base["snapshot_json"] = {"fixed": fixed, "open": open_layer, "tool_outputs": tool_outputs_raw}
    base["tool_outputs"] = tool_outputs_raw
    base["total_steps"] = base.get("total_steps") or len(steps)
    return base


def _public_tool_output(rec: Dict[str, Any]) -> Dict[str, Any]:
    brief = ""
    try:
        from .tool_output_schema import brief_from_tool_payload

        brief = brief_from_tool_payload(
            {
                "tool_name": rec.get("tool_name"),
                "tool_result": rec.get("tool_result"),
                "status": rec.get("status"),
            },
            max_len=240,
        )
    except Exception:
        tr = rec.get("tool_result")
        brief = str(tr)[:240] if tr is not None else ""
    inp = rec.get("tool_args") or rec.get("input") or rec.get("arguments")
    out = rec.get("tool_result") or rec.get("output")
    try:
        inp_s = json.dumps(inp, ensure_ascii=False)[:800] if inp is not None else ""
    except Exception:
        inp_s = str(inp)[:800] if inp is not None else ""
    try:
        out_s = json.dumps(out, ensure_ascii=False)[:1200] if out is not None else ""
    except Exception:
        out_s = str(out)[:1200] if out is not None else ""
    return {
        "tool_name": str(rec.get("tool_name") or rec.get("tool") or "tool"),
        "status": str(rec.get("status") or ""),
        "react_round": rec.get("react_round"),
        "sub_plan_id": str(rec.get("sub_plan_id") or ""),
        "brief": brief,
        "input_preview": inp_s,
        "output_preview": out_s,
        "at": str(rec.get("at") or rec.get("timestamp") or ""),
    }


def get_task_registry_detail(task_id: str, *, task_kind: str = "") -> Dict[str, Any]:
    """任务中心详情：双快照 fixed/open 子表 + 工具链 + SPAN 步骤。"""
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id 为空"}

    kind = (task_kind or "").strip().lower()
    if kind not in ("main", "pipeline"):
        kind = "main" if tid.startswith("task_") else "pipeline"

    from .history_manager import _public_span_step, _public_span_task

    if kind == "main":
        from .history_manager import _public_span_step, _public_span_task
        from .chat_context_memory import resolve_task_group_seq

        task = _hydrate_main_task(tid)
        snap = _parse_snapshot_json(task.get("snapshot_json"))
        fixed = snap.get("fixed") if isinstance(snap.get("fixed"), dict) else {}
        open_layer = snap.get("open") if isinstance(snap.get("open"), dict) else {}
        tool_outputs_raw = task.get("tool_outputs") or snap.get("tool_outputs") or []
        if not isinstance(tool_outputs_raw, list):
            tool_outputs_raw = []
        steps_raw = task.get("steps") or []
        sub_plans = task.get("sub_plans") or []
        meta = {
            "task_id": tid,
            "task_kind": "main",
            "session_id": task.get("session_id") or "",
            "user_query": (task.get("user_query") or "")[:500],
            "rewritten_query": (task.get("rewritten_query") or "")[:500],
            "query_summary": (task.get("query_summary") or "")[:120],
            "intent": str(task.get("intent") or ""),
            "status": str(task.get("status") or ""),
            "group_seq": resolve_task_group_seq(tid) if tid else 0,
            "total_duration_ms": task.get("total_duration_ms"),
            "total_token_count": task.get("total_token_count"),
            "total_steps": task.get("total_steps") or len(steps_raw),
            "completed_steps": task.get("completed_steps"),
            "failed_steps": task.get("failed_steps"),
            "sub_plans_count": len(sub_plans) if isinstance(sub_plans, list) else 0,
            "tool_outputs_count": len(tool_outputs_raw),
            "snapshot_fixed_count": len(_kv_rows(fixed)),
            "snapshot_open_count": len(_kv_rows(open_layer)),
            "started_at": task.get("started_at"),
            "ended_at": task.get("ended_at"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }
        return {
            "ok": bool(task.get("task_id")),
            "task_id": tid,
            "task_kind": "main",
            "meta": meta,
            "span_task": _public_span_task(task) if task else None,
            "snapshot_fixed_rows": _kv_rows(fixed),
            "snapshot_open_rows": _kv_rows(open_layer),
            "tool_outputs": [_public_tool_output(r) for r in tool_outputs_raw if isinstance(r, dict)],
            "steps": [_public_span_step(s) for s in steps_raw if isinstance(s, dict)],
            "sub_plans": sub_plans if isinstance(sub_plans, list) else [],
        }

    from .history_manager import build_task_log_bundle

    bundle = build_task_log_bundle(tid)
    mem_task = None
    try:
        from .task_manager import get_task

        mem_task = get_task(tid)
    except Exception:
        mem_task = None
    span_task = bundle.get("span_task") or {}
    meta = {
        "task_id": tid,
        "task_kind": "pipeline",
        "session_id": span_task.get("session_id") or "",
        "link": bundle.get("link") or (mem_task or {}).get("link") or "",
        "platform": bundle.get("platform") or (mem_task or {}).get("platform") or "",
        "title": bundle.get("title") or "",
        "user_query": (span_task.get("user_query") or bundle.get("link") or "")[:500],
        "query_summary": (bundle.get("title") or bundle.get("link") or tid)[:120],
        "status": bundle.get("status") or span_task.get("status") or "",
        "progress": (mem_task or {}).get("progress"),
        "total_duration_ms": span_task.get("total_duration_ms"),
        "total_token_count": span_task.get("total_token_count"),
        "total_steps": span_task.get("total_steps") or len(bundle.get("spans") or []),
        "completed_steps": span_task.get("completed_steps"),
        "failed_steps": span_task.get("failed_steps"),
        "log_count": bundle.get("log_count") or 0,
        "source": bundle.get("source") or "",
    }
    tool_outputs: List[Dict[str, Any]] = []
    for s in bundle.get("spans") or []:
        if not isinstance(s, dict):
            continue
        if str(s.get("step_type") or "") != "tool_call":
            continue
        io = s.get("tool_io_brief") if isinstance(s.get("tool_io_brief"), dict) else {}
        tool_outputs.append(
            _public_tool_output(
                {
                    "tool_name": io.get("tool_name") or s.get("step_name"),
                    "status": s.get("status"),
                    "tool_args": (s.get("input_payload") or {}).get("args")
                    or (s.get("input_payload") or {}).get("arguments"),
                    "tool_result": s.get("output_payload"),
                    "at": s.get("ended_at") or s.get("started_at"),
                }
            )
        )
    snap = _parse_snapshot_json((mem_task or {}).get("snapshot_json"))
    fixed = snap.get("fixed") if isinstance(snap.get("fixed"), dict) else {}
    open_layer = snap.get("open") if isinstance(snap.get("open"), dict) else {}
    return {
        "ok": bool(bundle.get("ok")),
        "task_id": tid,
        "task_kind": "pipeline",
        "meta": meta,
        "span_task": span_task or None,
        "snapshot_fixed_rows": _kv_rows(fixed),
        "snapshot_open_rows": _kv_rows(open_layer),
        "tool_outputs": tool_outputs,
        "steps": bundle.get("spans") or [],
        "sub_plans": [],
        "errors": bundle.get("errors") or [],
    }


def sync_task_to_mysql(task_id: str, *, task_kind: str = "") -> Dict[str, Any]:
    """手动同步单条任务到 MySQL（主任务 → span_tasks；流水线 → pipeline_task_history）。"""
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id 为空"}

    kind = (task_kind or "").strip().lower()
    if not kind:
        kind = "main" if tid.startswith("task_") else "pipeline"

    if kind == "main":
        from .span_audit import force_sync_task_to_mysql

        return force_sync_task_to_mysql(tid)

    if not _pipeline_db_enabled():
        return {"ok": False, "error": "MySQL pipeline_task_history 未配置（SBA_DATABASE_URL）"}

    snap: Optional[Dict[str, Any]] = None
    try:
        from .task_manager import get_task

        snap = get_task(tid)
    except Exception:
        snap = None

    if not snap:
        try:
            from .span_audit import get_task as span_get

            st = span_get(tid)
            if st:
                snap = {
                    "task_id": tid,
                    "id": tid,
                    "link": st.get("user_query") if str(st.get("user_query") or "").startswith("http") else "",
                    "user_prompt": st.get("user_query") or "",
                    "status": st.get("status") or "",
                    "platform": "",
                    "progress": 0,
                    "updated_at": st.get("updated_at"),
                    "created_at": st.get("created_at"),
                }
        except Exception:
            snap = None

    if not snap:
        try:
            from . import pipeline_history_store as phs

            snap = phs.get_by_task_id(tid)
        except Exception:
            snap = None

    if not snap:
        try:
            from .history_manager import list_history_tasks

            for t in list_history_tasks(limit=500, repair_html=False, persist_normalize=False):
                if str(t.get("task_id") or t.get("id") or "") == tid:
                    snap = t
                    break
        except Exception:
            snap = None

    if not snap:
        return {"ok": False, "error": f"未找到流水线任务 {tid}"}

    from .history_manager import add_or_update_task_in_history

    add_or_update_task_in_history(dict(snap))
    _log.info(
        "[任务中心-同步|task_registry_service.sync_task_to_mysql|pipeline_task_history|硬编执行|手动] "
        "完成; ok=true; task_id=%s",
        tid,
    )
    return {
        "ok": True,
        "task_id": tid,
        "task_kind": "pipeline",
        "mysql_table": "pipeline_task_history",
        "mysql_synced": True,
    }
