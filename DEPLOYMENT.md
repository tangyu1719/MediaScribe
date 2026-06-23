# MediaScribe Web 部署与使用指南

本文档说明如何在本机或服务器部署 **MediaScribe Web 版**（FastAPI + 前端 SPA），包括依赖安装、密钥配置、向量模型下载、Milvus/Redis 与 Docker 部署。**所有密钥仅放在本机 `.env` 或 `src/agent/config.json`，禁止提交 Git。**

---

## 目录

1. [仓库说明](#1-仓库说明)
2. [系统要求](#2-系统要求)
3. [克隆与 Python 依赖](#3-克隆与-python-依赖)
4. [Agent 核心模块（必读）](#4-agent-核心模块必读)
5. [API 密钥配置](#5-api-密钥配置)
6. [BGE 向量模型下载](#6-bge-向量模型下载)
7. [Milvus 向量库](#7-milvus-向量库)
8. [Redis（可选）](#8-redis可选)
9. [启动服务](#9-启动服务)
10. [Docker 部署](#10-docker-部署)
11. [常见问题](#11-常见问题)

---

## 1. 仓库说明

| 项目 | 说明 |
|------|------|
| **仓库** | [https://github.com/tangyu1719/MediaScribe](https://github.com/tangyu1719/MediaScribe) |
| **形态** | Web 版：`backend/`（FastAPI）、`frontend/`（静态 SPA）、`src/agent/`（Agent 运行时与核心模块） |
| **与桌面版关系** | 桌面 GUI 在完整 MediaScribe 主工程中；Web 版复用同一套 `src/agent` Python 模块（RAG、链接解析、网关等） |

克隆后目录即为仓库根，**不要**再套一层 `web_rebuild_v2/`：

```bash
git clone https://github.com/tangyu1719/MediaScribe.git
cd MediaScribe
```

---

## 2. 系统要求

| 项目 | 最低 | 推荐 |
|------|------|------|
| 操作系统 | Windows 10 / Ubuntu 22.04 / macOS 12 | Windows 11 / Ubuntu 22.04 |
| Python | 3.10 | 3.11 |
| 内存 | 8 GB | 16 GB+ |
| 磁盘 | 15 GB 可用 | 50 GB+（含 BGE 模型约 1.2 GB） |
| Docker | 可选（Milvus 推荐） | Docker Desktop 最新版 |
| 网络 | 可访问火山方舟 API | 首次下载 BGE 需 HuggingFace 或镜像 |

---

## 3. 克隆与 Python 依赖

```bash
git clone https://github.com/tangyu1719/MediaScribe.git
cd MediaScribe

# 建议使用虚拟环境
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:    source .venv/bin/activate

pip install -r backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install "volcengine-python-sdk[ark]>=1.0.0"
```

### RAG / 知识库额外依赖

入库与检索需要 **sentence-transformers** 与 **PyTorch（CPU 即可）**：

```bash
pip install sentence-transformers torch -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 视频转写（可选）

链接文档化「语音转文字」依赖 Whisper，体积较大，按需安装：

```bash
pip install openai-whisper
# 并确保系统已安装 ffmpeg（Docker 镜像已内置）
```

### MCP 工具链

AI 问答若启用 MCP，需已安装 `langchain-mcp-adapters`（已写入 `backend/requirements.txt`）。启动后可在「工具」页同步 MCP 服务清单。

---

## 4. Agent 核心模块（必读）

Web 后端通过 `PYTHONPATH` 加载 `src/agent` 下的模块，例如：

- `provider_adapters.py`、`ai_gateway.py` — LLM 网关
- `kb_manager_fast.py`、`rag_tools.py`、`kb_milvus_backend.py` — RAG
- `video_downloader.py`、`link_analyzer.py` — 链接文档化

**独立部署时**请确保 `src/agent/` 下存在上述 `.py` 文件（不仅是 `history.json` 等运行时目录）。

1. 若与完整 MediaScribe 主工程同机开发：`start_backend.bat` 会自动回退到上级 `../src/agent`。
2. 若仅克隆本仓库：请将完整 Agent 核心目录同步到本仓库 `src/agent/`（或设置环境变量指向已有目录）：

```bash
# 示例：从本机已有安装复制（路径按实际修改）
# xcopy /E /I D:\MediaScribe-full\src\agent\*.py  src\agent\
```

首次配置 LLM：

```bash
copy src\agent\config.json.example src\agent\config.json
# 编辑 config.json，填入方舟 API Key 与 endpoint（见下文）
```

> `src/agent/config.json` 已在 `.gitignore` 中，**切勿提交真实密钥**。

---

## 5. API 密钥配置

### 方式 A：本地 `config.json`（开发推荐）

```bash
copy src/agent/config.json.example src/agent/config.json
```

编辑 `src/agent/config.json`（**占位符示例，请替换为自己的值**）：

```json
{
  "volcengine_api_key": "你的方舟-API-Key",
  "volcengine_base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "ai_chat_model": "ep-你的问答接入点ID",
  "gateway_task_type_route": {
    "summary": "ep-你的摘要接入点ID",
    "qa": "ep-你的问答接入点ID"
  }
}
```

**申请步骤：**

1. 登录 [火山引擎方舟控制台](https://console.volcengine.com/ark)
2. **API Key 管理** → 创建 Key
3. **在线推理** → 创建接入点，记录 `ep-xxxxxxxx` 形式的 endpoint id

### 方式 B：环境变量 `.env`（Docker / 生产推荐）

```bash
copy .env.example .env
```

编辑 `.env`（仅保存在服务器，勿提交 Git）：

```env
VOLC_API_KEY=你的方舟-API-Key
VOLC_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
LLM_MODEL_QA=ep-你的问答接入点ID
LLM_MODEL_REASON=ep-你的摘要接入点ID
```

Docker 启动时 `docker/entrypoint.sh` 会将上述变量写入运行时 `config.json`，**镜像内不含密钥**。

### 飞书同步（可选）

在 `config.json` 中配置 `feishu_app_id`、`feishu_app_secret`，详见 [飞书开放平台](https://open.feishu.cn/)。未配置时不影响 Web 主链路。

---

## 6. BGE 向量模型下载

RAG 默认使用 **`BAAI/bge-large-zh-v1.5`**（1024 维，约 1.2 GB）。代码入口：`src/agent/kb_manager_fast.py`。

### 自动下载（需联网）

```bash
# 国内建议先设镜像
# Windows CMD:
set HF_ENDPOINT=https://hf-mirror.com
# PowerShell:
$env:HF_ENDPOINT="https://hf-mirror.com"

python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5', cache_folder='src/agent/knowledge_base/models')"
```

成功后目录结构类似：

```
src/agent/knowledge_base/models/
  models--BAAI--bge-large-zh-v1.5/
    snapshots/<hash>/
      config.json
      pytorch_model.bin
      ...
```

### 离线 / 拷贝已有快照

若另一台机器已下载，可整目录拷贝到 `src/agent/knowledge_base/models/`，或在 `.env` 中指定快照路径：

```env
SBA_BGE_SNAPSHOT_PATH=F:/path/to/models--BAAI--bge-large-zh-v1.5/snapshots/<hash>
```

须保证该目录下存在 `config.json` 与权重文件。设置后加载时会启用 `TRANSFORMERS_OFFLINE=1`，无需外网。

### 验证 RAG

1. 启动 Milvus（见下节）
2. 启动后端，浏览器打开 `http://127.0.0.1:8000/`
3. 进入知识库页入库文档，或调用 `GET /api/vector/health` 应返回 Milvus 连通

---

## 7. Milvus 向量库

```bash
docker compose -f docker-compose.milvus.yml up -d
```

默认 gRPC 端口 **19530**。`.env` 中可配置：

```env
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
KB_BACKEND=milvus
MILVUS_COLLECTION=kb_chunks_fast
```

健康检查：`GET http://127.0.0.1:8000/api/vector/health`

**容器反复退出 (Exited 134)** 时，可重置数据卷后重建（会清空向量数据，需重新入库）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_milvus_watch.ps1 -Reset
```

---

## 8. Redis（可选）

`start_backend.bat` / `start_backend.sh` 会尝试连接本机 Redis（默认 `127.0.0.1:6379`）。未安装时后端降级为本地缓存，不影响主流程。

Windows 可设置：

```bat
set SBA_REDIS_DIR=D:\redis
```

---

## 9. 启动服务

### Windows

```bat
start_backend.bat
```

### Linux / macOS

```bash
bash start_backend.sh
```

### 手动启动

```bash
export PYTHONPATH="$(pwd)/backend:$(pwd)/src/agent"
export SBA_AGENT_CONFIG="$(pwd)/src/agent/config.json"
export SBA_KB_DIR="$(pwd)/src/agent/knowledge_base"
export CHAT_USE_LANGGRAPH=1

cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器访问：**http://127.0.0.1:8000/**  
API 文档：**http://127.0.0.1:8000/docs**

首次启动会在控制台打印 **admin 随机密码**（仅本地，请妥善保存）。

---

## 10. Docker 部署

### 准备

```bash
# 1. Milvus（宿主机或同 compose 网络）
docker compose -f docker-compose.milvus.yml up -d

# 2. 配置密钥（勿提交 git）
cp .env.example .env
# 编辑 VOLC_API_KEY、LLM_MODEL_QA 等

# 3. 构建并启动（在仓库根目录）
docker compose up -d --build
```

`docker-compose.yml` 通过 `env_file: .env` 与 `environment:` 注入密钥；`docker/entrypoint.sh` 生成 `/app/runtime/agent/config.json`。

构建单镜像：

```bash
docker build -t mediscribe-web:latest .
```

访问：`http://<服务器IP>:8000/`

> **注意**：镜像内 `COPY src/agent` 仅包含仓库已提交的 Agent 文件。若仓库内缺少 `kb_manager_fast.py` 等核心模块，需在构建前补全 `src/agent/`，或挂载宿主机目录到 `/app/src/agent`。

---

## 11. 常见问题

### Milvus 连接失败

- 确认 `docker compose -f docker-compose.milvus.yml ps` 为 Running
- 容器内访问宿主机 Milvus 时使用 `MILVUS_HOST=host.docker.internal`

### 问答提示「未配置 LLM」

- 检查 `src/agent/config.json` 是否存在且 `volcengine_api_key`、`ai_chat_model` 非空
- 或检查 `.env` 中 `VOLC_API_KEY`、`LLM_MODEL_QA` 是否已注入

### BGE 模型加载失败

- 确认已安装 `sentence-transformers` 与 `torch`
- 使用 `SBA_BGE_SNAPSHOT_PATH` 指向完整快照，或设置 `HF_ENDPOINT` 后重新下载

### `langchain-mcp-adapters` 未安装

```bash
pip install langchain-mcp-adapters langchain-core mcp
```

### 密钥误提交 Git

立即在方舟控制台**轮换 API Key**，并使用 `git filter-repo` 或 BFG 从历史中清除；日常仅使用 `.env` 与已 gitignore 的 `config.json`。

---

## 相关链接

- [火山引擎方舟](https://console.volcengine.com/ark)
- [BGE 模型](https://huggingface.co/BAAI/bge-large-zh-v1.5)
- [Milvus 文档](https://milvus.io/docs)
- 项目总览见根目录 [README.md](./README.md)
