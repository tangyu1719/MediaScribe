@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   多模态文档化助手 - Web Rebuild V2
echo   后端服务启动
echo ============================================
echo.

rem 优先本仓库 src/agent（MediaScribe 独立克隆）；否则回退上级 monorepo
if exist "%~dp0src\agent\kb_manager_fast.py" (
    set "AGENT_ROOT=%~dp0src\agent"
) else (
    set "AGENT_ROOT=%~dp0..\src\agent"
)
set "PYTHONPATH=%~dp0backend;%AGENT_ROOT%;%PYTHONPATH%"
set "SBA_KB_DIR=%AGENT_ROOT%\knowledge_base"
if exist "%AGENT_ROOT%\config.json" (
    set "SBA_AGENT_CONFIG=%AGENT_ROOT%\config.json"
) else (
    set "SBA_AGENT_CONFIG=%AGENT_ROOT%\config.json"
)
rem 必须开启 LangGraph 编排（Query改写/粒度对齐/ReAct handoff）；勿在系统环境里设 CHAT_USE_LANGGRAPH=0
set "CHAT_USE_LANGGRAPH=1"

if not defined SBA_REDIS_DIR set "SBA_REDIS_DIR=D:\redis"
set "REDIS_CONF=%SBA_REDIS_DIR%\redis.windows.conf"

echo [1/4] 检查/启动 Redis (%SBA_REDIS_DIR%)...
if exist "%SBA_REDIS_DIR%\redis-cli.exe" (
    "%SBA_REDIS_DIR%\redis-cli.exe" ping 2>nul | findstr /i "PONG" >nul
    if errorlevel 1 (
        if exist "%SBA_REDIS_DIR%\redis-server.exe" (
            echo        正在启动 redis-server...
            start "" /B "%SBA_REDIS_DIR%\redis-server.exe" "%REDIS_CONF%"
            timeout /t 2 /nobreak >nul
            "%SBA_REDIS_DIR%\redis-cli.exe" ping 2>nul | findstr /i "PONG" >nul
            if errorlevel 1 (
                echo [警告] Redis 启动后仍未响应 PONG，后端将降级为本地缓存
            ) else (
                echo        Redis 已就绪 (127.0.0.1:6379)
            )
        ) else (
            echo [警告] 未找到 %SBA_REDIS_DIR%\redis-server.exe
        )
    ) else (
        echo        Redis 已在运行 (127.0.0.1:6379)
    )
) else (
    echo [警告] 未找到 %SBA_REDIS_DIR%\redis-cli.exe，跳过 Redis 自启
)

echo [2/4] 清理旧后端（uvicorn 主进程 + --reload 遗留 worker）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backend\stop_backend.ps1"
if errorlevel 1 (
    echo [警告] stop_backend.ps1 返回非零，继续尝试启动
)
timeout /t 1 /nobreak >nul

echo [3/4] 选择 Python 环境...
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

echo [4/4] 启动 FastAPI 后端...
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
