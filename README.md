# MediaScribe Web — 多模态文档化与 RAG 问答平台

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.100+-green.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/RAG-Milvus%2BBGE-orange.svg" alt="RAG">
</p>

FastAPI 后端 + 原生 HTML/Vue SPA 前端，覆盖链接文档化、任务编排、AI 问答（LangGraph）、知识库 RAG、订阅同步与 OPS 运维。

> **完整部署、模型下载与密钥配置**请参阅 **[DEPLOYMENT.md](./DEPLOYMENT.md)**（含 BGE 向量模型、Milvus、Docker、密钥脱敏说明）。

## 目录

- [快速开始](#快速开始)
- [大型流程图转 Mermaid](#大型流程图转-mermaid)
- [认证系统](#认证系统)
- [环境变量](#环境变量)
- [项目结构](#项目结构)
- [Milvus 向量库](#milvus-向量库)
- [Docker 部署](#docker-部署)
- [数据库](#数据库)
- [安全与密钥](#安全与密钥)
- [分支策略](#分支策略)

## 快速开始

```bash
git clone https://github.com/tangyu1719/MediaScribe.git
cd MediaScribe
```

### 一键安全部署（推荐）

这是给新机器准备的完整部署入口。脚本会检查并按需安装 Python 3.11、Git、Node.js、Docker、FFmpeg、Tesseract 中文 OCR，安装 yt-dlp、EasyOCR、Playwright 和 RAG SDK，交互询问私密配置，并启动 Web、MySQL、Redis、Milvus、etcd 与 MinIO。

#### Windows 10/11

```powershell
git clone https://github.com/tangyu1719/MediaScribe.git
cd MediaScribe
powershell -ExecutionPolicy Bypass -File .\deploy\install.ps1
```

如果脚本刚安装 Docker Desktop，请先启动 Docker Desktop，等待状态变为 Running，再重新执行同一条安装命令。

#### Ubuntu / Debian / macOS

```bash
git clone https://github.com/tangyu1719/MediaScribe.git
cd MediaScribe
bash deploy/install.sh
```

安装时会询问以下可选私密数据：火山方舟 API Key 与模型 endpoint、飞书 App ID/Secret，以及浏览器/小红书账号标识。MySQL、Redis、MinIO 和 JWT 密码由脚本自动随机生成。真实值只写入已被 Git 忽略的 `.env`；仓库中的 `config.yaml` 始终只保存 `${ENV_VAR}` 引用。

部署完成后访问：

- Web：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`
- Milvus 健康端口：仅本机 `127.0.0.1:9091`
- MySQL、Redis、Milvus 默认只绑定本机地址，不直接暴露到公网

常用运维命令：

```bash
# 查看服务与日志
docker compose --env-file .env -f deploy/docker-compose.yml ps
docker compose --env-file .env -f deploy/docker-compose.yml logs -f web

# 更新到远程最新版
git pull --ff-only
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build

# 停止服务（保留数据库和模型卷）
docker compose --env-file .env -f deploy/docker-compose.yml down
```

只生成配置、不启动服务：

```powershell
.\deploy\install.ps1 -NoStart -SkipRagModel
```

```bash
NO_START=1 SKIP_RAG_MODEL=1 bash deploy/install.sh
```

部署前后均可执行安全检查：

```bash
python deploy/security_scan.py
```

更多端口调整、离线模型、故障排查与手工部署说明见 [DEPLOYMENT.md](./DEPLOYMENT.md)。

## 大型流程图转 Mermaid

部署完成后打开 Web 的“多模态处理”，将处理模式切换为“流程图转 Mermaid”，上传 PDF 或图片并点击“转换为 Mermaid”。页面会显示节点数、连线数、OCR/拓扑叠图，并可直接复制或下载 `.mmd`。

转换链路专门处理大型流程图、彩色圆角框和思维导图：

1. PDF 按指定倍率渲染；页码填 `0` 时转换全部页（默认最多 20 页）。
2. `auto` 模式融合 EasyOCR 与 Tesseract 的坐标结果；若配置 Umi-OCR，则优先加入统一 OCR 融合。
3. 重叠框传递去重，序号碎片归并；彩色连接线导致的跨框文字会按视觉区域重新拆分。
4. 对每个节点放大复核文字，再按 LR/TD 空间层级恢复父子连线。
5. 已配置视觉模型时可启用“VLM 二次复核”，校正明确可见的文字、节点形状和连接关系。

可选 OCR 环境变量：

```env
# auto | umi-ocr | easyocr | tesseract | paddleocr
FLOWCHART_OCR_ENGINE=auto
# Umi-OCR HTTP API 地址；留空时自动使用 EasyOCR + Tesseract
FLOWCHART_UMI_OCR_URL=
# page=0 时允许处理的最大页数
FLOWCHART_MAX_PAGES=20
```

接口调用示例（`path` 必须是服务端可读取的 PDF/图片路径）：

```bash
curl -X POST http://127.0.0.1:8000/api/doc/flowchart/score \
  -H "Content-Type: application/json" \
  -d '{"path":"output/mm_uploads/example.pdf","page":0,"zoom":3,"ocr_engine":"auto","direction":"auto","vlm_refine":true}'
```

每次转换会在 `output/flowchart_scoring_web/<任务ID>/` 生成：

- `flowchart.mmd`：最终 Mermaid；
- `flowchart_conversion_report.json`：节点、边、方向与 OCR 诊断；
- `page_<页码>/flowchart_graph.json`：单页结构化图；
- `page_<页码>/ocr_topology_overlay.png`：节点和连线人工复核叠图。

`PaddleOCR` 仅作为显式可选引擎；若本机 Paddle 运行时不兼容，请保持 `auto`，系统不会让 Paddle 故障阻断 EasyOCR/Tesseract 主链路。低分辨率扫描件建议将渲染倍率调到 `3–4`，并检查叠图后再使用 Mermaid。

### 1. 安装依赖

```bash
pip install -r backend/requirements.txt
pip install sentence-transformers torch   # RAG 向量模型所需
```

### 2. 配置 LLM（勿提交真实 Key）

```bash
copy src\agent\config.json.example src\agent\config.json   # Windows
# cp src/agent/config.json.example src/agent/config.json   # Linux/macOS
```

编辑 `src/agent/config.json` 填入火山方舟 API Key 与 endpoint id，或使用 `.env`（见 [DEPLOYMENT.md](./DEPLOYMENT.md)）。

### 3. 启动 Milvus（RAG 需要）

```bash
docker compose -f docker-compose.milvus.yml up -d
```

### 4. 下载 BGE 模型（首次 RAG）

```bash
set HF_ENDPOINT=https://hf-mirror.com
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5', cache_folder='src/agent/knowledge_base/models')"
```

详见 [DEPLOYMENT.md §6](./DEPLOYMENT.md#6-bge-向量模型下载)。

### 5. 启动后端

**Windows：** `start_backend.bat`  
**Linux/macOS：** `bash start_backend.sh`

**手动启动：**

```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. 访问前端

浏览器打开 `http://127.0.0.1:8000/`

**首次启动**会自动创建管理员账号，密码打印在控制台：

```
============================================================
  SuperBizAgent 管理员账号已创建
  ─────────────────────────────
  用户名:  admin
  密码:    xxxxxxxxxxxxxxxx  ← 随机生成，请妥善保管
  ─────────────────────────────
============================================================
```

## 认证系统

### 登录方式

| 方式 | 说明 |
|------|------|
| 账号密码登录 | 用户名 / 手机号 / 邮箱 + 密码（默认激活） |
| 手机验证码登录 | SMS 暂未开通，验证码打印至服务端日志 |
| 邮箱注册 | 邮箱 + 验证码 + 密码（需配置 SMTP） |

### 角色权限

| 角色 | 权限 |
|------|------|
| `admin` | 所有页面、所有操作（*） |
| `viewer` | 所有页面只读（GET） |

权限基于 **pycasbin** RBAC 引擎，策略存储在 MySQL/SQLite 的 `casbin_rule` 表中，支持热更新。

### Token

- JWT 密钥持久化在 `output/.sba_jwt_secret`，重启不会导致已登录 token 失效
- 设置环境变量 `SBA_JWT_SECRET` 可覆盖密钥
- Token 有效期 24 小时

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SBA_DATABASE_URL` | SQLite (`output/sba_auth.sqlite3`) | 数据库连接串，推荐 MySQL：`mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4` |
| `SBA_JWT_SECRET` | 自动生成 + 持久化 | JWT 签名密钥 |
| `MILVUS_HOST` | `127.0.0.1` | 向量库地址 |
| `MILVUS_PORT` | `19530` | 向量库端口 |
| `SBA_SMTP_HOST` | — | SMTP 服务器（邮箱验证码） |
| `SBA_SMTP_PORT` | `587` | SMTP 端口 |
| `SBA_SMTP_USER` | — | SMTP 用户名 |
| `SBA_SMTP_PASS` | — | SMTP 密码 |
| `SBA_SMTP_FROM` | 同 USER | 发件人地址 |
| `SBA_SMTP_TLS` | `1` | 启用 STARTTLS |
| `FS_ALLOW_ROOTS` | — | 服务端路径浏览白名单（逗号分隔） |
| `VOLC_API_KEY` | — | **Docker/生产**：火山方舟 API Key（不写进镜像，见 [Docker 部署](#docker-部署)） |
| `VOLC_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | 方舟 OpenAI 兼容 Base URL |
| `LLM_MODEL_QA` | — | 问答路由 endpoint id（写入 runtime `config.json` 的 `ai_chat_model` / `gateway_task_type_route.qa`） |
| `LLM_MODEL_REASON` | — | 摘要/推理 endpoint id（写入 `gateway_task_type_route.summary`） |
| `SBA_AGENT_CONFIG` | 自动 | LLM 配置 JSON 路径；容器内由 entrypoint 生成 `/app/runtime/agent/config.json` |
| `FLOWCHART_OCR_ENGINE` | `auto` | 流程图 OCR：自动融合或显式选择 Umi/EasyOCR/Tesseract/PaddleOCR |
| `FLOWCHART_UMI_OCR_URL` | — | 可选 Umi-OCR HTTP API 地址 |
| `FLOWCHART_MAX_PAGES` | `20` | PDF 页码为 `0` 时的最大转换页数 |

本地开发仍可直接编辑 `src/agent/config.json`（`start_backend.bat` 会优先使用本仓库 `src/agent/config.json`）。

## Docker 部署

完整步骤见 [DEPLOYMENT.md §10](./DEPLOYMENT.md#10-docker-部署)。

### 环境变量存在哪？

`docker-compose.yml` 里的 `${VOLC_API_KEY}` **不是写死在镜像里**，而是在**服务器运行时**注入：

1. **服务器上的 `.env` 文件**（与 `docker-compose.yml` 同目录，已 gitignore）
2. **宿主机系统环境变量**
3. **CI/CD Secret**

镜像内只有 `docker/config.template.json`（**无密钥**）；`docker/entrypoint.sh` 把环境变量写入运行时 `config.json`。

### 快速启动

```bash
docker compose -f docker-compose.milvus.yml up -d
cp .env.example .env
# 编辑 .env：VOLC_API_KEY=你的密钥（勿提交 git）
docker compose up -d --build
```

访问：`http://<服务器IP>:8000/`

### 构建镜像

在**仓库根目录**执行：

```bash
docker build -t mediscribe-web:latest .
```

### compose 环境变量与 config.json 映射

| Compose / `.env` | 写入 runtime config |
|------------------|---------------------|
| `VOLC_API_KEY` | `volcengine_api_key` |
| `VOLC_BASE_URL` | `volcengine_base_url` |
| `LLM_MODEL_QA` | `ai_chat_model`、`gateway_task_type_route.qa` |
| `LLM_MODEL_REASON` | `gateway_task_type_route.summary` |

示例（`.env`，**请替换为自己的值**）：

```env
VOLC_API_KEY=你的方舟-API-Key
LLM_MODEL_QA=ep-你的问答接入点ID
LLM_MODEL_REASON=ep-你的摘要接入点ID
MILVUS_HOST=host.docker.internal
SBA_DATABASE_URL=mysql+pymysql://user:pass@db:3306/superbizagent?charset=utf8mb4
```

### 可选：完整 Agent 配置

若需完整 prompt / 网关节点池，可将**脱敏后的** `config.json` 挂载到卷（仍建议密钥字段留空，由 `VOLC_API_KEY` 覆盖）：

```yaml
volumes:
  - ./runtime/agent:/app/runtime/agent
```

## 项目结构

```
MediaScribe/                   # 仓库根（clone 后即此目录）
├── backend/
│   ├── app/                   # FastAPI 应用
│   └── requirements.txt
├── frontend/                  # SPA 静态资源
├── src/agent/                 # Agent 核心 + 运行时（config.json 不入库）
│   ├── config.json.example    # LLM 配置模板（无密钥）
│   └── knowledge_base/        # 知识库与 BGE 模型缓存
├── docker/
│   ├── entrypoint.sh
│   └── config.template.json
├── docker-compose.yml
├── docker-compose.milvus.yml
├── Dockerfile
├── start_backend.bat
├── start_backend.sh
├── .env.example
├── DEPLOYMENT.md              # 部署与模型下载详细说明
└── README.md
```

## Milvus 向量库

```bash
docker compose -f docker-compose.milvus.yml up -d
```

默认监听 `19530`。后端通过 `GET /api/vector/health` 轮询连通性。

**若容器反复 `Exited (134)`**（旧 segment/MinIO 数据损坏或版本不一致）：

```powershell
cd web_rebuild_v2
powershell -ExecutionPolicy Bypass -File .\scripts\start_milvus_watch.ps1 -Reset
```

脚本会清空三卷、重建栈、等待健康检查，并持续监测 60 秒日志。重建后需在 RAG 页重新入库/同步切片。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_milvus_watch.ps1 -Reset
```

## 安全与密钥

| 文件 | 是否入库 | 说明 |
|------|----------|------|
| `.env` | 否（gitignore） | Docker/生产环境注入 `VOLC_API_KEY` 等 |
| `src/agent/config.json` | 否（gitignore） | 本地开发 LLM 配置 |
| `src/agent/config.json.example` | 是 | 仅含空字段模板 |
| `docker/config.template.json` | 是 | 无密钥的运行时模板 |
| `ai_chat_config.json` | 否 | 私有机密配置 |

**禁止**将真实 API Key、飞书 Secret、Cookie、JWT 密钥提交到 Git。若误提交，请立即在控制台轮换密钥并清理 Git 历史。

## 数据库

### SQLite（默认）

无需配置，首次启动自动在 `output/` 下创建 `sba_auth.sqlite3` 和 `sba_casbin.sqlite3`。

### MySQL（生产推荐）

设置环境变量 `SBA_DATABASE_URL`：

```
mysql+pymysql://user:password@host:3306/database?charset=utf8mb4
```

启动后自动建表：`rbac_user`、`rbac_verify_code`、`casbin_rule`。

## 分支策略

| 分支 | 用途 |
|------|------|
| `master` | 主分支，稳定版本 |
| `feature/comment-scraping` | 评论区爬取功能开发 |

### 代码提交规范

- `feat(web):` — 新功能
- `fix(web):` — 修复
- `chore(web):` — 杂项
- `docs(web):` — 文档更新
