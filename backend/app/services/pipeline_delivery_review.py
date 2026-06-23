"""交付物审核 Agent — 对成品 MD 结构与内容质量打分，不通过则 fail。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .json_llm_output import parse_llm_json_object
from .llm_agent_signals import _LLM_SIGNAL_RULE
from .pipeline_logging import enrich_pipeline_llm_cfg, invoke_llm_via_gateway, log_llm_done, log_llm_prepare, pipeline_log
from .pipeline_output_quality import DELIVERY_REVIEW_FAILED, is_junk_body_text

CHAIN = "链接沉淀文档-交付审核"
LOG_MODULE = "pipeline_delivery_review.run_delivery_review"
DEFAULT_MIN_SCORE = 55

_REVIEW_JSON_RULE = (
    "\n【输出格式-硬性】仅输出 JSON："
    '{"status":"ok","score":0-100,"structure_ok":true|false,'
    '"checks":[{"name":"...","pass":true|false,"note":"..."}],'
    '"issues":["..."], "decision":"pass|fail", "summary":"50字内总评"}'
    ' 或 {"status":"reject","reject_code":"REVIEW_INPUT_INVALID","reject_reason":"..."}'
    + _LLM_SIGNAL_RULE
)


@dataclass
class DeliveryReviewResult:
    ok: bool
    score: int = 0
    structure_ok: bool = False
    decision: str = "fail"
    issues: List[str] = None
    checks: List[Dict[str, Any]] = None
    summary: str = ""
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self):
        if self.issues is None:
            self.issues = []
        if self.checks is None:
            self.checks = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "score": self.score,
            "structure_ok": self.structure_ok,
            "decision": self.decision,
            "issues": self.issues,
            "checks": self.checks,
            "summary": self.summary,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


def _build_review_prompt(
    *,
    doc_title: str,
    article: str,
    summary: str,
    link_title: str,
    platform: str,
    source_text_len: int,
    md_excerpt: str,
) -> str:
    return (
        "你是链接沉淀文档的交付审核 Agent。请审核以下产物是否可作为真实笔记/内容的交付物。\n"
        "审核维度：\n"
        "1. 结构：是否具备「内容信息 / 原始内容 / AI分析摘要」等基本章节（见 MD  excerpt）。\n"
        "2. 正文：原始内容是否为有效笔记正文，而非仅平台名/空壳/占位（如仅「小红书」）。\n"
        "3. 摘要：是否与 link_title/正文主题一致，而非泛化平台百科概述。\n"
        "4. 一致性：doc_title、link_title、摘要主题是否指向同一内容。\n"
        "打分 0-100；structure_ok 表示结构合格；decision=pass 仅当 score>=55 且正文与摘要均可信。\n\n"
        f"平台: {platform}\n"
        f"link_title: {link_title}\n"
        f"doc_title: {doc_title}\n"
        f"source_text_len: {source_text_len}\n"
        f"article_len: {len((article or '').strip())}\n"
        f"summary_len: {len((summary or '').strip())}\n\n"
        f"【原始内容 excerpt】\n{(article or '')[:4000]}\n\n"
        f"【AI摘要 excerpt】\n{(summary or '')[:4000]}\n\n"
        f"【MD excerpt】\n{(md_excerpt or '')[:2500]}\n"
    )


def run_delivery_review(
    *,
    task_id: str,
    doc_path: str,
    doc_title: str,
    article: str,
    summary: str,
    link_title: str = "",
    platform: str = "小红书",
    source_text_len: int = 0,
    llm_cfg: Optional[Dict] = None,
    min_score: int = DEFAULT_MIN_SCORE,
    log_cb: Optional[Callable[[str, str], None]] = None,
) -> DeliveryReviewResult:
    """调用审核 Agent；规则 + LLM 双重门禁后返回结果。"""
    llm_cfg = enrich_pipeline_llm_cfg(llm_cfg or {})
    art = (article or "").strip()
    summ = (summary or "").strip()
    lt = (link_title or "").strip()

    def log(msg: str, lvl: str = "INFO"):
        if log_cb:
            log_cb(msg, lvl)

    # 规则预审（不调 LLM 也能拦明显假文档）
    hard_issues: List[str] = []
    if is_junk_body_text(art):
        hard_issues.append("原始内容为占位/平台名，非有效正文")
    if len(art) < 12:
        hard_issues.append(f"原始内容过短（{len(art)} 字）")
    if len(summ) > 80 and is_junk_body_text(art) and ("平台概述" in summ or "UGC" in summ):
        hard_issues.append("摘要疑似泛化平台百科，与空/占位正文不匹配")
    if hard_issues:
        msg = "；".join(hard_issues)
        pipeline_log(
            task_id, CHAIN, LOG_MODULE, doc_path[:80], "规则预审", "硬编执行", "交付审核未通过", "ERROR",
            log_cb=log_cb, issues=msg,
        )
        return DeliveryReviewResult(
            ok=False,
            score=0,
            structure_ok=False,
            decision="fail",
            issues=hard_issues,
            error_code=DELIVERY_REVIEW_FAILED,
            error_message=msg,
        )

    md_excerpt = ""
    try:
        from pathlib import Path
        p = Path(doc_path)
        if p.is_file():
            md_excerpt = p.read_text(encoding="utf-8", errors="ignore")[:2500]
    except Exception:
        pass

    prompt = _build_review_prompt(
        doc_title=doc_title,
        article=art,
        summary=summ,
        link_title=lt,
        platform=platform,
        source_text_len=source_text_len,
        md_excerpt=md_excerpt,
    )
    routes = log_llm_prepare(
        task_id, CHAIN, LOG_MODULE, doc_path[:80],
        role="交付审核Agent",
        text_len=len(art) + len(summ),
        cfg=llm_cfg,
        agent_name="delivery_review_agent",
        task_type="summary",
    )
    messages = [
        {
            "role": "system",
            "content": "你是严格的文档交付质检员。对结构、正文真实性、摘要忠实度打分；发现问题必须 decision=fail。",
        },
        {"role": "user", "content": (_REVIEW_JSON_RULE + "\n\n" + prompt).strip()},
    ]
    ret = invoke_llm_via_gateway(
        llm_cfg,
        agent_name="delivery_review_agent",
        task_type="summary",
        messages=messages,
        temperature=0.2,
        max_tokens=2048,
        timeout_sec=90.0,
    )
    out = (ret.get("text") or "").strip()
    log_llm_done(
        task_id, CHAIN, LOG_MODULE, doc_path[:80],
        role="交付审核Agent", routes=routes, ok=bool(ret.get("ok") and out),
        out_len=len(out), error=str(ret.get("error") or ""),
    )
    if not out:
        return DeliveryReviewResult(
            ok=False,
            error_code=DELIVERY_REVIEW_FAILED,
            error_message="交付审核 Agent 无响应",
        )

    parsed = parse_llm_json_object(out, required_keys=("decision",))
    if not parsed.ok:
        return DeliveryReviewResult(
            ok=False,
            error_code=DELIVERY_REVIEW_FAILED,
            error_message=f"交付审核 JSON 解析失败: {parsed.error_code}",
        )
    data = parsed.data or {}
    if str(data.get("status") or "").lower() == "reject":
        return DeliveryReviewResult(
            ok=False,
            error_code=DELIVERY_REVIEW_FAILED,
            error_message=str(data.get("reject_reason") or "审核 Agent 拒答"),
            issues=[str(data.get("reject_reason") or "")],
        )

    score = int(data.get("score") or 0)
    structure_ok = bool(data.get("structure_ok"))
    decision = str(data.get("decision") or "fail").strip().lower()
    issues = [str(x) for x in (data.get("issues") or []) if str(x).strip()]
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    review_summary = str(data.get("summary") or "").strip()

    passed = decision == "pass" and score >= min_score and structure_ok
    if not passed and not issues:
        issues.append(review_summary or f"score={score}; structure_ok={structure_ok}; decision={decision}")

    pipeline_log(
        task_id, CHAIN, LOG_MODULE, doc_path[:80], "LLM审核", "Agent执行",
        "交付审核完成" if passed else "交付审核未通过",
        "INFO" if passed else "ERROR",
        log_cb=log_cb,
        score=score,
        structure_ok=structure_ok,
        decision=decision,
    )
    return DeliveryReviewResult(
        ok=passed,
        score=score,
        structure_ok=structure_ok,
        decision=decision if passed else "fail",
        issues=issues,
        checks=checks,
        summary=review_summary,
        error_code="" if passed else DELIVERY_REVIEW_FAILED,
        error_message="" if passed else ("；".join(issues[:5]) or "交付审核未通过"),
    )
