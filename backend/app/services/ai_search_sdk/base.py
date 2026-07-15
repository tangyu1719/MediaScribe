"""SearchBox SDK — 索引抽象（一个 Index = 一类可检索数据源，类 ES Index）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from .types import SearchHit


class SearchIndex(ABC):
    """可热插拔搜索索引。

    子类声明 ``index_id``（或向后兼容 ``provider_id``）即可挂载到搜索框。
    """

    index_id: str = ""
    label: str = ""
    description: str = ""
    categories: tuple[str, ...] = ()

    @property
    def resolved_index_id(self) -> str:
        cls = type(self)
        return (
            (getattr(cls, "index_id", None) or "").strip()
            or (getattr(cls, "provider_id", None) or "").strip()
        )

    @property
    def provider_id(self) -> str:
        return self.resolved_index_id

    @abstractmethod
    def search(
        self,
        query: str,
        terms: List[str],
        *,
        limit: int = 10,
        context: Dict[str, Any] | None = None,
    ) -> List[SearchHit]:
        """按扩展检索词返回候选列表。"""

    def health(self) -> Dict[str, Any]:
        return {"index_id": self.resolved_index_id, "status": "ok"}


SearchProvider = SearchIndex
