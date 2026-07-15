"""技能库搜索 Provider。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..types import SearchDocument, SearchHit
from ..base import SearchProvider
from .text_match import rank_documents


class SkillsSearchProvider(SearchProvider):
    provider_id = "skills"
    label = "技能库"
    description = "检索已导入 SKILL 的名称、描述与命令"
    categories = ("skill",)

    def _load_documents(self) -> List[SearchDocument]:
        from ...skill_registry import list_skills

        docs: List[SearchDocument] = []
        for skill in list_skills() or []:
            sid = str(skill.get("skill_id") or skill.get("id") or "").strip()
            if not sid:
                continue
            name = str(skill.get("name") or sid)
            desc = str(skill.get("description") or skill.get("summary") or "")
            commands = skill.get("commands") if isinstance(skill.get("commands"), list) else []
            cmd_text = " ".join(str(c) for c in commands[:12])
            searchable = " ".join(filter(None, [name, desc, cmd_text, sid]))
            docs.append(
                SearchDocument(
                    id=sid,
                    title=name,
                    subtitle="skill",
                    description=desc,
                    category="skill",
                    searchable_text=searchable,
                    payload={"skill": skill},
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
        return [
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
            for doc, score, reason in ranked
        ]


SkillsSearchIndex = SkillsSearchProvider
