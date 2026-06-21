@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [仅停止] 结束 8000 端口后端进程（不会重新启动）
echo         若要重启请双击「重启后端.bat」
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backend\stop_backend.ps1"
pause
