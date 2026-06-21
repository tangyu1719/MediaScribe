#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

REDIS_DIR="${SBA_REDIS_DIR:-D:/redis}"
REDIS_CONF="${REDIS_DIR}/redis.windows.conf"
REDIS_PORT="${SBA_REDIS_PORT:-6379}"
BACKEND_PORT="${SBA_BACKEND_PORT:-8000}"

export PYTHONPATH="${ROOT}/backend:${ROOT}/../src/agent:${PYTHONPATH:-}"
export SBA_KB_DIR="${ROOT}/../src/agent/knowledge_base"
export SBA_AGENT_CONFIG="${ROOT}/../src/agent/config.json"
export CHAT_USE_LANGGRAPH=1

echo "============================================"
echo "  多模态文档化助手 - Web Rebuild V2"
echo "  后端服务启动"
echo "============================================"
echo ""

_is_windows=0
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) _is_windows=1 ;;
esac

_redis_cli() {
  if [[ -x "${REDIS_DIR}/redis-cli.exe" ]]; then
    "${REDIS_DIR}/redis-cli.exe" -p "${REDIS_PORT}" "$@"
  elif command -v redis-cli >/dev/null 2>&1; then
    redis-cli -p "${REDIS_PORT}" "$@"
  else
    return 127
  fi
}

_redis_ping() {
  _redis_cli ping 2>/dev/null | grep -qi PONG
}

echo "[1/4] 检查/启动 Redis (${REDIS_DIR})..."
if _redis_ping; then
  echo "       Redis 已在运行 (127.0.0.1:${REDIS_PORT})"
else
  if [[ ! -f "${REDIS_DIR}/redis-server.exe" && ! -x "${REDIS_DIR}/redis-server" ]]; then
    echo "[警告] 未找到 Redis 可执行文件: ${REDIS_DIR}"
  else
    echo "       正在启动 redis-server..."
    if [[ -f "${REDIS_DIR}/redis-server.exe" ]]; then
      if [[ "${_is_windows}" -eq 1 ]]; then
        # Git Bash / MSYS：后台启动 Windows 版 Redis
        start //B "" "${REDIS_DIR}/redis-server.exe" "${REDIS_CONF}"
      else
        nohup "${REDIS_DIR}/redis-server.exe" "${REDIS_CONF}" >/dev/null 2>&1 &
      fi
    elif [[ -x "${REDIS_DIR}/redis-server" ]]; then
      nohup "${REDIS_DIR}/redis-server" "${REDIS_CONF}" >/dev/null 2>&1 &
    elif command -v redis-server >/dev/null 2>&1; then
      nohup redis-server "${REDIS_CONF}" >/dev/null 2>&1 &
    fi
    for _i in $(seq 1 15); do
      sleep 1
      if _redis_ping; then
        echo "       Redis 已就绪 (127.0.0.1:${REDIS_PORT})"
        break
      fi
      if [[ "${_i}" -eq 15 ]]; then
        echo "[警告] Redis 启动后仍未响应 PONG，后端将降级为本地缓存"
      fi
    done
  fi
fi

echo "[2/4] 清理旧后端（uvicorn 主进程 + --reload 遗留 worker）..."
if [[ "${_is_windows}" -eq 1 ]]; then
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${ROOT}/scripts/backend/stop_backend.ps1" || true
else
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti ":${BACKEND_PORT}" | xargs -r kill -9 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k "${BACKEND_PORT}/tcp" >/dev/null 2>&1 || true
  fi
fi
sleep 1

echo "[3/4] 选择 Python 环境..."
if [[ -f "../.venv/Scripts/python.exe" ]]; then
  PYTHON="../.venv/Scripts/python.exe"
  echo "       使用项目虚拟环境"
elif [[ -f "../.venv/bin/python" ]]; then
  PYTHON="../.venv/bin/python"
  echo "       使用项目虚拟环境"
elif command -v py >/dev/null 2>&1; then
  PYTHON="py"
  PY_ARGS=(-3)
  echo "       使用系统 Python (py -3)"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
  PY_ARGS=()
  echo "       使用系统 Python (python3)"
else
  PYTHON="python"
  PY_ARGS=()
  echo "       使用系统 Python (python)"
fi
PY_ARGS="${PY_ARGS:-()}"

echo "[4/4] 启动 FastAPI 后端..."
echo "       LLM 配置: ${SBA_AGENT_CONFIG}"
if [[ ! -f "${SBA_AGENT_CONFIG}" ]]; then
  echo "[警告] 未找到 config.json，问答页将提示「未配置 LLM」"
fi
echo ""
echo "   API 文档:  http://localhost:${BACKEND_PORT}/docs"
echo "   前端页面:  http://localhost:${BACKEND_PORT}/"
echo ""
echo "   Ctrl+C 停止服务"
echo "============================================"
echo ""

cd backend
if [[ "${PYTHON}" == "py" ]]; then
  exec py -3 -m uvicorn app.main:app --host 0.0.0.0 --port "${BACKEND_PORT}" --reload --log-level info
else
  exec "${PYTHON}" -m uvicorn app.main:app --host 0.0.0.0 --port "${BACKEND_PORT}" --reload --log-level info
fi
