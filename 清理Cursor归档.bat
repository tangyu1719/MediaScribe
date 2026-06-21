@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   清理 Cursor 已归档 Agent 对话
echo   （需先完全退出 Cursor）
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backend\cursor_purge_archived.ps1"
pause
