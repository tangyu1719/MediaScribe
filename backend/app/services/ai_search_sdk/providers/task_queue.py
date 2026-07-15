"""链接文档化队列搜索 Provider。"""
from __future__ import annotations

from typing import Any, Dict, List

from ...task_queue_search import filter_tasks, match_task_title
from ...task_source_meta import enrich_task_source_fields
from ..types import SearchHit
from ..base import SearchProvider


class TaskQueueSearchProvider(SearchProvider):
    provider_id = "task_queue"
    label = "链接队列"
    description = "检索链接文档化任务队列中的标题、链接与作者"
    categories = ("link", "task", "queue")

    def search(
        self,
        query: str,
        terms: List[str],
        *,
        limit: int = 10,
        context: Dict[str, Any] | None = None,
    ) -> List[SearchHit]:
        from ...task_manager import list_tasks

        tasks = list_tasks() or []
        if not tasks:
            return []
        filtered = filter_tasks(
            tasks,
            title_query=query,
            enable_title=True,
            enable_link=True,
            enable_author=True,
            sort="updated",
        )
        items = filtered.get("items") or []
        hits: List[SearchHit] = []
        for row in items[: max(1, int(limit or 10))]:
            row = enrich_task_source_fields(dict(row))
            title = str(row.get("link_title") or row.get("doc_title") or row.get("task_id") or "未命名任务")
            link = str(row.get("link") or row.get("canonical_url") or "")
            author = str(row.get("author_name") or "")
            subtitle = " · ".join(filter(None, [author, str(row.get("source_label") or "")]))
            desc_parts = [link] if link else []
            if row.get("task_note"):
                desc_parts.append(str(row.get("task_note")))
            score = 0.75 if match_task_title(row, query) else 0.5
            hits.append(
                SearchHit(
                    id=str(row.get("task_id") or link or title),
                    title=title,
                    provider_id=self.provider_id,
                    subtitle=subtitle,
                    description=" | ".join(desc_parts)[:240],
                    category="task",
                    score=score,
                    match_reason="队列标题/链接匹配",
                    payload={"task": row},
                )
            )
        hits.sort(key=lambda h: (-h.score, h.title))
        return hits[: max(1, int(limit or 10))]


TaskQueueSearchIndex = TaskQueueSearchProvider
