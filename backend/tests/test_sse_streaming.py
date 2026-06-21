"""SSE 流式测试 - 验证 Middleware 修复后 chunk 逐个到达"""
import os, sys, json, time
os.environ["CHAT_GRAPH_AUTO_HITL"] = "1"
os.environ["CHAT_USE_LANGGRAPH"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
r = client.post("/api/auth/login", json={"identifier":"admin","credential":"admin","login_type":"password"})
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

print("=== SSE 流式测试（验证 Middleware 修复）===")
t0 = time.time()
body = {
    "session_id": "sse_stream_test",
    "message": "搜索知识库中关于WMS系统报错的相关文档并总结",
    "web_search": False,
    "rag_prefetch": True,
    "deep_think": False,
    "orch_pipeline_nodes": {
        "intent_enhance": False,
        "rewrite_confirm": False,
        "rag_filter_confirm": False,
        "rag_decision": True,
    },
}

chunk_times = []
answer = ""
with client.stream("POST", "/api/chat/stream", json=body, headers=headers, timeout=120.0) as resp:
    print(f"HTTP {resp.status_code} TTFB={time.time()-t0:.1f}s", flush=True)
    raw = ""
    for chunk in resp.iter_text():
        raw += chunk
        chunk_times.append(time.time() - t0)
        while "\n\n" in raw:
            idx = raw.index("\n\n")
            block = raw[:idx]
            raw = raw[idx + 2 :]
            ev = ""
            data = ""
            for line in block.split("\n"):
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
            if not data:
                continue
            try:
                d = json.loads(data)
            except Exception:
                continue
            elapsed = time.time() - t0
            if ev in ("stream_open", "pipeline_progress"):
                print(f"  [{elapsed:.1f}s] {ev} {d.get('stage', d.get('detail', ''))[:80]}", flush=True)
            elif ev == "thought_step_end":
                phase = d.get("phase", "")
                cost = d.get("elapsed_ms", "?")
                print(f"  [{elapsed:.1f}s] STEP_DONE phase={phase} cost={cost}ms", flush=True)
            elif ev == "answer_start":
                print(f"  [{elapsed:.1f}s] ANSWER_START", flush=True)
            elif ev == "answer_delta":
                answer += d.get("delta", "")
            elif ev == "answer_end":
                print(f"  [{elapsed:.1f}s] ANSWER_END ({len(answer)}字)", flush=True)
            elif ev == "task_completed":
                print(f"  [{elapsed:.1f}s] DONE", flush=True)
            elif ev == "stream_error":
                print(f"  [{elapsed:.1f}s] ERROR: {json.dumps(d,ensure_ascii=False)[:200]}", flush=True)
        if len(chunk_times) > 200:
            break

total = time.time() - t0
print(f"\n总耗时: {total:.1f}s | chunks: {len(chunk_times)} | answer: {len(answer)}字")

if len(chunk_times) > 1:
    gaps = [chunk_times[i] - chunk_times[i - 1] for i in range(1, len(chunk_times))]
    max_gap = max(gaps)
    status = "PASS: 逐块到达" if max_gap < 3 else f"FAIL: {max_gap:.1f}s 间隔"
    print(f"最大chunk间隔: {max_gap:.1f}s ({status})")
    print(f"平均chunk间隔: {sum(gaps)/len(gaps):.1f}s")
else:
    print("FAIL: 只有 1 个chunk - SSE 完全缓冲")
