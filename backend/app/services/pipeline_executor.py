"""全链路线程池 —— 阻塞 I/O / LLM / 后台任务分离，避免互相抢槽位。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .config import load_config

_blocking_pool: Optional[ThreadPoolExecutor] = None
_blocking_size: int = 0
_runner_pool: Optional[ThreadPoolExecutor] = None
_runner_size: int = 0
_llm_pool: Optional[ThreadPoolExecutor] = None
_llm_size: int = 0
_bg_pool: Optional[ThreadPoolExecutor] = None
_bg_size: int = 0
_lock = threading.Lock()

_DEFAULT_LLM_WORKERS = 256
_DEFAULT_BG_WORKERS = 256
_UNLIMITED_PRACTICAL = 4096


def _cfg_int(cfg: dict, key: str, default: int, *, minimum: int = 1, cap: Optional[int] = None) -> int:
    try:
        raw = cfg.get(key, default)
        if raw is None:
            n = default
        else:
            n = int(raw)
    except (TypeError, ValueError):
        n = default
    if n <= 0 and cap is None:
        n = _UNLIMITED_PRACTICAL
    n = max(minimum, n)
    if cap is not None:
        n = min(n, cap)
    return n


def blocking_pool_size() -> int:
    cfg = load_config()
    return _cfg_int(cfg, "max_workers", 8, minimum=1)


def llm_pool_size() -> int:
    cfg = load_config()
    return _cfg_int(cfg, "llm_workers", _DEFAULT_LLM_WORKERS, minimum=1, cap=None)


def background_pool_size() -> int:
    cfg = load_config()
    if cfg.get("background_workers") is not None:
        return _cfg_int(cfg, "background_workers", _DEFAULT_BG_WORKERS, minimum=1, cap=None)
    return llm_pool_size()


def _get_or_resize(
    pool_attr: str,
    size_attr: str,
    size: int,
    prefix: str,
) -> ThreadPoolExecutor:
    with _lock:
        pool = globals()[pool_attr]
        cur = globals()[size_attr]
        if pool is None or cur != size:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
            pool = ThreadPoolExecutor(max_workers=size, thread_name_prefix=prefix)
            globals()[pool_attr] = pool
            globals()[size_attr] = size
        return pool


def get_blocking_executor() -> ThreadPoolExecutor:
    """下载 / OCR / 链接解析等 CPU/IO 阻塞步骤（流水线内部）。"""
    return _get_or_resize("_blocking_pool", "_blocking_size", blocking_pool_size(), "pipeline-io")


def get_pipeline_runner_executor() -> ThreadPoolExecutor:
    """整条流水线的承载线程池（与老项目队列 max_workers 对齐，默认 8）。"""
    return _get_or_resize("_runner_pool", "_runner_size", blocking_pool_size(), "pipeline-run")


def get_llm_executor() -> ThreadPoolExecutor:
    """文档沉淀、摘要、原文整理等 LLM HTTP 调用 —— 高并发，不设「2 路」硬顶。"""
    return _get_or_resize("_llm_pool", "_llm_size", llm_pool_size(), "pipeline-llm")


def get_background_executor() -> ThreadPoolExecutor:
    """HTML 长页等后台任务 —— 与主流水线解耦，并发由 LLM 网关决定。"""
    return _get_or_resize("_bg_pool", "_bg_size", background_pool_size(), "pipeline-bg")


def get_pipeline_executor() -> ThreadPoolExecutor:
    """兼容旧名：阻塞步骤线程池。"""
    return get_blocking_executor()
