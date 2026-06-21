@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [重启] 先停止旧进程，再启动 uvicorn（后台 + 健康检查）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backend\restart_backend.ps1"
if errorlevel 1 (
  echo.
  echo [失败] 重启未完成，请查看 backend\.run\uvicorn.err.log
  pause
  exit /b 1
)
echo.
echo [完成] 后端已在后台运行，日志: backend\.run\uvicorn.log
echo        浏览器: http://localhost:8000/
pause
