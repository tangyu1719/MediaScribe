"""查询分析管线（类 ES Analyzer：分词 → 近义词 → 可选 LLM 扩展）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ...follow_up_search import expand_search_terms
from ..llm import expand_query_llm


@dataclass
class AnalyzedQuery:
    """分析后的查询上下文。"""

    raw: str
    rule_terms: List[str] = field(default_factory=list)
    llm_terms: List[str] = field(default_factory=list)
    all_terms: List[str] = field(default_factory=list)
    llm_powered: bool = False
    llm_meta: Dict[str, Any] = field(default_factory=dict)


class AnalyzerPipeline:
    """可配置的分析链。"""

    def __init__(
        self,
        *,
        use_rule_synonym: bool = True,
        use_llm_expand: bool = False,
        domain_hint: str = "",
    ) -> None:
        self.use_rule_synonym = use_rule_synonym
        self.use_llm_expand = use_llm_expand
        self.domain_hint = domain_hint

    def run(self, query: str) -> AnalyzedQuery:
        q = (query or "").strip()
        if not q:
            return AnalyzedQuery(raw="")

        rule_terms = expand_search_terms(q) if self.use_rule_synonym else [q]
        llm_terms: List[str] = []
        llm_meta: Dict[str, Any] = {}
        llm_powered = False

        if self.use_llm_expand:
            llm_meta = expand_query_llm(q, domain_hint=self.domain_hint)
            llm_terms = [
                str(t).strip() for t in (llm_meta.get("expanded_terms") or []) if str(t).strip()
            ]
            llm_powered = bool(llm_meta.get("llm_powered"))

        all_terms: List[str] = []
        seen: set[str] = set()
        for t in list(rule_terms) + list(llm_terms):
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            all_terms.append(t)

        return AnalyzedQuery(
            raw=q,
            rule_terms=rule_terms,
            llm_terms=llm_terms,
            all_terms=all_terms or [q],
            llm_powered=llm_powered,
            llm_meta=llm_meta,
        )


def analyze_query(
    query: str,
    *,
    mode: str = "search",
    domain_hint: str = "",
) -> AnalyzedQuery:
    """快捷分析：suggest 模式仅规则扩展，search 模式可开 LLM。"""
    use_llm = mode == "search"
    return AnalyzerPipeline(
        use_rule_synonym=True,
        use_llm_expand=use_llm,
        domain_hint=domain_hint,
    ).run(query)
