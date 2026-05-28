"""Langfuse + LangSmith tracing 回调装配（可选依赖，未配置时静默跳过）。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .config import eval_sdk_root, langfuse_enabled, langsmith_tracing_enabled

_log = logging.getLogger("sba.eval.tracing")


def _try_langfuse_handler(
    *,
    session_id: str,
    trace_id: str,
    user_id: Optional[str] = None,
) -> Any:
    if not langfuse_enabled():
        return None
    try:
        from langfuse.callback import CallbackHandler
    except ImportError:
        _log.warning(
            "[Eval-追踪|eval.tracing._try_langfuse_handler|langfuse|硬编执行|导入] 失败; hint=pip install langfuse"
        )
        return None
    host = (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or "").strip() or None
    kwargs: Dict[str, Any] = {
        "session_id": session_id,
        "tags": ["sba", "langgraph"],
        "metadata": {"trace_id": trace_id, "session_id": session_id},
    }
    if user_id:
        kwargs["user_id"] = user_id
    if host:
        kwargs["host"] = host
    return CallbackHandler(**kwargs)


def _try_langsmith_callbacks() -> List[Any]:
    if not langsmith_tracing_enabled():
        return []
    try:
        from langsmith.run_helpers import get_tracing_context
        from langchain_core.tracers.context import tracing_v2_enabled

        _ = get_tracing_context  # 确认 langsmith 已安装
        _ = tracing_v2_enabled
        # LangGraph 通过环境变量 LANGSMITH_TRACING=true 自动挂 tracer；此处仅返回空列表占位
        return []
    except ImportError:
        _log.warning(
            "[Eval-追踪|eval.tracing._try_langsmith_callbacks|langsmith|硬编执行|导入] 失败; hint=pip install langsmith"
        )
        return []


def build_run_callbacks(
    *,
    session_id: str,
    trace_id: str,
    user_id: Optional[str] = None,
) -> List[Any]:
    """供 LangGraph RunnableConfig['callbacks'] 使用。"""
    out: List[Any] = []
    lf = _try_langfuse_handler(session_id=session_id, trace_id=trace_id, user_id=user_id)
    if lf is not None:
        out.append(lf)
    out.extend(_try_langsmith_callbacks())
    return out


def eval_tracing_status() -> Dict[str, Any]:
    """健康检查：是否已配置 tracing（不探测外网）。"""
    from .config import eval_enabled, ragas_eval_enabled
    from .packages import packages_installed

    root = eval_sdk_root()
    return {
        "eval_enabled": eval_enabled(),
        "ragas_enabled": ragas_eval_enabled(),
        "packages": packages_installed(),
        "sdk_root": str(root),
        "sdk_root_exists": root.is_dir(),
        "langfuse_configured": langfuse_enabled(),
        "langsmith_tracing": langsmith_tracing_enabled(),
        "langsmith_env": bool((os.environ.get("LANGSMITH_API_KEY") or "").strip()),
        "langfuse_host": (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or "").strip(),
        "local_repos": {
            name: (root / name).is_dir()
            for name in (
                "langgraph",
                "langfuse-python",
                "phoenix",
                "agentevals",
                "openevals",
                "langsmith-sdk",
                "ragas",
                "deepeval",
            )
        },
    }
