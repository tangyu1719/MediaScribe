#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "============================================"
echo "  多模态文档化助手 - Web Rebuild V2"
echo "  后端服务启动"
echo "============================================"
echo ""

export PYTHONPATH="$(pwd)/backend:$(pwd)/../src/agent:${PYTHONPATH}"

# Python 选择
if [ -f "../.venv/Scripts/python.exe" ]; then
    PYTHON="../.venv/Scripts/python.exe"
    echo "[1/2] 使用项目虚拟环境"
else
    PYTHON="python"
    echo "[1/2] 使用系统 Python"
fi

echo "[2/2] 启动 FastAPI 后端..."
echo ""
echo "   API 文档:  http://localhost:8000/docs"
echo "   前端页面:  http://localhost:8000/"
echo ""
echo "   Ctrl+C 停止服务"
echo "============================================"
echo ""

cd backend
"$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info
