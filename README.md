# Web Rebuild V2 — 多模态文档化助手平台

FastAPI 后端 + 原生 HTML/Vue SPA 前端，覆盖链接文档化、任务编排、AI 问答、文档处理、Redis 缓存、Agent 配置、OPS 运维七大功能模块。

## 目录

- [快速开始](#快速开始)
- [认证系统](#认证系统)
- [环境变量](#环境变量)
- [项目结构](#项目结构)
- [Milvus 向量库](#milvus-向量库)
- [Docker 部署](#docker-部署)
- [数据库](#数据库)
- [分支策略](#分支策略)

## 快速开始

### 1. 安装依赖

```bash
cd web_rebuild_v2/backend
pip install -r requirements.txt
```

### 2. 启动后端

**Windows：**
```bash
start_backend.bat
```

**Linux/macOS：**
```bash
bash start_backend.sh
```

**手动启动：**
```bash
cd web_rebuild_v2/backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 访问前端

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

本地开发仍可直接编辑上级 `src/agent/config.json`（`start_backend.bat` 已通过 `SBA_AGENT_CONFIG` 指向该文件）。

## Docker 部署

### 环境变量存在哪？

`docker-compose.yml` 里的 `${VOLC_API_KEY}` **不是写死在镜像里**，而是在**服务器运行时**注入，常见三种来源（优先级：compose `environment` > 宿主机 export > `.env` 文件）：

1. **服务器上的 `.env` 文件**（与 `docker-compose.yml` 同目录，已 gitignore，仅运维持有）
2. **宿主机系统环境变量**（如 systemd `Environment=`、K8s Secret 挂载为 env）
3. **CI/CD Secret**（构建时不传入，部署阶段注入）

镜像内只有 `docker/config.template.json`（**无密钥**）；容器启动时 `docker/entrypoint.sh` 把 `VOLC_API_KEY` 等写入卷 `sba_runtime_config` 下的 `config.json`，再设置 `SBA_AGENT_CONFIG`。

### 快速启动

```bash
# 1. Milvus（可选，RAG 需要）
cd web_rebuild_v2
docker compose -f docker-compose.milvus.yml up -d

# 2. 配置密钥（仅服务器，勿提交 git）
cp .env.example .env
# 编辑 .env：VOLC_API_KEY=...

# 3. 构建并启动 Web（构建上下文为仓库根目录）
docker compose up -d --build
```

访问：`http://<服务器IP>:8000/`

### Dockerfile 写什么？（`web_rebuild_v2/Dockerfile`）

| 区块 | 内容 | 是否含密钥 |
|------|------|------------|
| **基础镜像** | `python:3.11-slim` | 否 |
| **ENV** | `PYTHONUNBUFFERED`、`SBA_AGENT_CONFIG` 路径、`CHAT_USE_LANGGRAPH` | 否 |
| **apt** | `curl`（健康检查）、`ffmpeg`（视频链路，可按需删） | 否 |
| **pip** | `backend/requirements.txt` + `volcengine-python-sdk[ark]` | 否 |
| **COPY** | `backend/app`、`frontend`、`src/agent`、**config 模板** | 否（不含真实 `config.json`） |
| **禁止** | `ARG/ENV VOLC_API_KEY`、`COPY config.json`、把 `.env` 打进镜像 | — |
| **ENTRYPOINT** | `docker/entrypoint.sh` — 启动时用 env 生成 runtime config | 运行时注入 |
| **CMD** | `uvicorn app.main:app --app-dir /app/backend` | 否 |
| **HEALTHCHECK** | `GET /api/vector/health` | 否 |
| **USER** | 非 root `appuser` | 否 |

构建命令（**必须在仓库根 `SuperBizAgent-AgentFramework/`** 执行，否则 COPY 不到 `src/agent`）：

```bash
docker build -f web_rebuild_v2/Dockerfile -t superbizagent-web:latest .
```

### compose 环境变量与 config.json 映射

| Compose / `.env` | 写入 runtime config |
|------------------|---------------------|
| `VOLC_API_KEY` | `volcengine_api_key` |
| `VOLC_BASE_URL` | `volcengine_base_url` |
| `LLM_MODEL_QA` | `ai_chat_model`、`gateway_task_type_route.qa` |
| `LLM_MODEL_REASON` | `gateway_task_type_route.summary` |

示例（`.env`）：

```env
VOLC_API_KEY=sk-xxxxxxxx
LLM_MODEL_QA=ep-20260418230009-b9grz
LLM_MODEL_REASON=ep-20260413220727-84n92
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
web_rebuild_v2/
├── backend/
│   ├── app/
│   │   ├── auth/              # RBAC 认证模块
│   │   │   ├── casbin_model.conf
│   │   │   ├── enforcer.py
│   │   │   ├── auth_models.py
│   │   │   ├── user_service.py
│   │   │   ├── verify_code_service.py
│   │   │   ├── dependencies.py
│   │   │   ├── middleware.py
│   │   │   ├── auth_router.py
│   │   │   └── init_admin.py
│   │   ├── services/          # 业务服务
│   │   │   ├── ai_chat.py
│   │   │   ├── video_pipeline.py
│   │   │   ├── cache.py
│   │   │   ├── workflow.py
│   │   │   └── ...
│   │   ├── main.py            # FastAPI 应用入口
│   │   ├── models.py
│   │   └── platforms.py
│   └── requirements.txt
├── frontend/
│   ├── login.html             # 独立登录页
│   ├── index.html             # SPA 主页面
│   └── assets/
│       ├── css/app.css
│       └── js/app.js
├── docker-compose.yml         # Web 应用栈（密钥走 .env）
├── docker-compose.milvus.yml
├── Dockerfile                 # 生产镜像（无密钥）
├── docker/
│   ├── entrypoint.sh          # 启动时用 env 生成 config.json
│   └── config.template.json   # 无密钥模板
├── start_backend.bat
├── start_backend.sh
├── .gitignore
├── .env.example
└── README.md
```

## Milvus 向量库

```bash
cd web_rebuild_v2
docker compose -f docker-compose.milvus.yml up -d
```

默认监听 `19530`。后端通过 `GET /api/vector/health` 轮询连通性。

**若容器反复 `Exited (134)`**（旧 segment/MinIO 数据损坏或版本不一致）：

```powershell
cd web_rebuild_v2
powershell -ExecutionPolicy Bypass -File .\scripts\start_milvus_watch.ps1 -Reset
```

脚本会清空三卷、重建栈、等待健康检查，并持续监测 60 秒日志。重建后需在 RAG 页重新入库/同步切片。

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
