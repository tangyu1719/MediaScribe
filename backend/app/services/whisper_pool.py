"""Whisper 实例池 —— 开局预热 core 路，超出后按需加载临时实例（类 JVM core/max 线程）。"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Optional, Tuple

from .config import load_config

_log = logging.getLogger("sba.whisper_pool")

Slot = Tuple[str, Any]

_UNLIMITED_PRACTICAL = 512


class WhisperPool:
    def __init__(
        self,
        *,
        core_size: int = 4,
        max_size: int = 16,
        model_name: str = "",
    ) -> None:
        self._core_size = max(1, core_size)
        self._max_size = max(self._core_size, max_size)
        self._model_name = (model_name or os.environ.get("WHISPER_MODEL", "small")).strip() or "small"
        self._queue: queue.Queue[Slot] = queue.Queue()
        self._total = 0
        self._core_loaded = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._warmed = False

    @property
    def core_size(self) -> int:
        return self._core_size

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def _load_model(self) -> Any:
        import whisper

        name = self._model_name
        try:
            return whisper.load_model(name)
        except Exception as ex:
            _log.warning(
                "[链接沉淀文档-Whisper池|whisper_pool._load_model|whisper|硬编执行|加载] "
                "回退 small; model=%s; error=%s",
                name,
                ex,
            )
            self._model_name = "small"
            return whisper.load_model("small")

    def _register_instance(self, slot_id: str, model: Any, *, is_core: bool) -> None:
        with self._lock:
            self._total += 1
            if is_core:
                self._core_loaded += 1
        self._queue.put((slot_id, model))
        _log.info(
            "[链接沉淀文档-Whisper池|whisper_pool._register_instance|whisper|硬编执行|加载] "
            "实例就绪; slot=%s; is_core=%s; total=%s; core_loaded=%s; max=%s",
            slot_id,
            is_core,
            self._total,
            self._core_loaded,
            self._max_size,
        )

    def warmup(self) -> None:
        """启动时预热 core 路实例（后台线程调用，不阻塞 HTTP）。"""
        with self._lock:
            if self._warmed:
                return
            self._warmed = True
        _log.info(
            "[链接沉淀文档-Whisper池|whisper_pool.warmup|whisper|硬编执行|预热] "
            "开始; core=%s; max=%s; model=%s",
            self._core_size,
            self._max_size,
            self._model_name,
        )
        t0 = time.time()
        for i in range(self._core_size):
            model = self._load_model()
            self._register_instance(f"core-{i}", model, is_core=True)
        _log.info(
            "[链接沉淀文档-Whisper池|whisper_pool.warmup|whisper|硬编执行|预热] "
            "完成; elapsed_sec=%.1f; core=%s",
            time.time() - t0,
            self._core_size,
        )

    def _spawn_temp(self) -> None:
        idx = self._total
        model = self._load_model()
        self._register_instance(f"temp-{idx}", model, is_core=False)

    def acquire(self, timeout: float = 7200.0) -> Slot:
        """借出槽位；池满时动态扩容并加载新实例（不在锁内 load_model，避免阻塞全体等待）。"""
        deadline = time.time() + timeout if timeout and timeout > 0 else None
        while True:
            try:
                return self._queue.get_nowait()
            except queue.Empty:
                pass
            spawn = False
            with self._lock:
                if self._total < self._max_size:
                    spawn = True
                elif self._max_size < _UNLIMITED_PRACTICAL:
                    old_max = self._max_size
                    self._max_size = min(
                        _UNLIMITED_PRACTICAL,
                        self._max_size + max(1, self._core_size),
                    )
                    if self._max_size > old_max and self._total < self._max_size:
                        spawn = True
                        _log.info(
                            "[链接沉淀文档-Whisper池|whisper_pool.acquire|whisper|硬编执行|扩容] "
                            "池占满，动态扩容; old_max=%s; new_max=%s; total=%s",
                            old_max,
                            self._max_size,
                            self._total,
                        )
            if spawn:
                self._spawn_temp()
                continue
            with self._cond:
                if deadline is not None and time.time() >= deadline:
                    raise TimeoutError(
                        f"Whisper 池等待超时（max={self._max_size}, total={self._total}）"
                    )
                wait_sec = 1.0
                if deadline is not None:
                    wait_sec = min(wait_sec, max(0.05, deadline - time.time()))
                self._cond.wait(timeout=wait_sec)

    def release(self, slot: Slot) -> None:
        self._queue.put(slot)
        with self._cond:
            self._cond.notify()


_pool: Optional[WhisperPool] = None
_pool_lock = threading.Lock()


def _read_pool_sizes() -> Tuple[int, int, str]:
    cfg = load_config()
    try:
        core = int(cfg.get("whisper_pool_core_size", 4) or 4)
    except (TypeError, ValueError):
        core = 4
    try:
        raw_max = cfg.get("whisper_pool_size", 16)
        if raw_max is None:
            mx = 16
        else:
            mx = int(raw_max)
    except (TypeError, ValueError):
        mx = 16
    if mx <= 0:
        mx = _UNLIMITED_PRACTICAL
    core = max(1, core)
    mx = max(core, mx)
    model = (cfg.get("whisper_model") or os.environ.get("WHISPER_MODEL", "small") or "small").strip()
    return core, mx, model


def get_whisper_pool() -> WhisperPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            core, mx, model = _read_pool_sizes()
            _pool = WhisperPool(core_size=core, max_size=mx, model_name=model)
        return _pool


def schedule_whisper_warmup() -> None:
    """在后台线程预热 Whisper core 实例。"""

    def _run() -> None:
        try:
            get_whisper_pool().warmup()
        except Exception as ex:
            _log.warning(
                "[链接沉淀文档-Whisper池|whisper_pool.schedule_whisper_warmup|whisper|硬编执行|预热] "
                "失败; error=%s",
                ex,
            )

    threading.Thread(target=_run, name="whisper-warmup", daemon=True).start()


def register_whisper_pool_with_downloader(vd_mod: Any = None) -> bool:
    """
    将池注册到 video_downloader（须在 importlib.reload(video_downloader) 之后调用，
    reload 会重置模块全局变量 _whisper_pool）。
    """
    try:
        if vd_mod is None:
            import video_downloader as vd_mod  # type: ignore
        if not hasattr(vd_mod, "set_whisper_pool"):
            _log.warning(
                "[链接沉淀文档-Whisper池|whisper_pool.register_whisper_pool_with_downloader|"
                "video_downloader|硬编执行|注册] 跳过; 模块无 set_whisper_pool; path=%s",
                getattr(vd_mod, "__file__", ""),
            )
            return False
        pool = get_whisper_pool()
        vd_mod.set_whisper_pool(pool)
        _log.info(
            "[链接沉淀文档-Whisper池|whisper_pool.register_whisper_pool_with_downloader|"
            "video_downloader|硬编执行|注册] 成功; path=%s; core=%s; max=%s; warmed_total=%s",
            getattr(vd_mod, "__file__", ""),
            pool.core_size,
            pool.max_size,
            pool.total,
        )
        return True
    except Exception as ex:
        _log.warning(
            "[链接沉淀文档-Whisper池|whisper_pool.register_whisper_pool_with_downloader|"
            "video_downloader|硬编执行|注册] 失败; error=%s",
            ex,
        )
        return False
