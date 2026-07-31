"""Compare legacy favorites scraping with in-page response capture.

The command is read-only for Xiaohongshu: it reloads/scrolls the authenticated
favorites page but never follows, unfollows, collects, or uncollects anything.
Output contains aggregate completeness metrics only, not note URLs, cookies, or
signature values.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.cookie_manager import find_cdp_port  # noqa: E402
from app.services.xhs_favorites_adapter import favorites_catalog_metrics  # noqa: E402
from app.services.xhs_local_browser import (  # noqa: E402
    cdp_list_tabs,
    cdp_pick_owner_tab,
    cdp_tab_eval,
    scrape_favorites_feed_items_via_cdp,
)


def _detect_creator_id() -> str:
    """从已登录收藏页的个人主页链接识别账号，不输出候选 ID。"""
    port = find_cdp_port()
    if not port:
        return ""
    expression = r"""(() => {
        for (const anchor of document.querySelectorAll('a[href*="/user/profile/"]')) {
            const match = String(anchor.href || '').match(/\/user\/profile\/([a-f0-9]{24})/i);
            if (match && !/^0+$/.test(match[1])) return match[1];
        }
        return '';
    })()"""
    for tab in cdp_list_tabs(port):
        url = str(tab.get("url") or "")
        ws_url = tab.get("webSocketDebuggerUrl")
        if "xiaohongshu.com" not in url or not ws_url:
            continue
        value = str(cdp_tab_eval(ws_url, expression, timeout_sec=8) or "").strip()
        if len(value) == 24:
            return value
    return ""


def _prepare_same_page(creator_id: str, settle_seconds: float) -> None:
    port = find_cdp_port()
    if not port:
        return
    tab = cdp_pick_owner_tab(port, prefer_cid=creator_id)
    ws_url = (tab or {}).get("webSocketDebuggerUrl")
    if not ws_url:
        return
    cdp_tab_eval(
        ws_url,
        "window.scrollTo(0, 0); window.location.reload(); true",
        timeout_sec=8,
    )
    time.sleep(max(1.0, min(float(settle_seconds), 15.0)))


def _run_mode(
    *,
    creator_id: str,
    profile_url: str,
    scroll_rounds: int,
    capture_enabled: bool,
    settle_seconds: float,
) -> Dict[str, Any]:
    os.environ["SBA_XHS_FAVORITES_RESPONSE_CAPTURE"] = "1" if capture_enabled else "0"
    _prepare_same_page(creator_id, settle_seconds)
    started_at = time.perf_counter()
    try:
        items = scrape_favorites_feed_items_via_cdp(
            profile_url,
            creator_id=creator_id,
            scroll_rounds=scroll_rounds,
        )
    except Exception as ex:
        return {
            "ok": False,
            "capture_enabled": capture_enabled,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            "error_type": type(ex).__name__,
            "error": str(ex)[:500],
        }
    metrics = favorites_catalog_metrics(items)
    metrics.update(
        {
            "ok": True,
            "capture_enabled": capture_enabled,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        }
    )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(
        description="同参数对比小红书收藏旧采集路径与页面内响应捕获路径。"
    )
    parser.add_argument(
        "--creator-id",
        default="",
        help="当前登录账号的 24 位 creator_id；省略时从已打开的小红书页面自动识别",
    )
    parser.add_argument("--profile-url", default="", help="收藏页 URL；默认由 creator_id 生成")
    parser.add_argument("--scroll-rounds", type=int, default=4)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    args = parser.parse_args()

    creator_id = str(args.creator_id or "").strip() or _detect_creator_id()
    if len(creator_id) != 24:
        parser.error("未识别到 24 位 creator_id；请打开已登录的本人收藏页或显式传入 --creator-id")
    profile_url = str(args.profile_url or "").strip() or (
        f"https://www.xiaohongshu.com/user/profile/{creator_id}?tab=fav&subTab=note"
    )
    previous = os.environ.get("SBA_XHS_FAVORITES_RESPONSE_CAPTURE")
    try:
        baseline = _run_mode(
            creator_id=creator_id,
            profile_url=profile_url,
            scroll_rounds=max(1, args.scroll_rounds),
            capture_enabled=False,
            settle_seconds=args.settle_seconds,
        )
        captured = _run_mode(
            creator_id=creator_id,
            profile_url=profile_url,
            scroll_rounds=max(1, args.scroll_rounds),
            capture_enabled=True,
            settle_seconds=args.settle_seconds,
        )
    finally:
        if previous is None:
            os.environ.pop("SBA_XHS_FAVORITES_RESPONSE_CAPTURE", None)
        else:
            os.environ["SBA_XHS_FAVORITES_RESPONSE_CAPTURE"] = previous

    comparison: Dict[str, Any] = {
        "baseline": baseline,
        "response_capture": captured,
    }
    if baseline.get("ok") and captured.get("ok"):
        comparison["delta"] = {
            "count": int(captured.get("count") or 0) - int(baseline.get("count") or 0),
            "xsec_token_rate": round(
                float(captured.get("xsec_token_rate") or 0)
                - float(baseline.get("xsec_token_rate") or 0),
                4,
            ),
            "title_rate": round(
                float(captured.get("title_rate") or 0)
                - float(baseline.get("title_rate") or 0),
                4,
            ),
            "author_rate": round(
                float(captured.get("author_rate") or 0)
                - float(baseline.get("author_rate") or 0),
                4,
            ),
            "elapsed_ms": int(captured.get("elapsed_ms") or 0)
            - int(baseline.get("elapsed_ms") or 0),
        }
    print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if baseline.get("ok") and captured.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
