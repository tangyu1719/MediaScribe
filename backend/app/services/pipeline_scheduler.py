"""链接沉淀流水线调度 —— 对齐老项目 video_gui：整任务进线程池（默认 8 路），非 MainThread 编排。"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, Callable, Dict, Optional

from .task_manager import add_log, update_task

_log = logging.getLogger("sba.pipeline_scheduler")
_pipeline_sem: Optional[asyncio.Semaphore] = None
_pipeline_sem_size: int = 0

# 已提交调度、尚未释放槽位的任务
_scheduled_ids: set[str] = set()
_coro_factories: Dict[str, Callable[[], Awaitable[None]]] = {}
_dispatch_lock: Optional[asyncio.Lock] = None


def _get_dispatch_lock() -> asyncio.Lock:
    global _dispatch_lock
    if _dispatch_lock is None:
        _dispatch_lock = asyncio.Lock()
    return _dispatch_lock


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
    获得槽位前保持 pending；开始执行后不再参与优先级排序。
    """
    from .pipeline_executor import blocking_pool_size, get_pipeline_runner_executor

    workers = blocking_pool_size()
    scheduler_th = threading.current_thread().name
    update_task(task_id, stage="等待执行槽位")
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
        update_task(task_id, status="running", stage="流水线执行中")
        add_log(
            task_id,
            f"[调度] 已获得线程池槽位，提交执行; 当前线程={scheduler_th}",
        )
        await loop.run_in_executor(
            get_pipeline_runner_executor(),
            lambda: _run_pipeline_coro_in_worker(task_id, coro_factory),
        )


async def _dispatch_pending_pipelines() -> None:
    """按 importance 降序 + queue_seq 升序（先进先出）启动 pending 任务，直至占满并发槽。"""
    from .pipeline_executor import blocking_pool_size
    from .task_manager import list_pending_tasks

    max_w = blocking_pool_size()
    lock = _get_dispatch_lock()
    async with lock:
        pending = [
            t for t in list_pending_tasks()
            if (t.get("task_id") or "") not in _scheduled_ids
        ]
        while len(_scheduled_ids) < max_w and pending:
            task = pending.pop(0)
            tid = str(task.get("task_id") or "").strip()
            if not tid or tid in _scheduled_ids:
                continue
            factory = _coro_factories.get(tid)
            if not factory:
                continue
            _scheduled_ids.add(tid)

            async def _runner(tid: str = tid, factory=factory):
                try:
                    await run_pipeline_with_slot(tid, factory)
                finally:
                    async with _get_dispatch_lock():
                        _scheduled_ids.discard(tid)
                        _coro_factories.pop(tid, None)
                    await _dispatch_pending_pipelines()

            asyncio.create_task(_runner())


async def kick_pipeline_dispatch() -> None:
    """对外：尝试按重要度启动 pending 任务。"""
    await _dispatch_pending_pipelines()


def request_pipeline_task(task_id: str, coro_factory: Callable[[], Awaitable[None]]) -> None:
    """登记 pending 任务并触发按重要度调度（同级 FIFO）。"""
    tid = (task_id or "").strip()
    if not tid:
        return
    _coro_factories[tid] = coro_factory
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_dispatch_pending_pipelines())
    except RuntimeError:
        # 非 async 上下文（极少）：直接同步登记，由后续 start 触发
        pass


async def request_pipeline_task_async(task_id: str, coro_factory: Callable[[], Awaitable[None]]) -> None:
    """async 上下文登记并调度。"""
    tid = (task_id or "").strip()
    if not tid:
        return
    _coro_factories[tid] = coro_factory
    await _dispatch_pending_pipelines()


def request_video_pipeline(task_id: str) -> None:
    """默认视频/图文链接沉淀入口。"""
    from .video_pipeline import process_video_pipeline

    request_pipeline_task(task_id, lambda: process_video_pipeline(task_id))


async def request_video_pipeline_async(task_id: str) -> None:
    from .video_pipeline import process_video_pipeline

    await request_pipeline_task_async(task_id, lambda: process_video_pipeline(task_id))


def pipeline_max_workers() -> int:
    """兼容旧接口：阻塞 I/O 线程池大小（即老项目「8 路并行」）。"""
    from .pipeline_executor import blocking_pool_size

    return blocking_pool_size()
