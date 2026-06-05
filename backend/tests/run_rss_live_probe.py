"""RSS 订阅阅读 — 在线 API 全链路探测（需本地 8000 后端已启动）。"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
# GitHub 热门 AI 项目 Ollama 的 Releases Atom 订阅
FEED_URL = "https://github.com/ollama/ollama/releases.atom"


def _req(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            return resp.status, json.loads(raw.decode("utf-8"))
        return resp.status, raw


def _login() -> str:
    _, data = _req(
        "POST",
        "/api/auth/login",
        body={"identifier": "admin", "credential": "admin", "login_type": "password"},
    )
    token = (data or {}).get("access_token") or (data or {}).get("token")
    if not token:
        raise RuntimeError(f"登录失败: {data}")
    return token


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    print("[1/10] 登录 …")
    try:
        token = _login()
    except urllib.error.URLError as ex:
        print(f"后端未启动或不可达: {ex}")
        return 1
    print("       ok")

    print("[2/10] 健康检查 …")
    _, health = _req("GET", "/api/rss/health", token=token)
    _assert(health.get("ok") is True, f"health 异常: {health}")

    print("[3/10] 清理旧订阅（如有）…")
    _, feeds_data = _req("GET", "/api/rss/feeds", token=token)
    for f in feeds_data.get("feeds") or []:
        _req("DELETE", f"/api/rss/feeds/{f['id']}", token=token)
    print("       ok")

    print(f"[4/10] 添加订阅 {FEED_URL} …")
    _, add = _req("POST", "/api/rss/feeds", token=token, body={"url": FEED_URL})
    feed = add.get("feed") or {}
    feed_id = feed.get("id")
    _assert(feed_id, f"添加失败: {add}")
    _assert(feed.get("title"), "订阅标题为空")
    print(f"       标题: {feed.get('title')}")

    print("[5/10] 拉取文章列表 …")
    _, items_data = _req("GET", "/api/rss/items", token=token)
    items = items_data.get("items") or []
    _assert(len(items) > 0, "同步后文章数为 0")
    first = items[0]
    _assert(first.get("title"), "首条文章无标题")
    item_id = first["id"]
    print(f"       文章数: {len(items)}，首条: {first.get('title')[:60]}")

    print("[6/10] 统计与定时任务状态 …")
    _, stats = _req("GET", "/api/rss/stats", token=token)
    _assert(stats.get("feed_count", 0) >= 1, f"feed_count 异常: {stats}")
    _assert(stats.get("item_count", 0) >= 1, f"item_count 异常: {stats}")
    _, sched = _req("GET", "/api/rss/scheduler/status", token=token)
    print(f"       feeds={stats.get('feed_count')} items={stats.get('item_count')} scheduler={sched.get('scheduler_running')}")

    print("[7/10] 已读 / 星标 …")
    _, read_res = _req("POST", f"/api/rss/items/{item_id}/read", token=token, body={"read": True})
    _assert(read_res.get("item", {}).get("read") is True, f"标已读失败: {read_res}")
    _, star_res = _req("POST", f"/api/rss/items/{item_id}/star", token=token, body={"starred": True})
    _assert(star_res.get("item", {}).get("starred") is True, f"星标失败: {star_res}")
    _, stats2 = _req("GET", "/api/rss/stats", token=token)
    _assert(stats2.get("starred_count", 0) >= 1, f"starred_count 未更新: {stats2}")
    print("       ok")

    print("[8/10] 筛选（仅星标 / 按 feed）…")
    _, starred_only = _req("GET", "/api/rss/items?starred_only=true", token=token)
    _assert(len(starred_only.get("items") or []) >= 1, "仅星标筛选为空")
    _, feed_items = _req("GET", f"/api/rss/items?feed_id={feed_id}", token=token)
    _assert(len(feed_items.get("items") or []) >= 1, "按 feed 筛选为空")
    print("       ok")

    print("[9/10] 单源同步 + 全部同步 …")
    _, sync_one = _req("POST", f"/api/rss/feeds/{feed_id}/sync", token=token)
    _assert(sync_one.get("feed"), f"单源同步失败: {sync_one}")
    _, sync_all = _req("POST", "/api/rss/sync", token=token)
    _assert(sync_all.get("ok_count", 0) >= 1, f"全部同步失败: {sync_all}")
    print("       ok")

    print("[10/10] OPML 导出 …")
    status, opml_raw = _req("GET", "/api/rss/opml/export", token=token)
    opml = opml_raw.decode("utf-8") if isinstance(opml_raw, bytes) else str(opml_raw)
    _assert("xmlUrl" in opml and FEED_URL in opml, "OPML 未包含订阅地址")
    print("       ok")

    print("\n全部通过 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
