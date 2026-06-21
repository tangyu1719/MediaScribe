@echo off
cd /d "F:\java\AIOPS\SuperBizAgent-release-2026-01-02\demo_wendanghua\SuperBizAgent-AgentFramework\web_rebuild_v2ackend"
D:\pythonÈí¼þ\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --log-level info
pause
