"""SearchBox SDK — 类型定义（ES 风格 hits / suggest / aggregations）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class SearchDocument:
    """索引内文档（Provider 内部）。"""

    id: str
    title: str
    subtitle: str = ""
    description: str = ""
    category: str = ""
    searchable_text: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    """单条命中（=_source + _score）。"""

    id: str
    title: str
    provider_id: str
    subtitle: str = ""
    description: str = ""
    category: str = ""
    score: float = 0.0
    match_reason: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    highlight: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_es_hit(self) -> Dict[str, Any]:
        return {
            "_index": self.provider_id,
            "_id": self.id,
            "_score": round(self.score, 4),
            "_source": {
                "title": self.title,
                "subtitle": self.subtitle,
                "description": self.description,
                "category": self.category,
                "match_reason": self.match_reason,
                "payload": self.payload,
            },
            "highlight": self.highlight or None,
        }


@dataclass
class SearchQuery:
    """类 ES 查询体（搜索框 / _search 统一入口）。"""

    q: str
    indices: Optional[List[str]] = None
    size: int = 10
    from_: int = 0
    mode: Literal["search", "suggest"] = "search"
    use_llm_expand: bool = True
    use_llm_rerank: bool = False
    context: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: Dict[str, Any]) -> "SearchQuery":
        q = str(body.get("q") or body.get("query") or "").strip()
        indices = body.get("indices") or body.get("providers")
        if isinstance(indices, list):
            indices = [str(x).strip() for x in indices if str(x).strip()]
        else:
            indices = None
        mode = str(body.get("mode") or "search").strip().lower()
        if mode not in ("search", "suggest"):
            mode = "search"
        return cls(
            q=q,
            indices=indices,
            size=max(1, min(int(body.get("size") or body.get("limit") or 10), 50)),
            from_=max(0, int(body.get("from") or body.get("offset") or 0)),
            mode=mode,  # type: ignore[arg-type]
            use_llm_expand=body.get("use_llm_expand", True) is not False,
            use_llm_rerank=body.get("use_llm_rerank") is True,
            context=body.get("context") if isinstance(body.get("context"), dict) else {},
        )


# 向后兼容：旧调用方仍可用 query/limit/providers 字段
@dataclass
class AiSearchRequest:
    query: str
    limit: int = 10
    providers: Optional[List[str]] = None
    use_llm_expand: bool = True
    use_llm_rerank: bool = False
    context: Dict[str, Any] = field(default_factory=dict)

    def to_search_query(self) -> SearchQuery:
        return SearchQuery(
            q=self.query,
            indices=self.providers,
            size=self.limit,
            mode="search",
            use_llm_expand=self.use_llm_expand,
            use_llm_rerank=self.use_llm_rerank,
            context=self.context,
        )


@dataclass
class SuggestOption:
    text: str
    score: float = 0.0
    index: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FacetBucket:
    key: str
    count: int
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResponse:
    """类 ES 搜索响应。"""

    q: str
    took_ms: int
    hits: List[SearchHit]
    total: int
    indices_used: List[str] = field(default_factory=list)
    expanded_terms: List[str] = field(default_factory=list)
    llm_expanded_terms: List[str] = field(default_factory=list)
    llm_powered: bool = False
    suggest: List[SuggestOption] = field(default_factory=list)
    aggregations: Dict[str, List[FacetBucket]] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    # 向后兼容属性
    @property
    def items(self) -> List[SearchHit]:
        return self.hits

    @property
    def query(self) -> str:
        return self.q

    @property
    def providers_used(self) -> List[str]:
        return self.indices_used

    def to_dict(self) -> Dict[str, Any]:
        base = {
            "q": self.q,
            "query": self.q,
            "took_ms": self.took_ms,
            "hits": [h.to_dict() for h in self.hits],
            "items": [h.to_dict() for h in self.hits],
            "total": self.total,
            "count": len(self.hits),
            "indices_used": self.indices_used,
            "providers_used": self.indices_used,
            "expanded_terms": self.expanded_terms,
            "llm_expanded_terms": self.llm_expanded_terms,
            "llm_powered": self.llm_powered,
            "suggest": [s.to_dict() for s in self.suggest],
            "aggregations": {k: [b.to_dict() for b in v] for k, v in self.aggregations.items()},
            "meta": self.meta,
        }
        return base

    def to_es_response(self) -> Dict[str, Any]:
        """Elasticsearch 风格 JSON（便于搜索框组件直接消费）。"""
        return {
            "took": self.took_ms,
            "timed_out": False,
            "_shards": {"total": len(self.indices_used), "successful": len(self.indices_used), "failed": 0},
            "hits": {
                "total": {"value": self.total, "relation": "eq"},
                "max_score": round(max((h.score for h in self.hits), default=0.0), 4),
                "hits": [h.to_es_hit() for h in self.hits],
            },
            "suggest": {
                "completion": [
                    {"text": s.text, "score": s.score, "_index": s.index, "payload": s.payload}
                    for s in self.suggest
                ]
            },
            "aggregations": {
                name: {"buckets": [b.to_dict() for b in buckets]}
                for name, buckets in self.aggregations.items()
            },
            "meta": self.meta,
        }


AiSearchResult = SearchResponse
