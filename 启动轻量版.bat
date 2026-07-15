@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 仅启动：首页链接分析 + MD 识别器
echo 不启动：AI 对话、RAG、RSS、订阅、定时任务、自动脚本等
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_lightweight.ps1"
if errorlevel 1 pause
