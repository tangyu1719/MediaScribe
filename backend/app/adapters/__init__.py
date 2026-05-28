"""适配器层 —— 桥接 src/agent 已有代码与 Web API 层

所有适配器都不包含业务逻辑，只做：
1. 回调签名转换（如 log_callback(message, level) → add_log(task_id, message, level)）
2. 同步→异步包装（run_in_executor）
3. 数据格式转换
"""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, Callable

# 全局线程池，避免每个请求都创建新线程
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="svc-adapter")


def run_blocking(func, *args, **kwargs):
    """在线程池中执行同步阻塞函数，返回 awaitable"""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_executor, lambda: func(*args, **kwargs))


def make_log_adapter(log_func: Callable):
    """将 log_callback(message, level) 适配为任意 log_func(task_id, message, level)"""
    def adapter(message: str, level: str = "INFO"):
        log_func(message, level)
    return adapter


def make_progress_adapter(progress_func: Callable):
    """将 progress_callback(progress, message) 适配"""
    def adapter(progress: int, message: str):
        progress_func(progress, message)
    return adapter
