"""订阅批量入队 — 批次检查、失败重试、总量校验（硬编码规则 + 可选运维 Agent）。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional, Tuple

_log = logging.getLogger("sba.subscription_batch_gate")
_CHAIN = "社媒订阅-批量入队-检查重试"

RetryAction = Literal["full_rerun", "skip", "repair_link", "ops_review"]

# 硬编码：错误文案 / 阶段 → 重试策略（按优先级匹配）
_RETRY_RULES: List[Tuple[re.Pattern[str], RetryAction, int]] = [
    (re.compile(r"xsec_token|裸链|bare_explore", re.I), "repair_link", 1),
    (re.compile(r"shutdown|cannot schedule new futures", re.I), "full_rerun", 3),
    (re.compile(r"下载.*失败|download.*fail|DOWNLOAD", re.I), "full_rerun", 2),
    (re.compile(r"timeout|超时", re.I), "full_rerun", 2),
    (re.compile(r"PIPE_INVALID_INPUT_EMPTY|正文提取失败|无效占位|内容为空", re.I), "skip", 0),
    (re.compile(r"登录|LOGIN|GUEST|Cookie", re.I), "ops_review", 1),
]


@dataclass
class FailureItem:
    note_id: str
    canonical_url: str
    analysis_task_id: str
    failed_stage: str
    error_message: str
    action: RetryAction
    max_attempts: int
    attempt: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BatchAuditReport:
    subscription_id: str
    sync_run_id: str
    expected_total: int
    seen_total: int
    already_imported: int
    queued: int
    catalog: int
    bare_links: int
    sync_run_items: int
    completed_in_run: int
    failed_in_run: int
    pending_in_run: int
    failures: List[FailureItem] = field(default_factory=list)
    ok: bool = False
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["failures"] = [f if isinstance(f, dict) else f.to_dict() for f in self.failures]
        return d


# 运维 Agent 输入 schema（JSON）
OPS_BATCH_RETRY_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["subscription_id", "sync_run_id", "failures"],
    "properties": {
        "subscription_id": {"type": "string"},
        "sync_run_id": {"type": "string"},
        "expected_total": {"type": "integer"},
        "audit_summary": {"type": "object"},
        "failures": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["note_id", "error_message", "failed_stage", "hardcoded_action"],
                "properties": {
                    "note_id": {"type": "string"},
                    "canonical_url": {"type": "string"},
                    "failed_stage": {"type": "string"},
                    "error_message": {"type": "string"},
                    "hardcoded_action": {"type": "string"},
                },
            },
        },
    },
}

OPS_BATCH_RETRY_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["plans"],
    "properties": {
        "plans": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["note_id", "action", "reason"],
                "properties": {
                    "note_id": {"type": "string"},
                    "action": {"enum": ["full_rerun", "skip", "repair_link", "wait"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}

OPS_BATCH_RETRY_SYSTEM_PROMPT = (
    "你是订阅博主链接批量沉淀任务的运维决策助手。"
    "根据每条失败记录的 error_message、failed_stage 与硬编码初判 action，"
    "输出 JSON（不要 markdown 代码块），严格符合 output_schema。"
    "优先 full_rerun；纯图无正文可 skip；缺 xsec_token 用 repair_link；"
    "仅当硬编码已足够时不要改 action。"
)


def classify_failure(error_message: str, failed_stage: str = "") -> Tuple[RetryAction, int]:
    blob = f"{failed_stage} {error_message}"
    for pat, action, max_attempts in _RETRY_RULES:
        if pat.search(blob):
            return action, max_attempts
    return "full_rerun", 2


def sync_seen_from_history(subscription_id: str) -> int:
    """将历史已完成的链接同步为 seen.already_imported。"""
    from .creator_subscription_store import list_seen_notes_by_subscription, update_seen_analysis
    from .history_manager import get_task_history
    from .link_hash import url_hash as link_uh

    synced = 0
    for row in list_seen_notes_by_subscription(subscription_id, page_size=200):
        if row.get("analysis_status") == "already_imported":
            continue
        url = str(row.get("canonical_url") or "")
        if not url:
            continue
        hist = get_task_history(link=url, url_hash=link_uh(url)) or {}
        st = (hist.get("status") or "").lower()
        if st in ("completed", "success", "done"):
            tid = str(hist.get("id") or hist.get("task_id") or row.get("analysis_task_id") or "")
            update_seen_analysis(
                str(row.get("platform") or "xiaohongshu"),
                str(row.get("note_id") or ""),
                tid,
                "already_imported",
            )
            synced += 1
    return synced


def audit_subscription_batch(
    subscription_id: str,
    *,
    sync_run_id: str = "",
    expected_total: Optional[int] = None,
) -> BatchAuditReport:
    from .creator_subscription_store import list_seen_notes_by_subscription, list_sync_run_items
    from .history_manager import get_task_history
    from .link_hash import url_hash as link_uh
    from .task_manager import get_task, _ensure_queue_persistence_loaded

    _ensure_queue_persistence_loaded()
    rows = list_seen_notes_by_subscription(subscription_id, page_size=200)
    seen_total = len(rows)
    already_imported = sum(1 for r in rows if r.get("analysis_status") == "already_imported")
    queued = sum(1 for r in rows if r.get("analysis_status") == "queued")
    catalog = sum(1 for r in rows if r.get("analysis_status") == "catalog")
    bare = sum(1 for r in rows if "xsec_token" not in (r.get("canonical_url") or ""))

    run_items = list_sync_run_items(sync_run_id) if sync_run_id else []
    completed_run = sum(1 for it in run_items if it.get("analysis_status") in ("completed", "already_imported"))
    failed_run = sum(1 for it in run_items if it.get("analysis_status") == "failed")
    pending_run = len(run_items) - completed_run - failed_run

    failures: List[FailureItem] = []
    for row in rows:
        if row.get("analysis_status") == "already_imported":
            continue
        url = str(row.get("canonical_url") or "")
        tid = str(row.get("analysis_task_id") or "").strip()
        hist = get_task_history(link=url, url_hash=link_uh(url)) if url else {}
        hist = hist or {}
        hist_st = (hist.get("status") or "").lower()
        task = get_task(tid) if tid else None
        task_st = (task.get("status") or "").lower() if task else ""

        if hist_st in ("completed", "success", "done") or task_st in ("completed", "success", "done"):
            continue

        if hist_st in ("failed", "error") or task_st in ("failed", "error") or row.get("analysis_status") == "failed":
            err = str(
                (task or {}).get("error")
                or (task or {}).get("message")
                or hist.get("error")
                or hist.get("message")
                or row.get("analysis_status")
                or ""
            )
            stage = str(
                (task or {}).get("failed_stage_label")
                or (task or {}).get("failed_stage")
                or (task or {}).get("stage")
                or hist.get("failed_stage_label")
                or hist.get("failed_stage")
                or ""
            )
            action, max_att = classify_failure(err, stage)
            failures.append(
                FailureItem(
                    note_id=str(row.get("note_id") or ""),
                    canonical_url=url,
                    analysis_task_id=tid,
                    failed_stage=stage,
                    error_message=err[:500],
                    action=action,
                    max_attempts=max_att,
                )
            )
        elif "xsec_token" not in url:
            failures.append(
                FailureItem(
                    note_id=str(row.get("note_id") or ""),
                    canonical_url=url,
                    analysis_task_id=tid,
                    failed_stage="bare_link",
                    error_message="缺少 xsec_token",
                    action="repair_link",
                    max_attempts=1,
                )
            )

    exp = int(expected_total or seen_total)
    gap = exp - already_imported if expected_total is not None else 0

    run_ok = True
    if sync_run_id and run_items:
        run_ok = failed_run == 0 and pending_run == 0

    total_ok = expected_total is None or gap <= 0
    ok = bare == 0 and not failures and run_ok and total_ok
    msg = (
        f"总量校验: 期望={exp}, 已导入={already_imported}, 缺口={gap}; "
        f"裸链={bare}; 本批待处理失败={failed_run}; 全局待重试={len(failures)}"
    )
    return BatchAuditReport(
        subscription_id=subscription_id,
        sync_run_id=sync_run_id,
        expected_total=exp,
        seen_total=seen_total,
        already_imported=already_imported,
        queued=queued,
        catalog=catalog,
        bare_links=bare,
        sync_run_items=len(run_items),
        completed_in_run=completed_run,
        failed_in_run=failed_run,
        pending_in_run=pending_run,
        failures=failures,
        ok=ok,
        message=msg,
    )


async def _wait_tasks(task_ids: List[str], timeout_sec: int) -> Dict[str, str]:
    from .task_manager import get_task

    deadline = asyncio.get_event_loop().time() + max(30, timeout_sec)
    pending = set(task_ids)
    outcomes: Dict[str, str] = {}
    while pending and asyncio.get_event_loop().time() < deadline:
        for tid in list(pending):
            t = get_task(tid)
            if not t:
                continue
            st = (t.get("status") or "pending").lower()
            if st in ("completed", "success", "done", "failed", "error", "cancelled"):
                outcomes[tid] = st
                pending.discard(tid)
        if pending:
            await asyncio.sleep(3)
    for tid in pending:
        outcomes[tid] = "timeout"
    return outcomes


async def _rerun_one(
    subscription_id: str,
    sync_run_id: str,
    item: FailureItem,
    *,
    platform: str = "xiaohongshu",
) -> Optional[str]:
    from .creator_subscription_store import get_subscription, update_seen_analysis, update_sync_run_item
    from .pipeline_scheduler import request_video_pipeline_async, kick_pipeline_dispatch
    from .task_manager import reuse_or_enqueue_task

    url = item.canonical_url
    if not url or "xsec_token" not in url:
        return None
    sub = get_subscription(subscription_id) or {}
    comments = {"enabled": bool((sub or {}).get("read_comments")), "count": 10, "sort": "hot"}
    plat = str(sub.get("platform") or "xiaohongshu")
    from .task_source_meta import SOURCE_SUB_FAVORITES, SOURCE_SUB_CREATOR, source_meta_kwargs

    src_key = SOURCE_SUB_FAVORITES if "favorite" in plat.lower() else SOURCE_SUB_CREATOR
    src_meta = source_meta_kwargs(
        src_key,
        display_name=str(sub.get("display_name") or ""),
        platform=plat,
        subscription_id=subscription_id,
    )
    task_id, _, _ = reuse_or_enqueue_task(
        "小红书",
        url,
        "",
        comments,
        task_id=item.analysis_task_id or None,
        action="rerun",
        fast_enqueue=True,
        **src_meta,
    )
    update_seen_analysis(platform, item.note_id, task_id, "queued")
    if sync_run_id:
        update_sync_run_item(
            sync_run_id,
            item.note_id,
            analysis_task_id=task_id,
            analysis_status="queued",
            error_message="",
        )
    await request_video_pipeline_async(task_id)
    await kick_pipeline_dispatch()
    return task_id


async def apply_hardcoded_retries(
    report: BatchAuditReport,
    *,
    max_rounds: int = 2,
) -> Dict[str, Any]:
    """按硬编码规则重试失败项。"""
    retried = 0
    skipped = 0
    repair_first = [f for f in report.failures if f.action == "repair_link"]
    if repair_first:
        from .creator_catalog_seed import repair_subscription_note_links

        await repair_subscription_note_links(report.subscription_id, limit=200)

    for round_i in range(max(1, max_rounds)):
        audit = audit_subscription_batch(
            report.subscription_id,
            sync_run_id=report.sync_run_id,
            expected_total=report.expected_total,
        )
        todo = [f for f in audit.failures if f.action == "full_rerun" and f.max_attempts > round_i]
        if not todo:
            break
        task_ids: List[str] = []
        for f in todo:
            tid = await _rerun_one(report.subscription_id, report.sync_run_id, f)
            if tid:
                task_ids.append(tid)
                retried += 1
        if not task_ids:
            break
        wait_sec = int(os.environ.get("SUB_BATCH_RETRY_WAIT_SEC", "3600"))
        outcomes = await _wait_tasks(task_ids, wait_sec)
        failed_wait = sum(1 for s in outcomes.values() if s in ("failed", "error", "timeout"))
        _log.info(
            "[%s|apply_hardcoded_retries|%s|Agent执行|轮次] round=%s; retried=%s; outcomes=%s",
            _CHAIN,
            report.subscription_id,
            round_i + 1,
            len(task_ids),
            outcomes,
        )
        if failed_wait == len(task_ids):
            break

    for f in report.failures:
        if f.action == "skip":
            skipped += 1

    sync_seen_from_history(report.subscription_id)
    return {"retried": retried, "skipped": skipped, "rounds": max_rounds}


def ops_plan_batch_retries(report: BatchAuditReport) -> Dict[str, Any]:
    """可选：运维 Agent 对 ops_review / 疑难失败给出定点重试计划。"""
    from .config import load_config
    from .ops import _get_ops_agent

    cfg = load_config()
    if not bool(cfg.get("ops_async_check_enabled", False)):
        return {"ok": False, "skipped": True, "reason": "ops_async_check_enabled=false"}

    ambiguous = [f for f in report.failures if f.action in ("ops_review", "full_rerun")]
    if not ambiguous:
        return {"ok": True, "plans": [], "summary": "无待 Agent 研判项"}

    agent = _get_ops_agent()
    if agent is None or not (agent.api_key and agent.api_model):
        return {"ok": False, "error": "OpsAgent 未就绪", "llm_powered": False}

    payload = {
        "subscription_id": report.subscription_id,
        "sync_run_id": report.sync_run_id,
        "expected_total": report.expected_total,
        "audit_summary": {
            "already_imported": report.already_imported,
            "queued": report.queued,
            "bare_links": report.bare_links,
            "message": report.message,
        },
        "failures": [
            {
                "note_id": f.note_id,
                "canonical_url": f.canonical_url[:120],
                "failed_stage": f.failed_stage,
                "error_message": f.error_message[:300],
                "hardcoded_action": f.action,
            }
            for f in ambiguous[:30]
        ],
    }
    prompt = (
        f"input_schema={json.dumps(OPS_BATCH_RETRY_INPUT_SCHEMA, ensure_ascii=False)}\n"
        f"output_schema={json.dumps(OPS_BATCH_RETRY_OUTPUT_SCHEMA, ensure_ascii=False)}\n"
        f"input={json.dumps(payload, ensure_ascii=False)}\n"
        "请只输出符合 output_schema 的 JSON。"
    )
    raw = agent._call_llm(prompt)  # noqa: SLF001 — 订阅批次专用 prompt
    llm_powered = bool(raw)
    plans: List[Dict[str, Any]] = []
    summary = ""
    if raw:
        try:
            # 剥离可能的 markdown 包裹
            text = raw.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            parsed = json.loads(text)
            plans = parsed.get("plans") or []
            summary = str(parsed.get("summary") or "")
        except Exception as ex:
            _log.warning(
                "[%s|ops_plan_batch_retries|%s|Agent执行|解析失败] error=%s",
                _CHAIN,
                report.subscription_id,
                ex,
            )
    return {"ok": True, "llm_powered": llm_powered, "plans": plans, "summary": summary, "raw": (raw or "")[:2000]}


async def finalize_subscription_batch(
    subscription_id: str,
    sync_run_id: str,
    *,
    expected_total: Optional[int] = None,
    wait_timeout_sec: int = 0,
    max_retry_rounds: int = 2,
    task_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    批次收尾：等待本批任务 → 同步 seen → 审计 → 硬编码重试 → 可选 Ops → 终检。
    """
    from .creator_subscription_store import update_sync_run
    from .pipeline_scheduler import kick_pipeline_dispatch

    wait_sec = int(wait_timeout_sec or os.environ.get("SUB_BATCH_FINALIZE_WAIT_SEC", "14400"))
    if task_ids:
        outcomes = await _wait_tasks(task_ids, wait_sec)
        _log.info(
            "[%s|finalize_subscription_batch|%s|Agent执行|等待] sync_run=%s; outcomes=%s",
            _CHAIN,
            subscription_id,
            sync_run_id,
            outcomes,
        )

    synced = sync_seen_from_history(subscription_id)
    report = audit_subscription_batch(
        subscription_id,
        sync_run_id=sync_run_id,
        expected_total=expected_total,
    )

    retry_result: Dict[str, Any] = {"retried": 0}
    if report.failures:
        retry_result = await apply_hardcoded_retries(report, max_rounds=max_retry_rounds)
        ops_result = ops_plan_batch_retries(report)
        retry_result["ops_plan"] = ops_result
        # 执行 Agent 建议的 full_rerun（硬编码未覆盖时）
        for plan in (ops_result.get("plans") or []):
            if (plan.get("action") or "") != "full_rerun":
                continue
            nid = str(plan.get("note_id") or "")
            row = next((f for f in report.failures if f.note_id == nid), None)
            if row:
                await _rerun_one(subscription_id, sync_run_id, row)

    final_synced = sync_seen_from_history(subscription_id)
    final_report = audit_subscription_batch(
        subscription_id,
        sync_run_id=sync_run_id,
        expected_total=expected_total,
    )

    status = "completed" if final_report.ok else "partial"
    if final_report.bare_links:
        status = "failed"
    if sync_run_id and final_report.sync_run_items:
        analyzed_count = final_report.completed_in_run
        failed_count = final_report.failed_in_run
    else:
        analyzed_count = final_report.already_imported
        failed_count = len(final_report.failures)
    update_sync_run(
        sync_run_id,
        status=status,
        analyzed_count=analyzed_count,
        failed_count=failed_count,
        error_message=final_report.message[:500] if not final_report.ok else "",
    )
    await kick_pipeline_dispatch()

    out = {
        "ok": final_report.ok,
        "subscription_id": subscription_id,
        "sync_run_id": sync_run_id,
        "status": status,
        "synced_from_history": synced + final_synced,
        "retry": retry_result,
        "audit": final_report.to_dict(),
    }
    _log.info(
        "[%s|finalize_subscription_batch|%s|Agent执行|完成] ok=%s; imported=%s; failures=%s",
        _CHAIN,
        subscription_id,
        final_report.ok,
        final_report.already_imported,
        len(final_report.failures),
    )
    return out
