"""SearchBox SDK — 对外门面（搜索框唯一入口，类 ES Client）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import SearchIndex
from .engine import SearchBoxEngine
from .registry import IndexRegistry
from .types import SearchQuery, SearchResponse


class SearchBoxSDK:
    """搜索框 SDK 门面。

    类比 Elasticsearch：
    - Index（索引）  ≈  SearchIndex 实现类，热插拔 mount/unmount
    - _search         ≈  search() / execute()
    - _suggest        ≈  suggest()  （快速补全，默认不走 LLM）
    - aggregations    ≈  facets() 分面统计

    示例::

        sdk = get_search_box_sdk()
        sdk.mount(MyCustomIndex())

        # 搜索框回车
        res = sdk.search("rag", size=8)

        # 输入联想（轻量）
        sug = sdk.suggest("链")

        # ES 风格 JSON
        es_json = res.to_es_response()
    """

    def __init__(self, engine: Optional[SearchBoxEngine] = None) -> None:
        self._engine = engine or SearchBoxEngine()

    @property
    def registry(self) -> IndexRegistry:
        return self._engine.registry

    # ── 索引热插拔 ──

    def mount(self, index: SearchIndex, *, replace: bool = False) -> None:
        """挂载索引（运行时热插拔）。"""
        self.registry.mount(index, replace=replace)

    def unmount(self, index_id: str) -> bool:
        """卸载索引。"""
        return self.registry.unmount(index_id)

    def disable_index(self, index_id: str) -> bool:
        return self.registry.disable(index_id)

    def enable_index(self, index_id: str) -> bool:
        return self.registry.enable(index_id)

    def list_indices(self) -> List[Dict[str, object]]:
        return self.registry.list_indices()

    # 向后兼容
    def register(self, provider: SearchIndex, *, replace: bool = False) -> None:
        self.mount(provider, replace=replace)

    def unregister(self, provider_id: str) -> bool:
        return self.unmount(provider_id)

    # ── 查询 API ──

    def execute(self, query: SearchQuery) -> SearchResponse:
        return self._engine.execute(query)

    def search(
        self,
        q: str,
        *,
        indices: Optional[List[str]] = None,
        size: int = 10,
        from_: int = 0,
        use_llm_expand: bool = True,
        use_llm_rerank: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> SearchResponse:
        """完整搜索（可多索引，可选 LLM 扩展）。"""
        return self.execute(
            SearchQuery(
                q=q,
                indices=indices,
                size=size,
                from_=from_,
                mode="search",
                use_llm_expand=use_llm_expand,
                use_llm_rerank=use_llm_rerank,
                context=context or {},
            )
        )

    def suggest(
        self,
        q: str,
        *,
        indices: Optional[List[str]] = None,
        size: int = 8,
        context: Optional[Dict[str, Any]] = None,
    ) -> SearchResponse:
        """搜索框联想补全（轻量：规则扩展，不走 LLM）。"""
        return self.execute(
            SearchQuery(
                q=q,
                indices=indices,
                size=size,
                mode="suggest",
                use_llm_expand=False,
                use_llm_rerank=False,
                context=context or {},
            )
        )

    def facets(
        self,
        q: str,
        *,
        indices: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Dict[str, object]]]:
        """分面聚合（按索引 / 类别统计命中数）。"""
        res = self.execute(
            SearchQuery(
                q=q,
                indices=indices,
                size=50,
                mode="suggest",
                use_llm_expand=False,
                context=context or {},
            )
        )
        return {k: [b.to_dict() for b in v] for k, v in res.aggregations.items()}

    @staticmethod
    def parse_body(body: Dict[str, Any]) -> SearchQuery:
        return SearchQuery.from_body(body)
