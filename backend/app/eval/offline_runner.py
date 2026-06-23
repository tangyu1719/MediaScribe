"""离线 Eval Pipeline：检索指标 + GATE + RUBRIC + RAGAS（RegEval 回归入口）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import rag_eval_dataset_path, ragas_eval_enabled
from .gate_rubric import evaluate_rubric_scores, run_gate_batch, rubric_schema
from .rag_eval import load_rag_eval_dataset, run_ragas_on_dataset
from .retrieval_metrics import aggregate_retrieval_metrics
from .run_store import save_run

_log = logging.getLogger("sba.eval.offline")

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_MANIFEST = _FIXTURES / "datasets" / "manifest.json"


def load_dataset_manifest() -> Dict[str, Any]:
    """读取测试集规模与分层说明（非业务假问答正文）。"""
    if not _MANIFEST.is_file():
        return {"ok": False, "reason": "manifest.json 不存在"}
    try:
        data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        return {"ok": True, "data": data}
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}


def run_offline_eval(
    *,
    dataset_path: Optional[Path] = None,
    llm=None,
    embeddings=None,
    include_ragas: bool = True,
    k_list: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    一次 RegEval 离线跑批：
      1. 检索层指标（需行内 relevant_doc_ids + retrieved_doc_ids）
      2. GATE 硬规则
      3. RUBRIC 阈值（需行内 rubric_scores）
      4. RAGAS（可选，需 faithfulness 等）
    """
    p = dataset_path or rag_eval_dataset_path()
    if not p or not p.is_file():
        return {
            "ok": False,
            "skipped": True,
            "reason": "未配置 SBA_RAG_EVAL_DATASET 或文件不存在",
        }

    rows = load_rag_eval_dataset(p)
    ts = datetime.now().isoformat(timespec="seconds")
    report: Dict[str, Any] = {
        "ok": True,
        "dataset": str(p),
        "row_count": len(rows),
        "ts": ts,
        "layers": {},
    }

    # L1 检索
    ret_rows = [r for r in rows if r.get("relevant_doc_ids") or r.get("gold_doc_ids")]
    if ret_rows:
        report["layers"]["retrieval"] = aggregate_retrieval_metrics(ret_rows, k_list=k_list)
    else:
        report["layers"]["retrieval"] = {"ok": False, "reason": "无 relevant_doc_ids 字段，跳过检索层"}

    # L2 GATE
    report["layers"]["gate"] = run_gate_batch(rows)

    # L2 RUBRIC（仅含 rubric_scores 的行）
    rubric_rows = [r for r in rows if isinstance(r.get("rubric_scores"), dict)]
    if rubric_rows:
        rubric_outcomes = [evaluate_rubric_scores(r["rubric_scores"]) for r in rubric_rows]
        pass_n = sum(1 for o in rubric_outcomes if o.get("rubric_pass"))
        report["layers"]["rubric"] = {
            "ok": True,
            "count": len(rubric_rows),
            "rubric_pass_rate": round(pass_n / len(rubric_rows), 4),
            "schema": rubric_schema().get("name"),
        }
    else:
        report["layers"]["rubric"] = {"ok": False, "reason": "无 rubric_scores 字段，跳过 RUBRIC"}

    # L3 RAGAS
    if include_ragas and ragas_eval_enabled():
        ragas_result = run_ragas_on_dataset(dataset_path=p, llm=llm, embeddings=embeddings)
        report["layers"]["ragas"] = ragas_result
    else:
        report["layers"]["ragas"] = {
            "ok": False,
            "skipped": True,
            "reason": "RAGAS 未开启或未注入 llm/embeddings",
        }

    save_run(
        "offline_regeval",
        {
            "ok": report.get("ok"),
            "row_count": len(rows),
            "dataset": str(p),
            "retrieval": (report["layers"].get("retrieval") or {}).get("metrics"),
            "gate_pass_rate": (report["layers"].get("gate") or {}).get("gate_pass_rate"),
        },
    )
    _log.info(
        "[Eval-离线|eval.offline_runner.run_offline_eval|regeval|Agent执行|完成] rows=%s; gate=%s",
        len(rows),
        (report["layers"].get("gate") or {}).get("gate_pass_rate"),
    )
    return report


def eval_baseline_targets() -> Dict[str, Any]:
    """读取基线→目标指标（fixtures/baseline_targets.json），供 OPS 展示与回归对比。"""
    p = _FIXTURES / "baseline_targets.json"
    if not p.is_file():
        return {"ok": False, "reason": "baseline_targets.json 不存在"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {"ok": True, "data": data}
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}
