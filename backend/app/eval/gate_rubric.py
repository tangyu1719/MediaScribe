"""GATE 硬规则校验 + RUBRIC 维度定义（可配置 JSON，不硬编码业务答案）。"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("sba.eval.gate_rubric")

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(name: str) -> Dict[str, Any]:
    p = _FIXTURES / name
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        _log.warning(
            "[Eval-GATE|eval.gate_rubric._load_json|%s|硬编执行|读取] 失败; error=%s",
            name,
            ex,
        )
        return {}


def gate_rules() -> List[Dict[str, Any]]:
    """返回 GATE 规则清单（fixtures/gate_rules.json）。"""
    data = _load_json("gate_rules.json")
    return list(data.get("rules") or [])


def rubric_schema() -> Dict[str, Any]:
    """返回 RUBRIC 评分维度与阈值（fixtures/rubric_schema.json）。"""
    return _load_json("rubric_schema.json")


def _check_rule(rule: Dict[str, Any], sample: Dict[str, Any]) -> Dict[str, Any]:
    """执行单条 GATE 规则，返回 {rule_id, pass, detail}。"""
    rid = str(rule.get("id") or "unknown")
    rtype = str(rule.get("type") or "").strip().lower()
    cfg = rule.get("config") or {}

    answer = str(sample.get("answer") or sample.get("generation") or "")
    contexts = sample.get("contexts") or sample.get("retrieved_texts") or []
    ctx_text = "\n".join(str(c) for c in contexts) if isinstance(contexts, list) else str(contexts)
    module = str(sample.get("expected_module") or sample.get("module") or "")
    meta = sample.get("metadata") or {}

    passed = True
    detail = ""

    if rtype == "require_citation":
        # 答案须含 [DocN] 或 doc_id 引用
        pattern = cfg.get("pattern") or r"\[Doc[\w\-]+\]|\[doc:[\w\-]+\]"
        passed = bool(re.search(pattern, answer, re.I))
        detail = "缺少文档引用标记" if not passed else "引用标记存在"

    elif rtype == "forbidden_phrase":
        phrases = cfg.get("phrases") or []
        hit = [p for p in phrases if p and p in answer]
        passed = len(hit) == 0
        detail = f"命中禁用短语: {hit}" if hit else "无禁用短语"

    elif rtype == "module_match":
        pred = str(sample.get("predicted_module") or meta.get("module") or "")
        if module and pred:
            passed = module.strip() == pred.strip()
            detail = f"期望={module}, 实际={pred}"
        else:
            passed = True
            detail = "未配置 expected_module，跳过"

    elif rtype == "min_context_overlap":
        # 答案与检索片段字符重叠率（粗粒度 groundedness 代理）
        min_ratio = float(cfg.get("min_ratio") or 0.08)
        if not answer or not ctx_text:
            passed = False
            detail = "答案或上下文为空"
        else:
            ans_chars = set(answer)
            overlap = sum(1 for c in ans_chars if c in ctx_text) / max(len(ans_chars), 1)
            passed = overlap >= min_ratio
            detail = f"overlap={overlap:.3f}, threshold={min_ratio}"

    elif rtype == "max_answer_length":
        max_len = int(cfg.get("max_chars") or 2000)
        passed = len(answer) <= max_len
        detail = f"len={len(answer)}, max={max_len}"

    elif rtype == "require_field":
        field = str(cfg.get("field") or "")
        passed = bool(str(sample.get(field) or meta.get(field) or "").strip())
        detail = f"字段 {field} {'存在' if passed else '缺失'}"

    elif rtype == "regex_answer":
        pattern = str(cfg.get("pattern") or "")
        passed = bool(pattern and re.search(pattern, answer, re.I))
        detail = f"pattern={'匹配' if passed else '未匹配'}"

    else:
        passed = True
        detail = f"未知规则类型 {rtype}，跳过"

    return {"rule_id": rid, "name": rule.get("name"), "pass": passed, "detail": detail}


def run_gate_checks(sample: Dict[str, Any], *, rules: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """对单条样本跑全部 GATE；全部通过则 gate_pass=True。"""
    rule_list = rules if rules is not None else gate_rules()
    results = [_check_rule(r, sample) for r in rule_list]
    all_pass = all(r["pass"] for r in results) if results else True
    return {
        "ok": True,
        "gate_pass": all_pass,
        "passed_count": sum(1 for r in results if r["pass"]),
        "total_rules": len(results),
        "results": results,
    }


def run_gate_batch(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """批量 GATE，返回通过率。"""
    if not rows:
        return {"ok": False, "reason": "空数据集", "count": 0}
    outcomes = [run_gate_checks(row) for row in rows]
    pass_n = sum(1 for o in outcomes if o.get("gate_pass"))
    return {
        "ok": True,
        "count": len(rows),
        "gate_pass_rate": round(pass_n / len(rows), 4),
        "rule_count": len(gate_rules()),
        "samples": outcomes,
    }


def rubric_dimensions() -> List[Dict[str, Any]]:
    schema = rubric_schema()
    return list(schema.get("dimensions") or [])


def rubric_thresholds() -> Dict[str, float]:
    schema = rubric_schema()
    return dict(schema.get("pass_thresholds") or {})


def evaluate_rubric_scores(scores: Dict[str, float]) -> Dict[str, Any]:
    """
    根据 fixtures/rubric_schema.json 阈值判定 RUBRIC 是否通过。
    scores: {dimension_id: 0~1 或 1~5 分，由调用方按 schema.scale 归一化后传入 0~1。
    """
    dims = rubric_dimensions()
    thresholds = rubric_thresholds()
    details: List[Dict[str, Any]] = []
    for dim in dims:
        did = str(dim.get("id") or "")
        val = float(scores.get(did) or 0.0)
        th = float(thresholds.get(did) or dim.get("min_score") or 0.7)
        details.append(
            {
                "dimension_id": did,
                "name": dim.get("name"),
                "score": val,
                "threshold": th,
                "pass": val >= th,
            }
        )
    all_pass = all(d["pass"] for d in details) if details else True
    avg = sum(d["score"] for d in details) / len(details) if details else 0.0
    return {
        "ok": True,
        "rubric_pass": all_pass,
        "average_score": round(avg, 4),
        "dimensions": details,
    }
