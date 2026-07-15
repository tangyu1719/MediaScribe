# =============================================================================
# MediaScribe Web — 生产镜像
#
# 构建（在仓库根目录执行）：
#   docker build -t mediscribe-web:latest .
#
# 密钥禁止写入镜像：VOLC_API_KEY 等仅在运行时通过 docker compose / 服务器 .env 注入。
# 详见 DEPLOYMENT.md 与 docker/entrypoint.sh
# =============================================================================

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    SBA_CONFIG_TEMPLATE=/app/docker/config.template.json \
    SBA_AGENT_CONFIG=/app/runtime/agent/config.json \
    SBA_KB_DIR=/app/src/agent/knowledge_base \
    CHAT_USE_LANGGRAPH=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY backend/requirements.txt backend/requirements-deploy.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements-deploy.txt

COPY backend/app /app/backend/app
COPY frontend /app/frontend
COPY src/agent /app/src/agent
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/config.template.json /app/docker/config.template.json
COPY config.yaml /app/config.yaml

RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/runtime/agent /app/output /app/logs

RUN useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app/runtime /app/output /app/logs
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/vector/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
