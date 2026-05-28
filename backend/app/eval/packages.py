"""Eval 可选依赖探测（不探测外网）。"""
from __future__ import annotations

from typing import Dict


def packages_installed() -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for name, mod in (
        ("langfuse", "langfuse"),
        ("langsmith", "langsmith"),
        ("agentevals", "agentevals"),
        ("ragas", "ragas"),
        ("datasets", "datasets"),
        ("opentelemetry", "opentelemetry"),
    ):
        try:
            __import__(mod)
            out[name] = True
        except ImportError:
            out[name] = False
    return out
