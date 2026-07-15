#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_SYSTEM_PACKAGES="${SKIP_SYSTEM_PACKAGES:-0}"
SKIP_RAG_MODEL="${SKIP_RAG_MODEL:-0}"
NO_START="${NO_START:-0}"
NON_INTERACTIVE="${NON_INTERACTIVE:-0}"

have() { command -v "$1" >/dev/null 2>&1; }

install_system_packages() {
  if [[ "$SKIP_SYSTEM_PACKAGES" == "1" ]]; then return; fi
  case "$(uname -s)" in
    Linux)
      have apt-get || { echo "仅自动支持 apt-get；请手动安装 Python、Git、Node.js、FFmpeg、Docker Compose v2。"; exit 1; }
      SUDO=""
      if [[ "$EUID" -ne 0 ]]; then SUDO="sudo"; fi
      $SUDO apt-get update
      $SUDO apt-get install -y git python3 python3-venv python3-pip nodejs npm ffmpeg docker.io docker-compose-plugin
      if have systemctl; then $SUDO systemctl enable --now docker; fi
      ;;
    Darwin)
      have brew || { echo "缺少 Homebrew：https://brew.sh"; exit 1; }
      brew install git python@3.11 node ffmpeg
      have docker || brew install --cask docker
      ;;
    *)
      echo "当前系统请使用 deploy/install.ps1，或手动安装依赖。"
      exit 1
      ;;
  esac
}

read_plain() {
  local prompt="$1" default="${2:-}" value=""
  if [[ "$NON_INTERACTIVE" == "1" ]]; then printf '%s' "$default"; return; fi
  read -r -p "$prompt${default:+ [$default]}: " value
  printf '%s' "${value:-$default}"
}

read_secret() {
  local prompt="$1" default="${2:-}" value=""
  if [[ "$NON_INTERACTIVE" == "1" ]]; then printf '%s' "$default"; return; fi
  read -r -s -p "$prompt: " value
  echo >&2
  printf '%s' "${value:-$default}"
}

random_secret() {
  python3 -c "import secrets; print(secrets.token_hex(${1:-24}))"
}

env_value() {
  local key="$1"
  if [[ -f .env ]]; then
    awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/, ""); print; exit}' .env
  fi
}

install_system_packages
for cmd in git python3 ffmpeg docker; do
  have "$cmd" || { echo "缺少 $cmd，请安装后重试。"; exit 1; }
done
docker compose version >/dev/null
docker info >/dev/null || { echo "Docker daemon 未启动，请启动后重试。"; exit 1; }

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements-deploy.txt
.venv/bin/python -m yt_dlp --version
if have npm && [[ -f package.json ]]; then
  npm install
  npx playwright install chromium
fi

echo "[配置] 私密值只写入本机 .env；config.yaml 保持 ${ENV_VAR} 占位符。"
VOLC_API_KEY="$(read_secret "火山方舟 API Key（可留空）" "${VOLC_API_KEY:-$(env_value VOLC_API_KEY)}")"
LLM_MODEL_QA="$(read_plain "问答模型 endpoint id" "${LLM_MODEL_QA:-$(env_value LLM_MODEL_QA)}")"
LLM_MODEL_REASON="$(read_plain "摘要/推理模型 endpoint id" "${LLM_MODEL_REASON:-$(env_value LLM_MODEL_REASON)}")"
FEISHU_APP_ID="$(read_plain "飞书 App ID（可留空）" "${FEISHU_APP_ID:-$(env_value FEISHU_APP_ID)}")"
FEISHU_APP_SECRET="$(read_secret "飞书 App Secret（可留空）" "${FEISHU_APP_SECRET:-$(env_value FEISHU_APP_SECRET)}")"
SBA_CHROME_EXPECTED_GAIA="$(read_plain "Chrome 预期用户名称（可留空）" "${SBA_CHROME_EXPECTED_GAIA:-$(env_value SBA_CHROME_EXPECTED_GAIA)}")"
SBA_CHROME_EXPECTED_EMAIL="$(read_plain "Chrome 预期邮箱（可留空）" "${SBA_CHROME_EXPECTED_EMAIL:-$(env_value SBA_CHROME_EXPECTED_EMAIL)}")"
SBA_XHS_OWNER_NICKNAME="$(read_plain "小红书本人昵称（可留空）" "${SBA_XHS_OWNER_NICKNAME:-$(env_value SBA_XHS_OWNER_NICKNAME)}")"
XHS_FAVORITES_RED_ID="$(read_plain "小红书号（可留空）" "${XHS_FAVORITES_RED_ID:-$(env_value XHS_FAVORITES_RED_ID)}")"
XHS_FAVORITES_CREATOR_ID="$(read_plain "小红书 creator id（可留空）" "${XHS_FAVORITES_CREATOR_ID:-$(env_value XHS_FAVORITES_CREATOR_ID)}")"

MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(env_value MYSQL_ROOT_PASSWORD)}"; MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(random_secret)}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(env_value MYSQL_PASSWORD)}"; MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(random_secret)}"
REDIS_PASSWORD="${REDIS_PASSWORD:-$(env_value REDIS_PASSWORD)}"; REDIS_PASSWORD="${REDIS_PASSWORD:-$(random_secret)}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-$(env_value MINIO_ROOT_PASSWORD)}"; MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-$(random_secret)}"
SBA_JWT_SECRET="${SBA_JWT_SECRET:-$(env_value SBA_JWT_SECRET)}"; SBA_JWT_SECRET="${SBA_JWT_SECRET:-$(random_secret 32)}"

umask 077
cat > .env <<EOF
VOLC_API_KEY=$VOLC_API_KEY
VOLC_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
LLM_MODEL_QA=$LLM_MODEL_QA
LLM_MODEL_REASON=$LLM_MODEL_REASON
FEISHU_APP_ID=$FEISHU_APP_ID
FEISHU_APP_SECRET=$FEISHU_APP_SECRET
MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD
MYSQL_DATABASE=superbizagent
MYSQL_USER=mediascribe
MYSQL_PASSWORD=$MYSQL_PASSWORD
REDIS_PASSWORD=$REDIS_PASSWORD
MINIO_ROOT_USER=mediascribe
MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD
SBA_JWT_SECRET=$SBA_JWT_SECRET
KB_BACKEND=milvus
MILVUS_HOST=milvus
MILVUS_PORT=19530
WEB_PORT=8000
SBA_CHROME_EXPECTED_GAIA=$SBA_CHROME_EXPECTED_GAIA
SBA_CHROME_EXPECTED_EMAIL=$SBA_CHROME_EXPECTED_EMAIL
SBA_XHS_OWNER_NICKNAME=$SBA_XHS_OWNER_NICKNAME
XHS_FAVORITES_RED_ID=$XHS_FAVORITES_RED_ID
XHS_FAVORITES_CREATOR_ID=$XHS_FAVORITES_CREATOR_ID
EOF

docker compose --env-file .env -f deploy/docker-compose.yml config --quiet

if [[ "$SKIP_RAG_MODEL" != "1" ]]; then
  export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
  .venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5', cache_folder='src/agent/knowledge_base/models')"
fi

if [[ "$NO_START" != "1" ]]; then
  docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
  docker compose --env-file .env -f deploy/docker-compose.yml ps
  echo "完成：http://127.0.0.1:8000/"
fi
