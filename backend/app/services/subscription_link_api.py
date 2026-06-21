"""订阅链接卡片 API。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .subscription_link_card_store import list_link_cards, sync_link_card_from_task, upsert_link_card

_log = logging.getLogger("sba.subscription_link_api")


def _db_link_cards_enabled() -> bool:
    return bool((os.environ.get("SBA_DATABASE_URL") or "").strip())


def api_list_subscription_link_cards(
    subscription_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """优先 MySQL：seen_notes JOIN pipeline_task_history 一次查询；无库时回退 Redis。"""
    if _db_link_cards_enabled():
        try:
            from .creator_subscription_store import (
                SubscriptionDbError,
                init_db,
                list_subscription_link_cards,
            )

            init_db()
            result = list_subscription_link_cards(
                subscription_id, page=page, page_size=page_size
            )
            _log.debug(
                "[社媒订阅-链接卡片|subscription_link_api.api_list_subscription_link_cards|"
                "subscription|硬编执行|MySQL] subscription_id=%s; total=%s; page=%s",
                subscription_id,
                result.get("total"),
                page,
            )
            return result
        except SubscriptionDbError as ex:
            _log.warning(
                "[社媒订阅-链接卡片|subscription_link_api.api_list_subscription_link_cards|"
                "subscription|硬编执行|MySQL不可用] error_message=%s",
                str(ex)[:120],
            )
        except Exception as ex:
            _log.warning(
                "[社媒订阅-链接卡片|subscription_link_api.api_list_subscription_link_cards|"
                "subscription|硬编执行|MySQL失败] error_type=%s; error_message=%s",
                type(ex).__name__,
                str(ex)[:120],
            )
    return list_link_cards(subscription_id, page=page, page_size=page_size)


def api_refresh_link_card_from_task(subscription_id: str, task_id: str) -> Dict[str, Any]:
    from .task_manager import get_task

    task = get_task(task_id)
    if not task:
        return {"ok": False, "error": "任务不存在"}
    card = sync_link_card_from_task(subscription_id, task)
    return {"ok": True, "card": card}


def api_upsert_link_card_from_feed(
    subscription_id: str,
    feed_item: Any,
    *,
    task_id: Optional[str] = None,
    analysis_status: str = "pending",
) -> Dict[str, Any]:
    card = upsert_link_card(
        subscription_id=subscription_id,
        platform=str(getattr(feed_item, "platform", "") or ""),
        note_id=str(getattr(feed_item, "note_id", "") or ""),
        canonical_url=str(getattr(feed_item, "canonical_url", "") or ""),
        url_hash=str(getattr(feed_item, "url_hash", "") or ""),
        title=str(getattr(feed_item, "title", "") or ""),
        content_type=str(getattr(feed_item, "content_type", "") or ""),
        published_at=str(getattr(feed_item, "published_at", "") or ""),
        task_id=task_id,
        analysis_status=analysis_status,
    )
    return {"ok": True, "card": card}
