@echo off
chcp 65001 >nul
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\backend\stop_backend.ps1"
timeout /t 1 /nobreak >nul
cd /d "%~dp0"
D:\python软件\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --log-level info
pause
