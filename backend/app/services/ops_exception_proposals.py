"""运维异常分类与 AI 可执行解决码提案。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 每条：code、匹配模式、分析、给 AI 的执行方案（可直接粘贴给 Cursor Agent）
EXCEPTION_CATALOG: List[Dict[str, Any]] = [
    {
        "code": "PIPE_EXECUTOR_SHUTDOWN",
        "category": "pipeline",
        "severity": "high",
        "title": "流水线 executor 已 shutdown",
        "patterns": [r"shutdown", r"cannot schedule new futures", r"interpreter shutdown"],
        "analysis": "短脚本 asyncio.run 结束后线程池被关闭，但仍有流水线任务在投递；或后端进程正在退出时仍有任务入队。",
        "proposal_md": (
            "## 目标\n修复「cannot schedule new futures after shutdown」导致的批量入队失败。\n\n"
            "## 步骤\n"
            "1. 确认批量入队走后端 API 或长驻 uvicorn 进程，禁止用一次性 `asyncio.run` 脚本跑大批量。\n"
            "2. 检查 `pipeline_scheduler.run_pipeline_with_slot` 是否在 shutdown 后仍被调用；"
            "在 `main.py` shutdown 钩子中先 `stop_pipeline_scheduler` 再关 executor。\n"
            "3. 对失败项调用 `subscription_batch_gate.apply_hardcoded_retries` 或 `POST /api/subscriptions/{id}/catalog/finalize`。\n"
            "4. 验证：入队 3 条后等待完成，history 与 seen 均为 completed。\n\n"
            "## 验收\n- 无 shutdown 相关 ERROR；批次终检 ok=true。"
        ),
    },
    {
        "code": "VIDEO_DOWNLOAD_FAIL",
        "category": "pipeline",
        "severity": "high",
        "title": "视频下载失败",
        "patterns": [r"下载.*失败", r"download.*fail", r"yt-dlp", r"VideoDownload.*ERROR"],
        "analysis": "小红书视频链过期、Cookie/Referer 无效、yt-dlp 超时或视频过长导致下载中断。",
        "proposal_md": (
            "## 目标\n恢复失败视频链接的下载与转写。\n\n"
            "## 步骤\n"
            "1. 确认 Chrome CDP 9223 与小红书登录 Cookie 有效（设置 → 链接分析 → Cookie）。\n"
            "2. 检查 `video_downloader.py` 日志中的 yt-dlp 命令与超时；长视频确认 `VideoDownload` 超时是否足够。\n"
            "3. 对单条失败任务：`POST /api/history/rerun` 或批次 gate 的 `full_rerun`。\n"
            "4. 若仅单条反复失败，检查链接 `xsec_token` 是否过期，必要时 `repair-links` 后重跑。\n\n"
            "## 验收\n- 任务 status=completed；doc_path 存在且非空。"
        ),
    },
    {
        "code": "MILVUS_RAG_DOWN",
        "category": "rag",
        "severity": "high",
        "title": "Milvus / RAG 向量库不可用",
        "patterns": [r"Milvus", r"milvus", r"RAG.*失败", r"vector.*connect", r"UNAVAILABLE"],
        "analysis": "Docker 中 Milvus 未启动或反复崩溃，导致 RAG 检索与问答降级。",
        "proposal_md": (
            "## 目标\n恢复 RAG 向量检索。\n\n"
            "## 步骤\n"
            "1. `docker ps` 确认 milvus-standalone 容器运行；未运行则 `docker compose up -d milvus`。\n"
            "2. 检查 `config.json` / 环境变量中 Milvus host/port。\n"
            "3. 重启后端后访问 `/api/health` 与 RAG 页探测。\n"
            "4. 若容器反复 OOM，调高 Docker 内存或降低并发 RAG 预取。\n\n"
            "## 验收\n- RAG 检索返回命中；问答无「Milvus 连接失败」。"
        ),
    },
    {
        "code": "PIPE_INVALID_INPUT_EMPTY",
        "category": "pipeline",
        "severity": "medium",
        "title": "正文/输入为空",
        "patterns": [r"PIPE_INVALID_INPUT_EMPTY", r"正文提取失败", r"内容为空", r"无效占位"],
        "analysis": "纯图笔记、无正文或页面结构变化导致 normalize 阶段无有效文本，不应无限重试。",
        "proposal_md": (
            "## 目标\n将纯图/无正文笔记标记为 skip，避免批次 gate 反复重跑。\n\n"
            "## 步骤\n"
            "1. 确认 `subscription_batch_gate.classify_failure` 对 PIPE_INVALID_INPUT_EMPTY 返回 action=skip。\n"
            "2. 在 seen 表将该 note 标为 skipped 并记录原因。\n"
            "3. 批次审计时计入「已处理」而非失败缺口。\n\n"
            "## 验收\n- 终检 ok=true；该 note 不再出现在 failures 列表。"
        ),
    },
    {
        "code": "BARE_XHS_LINK",
        "category": "subscription",
        "severity": "medium",
        "title": "小红书裸链缺 xsec_token",
        "patterns": [r"xsec_token", r"裸链", r"bare_explore"],
        "analysis": "主页摘录只有 noteId，未通过点击卡片补全 token，入队会被跳过或下载失败。",
        "proposal_md": (
            "## 目标\n补全 seen 表中裸 explore 链接的 xsec_token。\n\n"
            "## 步骤\n"
            "1. 启动 Chrome CDP 9223，确认小红书已登录。\n"
            "2. `POST /api/subscriptions/{id}/catalog/repair-links`。\n"
            "3. 审计 `bare_links=0` 后 `catalog/finalize` 或 enqueue。\n\n"
            "## 验收\n- 所有 canonical_url 含 xsec_token；批次 bare=0。"
        ),
    },
    {
        "code": "XHS_LOGIN_COOKIE",
        "category": "subscription",
        "severity": "high",
        "title": "小红书登录/Cookie 失效",
        "patterns": [r"登录", r"LOGIN", r"GUEST", r"Cookie", r"未登录", r"auth.*fail"],
        "analysis": "Cookie 过期或未绑定，导致拉取主页/收藏夹/下载失败。",
        "proposal_md": (
            "## 目标\n恢复小红书登录态。\n\n"
            "## 步骤\n"
            "1. 订阅 → 小红书绑定：重新扫码/导入 Cookie。\n"
            "2. CDP 打开 xiaohongshu.com 确认非游客态。\n"
            "3. 重跑失败的 sync_run 或定时任务测试执行。\n\n"
            "## 验收\n- `fetch_catalog` 返回笔记；sync status=completed。"
        ),
    },
    {
        "code": "CHAT_BACKEND_TIMEOUT",
        "category": "chat",
        "severity": "medium",
        "title": "AI 问答后端超时",
        "patterns": [r"连接.*失败", r"timeout", r"超时", r"8000.*无响应"],
        "analysis": "uvicorn 未启动、预热阻塞、Milvus/MCP 探测并发过高导致首包超时。",
        "proposal_md": (
            "## 目标\n恢复 AI 问答 SSE 连接。\n\n"
            "## 步骤\n"
            "1. 确认 `start_backend.bat` / uvicorn 8000 在跑，`GET /api/health` 200。\n"
            "2. 暂时关闭前端「RAG 预取」后重试。\n"
            "3. 检查 chat_warmup 日志是否 ready；MCP 未装时忽略 mcp=0。\n\n"
            "## 验收\n- 问答流式返回；无连接超时提示。"
        ),
    },
    {
        "code": "SUB_DB_UNAVAILABLE",
        "category": "subscription",
        "severity": "high",
        "title": "订阅库 MariaDB 不可用",
        "patterns": [r"SBA_DATABASE_URL", r"SUB_DB_UNAVAILABLE", r"pymysql", r"Can't connect to MySQL"],
        "analysis": "MariaDB 未启动或连接串错误，订阅/定时任务无法读写。",
        "proposal_md": (
            "## 目标\n恢复 MariaDB 连接。\n\n"
            "## 步骤\n"
            "1. 确认 MySQL/MariaDB 服务运行，库 superbizagent 存在。\n"
            "2. 核对 `.env` 中 `SBA_DATABASE_URL=mysql+pymysql://...`。\n"
            "3. 重启后端，访问 `/api/subscriptions` 验证。\n\n"
            "## 验收\n- 订阅列表可加载；scheduled_jobs 表可读写。"
        ),
    },
]


def match_exceptions(text: str, limit: int = 5) -> List[Dict[str, Any]]:
    blob = (text or "").strip()
    if not blob:
        return []
    hits: List[Tuple[int, Dict[str, Any]]] = []
    for item in EXCEPTION_CATALOG:
        for pat in item.get("patterns") or []:
            if re.search(pat, blob, re.I):
                hits.append((len(item.get("code", "")), item))
                break
    hits.sort(key=lambda x: -x[0])
    out = []
    for _, item in hits[:limit]:
        out.append(
            {
                "code": item["code"],
                "category": item.get("category"),
                "severity": item.get("severity"),
                "title": item.get("title"),
                "analysis": item.get("analysis"),
                "proposal_md": item.get("proposal_md"),
            }
        )
    return out


def list_exception_catalog() -> Dict[str, Any]:
    return {
        "ok": True,
        "items": [
            {
                "code": x["code"],
                "category": x.get("category"),
                "severity": x.get("severity"),
                "title": x.get("title"),
                "analysis": x.get("analysis"),
            }
            for x in EXCEPTION_CATALOG
        ],
    }


def get_proposal_for_error(error_message: str, context: str = "") -> Dict[str, Any]:
    blob = f"{context}\n{error_message}"
    matches = match_exceptions(blob)
    return {
        "ok": True,
        "error_message": (error_message or "")[:2000],
        "matches": matches,
        "primary": matches[0] if matches else None,
    }
