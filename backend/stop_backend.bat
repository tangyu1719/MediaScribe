@echo off
chcp 65001 >nul
echo 正在停止 SuperBizAgent 后端...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\backend\stop_backend.ps1"
echo 已停止
pause
