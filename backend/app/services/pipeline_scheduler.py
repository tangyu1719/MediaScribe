"""链接沉淀流水线调度 —— 对齐老项目 video_gui：整任务进线程池（默认 8 路），非 MainThread 编排。"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, Callable, Optional

from .task_manager import add_log, update_task

_log = logging.getLogger("sba.pipeline_scheduler")
_pipeline_sem: Optional[asyncio.Semaphore] = None
_pipeline_sem_size: int = 0


def _get_pipeline_semaphore() -> asyncio.Semaphore:
    """限制同时执行的流水线条数（与老项目 max_workers 一致）。"""
    global _pipeline_sem, _pipeline_sem_size
    from .pipeline_executor import blocking_pool_size

    size = blocking_pool_size()
    if _pipeline_sem is None or _pipeline_sem_size != size:
        _pipeline_sem = asyncio.Semaphore(size)
        _pipeline_sem_size = size
    return _pipeline_sem


def _run_pipeline_coro_in_worker(task_id: str, coro_factory: Callable[[], Awaitable[None]]) -> str:
    """
    在 pipeline-run 工作线程内用独立事件循环跑完整条 async 流水线。
    此后 SPAN / 阶段日志的线程名均为 pipeline-run_N；内部 I/O 再进 pipeline-io 池。
    """
    worker = threading.current_thread().name
    add_log(
        task_id,
        f"[调度] 工作线程接管整条流水线; thread={worker}",
    )
    try:
        asyncio.run(coro_factory())
    finally:
        add_log(
            task_id,
            f"[调度] 工作线程流水线结束; thread={threading.current_thread().name}",
        )
    return worker


async def run_pipeline_with_slot(task_id: str, coro_factory: Callable[[], Awaitable[None]]):
    """
    将整条流水线提交到阻塞 I/O 线程池（max_workers，默认 8），并用信号量限制并发路数。
    与老项目 ThreadPoolExecutor(max_workers=8) + 队列调度行为对齐。
    """
    from .pipeline_executor import blocking_pool_size, get_pipeline_runner_executor

    workers = blocking_pool_size()
    scheduler_th = threading.current_thread().name
    update_task(task_id, status="running", stage="流水线执行中")
    add_log(
        task_id,
        f"[调度] 流水线排队（线程池 max_workers={workers}）; 调度协程={scheduler_th}",
    )
    _log.info(
        "[链接沉淀-调度|pipeline_scheduler.run_pipeline_with_slot|task|硬编执行|排队] "
        "task_id=%s; max_workers=%s; scheduler_thread=%s",
        task_id,
        workers,
        scheduler_th,
    )

    sem = _get_pipeline_semaphore()
    loop = asyncio.get_running_loop()
    async with sem:
        add_log(
            task_id,
            f"[调度] 已获得线程池槽位，提交执行; 当前线程={scheduler_th}",
        )
        await loop.run_in_executor(
            get_pipeline_runner_executor(),
            lambda: _run_pipeline_coro_in_worker(task_id, coro_factory),
        )


def pipeline_max_workers() -> int:
    """兼容旧接口：阻塞 I/O 线程池大小（即老项目「8 路并行」）。"""
    from .pipeline_executor import blocking_pool_size

    return blocking_pool_size()
