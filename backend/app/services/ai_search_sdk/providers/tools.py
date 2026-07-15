"""内置工具搜索 Provider。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..types import SearchDocument, SearchHit
from ..base import SearchProvider
from .text_match import rank_documents


class BuiltinToolsSearchProvider(SearchProvider):
    provider_id = "builtin_tools"
    label = "内置工具"
    description = "检索 AI 对话可挂载的内置 Tool Call"
    categories = ("tool", "tool_call")

    def _load_documents(self) -> List[SearchDocument]:
        from ...builtin_tools import list_builtin_tools

        docs: List[SearchDocument] = []
        for tool in list_builtin_tools() or []:
            tid = str(tool.get("id") or "").strip()
            if not tid:
                continue
            name = str(tool.get("name") or tid)
            desc = str(tool.get("description") or "")
            kind = str(tool.get("kind") or "tool_call")
            inputs = tool.get("inputs") if isinstance(tool.get("inputs"), list) else []
            input_hints = " ".join(
                str(i.get("hint") or i.get("name") or "")
                for i in inputs
                if isinstance(i, dict)
            )
            searchable = " ".join(filter(None, [name, desc, kind, input_hints, tid]))
            docs.append(
                SearchDocument(
                    id=tid,
                    title=name,
                    subtitle=kind,
                    description=desc,
                    category="tool",
                    searchable_text=searchable,
                    payload={"tool": tool},
                )
            )
        return docs

    def search(
        self,
        query: str,
        terms: List[str],
        *,
        limit: int = 10,
        context: Dict[str, Any] | None = None,
    ) -> List[SearchHit]:
        docs = self._load_documents()
        ranked = rank_documents(docs, terms or [query], limit=limit)
        hits: List[SearchHit] = []
        for doc, score, reason in ranked:
            hits.append(
                SearchHit(
                    id=doc.id,
                    title=doc.title,
                    provider_id=self.provider_id,
                    subtitle=doc.subtitle,
                    description=doc.description,
                    category=doc.category,
                    score=score,
                    match_reason=reason,
                    payload=doc.payload,
                )
            )
        return hits


BuiltinToolsSearchIndex = BuiltinToolsSearchProvider
