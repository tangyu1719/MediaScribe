"""RSS 沉淀 MD 回归：全文抓取 API + 链接沉淀入队（需 8000 后端 + LLM 配置）。"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
FEED_URL = "https://github.com/ollama/ollama/releases.atom"
POLL_SEC = 2
MAX_WAIT = 300


def _req(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=MAX_WAIT) as resp:
        raw = resp.read()
        if "json" in (resp.headers.get("Content-Type") or ""):
            return resp.status, json.loads(raw.decode("utf-8"))
        return resp.status, raw


def main() -> int:
    print("[1/5] 登录 …")
    try:
        _, login = _req(
            "POST",
            "/api/auth/login",
            body={"identifier": "admin", "credential": "admin", "login_type": "password"},
        )
        token = login.get("access_token")
        if not token:
            raise RuntimeError("登录失败")
    except urllib.error.URLError as ex:
        print(f"后端不可达: {ex}")
        return 1

    print("[2/5] 确保有 RSS 文章 …")
    _, feeds = _req("GET", "/api/rss/feeds", token=token)
    if not (feeds.get("feeds") or []):
        _req("POST", "/api/rss/feeds", token=token, body={"url": FEED_URL})
    _, items_data = _req("GET", "/api/rss/items", token=token)
    items = items_data.get("items") or []
    if not items:
        print("无文章条目")
        return 1
    item = items[0]
    item_id = item["id"]
    link = item.get("link") or ""
    print(f"       条目: {(item.get('title') or '')[:50]}")

    print("[3/5] 提交沉淀 …")
    _, doc_meta = _req(
        "POST",
        f"/api/rss/items/{item_id}/document",
        token=token,
        body={},
    )
    task_id = doc_meta.get("task_id")
    if not task_id:
        print(f"入队失败: {doc_meta}")
        return 1
    print(f"       task_id={task_id}")

    print("[4/5] 轮询任务直至 MD 完成 …")
    deadline = time.time() + MAX_WAIT
    last_stage = ""
    while time.time() < deadline:
        _, st = _req("GET", f"/api/process/status/{task_id}", token=token)
        status = st.get("status") or ""
        stage = st.get("stage") or ""
        if stage != last_stage:
            print(f"       状态: {status} | {stage}")
            last_stage = stage
        if status == "completed" and st.get("doc_filename"):
            print(f"       MD: {st.get('doc_filename')}")
            break
        if status == "failed":
            print(f"沉淀失败: {st.get('error')}")
            return 1
        time.sleep(POLL_SEC)
    else:
        print("超时未完成沉淀")
        return 1

    print("[5/5] 校验 RSS 条目已挂载 doc …")
    _, items2 = _req("GET", "/api/rss/items", token=token)
    row = next((x for x in (items2.get("items") or []) if x.get("id") == item_id), {})
    if not row.get("doc_filename"):
        print("RSS 条目未回写 doc_filename")
        return 1
    print(f"       doc_filename={row.get('doc_filename')}")
    print("\nRSS 沉淀回归通过 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
