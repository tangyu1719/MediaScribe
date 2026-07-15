"""SKILL 使用归档：使用后延迟总结，写入 output/skill_usage_archives。"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)
_BASE = Path(__file__).resolve().parents[3]
_ARCHIVE_ROOT = _BASE / "output" / "skill_usage_archives"
_PENDING_DIR = _ARCHIVE_ROOT / "_pending"
_INDEX_FILE = _ARCHIVE_ROOT / "index.json"

_lock = threading.Lock()
_timer: Optional[threading.Timer] = None
_IDLE_SEC = 90.0  # 使用后静默多久再尝试归档
_TASK_TYPE = "skill_usage_archive"
_AGENT_NAME = "diagram_legend_agent"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_id(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", (text or "").strip())[:80] or "unknown"


def _load_index() -> Dict[str, Any]:
    if _INDEX_FILE.exists():
        try:
            data = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"items": [], "updated_at": ""}


def _save_index(data: Dict[str, Any]) -> None:
    _ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now_iso()
    _INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pending_path(session_id: str) -> Path:
    return _PENDING_DIR / f"{_safe_id(session_id)}.json"


def record_skill_usage_start(
    *,
    skill_id: str,
    skill_name: str = "",
    session_id: str = "",
    user_request: str = "",
    trigger: str = "invoke",
) -> str:
    """SKILL 被调用时登记待归档（不在调用中做 LLM 总结）。"""
    sid = (skill_id or "").strip()
    if not sid:
        return ""
    sess = (session_id or "").strip() or uuid.uuid4().hex[:16]
    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": sess,
        "skill_id": sid,
        "skill_name": skill_name or sid,
        "user_request": (user_request or "")[:2000],
        "trigger": trigger,
        "started_at": _now_iso(),
        "status": "pending_archive",
    }
    _pending_path(sess).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _schedule_archive_sweep(_IDLE_SEC)
    _LOG.info(
        "[SKILL使用归档|skill_usage_archive_service.record_skill_usage_start|skill_id=%s|硬编执行|登记] "
        "待归档已登记; session_id=%s; trigger=%s",
        sid,
        sess,
        trigger,
    )
    return sess


def list_archives(skill_id: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    data = _load_index()
    items = list(data.get("items") or [])
    if skill_id:
        items = [x for x in items if x.get("skill_id") == skill_id]
    items.sort(key=lambda x: x.get("archived_at") or "", reverse=True)
    return items[: max(1, min(limit, 100))]


def get_archive(archive_id: str) -> Optional[Dict[str, Any]]:
    aid = _safe_id(archive_id)
    md_path = _ARCHIVE_ROOT / aid / "report.md"
    meta_path = _ARCHIVE_ROOT / aid / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["report_md"] = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        return meta
    except Exception:
        return None


def _schedule_archive_sweep(delay_sec: float) -> None:
    global _timer
    with _lock:

        def _fire():
            try:
                sweep_pending_archives()
            except Exception as e:
                _LOG.warning(
                    "[SKILL使用归档|skill_usage_archive_service._schedule_archive_sweep|timer|硬编执行|异常] "
                    "sweep 失败; error_type=%s",
                    type(e).__name__,
                )

        if _timer:
            _timer.cancel()
        _timer = threading.Timer(max(5.0, delay_sec), _fire)
        _timer.daemon = True
        _timer.start()


def sweep_pending_archives(*, force: bool = False) -> Dict[str, Any]:
    """扫描 pending，尝试结合会话上下文生成归档。"""
    if not _PENDING_DIR.exists():
        return {"ok": True, "processed": 0, "skipped": 0}
    processed = 0
    skipped = 0
    errors: List[str] = []
    now = time.time()
    for fp in sorted(_PENDING_DIR.glob("*.json")):
        try:
            row = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            fp.unlink(missing_ok=True)
            continue
        started = row.get("started_at") or ""
        try:
            ts = datetime.fromisoformat(started).timestamp()
        except Exception:
            ts = now - _IDLE_SEC - 1
        if not force and (now - ts) < _IDLE_SEC:
            skipped += 1
            continue
        try:
            ok = _finalize_one(row)
            if ok:
                processed += 1
                fp.unlink(missing_ok=True)
            else:
                skipped += 1
        except Exception as e:
            errors.append(str(e)[:200])
            row["status"] = "archive_error"
            row["error"] = str(e)[:300]
            fp.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": not errors, "processed": processed, "skipped": skipped, "errors": errors}


def _gather_session_context(session_id: str) -> str:
    """从 chat_sessions 读取会话摘要（若存在）。"""
    sess_dir = _BASE / "data" / "chat_sessions"
    if not sess_dir.is_dir():
        return ""
    for fp in sess_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = str(data.get("session_id") or data.get("id") or fp.stem)
        if session_id and sid != session_id and session_id not in fp.stem:
            continue
        msgs = data.get("messages") or []
        if not isinstance(msgs, list):
            continue
        tail = msgs[-12:]
        lines: List[str] = []
        for m in tail:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "")
            content = str(m.get("content") or "")[:800]
            if content.strip():
                lines.append(f"[{role}] {content}")
        if lines:
            return "\n".join(lines)
    return ""


def _build_archive_prompt(row: Dict[str, Any], context: str) -> str:
    return (
        f"请为以下 SKILL 使用生成归档报告 JSON。\n"
        f"SKILL: {row.get('skill_name')} (id={row.get('skill_id')})\n"
        f"触发: {row.get('trigger')}\n"
        f"用户请求: {row.get('user_request') or '—'}\n"
        f"开始时间: {row.get('started_at')}\n\n"
        f"--- 会话上下文（使用后） ---\n{context or '（无会话上下文，请基于用户请求推断）'}\n"
    )


def _parse_archive_json(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _generate_archive_llm(row: Dict[str, Any], context: str) -> Dict[str, Any]:
    system = (
        "你是 SKILL 使用归档助手。根据 SKILL 调用信息与对话上下文，输出 JSON（禁止 markdown 外壳）：\n"
        "字段：task_scene(使用任务/场景), process(使用过程步骤数组), output_examples(产出文件示例数组), "
        "results(结果及效果), satisfaction(1-5), adoption(1-5), adoption_note(采纳说明), "
        "summary_md(完整 Markdown 报告正文，含上述各节标题)\n"
        "务实、基于上下文，勿编造未出现的文件路径；信息不足时在 summary_md 标注「待用户补充」。"
    )
    user = _build_archive_prompt(row, context)
    try:
        from .config import load_config
        from .pipeline_logging import enrich_pipeline_llm_cfg, invoke_llm_via_gateway

        cfg = enrich_pipeline_llm_cfg(load_config())
        if not bool(cfg.get("longpage_legend_agent_enabled", True)):
            return _fallback_archive(row, context, "图例 Agent 已关闭")

        timeout = float(cfg.get("longpage_legend_agent_timeout_sec") or 120.0)
        gw = invoke_llm_via_gateway(
            cfg,
            agent_name=_AGENT_NAME,
            task_type=_TASK_TYPE,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=3200,
            timeout_sec=timeout,
        )
        if not gw.get("ok"):
            return _fallback_archive(row, context, str(gw.get("error") or "gateway_failed"))
        parsed = _parse_archive_json(str(gw.get("text") or ""))
        if not parsed:
            return _fallback_archive(row, context, "JSON 解析失败")
        return parsed
    except Exception as e:
        return _fallback_archive(row, context, str(e))


def _fallback_archive(row: Dict[str, Any], context: str, reason: str) -> Dict[str, Any]:
    name = row.get("skill_name") or row.get("skill_id")
    req = row.get("user_request") or "—"
    md = (
        f"# SKILL 使用归档：{name}\n\n"
        f"- 归档时间：{_now_iso()}\n"
        f"- 触发：{row.get('trigger')}\n"
        f"- 用户请求：{req}\n\n"
        f"## 使用任务/场景\n\n待补充（自动总结未完成：{reason}）\n\n"
        f"## 使用过程\n\n1. 用户通过 {row.get('trigger')} 调用 SKILL\n"
        f"2. Agent 加载 SKILL 正文并按指引执行\n\n"
        f"## 产出文件示例\n\n（暂无）\n\n"
        f"## 结果及效果\n\n待观察\n\n"
        f"## 满意度与采纳度\n\n- 满意度：待评\n- 采纳度：待评\n\n"
        f"## 会话摘要\n\n```\n{(context or '无')[:2000]}\n```\n"
    )
    return {
        "task_scene": req[:200],
        "process": ["调用 SKILL", "Agent 执行", "待用户确认结果"],
        "output_examples": [],
        "results": "待补充",
        "satisfaction": 0,
        "adoption": 0,
        "adoption_note": reason,
        "summary_md": md,
    }


def _finalize_one(row: Dict[str, Any]) -> bool:
    session_id = str(row.get("session_id") or "")
    skill_id = str(row.get("skill_id") or "")
    context = _gather_session_context(session_id)
    archive_data = _generate_archive_llm(row, context)
    archive_id = uuid.uuid4().hex[:12]
    out_dir = _ARCHIVE_ROOT / archive_id
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_md = str(archive_data.get("summary_md") or "").strip()
    if not summary_md:
        summary_md = _fallback_archive(row, context, "empty_summary")["summary_md"]

    meta = {
        "archive_id": archive_id,
        "skill_id": skill_id,
        "skill_name": row.get("skill_name"),
        "session_id": session_id,
        "trigger": row.get("trigger"),
        "user_request": row.get("user_request"),
        "started_at": row.get("started_at"),
        "archived_at": _now_iso(),
        "task_scene": archive_data.get("task_scene"),
        "process": archive_data.get("process"),
        "output_examples": archive_data.get("output_examples"),
        "results": archive_data.get("results"),
        "satisfaction": archive_data.get("satisfaction"),
        "adoption": archive_data.get("adoption"),
        "adoption_note": archive_data.get("adoption_note"),
        "report_path": f"output/skill_usage_archives/{archive_id}/report.md",
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(summary_md, encoding="utf-8")

    idx = _load_index()
    items = list(idx.get("items") or [])
    items.insert(
        0,
        {
            "archive_id": archive_id,
            "skill_id": skill_id,
            "skill_name": row.get("skill_name"),
            "archived_at": meta["archived_at"],
            "task_scene": str(meta.get("task_scene") or "")[:120],
            "satisfaction": meta.get("satisfaction"),
            "adoption": meta.get("adoption"),
        },
    )
    idx["items"] = items[:500]
    _save_index(idx)
    _LOG.info(
        "[SKILL使用归档|skill_usage_archive_service._finalize_one|archive_id=%s|Agent执行|完成] "
        "归档已写入; skill_id=%s; ok=true",
        archive_id,
        skill_id,
    )
    return True
