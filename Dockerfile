# =============================================================================
# SuperBizAgent Web Rebuild V2 — 生产镜像
#
# 构建（须在仓库根目录 SuperBizAgent-AgentFramework/ 执行，以便 COPY src/agent）：
#   docker build -f web_rebuild_v2/Dockerfile -t superbizagent-web:latest .
#
# 密钥禁止写入镜像：VOLC_API_KEY 等仅在运行时通过 docker compose / 服务器 .env 注入。
# 详见 web_rebuild_v2/README.md「Docker 部署」与 docker/entrypoint.sh
# =============================================================================

FROM python:3.11-slim

# 非密钥：Python 运行时行为
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    # 应用内路径（entrypoint 会写入 runtime config）
    SBA_CONFIG_TEMPLATE=/app/docker/config.template.json \
    SBA_AGENT_CONFIG=/app/runtime/agent/config.json \
    SBA_KB_DIR=/app/src/agent/knowledge_base \
    CHAT_USE_LANGGRAPH=1

WORKDIR /app

# 系统依赖：curl 健康检查；ffmpeg 视频下载/转写链路（可按需裁剪 slim 版）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Python 依赖（仅 Web 后端；Whisper/torch 等大包见 README 可选扩展）
COPY web_rebuild_v2/backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
    && pip install --no-cache-dir "volcengine-python-sdk[ark]>=1.0.0"

# 应用代码（不含密钥 config.json；模板由 docker/config.template.json 提供）
COPY web_rebuild_v2/backend/app /app/backend/app
COPY web_rebuild_v2/frontend /app/frontend
COPY src/agent /app/src/agent
COPY web_rebuild_v2/docker/entrypoint.sh /entrypoint.sh
COPY web_rebuild_v2/docker/config.template.json /app/docker/config.template.json

RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/runtime/agent /app/output /app/logs

# 非 root 运行（可选，需确保 /app/output 挂载目录权限）
RUN useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app/runtime /app/output /app/logs
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/vector/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
