#!/usr/bin/env py
"""
链接沉淀流水线 — 边界批量实跑（13 条必跑 + 补充项）。

用法（在 backend 目录）:
  py -3 tests/run_link_pipeline_batch_boundary.py
  py -3 tests/run_link_pipeline_batch_boundary.py --only 1,3,5
  py -3 tests/run_link_pipeline_batch_boundary.py --resume report.json

强调：顺序执行，全量评论 count=0，MD 产出为通过门槛（HTML 后台可选）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# 用户指定 13 条（必跑）
REQUIRED_LINKS: List[Dict[str, str]] = [
    {"id": "1", "platform": "抖音", "link": "https://www.douyin.com/video/7522305267925634356"},
    {"id": "2", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a13fbe30000000035030727?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "3", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a0ae15e000000003601db35?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "4", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a1ba0d2000000000603312e?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "5", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a03567a0000000038021532?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "6", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a15bf0700000000370353f8?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "7", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/69f4ff4000000000360331a8?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "8", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/69fcc036000000001a034be5?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "9", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a14433d00000000360307f7?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "10", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a1a5a8a0000000007026aca?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "11", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a1a4d7500000000060329f6?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "12", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/69c1e81200000000230237ef?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
    {"id": "13", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/6a180dd8000000003802116d?xsec_token=REDACTED_TEST_TOKEN&xsec_source=pc_collect"},
]

# 补充边界（可选，默认一并跑）
SUPPLEMENT_LINKS: List[Dict[str, str]] = [
    {"id": "S1", "platform": "小红书", "link": "https://www.xiaohongshu.com/explore/invalid-not-exist", "expect_fail": True},
]

FULL_COMMENTS = {"enabled": True, "count": 0, "sort": "hot"}
REPORT_DIR = _BACKEND.parent / "reports" / "link_pipeline_batch"


def _platform_from_link(link: str) -> str:
    low = link.lower()
    if "douyin.com" in low:
        return "抖音"
    if "xiaohongshu.com" in low:
        return "小红书"
    if "bilibili.com" in low or "b23.tv" in low:
        return "B站"
    return "抖音"


async def run_one(item: Dict[str, Any], *, user_prompt: str = "边界批量测试-全量评论") -> Dict[str, Any]:
    from app.services.task_manager import get_task, reuse_or_enqueue_task
    from app.services.video_pipeline import process_video_pipeline

    link = item["link"]
    platform = item.get("platform") or _platform_from_link(link)
    t0 = time.perf_counter()
    tid, reused = reuse_or_enqueue_task(
        platform,
        link,
        user_prompt=user_prompt,
        comments=dict(FULL_COMMENTS),
        action="rerun",
    )
    print(f"[batch] #{item.get('id')} start task_id={tid} reused={reused} platform={platform}")
    try:
        await process_video_pipeline(tid)
    except Exception as ex:
        print(f"[batch] #{item.get('id')} exception: {ex}")
    task = get_task(tid) or {}
    elapsed = int((time.perf_counter() - t0) * 1000)
    status = (task.get("status") or "").strip()
    doc_path = task.get("doc_path") or task.get("doc_filename") or ""
    ok = status == "completed" and bool(doc_path)
    err_blob = " ".join(
        str(x.get("message") or "") for x in (task.get("logs") or [])[-30:]
    )
    row = {
        "id": item.get("id"),
        "link": link,
        "platform": platform,
        "task_id": tid,
        "status": status,
        "ok": ok,
        "elapsed_ms": elapsed,
        "pipeline_route": task.get("pipeline_route") or "",
        "doc_path": doc_path,
        "error": task.get("error") or "",
        "failed_stage": task.get("failed_stage") or "",
        "comments_enabled": bool((task.get("comments") or {}).get("enabled")),
        "expect_fail": bool(item.get("expect_fail")),
        "log_tail": err_blob[-2000:],
    }
    if item.get("expect_fail"):
        row["ok"] = status == "failed"
    print(
        f"[batch] #{item.get('id')} done ok={row['ok']} status={status} "
        f"route={row['pipeline_route']} elapsed_ms={elapsed}"
    )
    return row


async def main_async(args: argparse.Namespace) -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.resume) if args.resume else REPORT_DIR / f"batch_{ts}.json"

    items = list(REQUIRED_LINKS)
    if args.with_supplement:
        items.extend(SUPPLEMENT_LINKS)

    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        items = [x for x in items if x.get("id") in want]

    results: List[Dict[str, Any]] = []
    if args.resume and report_path.is_file():
        prev = json.loads(report_path.read_text(encoding="utf-8"))
        results = list(prev.get("results") or [])
        done_ids = {r.get("id") for r in results if r.get("ok")}
        items = [x for x in items if x.get("id") not in done_ids]

    for item in items:
        row = await run_one(item)
        results.append(row)
        report_path.write_text(
            json.dumps(
                {
                    "started_at": ts,
                    "comments": FULL_COMMENTS,
                    "results": results,
                    "summary": {
                        "total": len(results),
                        "ok": sum(1 for r in results if r.get("ok")),
                        "failed": sum(1 for r in results if not r.get("ok")),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    ok_n = sum(1 for r in results if r.get("ok"))
    print(f"[batch] report={report_path} ok={ok_n}/{len(results)}")
    required_ids = {x["id"] for x in REQUIRED_LINKS}
    required_ok = sum(
        1 for r in results if r.get("id") in required_ids and r.get("ok")
    )
    if required_ok < len(required_ids) and not args.only:
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="链接沉淀流水线边界批量实跑")
    p.add_argument("--only", help="仅跑指定 id，逗号分隔，如 1,3,5")
    p.add_argument("--with-supplement", action="store_true", help="附加补充边界链接")
    p.add_argument("--resume", help="从已有 report.json 续跑（跳过已成功 id）")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
