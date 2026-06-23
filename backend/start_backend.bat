@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo [cleanup] uvicorn + orphan workers ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\backend\stop_backend.ps1"
timeout /t 2 /nobreak >nul
cd /d "%~dp0"
echo Starting SuperBizAgent backend on port 8000...
echo Python: D:\python解释器\python.exe
"D:\python解释器\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info
pause
