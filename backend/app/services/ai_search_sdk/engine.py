"""SearchBox SDK — 多索引检索引擎（fan-out + merge，类 ES _search）。"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .analyzers.pipeline import AnalyzerPipeline
from .highlight import apply_highlights
from .llm import rerank_hits_llm
from .ollama_config import ai_search_ollama_settings
from .ranker import boost_exact_title, merge_hits
from .registry import IndexRegistry
from .types import FacetBucket, SearchHit, SearchQuery, SearchResponse, SuggestOption

_log = logging.getLogger("sba.search_box.engine")


class SearchBoxEngine:
    """底层搜索引擎：Analyzer → multi-index fan-out → rank → highlight。"""

    def __init__(self, registry: Optional[IndexRegistry] = None) -> None:
        self.registry = registry or IndexRegistry()

    def execute(self, query: SearchQuery) -> SearchResponse:
        t0 = time.perf_counter()
        q = (query.q or "").strip()
        if not q:
            return SearchResponse(q="", took_ms=0, hits=[], total=0)

        is_suggest = query.mode == "suggest"
        use_llm = (not is_suggest) and query.use_llm_expand
        analyzed = AnalyzerPipeline(
            use_rule_synonym=True,
            use_llm_expand=use_llm,
            domain_hint=str((query.context or {}).get("domain_hint") or ""),
        ).run(q)

        per_index_limit = max(query.size, min(query.size * 2, 30))
        all_hits: List[SearchHit] = []
        indices_used: List[str] = []

        for index in self.registry.iter_indices(query.indices):
            try:
                hits = index.search(
                    q,
                    analyzed.all_terms,
                    limit=per_index_limit,
                    context=query.context or {},
                )
                if hits:
                    indices_used.append(index.resolved_index_id)
                    all_hits.extend(hits)
            except Exception as exc:
                _log.warning(
                    "[搜索框SDK-索引检索|SearchBoxEngine|execute|工具执行|失败] "
                    "index_id=%s; error_type=%s; error_message=%s",
                    index.resolved_index_id,
                    type(exc).__name__,
                    str(exc)[:160],
                )

        merged = merge_hits(all_hits, limit=per_index_limit)
        merged = boost_exact_title(q, merged)
        if query.use_llm_rerank and merged and not is_suggest:
            merged = rerank_hits_llm(q, merged, limit=query.size + query.from_)
        merged = apply_highlights(merged, analyzed.all_terms)

        total = len(merged)
        page_hits = merged[query.from_ : query.from_ + query.size]
        suggest = self._build_suggest(page_hits or merged[: query.size])
        aggregations = self._build_aggregations(merged)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _log.info(
            "[搜索框SDK-统一检索|SearchBoxEngine|execute|Agent执行|完成] "
            "q=%s; mode=%s; count=%s; indices=%s; llm=%s; took_ms=%s",
            q[:80],
            query.mode,
            len(page_hits),
            ",".join(indices_used),
            analyzed.llm_powered,
            elapsed_ms,
        )
        return SearchResponse(
            q=q,
            took_ms=elapsed_ms,
            hits=page_hits,
            total=total,
            indices_used=indices_used,
            expanded_terms=analyzed.rule_terms,
            llm_expanded_terms=analyzed.llm_terms,
            llm_powered=analyzed.llm_powered,
            suggest=suggest,
            aggregations=aggregations,
            meta={
                "mode": query.mode,
                "intent_hint": analyzed.llm_meta.get("intent_hint") or "",
                "llm_node_id": analyzed.llm_meta.get("node_id") or "",
                "term_count": len(analyzed.all_terms),
                "ollama": self._ollama_snapshot(),
            },
        )

    def search(self, request) -> SearchResponse:
        """向后兼容：接受 AiSearchRequest 或 SearchQuery。"""
        from .types import AiSearchRequest

        if isinstance(request, SearchQuery):
            return self.execute(request)
        if isinstance(request, AiSearchRequest):
            return self.execute(request.to_search_query())
        raise TypeError("request 须为 SearchQuery 或 AiSearchRequest")

    def _build_suggest(self, hits: List[SearchHit]) -> List[SuggestOption]:
        options: List[SuggestOption] = []
        seen: set[str] = set()
        for hit in hits:
            text = (hit.title or "").strip()
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            options.append(
                SuggestOption(
                    text=text,
                    score=hit.score,
                    index=hit.provider_id,
                    payload={"id": hit.id, "category": hit.category},
                )
            )
        return options

    def _build_aggregations(self, hits: List[SearchHit]) -> Dict[str, List[FacetBucket]]:
        by_index: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        for hit in hits:
            by_index[hit.provider_id] = by_index.get(hit.provider_id, 0) + 1
            cat = hit.category or "other"
            by_category[cat] = by_category.get(cat, 0) + 1
        return {
            "by_index": [
                FacetBucket(key=k, count=v, label=k) for k, v in sorted(by_index.items(), key=lambda x: -x[1])
            ],
            "by_category": [
                FacetBucket(key=k, count=v, label=k) for k, v in sorted(by_category.items(), key=lambda x: -x[1])
            ],
        }

    def _ollama_snapshot(self) -> Dict[str, Any]:
        try:
            cfg = ai_search_ollama_settings()
            return {
                "enabled": cfg["enabled"],
                "base_url": cfg["ollama_base_url"],
                "model": cfg["ollama_model"],
                "timeout_sec": cfg["timeout_sec"],
                "gateway_fallback": cfg["gateway_fallback"],
            }
        except Exception:
            return {}


# 向后兼容
AiSearchEngine = SearchBoxEngine
