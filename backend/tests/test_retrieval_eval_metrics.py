"""检索指标、GATE/RUBRIC、离线 manifest 单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval.retrieval_metrics import (
    aggregate_retrieval_metrics,
    hit_at_k,
    ndcg_at_k,
    pass_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.eval.gate_rubric import run_gate_checks, run_gate_batch, evaluate_rubric_scores, gate_rules
from app.eval.offline_runner import load_dataset_manifest, eval_baseline_targets

_FIXTURES = Path(__file__).resolve().parents[1] / "app" / "eval" / "fixtures"


def test_hit_recall_precision():
    rel = {"a", "b"}
    ret = ["x", "a", "b", "c"]
    assert hit_at_k(rel, ret, 5) == 1.0
    assert recall_at_k(rel, ret, 2) == 0.5
    assert precision_at_k(rel, ret, 3) == pytest.approx(2 / 3)
    assert reciprocal_rank(rel, ret) == 0.5


def test_ndcg_and_pass_at_k():
    rel = {"doc1"}
    ret = ["doc2", "doc1", "doc3"]
    assert ndcg_at_k(rel, ret, 3) > 0.5
    assert pass_at_k([False, True, False], 3) == 1.0
    assert pass_at_k([False, False], 2) == 0.0


def test_aggregate_retrieval_metrics():
    rows = [
        {
            "relevant_doc_ids": ["d1"],
            "retrieved_doc_ids": ["d1", "d2"],
        },
        {
            "relevant_doc_ids": ["d9"],
            "retrieved_doc_ids": ["d1", "d2"],
        },
    ]
    out = aggregate_retrieval_metrics(rows, k_list=[1, 3])
    assert out["ok"] is True
    assert out["count"] == 2
    assert "hit@1" in out["metrics"]
    assert out["metrics"]["hit@1"] == 0.5


def test_gate_pass_sample():
    sample = {
        "answer": "请见配置说明 [Doc_abc123]，在极简上架启用批次属性。",
        "contexts": ["极简上架启用批次属性开关"],
        "expected_module": "极简上架",
        "predicted_module": "极简上架",
        "metadata": {"domain": "WMS", "doc_type": "SOP"},
    }
    result = run_gate_checks(sample)
    assert result["ok"] is True
    assert result["gate_pass"] is True


def test_gate_rules_count():
    assert len(gate_rules()) == 28


def test_rubric_evaluate():
    scores = {
        "faithfulness": 0.9,
        "correctness": 0.85,
        "completeness": 0.8,
        "attribution": 0.82,
        "fluency": 0.75,
    }
    out = evaluate_rubric_scores(scores)
    assert out["rubric_pass"] is True
    assert out["average_score"] > 0.8


def test_manifest_and_baseline():
    m = load_dataset_manifest()
    assert m.get("ok") is True
    data = m.get("data") or {}
    assert data.get("sets", {}).get("gold_full", {}).get("count") == 108

    b = eval_baseline_targets()
    assert b.get("ok") is True
    assert "retrieval_baseline_to_target" in (b.get("data") or {})


def test_smoke_jsonl_gate_batch():
    p = _FIXTURES / "datasets" / "smoke_6.jsonl"
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 6
    out = run_gate_batch(rows)
    assert out["ok"] is True
    assert out["count"] == len(rows)
    assert out["gate_pass_rate"] >= 0.0
