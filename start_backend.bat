@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   多模态文档化助手 - Web Rebuild V2
echo   后端服务启动
echo ============================================
echo.

set "PYTHONPATH=%~dp0backend;%~dp0..\src\agent;%PYTHONPATH%"
rem 固定知识库登记目录，避免误读 web_rebuild_v2\src\agent 空桩
set "SBA_KB_DIR=%~dp0..\src\agent\knowledge_base"
rem 问答/编排 LLM 配置：固定指向原项目 src/agent/config.json（勿删此文件）
set "SBA_AGENT_CONFIG=%~dp0..\src\agent\config.json"
rem 必须开启 LangGraph 编排（Query改写/粒度对齐/ReAct handoff）；勿在系统环境里设 CHAT_USE_LANGGRAPH=0
set "CHAT_USE_LANGGRAPH=1"

echo [1/2] 选择 Python 环境...
set "PYTHON=py -3"
if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" -c "import uvicorn; import app.main" 2>nul
    if not errorlevel 1 (
        set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
        echo        使用项目虚拟环境
    ) else (
        echo        虚拟环境依赖不全，改用系统 Python ^(py -3^)
    )
) else (
    echo        使用系统 Python ^(py -3^)
)

echo [1.5/2] 释放 8000 端口（结束占用中的旧 uvicorn）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [2/2] 启动 FastAPI 后端...
echo    LLM 配置:  %SBA_AGENT_CONFIG%
if not exist "%SBA_AGENT_CONFIG%" (
    echo [警告] 未找到 config.json，问答页将提示「未配置 LLM」
)
echo.
echo    API 文档:  http://localhost:8000/docs
echo    前端页面:  http://localhost:8000/
echo.
echo    Ctrl+C 停止服务
echo ============================================
echo.

cd /d "%~dp0backend"
%PYTHON% -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

pause
