"""SearchBox SDK — 单例与默认索引挂载。"""
from __future__ import annotations

import threading
from typing import Optional

from .engine import SearchBoxEngine
from .facade import SearchBoxSDK
from .providers.builtin import mount_default_indices
from .registry import IndexRegistry

_sdk_lock = threading.RLock()
_sdk: Optional[SearchBoxSDK] = None


def get_search_box_sdk(*, reload: bool = False) -> SearchBoxSDK:
    """获取全局 SearchBoxSDK（懒加载 + 默认索引）。"""
    global _sdk
    with _sdk_lock:
        if _sdk is None or reload:
            registry = IndexRegistry()
            mount_default_indices(registry)
            _sdk = SearchBoxSDK(SearchBoxEngine(registry=registry))
        return _sdk


def reset_search_box_sdk() -> None:
    global _sdk
    with _sdk_lock:
        _sdk = None


# 向后兼容
def get_ai_search_engine(*, reload: bool = False) -> SearchBoxEngine:
    return get_search_box_sdk(reload=reload)._engine


def reset_ai_search_engine() -> None:
    reset_search_box_sdk()
