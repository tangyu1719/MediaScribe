"""SSE 流式简单测试"""
import os,sys,json,time
os.environ["CHAT_GRAPH_AUTO_HITL"]="1"
os.environ["CHAT_USE_LANGGRAPH"]="1"
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)
r=client.post("/api/auth/login",json={"identifier":"admin","credential":"admin","login_type":"password"})
token=r.json()["access_token"]
headers={"Authorization":f"Bearer {token}"}

print("=== SSE 流式测试 ===")
t0=time.time()
body={"session_id":"sse_simple","message":"你好，简单介绍一下你自己","web_search":False,"rag_prefetch":False,"deep_think":False}

chunk_times=[]
with client.stream("POST","/api/chat/stream",json=body,headers=headers,timeout=60.0) as resp:
    print(f"HTTP {resp.status_code} TTFB={time.time()-t0:.1f}s",flush=True)
    raw=""
    for chunk in resp.iter_text():
        raw+=chunk
        chunk_times.append(time.time()-t0)
        while "\n\n" in raw:
            idx=raw.index("\n\n"); block=raw[:idx]; raw=raw[idx+2:]
            ev=""; data=""
            for line in block.split("\n"):
                if line.startswith("event:"): ev=line[6:].strip()
                elif line.startswith("data:"): data=line[5:].strip()
            if not data: continue
            try: d=json.loads(data)
            except: continue
            elapsed=time.time()-t0
            if ev=="stream_open":
                print(f"  [{elapsed:.1f}s] OPEN {d.get('stage','')}",flush=True)
            elif ev=="pipeline_progress":
                print(f"  [{elapsed:.1f}s] PROG {d.get('stage',d.get('detail',''))[:60]}",flush=True)
            elif ev=="thought_step_end":
                phase=d.get("phase",""); cost=d.get("elapsed_ms","?")
                print(f"  [{elapsed:.1f}s] DONE phase={phase} cost={cost}ms",flush=True)
            elif ev=="answer_start":
                print(f"  [{elapsed:.1f}s] ANSWER_START",flush=True)
            elif ev=="answer_delta":
                pass
            elif ev=="answer_end":
                print(f"  [{elapsed:.1f}s] ANSWER_END",flush=True)
            elif ev=="task_completed":
                print(f"  [{elapsed:.1f}s] DONE",flush=True)
        if len(chunk_times)>100: break

total=time.time()-t0
print(f"\n总耗时: {total:.1f}s chunks: {len(chunk_times)}")
if len(chunk_times)>1:
    gaps=[chunk_times[i]-chunk_times[i-1] for i in range(1,len(chunk_times))]
    print(f"最大间隔: {max(gaps):.1f}s 平均: {sum(gaps)/len(gaps):.1f}s")
    print("PASS: 流式正常" if max(gaps)<3 else "WARN: 有缓冲")
else:
    print("WARN: 仅1个chunk")
