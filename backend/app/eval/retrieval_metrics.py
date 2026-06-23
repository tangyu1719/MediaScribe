"""检索层离线指标：Recall/Precision/Hit/MRR/nDCG/Pass@K（纯函数，无假数据）。"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


def _as_set(items: Optional[Iterable[Any]]) -> Set[str]:
    if not items:
        return set()
    return {str(x).strip() for x in items if str(x).strip()}


def hit_at_k(relevant: Set[str], retrieved: Sequence[str], k: int) -> float:
    """Hit@K：Top-K 是否至少命中一条金标 doc/chunk。"""
    if not relevant or k <= 0:
        return 0.0
    top = [str(x).strip() for x in retrieved[:k] if str(x).strip()]
    return 1.0 if any(x in relevant for x in top) else 0.0


def recall_at_k(relevant: Set[str], retrieved: Sequence[str], k: int) -> float:
    """Recall@K：金标被 Top-K 覆盖比例。"""
    if not relevant or k <= 0:
        return 0.0
    top = _as_set(retrieved[:k])
    if not top:
        return 0.0
    return len(relevant & top) / len(relevant)


def precision_at_k(relevant: Set[str], retrieved: Sequence[str], k: int) -> float:
    """Precision@K：Top-K 中相关占比。"""
    if k <= 0:
        return 0.0
    top = [str(x).strip() for x in retrieved[:k] if str(x).strip()]
    if not top:
        return 0.0
    hits = sum(1 for x in top if x in relevant)
    return hits / len(top)


def reciprocal_rank(relevant: Set[str], retrieved: Sequence[str]) -> float:
    """单条 MRR 分量：第一个正确结果的倒数排名。"""
    for idx, doc in enumerate(retrieved, start=1):
        if str(doc).strip() in relevant:
            return 1.0 / idx
    return 0.0


def ndcg_at_k(relevant: Set[str], retrieved: Sequence[str], k: int) -> float:
    """nDCG@K：二元相关（在 relevant 集合即相关）。"""
    if k <= 0:
        return 0.0
    top = [str(x).strip() for x in retrieved[:k] if str(x).strip()]
    if not top:
        return 0.0
    dcg = 0.0
    for i, doc in enumerate(top):
        rel = 1.0 if doc in relevant else 0.0
        if rel > 0:
            dcg += rel / math.log2(i + 2)
    ideal_hits = min(len(relevant), k)
    if ideal_hits <= 0:
        return 0.0
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def pass_at_k(success_flags: Sequence[bool], k: int) -> float:
    """
    Pass@K：k 次独立尝试中至少一次成功的比例（常用于 codegen；此处用于多路 query 扩展）。
    success_flags 长度应 >= k。
    """
    if k <= 0 or not success_flags:
        return 0.0
    window = success_flags[:k]
    return 1.0 if any(window) else 0.0


def aggregate_retrieval_metrics(
    rows: List[Dict[str, Any]],
    *,
    k_list: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    对 JSONL 数据集批量聚合检索指标。

    每行字段：
      - question
      - relevant_doc_ids: list[str]  金标 chunk/doc id
      - retrieved_doc_ids: list[str] 实际召回排序
    """
    ks = k_list or [1, 3, 5, 10]
    if not rows:
        return {"ok": False, "reason": "空数据集", "count": 0}

    sums: Dict[str, float] = {}
    counts = len(rows)
    mrr_sum = 0.0
    pass1_flags: List[bool] = []
    pass3_flags: List[bool] = []

    for row in rows:
        rel = _as_set(row.get("relevant_doc_ids") or row.get("gold_doc_ids"))
        ret = list(row.get("retrieved_doc_ids") or row.get("contexts_ids") or [])
        for k in ks:
            sums[f"hit@{k}"] = sums.get(f"hit@{k}", 0.0) + hit_at_k(rel, ret, k)
            sums[f"recall@{k}"] = sums.get(f"recall@{k}", 0.0) + recall_at_k(rel, ret, k)
            sums[f"precision@{k}"] = sums.get(f"precision@{k}", 0.0) + precision_at_k(rel, ret, k)
            sums[f"ndcg@{k}"] = sums.get(f"ndcg@{k}", 0.0) + ndcg_at_k(rel, ret, k)
        mrr_sum += reciprocal_rank(rel, ret)
        pass1_flags.append(hit_at_k(rel, ret, 1) >= 1.0)
        pass3_flags.append(hit_at_k(rel, ret, 3) >= 1.0)

    metrics: Dict[str, float] = {key: val / counts for key, val in sums.items()}
    metrics["mrr"] = mrr_sum / counts
    metrics["pass@1"] = sum(1 for x in pass1_flags if x) / counts
    metrics["pass@3"] = sum(1 for x in pass3_flags if x) / counts
    return {
        "ok": True,
        "count": counts,
        "metrics": {k: round(v, 4) for k, v in sorted(metrics.items())},
    }
