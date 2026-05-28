"""RAG 分层 eval：RAGAS 离线数据集（须显式配置路径，无默认假数据）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import rag_eval_dataset_path, ragas_eval_enabled

_log = logging.getLogger("sba.eval.rag")


def load_rag_eval_dataset(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    JSONL 每行字段：question, answer(可选), contexts(列表), ground_truth(可选)
    """
    p = path or rag_eval_dataset_path()
    if not p:
        return []
    rows: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def run_ragas_on_dataset(
    *,
    dataset_path: Optional[Path] = None,
    llm=None,
    embeddings=None,
) -> Dict[str, Any]:
    """
    对 JSONL 数据集跑 RAGAS。需安装 ragas；LLM/embedding 由调用方注入（避免硬编码密钥）。
    """
    if not ragas_eval_enabled():
        return {
            "ok": False,
            "skipped": True,
            "reason": "SBA_RAG_EVAL_ENABLED 未开启",
        }
    rows = load_rag_eval_dataset(dataset_path)
    if not rows:
        return {
            "ok": False,
            "skipped": True,
            "reason": "未配置 SBA_RAG_EVAL_DATASET 或文件为空",
        }
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, faithfulness
    except ImportError as ex:
        _log.warning(
            "[Eval-RAG|eval.rag_eval.run_ragas_on_dataset|ragas|硬编执行|导入] 失败; error=%s",
            ex,
        )
        return {"ok": False, "skipped": True, "error": "ragas/datasets 未安装"}

    if llm is None or embeddings is None:
        return {
            "ok": False,
            "skipped": True,
            "reason": "需提供 llm 与 embeddings 实例（见 tests/eval 示例）",
            "rows": len(rows),
        }

    ds = Dataset.from_list(rows)
    metrics = [faithfulness, answer_relevancy, context_precision]
    result = evaluate(ds, metrics=metrics, llm=llm, embeddings=embeddings)
    _log.info(
        "[Eval-RAG|eval.rag_eval.run_ragas_on_dataset|ragas|Agent执行|完成] rows=%s",
        len(rows),
    )
    return {"ok": True, "rows": len(rows), "scores": result.to_pandas().to_dict()}
