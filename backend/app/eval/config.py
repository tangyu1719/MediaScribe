"""Eval 配置：环境变量 + 本地 sdk_repos 路径（不硬编码业务数据）。"""
from __future__ import annotations

import os
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_FRAMEWORK_ROOT = _BACKEND.parents[1]
_DEFAULT_SDK_ROOT = _FRAMEWORK_ROOT / "sdk_repos" / "eval"


def eval_sdk_root() -> Path:
    raw = (os.environ.get("SBA_EVAL_SDK_ROOT") or "").strip()
    return Path(raw).resolve() if raw else _DEFAULT_SDK_ROOT.resolve()


def eval_enabled() -> bool:
    return os.environ.get("SBA_EVAL_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def langfuse_enabled() -> bool:
    if not eval_enabled():
        return False
    return bool(
        (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
        and (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    )


def langsmith_tracing_enabled() -> bool:
    if not eval_enabled():
        return False
    if os.environ.get("LANGSMITH_TRACING", "").strip().lower() in ("true", "1", "yes"):
        return bool((os.environ.get("LANGSMITH_API_KEY") or "").strip())
    return False


def ragas_eval_enabled() -> bool:
    return eval_enabled() and os.environ.get("SBA_RAG_EVAL_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def rag_eval_dataset_path() -> Path | None:
    """显式指定 JSONL 数据集路径；未设置则不跑 RAGAS 离线集。"""
    raw = (os.environ.get("SBA_RAG_EVAL_DATASET") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_file() else None
