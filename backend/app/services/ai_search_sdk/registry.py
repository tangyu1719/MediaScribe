"""SearchBox SDK — 索引注册表（类 ES Index，热插拔 mount/unmount）。"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Iterable, List, Optional

from .base import SearchIndex

_log = logging.getLogger("sba.search_box.registry")

# 向后兼容
SearchProvider = SearchIndex


class IndexRegistry:
    """线程安全的索引注册中心。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._indices: Dict[str, SearchIndex] = {}
        self._disabled: set[str] = set()

    def mount(self, index: SearchIndex, *, replace: bool = False) -> None:
        """挂载索引（热插拔）。"""
        iid = (index.resolved_index_id or "").strip()
        if not iid:
            raise ValueError("index_id 不能为空")
        with self._lock:
            if iid in self._indices and not replace:
                raise ValueError(f"索引已存在: {iid}")
            self._indices[iid] = index
            self._disabled.discard(iid)
            _log.info(
                "[搜索框SDK-索引挂载|IndexRegistry|mount|硬编执行|完成] "
                "index_id=%s; label=%s; ok=true",
                iid,
                index.label,
            )

    def unmount(self, index_id: str) -> bool:
        """卸载索引。"""
        iid = (index_id or "").strip()
        with self._lock:
            if iid not in self._indices:
                return False
            del self._indices[iid]
            self._disabled.discard(iid)
            _log.info(
                "[搜索框SDK-索引卸载|IndexRegistry|unmount|硬编执行|完成] index_id=%s; ok=true",
                iid,
            )
            return True

    def disable(self, index_id: str) -> bool:
        with self._lock:
            if index_id not in self._indices:
                return False
            self._disabled.add(index_id)
            return True

    def enable(self, index_id: str) -> bool:
        with self._lock:
            if index_id not in self._indices:
                return False
            self._disabled.discard(index_id)
            return True

    def get(self, index_id: str) -> Optional[SearchIndex]:
        with self._lock:
            return self._indices.get((index_id or "").strip())

    def list_indices(self) -> List[Dict[str, object]]:
        with self._lock:
            return [
                {
                    "index_id": idx.resolved_index_id,
                    "label": idx.label,
                    "description": idx.description,
                    "categories": list(idx.categories),
                    "enabled": idx.resolved_index_id not in self._disabled,
                    "provider_id": idx.resolved_index_id,
                }
                for idx in self._indices.values()
            ]

    def iter_indices(self, index_ids: Optional[Iterable[str]] = None):
        wanted = {str(x).strip() for x in (index_ids or []) if str(x).strip()}
        with self._lock:
            for iid, index in self._indices.items():
                rid = index.resolved_index_id
                if rid in self._disabled or iid in self._disabled:
                    continue
                if wanted and rid not in wanted and iid not in wanted:
                    continue
                yield index

    # ── 向后兼容 Provider 命名 ──
    def register(self, provider: SearchIndex, *, replace: bool = False) -> None:
        self.mount(provider, replace=replace)

    def unregister(self, provider_id: str) -> bool:
        return self.unmount(provider_id)

    def list_providers(self) -> List[Dict[str, object]]:
        return self.list_indices()


SearchProviderRegistry = IndexRegistry
