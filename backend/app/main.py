"""Web Rebuild V2 — 完整 FastAPI 后端
架构: routes(main.py) → services/ → src/agent(已有代码)
覆盖全部 7 页面: 链接文档化|任务编排|AI问答|文档处理|Redis缓存|Agent配置|OPS运维

所有路由都不包含业务逻辑，只做: 参数校验 → 调用 service → 返回 JSON/SSE
"""
from __future__ import annotations
import asyncio, json, logging, os, time, uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _load_project_dotenv() -> None:
    """加载 web_rebuild_v2/.env（不覆盖已存在的环境变量）。"""
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "frontend").is_dir() and (p / "backend").is_dir():
            env_path = p / ".env"
            if not env_path.is_file():
                return
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
            return


_load_project_dotenv()
# 默认启用 LangGraph 固定编排（仅当 .env / 系统环境未显式设置时）
os.environ.setdefault("CHAT_USE_LANGGRAPH", "1")
from fastapi import FastAPI, HTTPException, Request, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from .models import ProcessRequest
from .platforms import PLATFORM_CONFIGS

# ─── 所有业务逻辑来自 services/ 薄封装层 ───
from .services.task_manager import (
    create_task,
    get_task,
    add_log,
    update_task,
    list_tasks,
    delete_task,
    get_output_dir,
    set_output_dir,
    get_task_by_link,
    import_history_task_to_queue,
)
from .services.milvus_health import check_milvus
from .services.vector_connection import get_connection_state, probe_connection, retry_connection, set_connection_params
from .services.link_hash import coerce_pasted_link, normalize_link_for_hash, url_hash as link_url_hash_fn
from .services.link_doc_routing import platform_from_url
from .services.skill_registry import (
    list_skills as skill_list,
    get_skill as skill_get,
    delete_skill as skill_delete,
    import_skill as skill_import,
    import_from_markdown as skill_import_md,
    import_batch_from_roots as skill_import_batch,
    import_skill_bundle as skill_import_bundle,
    commit_skill_commands as skill_commit_commands,
    patch_skill as skill_patch,
    slash_suggestions as skill_slash_suggestions,
    expand_message_with_skill_meta,
)
from .services.video_pipeline import process_video_pipeline
from .services.config import (
    load_config,
    save_config,
    get_gateway_nodes,
    upsert_gateway_node,
    delete_gateway_node,
    get_agent_routing,
    save_agent_routing,
    get_agent_prompt,
    save_agent_prompt,
    get_agent_md,
    save_agent_md,
    resolve_agent_dir,
    agent_config_path,
    runtime_agent_dir,
)
from .services.kb_rag import (
    kb_stats,
    kb_list_files,
    kb_add_file,
    kb_add_folder,
    kb_search,
    kb_metadata_options,
    kb_file_detail,
    kb_auto_metadata,
    kb_update_file_metadata,
    kb_file_chunks,
    kb_sync_chunk_counts,
    kb_persisted_inventory,
    kb_rebuild_catalog_from_persisted,
    kb_read_persisted_text,
)
from .services.fs_browse import browse as fs_browse
from .services import rag_libraries as rag_lib
from .services.ai_chat import (
    create_session, list_sessions, delete_session, rename_session,
    get_session_messages, save_session_state, export_session_markdown,
    init_chat_persistence,
    chat_stream as _svc_chat_stream,
)
from .services.cache import (
    cache_query, cache_get_entry, cache_update_entry,
    cache_create_entry, cache_export_by_task,
)
from .services.workflow import (
    list_workflow_nodes, list_workflow_definitions,
    save_workflow_definition, delete_workflow_definition,
    run_workflow, resume_workflow, stop_current_workflow,
    start_workflow_scheduler, stop_workflow_scheduler, get_workflow_state,
)
from .services.feishu import (
    feishu_get_config,
    feishu_save_config,
    feishu_list_records,
    feishu_handle_event,
)
from .services.feishu_group_im import event_status
from .services.im_robot import (
    im_robot_list_platforms,
    im_robot_get_wechat,
    im_robot_save_wechat,
    im_robot_wechat_qr_start,
    im_robot_wechat_qr_poll,
    im_robot_wechat_refresh_status,
    im_robot_wechat_disconnect,
    im_robot_wechat_inbound,
)
from .services.ops import (
    ops_get_overview,
    ops_get_events,
    ops_add_event,
    ops_get_suggestions,
    ops_get_status,
    ops_get_memory,
    ops_list_span_tasks,
    ops_get_span_task_detail,
    ops_list_span_exceptions,
    ops_list_reports,
    ops_get_report,
    ops_get_daily_stats,
    ops_analyze_logs,
    ops_monitor_task,
    ops_route_action,
    ops_get_dashboard,
)
from .services.builtin_tools import list_builtin_tools as _list_builtin_tools
from .services import mcp_langchain as mcp_lc
from .services.mcp_vendor_presets import list_mcp_vendor_presets
from .services.tools_detail_llm import generate_tool_detail_html
from .services.history_manager import (
    list_history_tasks,
    delete_history_task,
    clear_completed_history,
    restore_history_from_backups,
    discover_history_backup_paths,
    add_or_update_task_in_history,
    get_history_logs,
    get_task_history,
    build_task_log_bundle,
)

from .auth.middleware import AuthMiddleware
from .auth.auth_router import router as auth_router
from .auth.init_admin import ensure_auth_ready

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"

# 多模态页本地上传：落盘到 output/mm_uploads（与 FS 浏览白名单一致）
_MM_UPLOAD_DIR = ROOT / "output" / "mm_uploads"
_KB_UPLOAD_DIR = ROOT / "output" / "kb_uploads"
_KB_UPLOAD_SUFFIXES = frozenset({".md", ".txt", ".markdown"})
_MM_UPLOAD_MAX_BYTES = 100 * 1024 * 1024
_KB_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
_MM_UPLOAD_SUFFIXES = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".md",
        ".txt",
        ".markdown",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".mp3",
        ".wav",
        ".m4a",
        ".flac",
        ".ogg",
        ".mp4",
        ".webm",
        ".mov",
        ".mkv",
    }
)
_AGENT_DIR = resolve_agent_dir()
_RUNTIME_AGENT_DIR = runtime_agent_dir()
_CONFIG_PATH = agent_config_path()
_HISTORY_PATH = _RUNTIME_AGENT_DIR / "history.json"
_AI_CHAT_CFG_PATH = ROOT / "ai_chat_config.json"

CONFIG = load_config()
_LOG_CHAT = logging.getLogger("sba.chat")
if CONFIG.get("volcengine_api_key") or (CONFIG.get("api_gateway_nodes")):
    _LOG_CHAT.info(
        "[AI问答-配置|main.startup|config.json|硬编执行|加载] 已加载 LLM 配置; path=%s; model=%s; nodes=%s",
        _CONFIG_PATH,
        CONFIG.get("ai_chat_model") or "",
        len(CONFIG.get("api_gateway_nodes") or []),
    )
else:
    _LOG_CHAT.error(
        "[AI问答-配置|main.startup|config.json|硬编执行|加载] config.json 无 volcengine_api_key/节点池; path=%s",
        _CONFIG_PATH,
    )


def _load_history():
    if _HISTORY_PATH.exists():
        try:
            return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tasks": []}


def _save_history(h):
    _HISTORY_PATH.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── 安全响应头中间件（纯 ASGI，不缓冲 StreamingResponse）───
class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers") or [])
                headers[b"x-content-type-options"] = b"nosniff"
                headers[b"x-frame-options"] = b"DENY"
                headers[b"x-xss-protection"] = b"1; mode=block"
                headers[b"referrer-policy"] = b"strict-origin-when-cross-origin"
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, _send)


class OpsObservabilityMiddleware:
    """自动采集 /api/* 请求耗时与状态（纯 ASGI，不缓冲 StreamingResponse）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        t0 = time.perf_counter()
        method = scope.get("method", "")
        query = (scope.get("query_string") or b"").decode("utf-8", errors="replace")
        status_code = 200

        async def _send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            elif message["type"] == "http.response.body":
                cost_ms = int((time.perf_counter() - t0) * 1000)
                try:
                    ops_add_event(
                        method=method, path=path, status_code=status_code,
                        cost_ms=cost_ms, query=query,
                        error_detail="" if status_code < 400 else f"HTTP {status_code}",
                    )
                except Exception:
                    pass
            await send(message)

        await self.app(scope, receive, _send)

# ─── FastAPI App ───
app = FastAPI(title="多模态文档化助手 Web")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(OpsObservabilityMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router)


@app.on_event("startup")
def _startup_auth_and_db():
    try:
        ensure_auth_ready()
    except Exception:
        logging.getLogger("sba.auth").exception("Auth startup failed")
    try:
        from .services.agent_personalization_db import get_engine

        get_engine()
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("agent_personalization DB init: %s", e)
    try:
        from .services.pipeline_executor import blocking_pool_size, llm_pool_size

        logging.getLogger("sba.pipeline_scheduler").info(
            "[链接沉淀-调度|main._startup_auth_and_db|线程池|硬编执行|初始化] "
            "pipeline-run_workers=%s; pipeline-io_workers=%s; pipeline-llm_workers=%s",
            blocking_pool_size(),
            blocking_pool_size(),
            llm_pool_size(),
        )
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("pipeline executor init log: %s", e)
    try:
        from .services.history_manager import init_history_persistence

        info = init_history_persistence()
        logging.getLogger("sba.history_manager").info(
            "[链接沉淀-历史|main._startup_auth_and_db|pipeline_task_history|硬编执行|初始化] "
            "ok=true; enabled=%s; count=%s; imported=%s",
            info.get("enabled"),
            info.get("count"),
            info.get("imported_from_json"),
        )
        from .services.task_manager import init_queue_from_history

        restored = init_queue_from_history()
        logging.getLogger("sba.task_manager").info(
            "[链接沉淀-队列|main._startup_auth_and_db|task_queue|硬编执行|恢复] "
            "restored=%s; memory_count=%s",
            restored,
            len(list_tasks()),
        )
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("pipeline history DB init: %s", e)
    try:
        init_chat_persistence()
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("chat session persistence init: %s", e)


@app.on_event("startup")
async def _startup_chat_runtime_warmup():
    """阻塞完成 LangGraph 编译 + 工具/MCP 加载；uvicorn 就绪前完成，首条问答零冷启动。"""
    try:
        from .services.chat_warmup import await_chat_warmup_on_startup

        await await_chat_warmup_on_startup(timeout_sec=120.0)
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("chat warmup blocking startup: %s", e)
    try:
        from .services.favorites_scheduler import register_main_event_loop

        register_main_event_loop(asyncio.get_running_loop())
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("favorites main loop register: %s", e)
    try:
        from .services.scheduled_job_scheduler import register_main_event_loop as sched_reg_loop

        sched_reg_loop(asyncio.get_running_loop())
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("scheduled job main loop register: %s", e)


@app.on_event("startup")
def _startup_deferred_services():
    """问答运行时预热完成后再启动其它后台任务，避免争抢 LangGraph 导入锁。"""
    try:
        from .services.platform_health import schedule_startup_health_check

        asyncio.create_task(schedule_startup_health_check())
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("platform health startup: %s", e)
    unified_ok = False
    try:
        from .services.scheduled_job_scheduler import start_scheduled_job_scheduler
        from .services.favorites_scheduler import schedule_favorites_on_startup

        sched_status = start_scheduled_job_scheduler()
        unified_ok = bool(sched_status.get("scheduler_running"))
        if unified_ok:
            schedule_favorites_on_startup()
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("unified scheduled job scheduler: %s", e)
    if not unified_ok:
        try:
            from .services.creator_scheduler import start_scheduler

            start_scheduler()
        except Exception as e:
            logging.getLogger("uvicorn.error").warning("creator subscription scheduler: %s", e)
        try:
            from .services.favorites_scheduler import schedule_favorites_on_startup, start_scheduler as start_favorites_scheduler

            start_favorites_scheduler()
            schedule_favorites_on_startup()
        except Exception as e:
            logging.getLogger("uvicorn.error").warning("favorites scheduler startup: %s", e)
        try:
            from .services.rss_scheduler import start_scheduler as start_rss_scheduler

            start_rss_scheduler()
        except Exception as e:
            logging.getLogger("uvicorn.error").warning("rss scheduler startup: %s", e)
    try:
        from .services.whisper_pool import (
            register_whisper_pool_with_downloader,
            schedule_whisper_warmup,
        )

        register_whisper_pool_with_downloader()
        schedule_whisper_warmup()
    except Exception as e:
        logging.getLogger("uvicorn.error").warning("whisper pool startup: %s", e)


@app.on_event("shutdown")
def _shutdown_creator_scheduler():
    try:
        from .services.scheduled_job_scheduler import stop_scheduled_job_scheduler

        stop_scheduled_job_scheduler()
    except Exception:
        pass
    try:
        from .services.creator_scheduler import stop_scheduler

        stop_scheduler()
    except Exception:
        pass
    try:
        from .services.favorites_scheduler import stop_scheduler as stop_favorites_scheduler

        stop_favorites_scheduler()
    except Exception:
        pass
    try:
        from .services.rss_scheduler import stop_scheduler as stop_rss_scheduler

        stop_rss_scheduler()
    except Exception:
        pass


_out_root = get_output_dir()
if _out_root.is_dir():
    app.mount("/output", StaticFiles(directory=str(_out_root)), name="output")

if FRONTEND_DIR.exists():
    # 静态资源目录
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    else:
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR)), name="assets")
    
    # Vendor 目录（本地 CDN 资源）
    _vendor_dir = FRONTEND_DIR / "vendor"
    if _vendor_dir.is_dir():
        app.mount("/vendor", StaticFiles(directory=str(_vendor_dir)), name="vendor")
    
    # Preview 目录
    _preview_dir = FRONTEND_DIR / "preview"
    if _preview_dir.is_dir():
        app.mount("/preview", StaticFiles(directory=str(_preview_dir), html=True), name="preview")


@app.get("/")
def index():
    if (FRONTEND_DIR / "index.html").exists():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
    return {"ok": True, "service": "web-rebuild-v2"}


@app.get("/login.html")
def login_page():
    """独立登录页面"""
    p = FRONTEND_DIR / "login.html"
    if p.exists():
        return FileResponse(str(p))
    raise HTTPException(404, "登录页不存在")


@app.get("/favicon.ico")
def favicon():
    """浏览器 favicon 请求，避免 404。"""
    from fastapi.responses import Response

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="8" fill="#2563eb"/>'
        '<path d="M8 14s1.5 2 4 2 4-2 4-2" stroke="#fff" stroke-width="1.6" fill="none"/>'
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/platform/health")
async def platform_health(refresh: bool = False, chat_model: str = ""):
    """平台健康检查（启动时后台跑，支持 ?refresh=1 强制刷新）。"""
    from .services.platform_health import get_platform_health_snapshot, run_platform_health_check

    cm = (chat_model or "").strip()
    if refresh:
        return await run_platform_health_check(force=True, chat_model=cm)
    snap = get_platform_health_snapshot()
    if not snap.get("ready"):
        return await run_platform_health_check(force=True, chat_model=cm)
    return snap


@app.get("/api/health")
def health():
    """轻量探活：禁止在此导入 LangGraph/LangChain，避免与预热线程争抢模块锁导致探活超时。"""
    return {
        "ok": True,
        "service": "web-rebuild-v2",
        "config_loaded": bool(CONFIG),
    }


@app.get("/api/vector/health")
def route_vector_health(host: str = "", port: str = ""):
    """Milvus 连通性（供 WEB 状态展示）；不抛 503，由 milvus_ok 区分。"""
    return check_milvus(host=host or None, port=port or None)


@app.get("/api/vector/connection")
def route_vector_connection():
    return get_connection_state()


@app.post("/api/vector/connection/params")
def route_vector_connection_params(payload: dict):
    return set_connection_params(payload.get("host"), payload.get("port"), **{k: v for k, v in payload.items() if k not in {"host", "port"}})


@app.post("/api/vector/connection/probe")
def route_vector_connection_probe(payload: dict):
    return probe_connection(payload.get("host"), payload.get("port"))


@app.post("/api/vector/connection/retry")
def route_vector_connection_retry():
    return retry_connection()


@app.get("/api/link/url-hash")
def route_link_url_hash(link: str = ""):
    """规范化链接与稳定哈希（与任务判重、落盘文件名一致）。"""
    extracted = coerce_pasted_link(link)
    if not extracted:
        raise HTTPException(400, "未能从输入中识别有效链接，请粘贴含 http(s) 的分享链接")
    return {
        "link": extracted,
        "normalized": normalize_link_for_hash(extracted),
        "url_hash": link_url_hash_fn(extracted),
        "platform": platform_from_url(extracted) or "",
    }


@app.get("/api/process/queue")
def route_process_queue():
    """内存中当前任务列表（含 url_hash），供链接页队列展示。"""
    from .services.history_manager import get_task_history
    from .services.pipeline_stages import pipeline_summary

    from .services.task_manager import list_queue_tasks

    rows = list_queue_tasks()
    keys = [
        "task_id",
        "status",
        "platform",
        "link",
        "normalized_link",
        "url_hash",
        "progress",
        "stage",
        "link_title",
        "doc_title",
        "content_type",
        "cover_url",
        "route_type",
        "pipeline_route",
        "pipeline_stages",
        "failed_stage",
        "failed_stage_label",
        "resume_from",
        "resume_context",
        "doc_filename",
        "doc_path",
        "html_path",
        "html_status",
        "html_message",
        "feishu_doc_url",
        "feishu_doc_token",
        "feishu_status",
        "feishu_message",
        "error",
        "priority",
        "queue_seq",
        "importance",
        "task_note",
        "task_keywords",
        "extracted_metadata",
        "created_at",
        "updated_at",
        "read_status",
        "import_source",
        "source_label",
        "author_name",
        "author_id",
        "subscription_id",
        "pipeline_started_at",
        "md_completed_at",
        "total_duration_ms",
        "total_token_count",
        "article_char_count",
        "summary_char_count",
    ]
    out = []
    from .services.pipeline_finalize import apply_task_card_metrics
    from .services.task_source_meta import enrich_task_source_fields

    for i, t in enumerate(rows):
        row = enrich_task_source_fields({k: t.get(k) for k in keys})
        row["queue_pos"] = i + 1
        tid = row.get("task_id")
        if tid and row.get("status") == "completed":
            try:
                live = apply_task_card_metrics(tid, persist=False)
                row["total_duration_ms"] = live.get("total_duration_ms") or row.get("total_duration_ms") or 0
                row["total_token_count"] = live.get("total_token_count") or row.get("total_token_count") or 0
                row["article_char_count"] = live.get("article_char_count") or row.get("article_char_count") or 0
                row["summary_char_count"] = live.get("summary_char_count") or row.get("summary_char_count") or 0
            except Exception:
                pass
        if not row.get("pipeline_stages"):
            hist = get_task_history(link=row.get("link"), url_hash=row.get("url_hash"))
            if hist:
                for hk in ("pipeline_route", "pipeline_stages", "failed_stage", "failed_stage_label", "resume_from", "resume_context"):
                    if hist.get(hk) is not None:
                        row[hk] = hist.get(hk)
        route = row.get("pipeline_route") or row.get("route_type") or "video"
        row["pipeline_steps"] = pipeline_summary(row.get("pipeline_stages"), route)
        out.append(row)
    return {"tasks": out}


@app.get("/api/process/queue/suggest")
def route_queue_search_suggest(q: str = Query("", max_length=200)):
    """标题检索近义词扩展（供前端展示推荐词）。"""
    from .follow_up_search import expand_search_terms

    terms = expand_search_terms(q)
    return {"ok": True, "query": q, "expanded_terms": terms[:20]}


@app.get("/api/process/queue/filter")
def route_queue_filter(
    q: str = Query("", max_length=200),
    author: str = Query("", max_length=120),
    read: str = Query("all"),
    sort: str = Query("default"),
    enable_title: bool = Query(True),
    enable_author: bool = Query(False),
    enable_read: bool = Query(False),
    enable_source: bool = Query(False),
    sources: str = Query(""),
):
    """队列任务筛选（多重条件，勾选启用）。"""
    from .services.task_manager import list_queue_tasks
    from .services.task_queue_search import collect_author_facets, filter_tasks

    src_list = [s.strip() for s in (sources or "").split(",") if s.strip()]
    rows = list_queue_tasks()
    result = filter_tasks(
        rows,
        title_query=q,
        author_query=author,
        read_filter=read,
        sources=src_list or None,
        enable_title=enable_title,
        enable_author=enable_author,
        enable_read=enable_read,
        enable_source=enable_source,
        sort=sort,
    )
    result["author_facets"] = collect_author_facets(rows)
    return {"ok": True, **result}


@app.post("/api/process/queue/importance")
async def route_queue_importance(request: Request):
    """更新 pending 任务的重要度（1-10）。"""
    body = await request.json()
    task_id = (body.get("task_id") or "").strip()
    importance = body.get("importance", 5)
    from .services.task_manager import set_task_importance
    from .services.pipeline_scheduler import kick_pipeline_dispatch

    ok = set_task_importance(task_id, int(importance))
    if ok:
        await kick_pipeline_dispatch()
    return {"ok": ok}


@app.post("/api/process/queue/meta")
async def route_queue_meta(request: Request):
    """更新任务备注与关键词。"""
    body = await request.json()
    task_id = (body.get("task_id") or "").strip()
    from .services.task_manager import set_task_note_keywords

    ok = set_task_note_keywords(
        task_id,
        task_note=body.get("task_note"),
        task_keywords=body.get("task_keywords"),
    )
    return {"ok": ok}


@app.post("/api/process/queue/move")
async def route_queue_move(request: Request):
    """移动队列中任务的优先级（仅 pending 状态）"""
    body = await request.json()
    task_id = body.get("task_id", "")
    direction = body.get("direction", "up")  # up / down
    
    from .services.task_manager import move_task_priority
    success = move_task_priority(task_id, direction)
    return {"ok": success}


@app.post("/api/process/queue/cancel")
async def route_queue_cancel(request: Request):
    """取消队列中的任务"""
    body = await request.json()
    task_id = body.get("task_id", "")
    
    from .services.task_manager import cancel_task, add_log
    success = cancel_task(task_id)
    if success:
        add_log(task_id, "任务已取消", "WARNING")
    return {"ok": success}


@app.post("/api/process/queue/cleanup")
def route_queue_cleanup():
    """清理已完成的任务"""
    from .services.task_manager import cleanup_completed_tasks
    removed = cleanup_completed_tasks()
    return {"ok": True, "removed": removed}


@app.post("/api/process/queue/read")
async def route_queue_mark_read(request: Request):
    """将已完成任务标记为已读（单向）。"""
    body = await request.json()
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(400, "缺少 task_id")
    from .services.task_manager import mark_task_read

    ok = mark_task_read(task_id)
    if not ok:
        raise HTTPException(400, "仅已完成的卡片可标记为已读")
    return {"ok": True, "read_status": "read"}


@app.post("/api/process/queue/delete")
async def route_queue_delete(request: Request):
    """手动移除队列卡片（不会删除历史记录与产出文件）。"""
    body = await request.json()
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(400, "缺少 task_id")
    from .services.task_manager import dismiss_queue_task

    ok = dismiss_queue_task(task_id)
    if not ok:
        raise HTTPException(400, "移除失败：任务不存在或已移除")
    return {"ok": True}


@app.post("/api/process/queue/delete-batch")
async def route_queue_delete_batch(request: Request):
    """批量移除队列卡片（不会删除历史记录与产出文件）。"""
    body = await request.json()
    raw_ids = body.get("task_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(400, "缺少 task_ids 数组")
    from .services.task_manager import dismiss_queue_tasks

    result = dismiss_queue_tasks(raw_ids)
    if result.get("removed", 0) <= 0:
        raise HTTPException(400, "未移除任何卡片")
    return result


# ═══════════════════════════════════════════════════════════════════
# PAGE 1: 链接文档化
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/platforms")
def route_platforms():
    return {"platforms": list(PLATFORM_CONFIGS.keys())}


@app.get("/api/workflow/selector")
def route_workflow_selector():
    wfs = list_workflow_definitions()
    return {"workflows": [{"key": k, "name": v.get("name", k) if isinstance(v, dict) else str(v)} for k, v in wfs.items()]}


@app.get("/api/output/path")
def route_output_path():
    root = get_output_dir()
    files = sorted([f.name for f in root.iterdir() if f.is_file()], reverse=True)[:50] if root.exists() else []
    return {"path": str(root), "files": files}


@app.post("/api/output/open-local")
async def route_output_open_local(request: Request):
    """在本机用默认程序打开文件，或在资源管理器中定位（仅允许输出目录内路径）。"""
    import subprocess
    import sys

    body = await request.json()
    path = (body.get("path") or body.get("doc_path") or "").strip()
    action = (body.get("action") or "file").strip().lower()
    if not path:
        raise HTTPException(400, "缺少 path")
    from .services.file_naming import resolve_output_abs, is_under_output_dir

    abs_p = resolve_output_abs(path)
    if not abs_p or not abs_p.exists():
        raise HTTPException(404, "文件不存在")
    if not is_under_output_dir(abs_p):
        raise HTTPException(403, "仅允许打开输出目录内的文件")

    try:
        if action in ("folder", "reveal", "explorer"):
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(abs_p)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(abs_p)])
            else:
                subprocess.Popen(["xdg-open", str(abs_p.parent)])
        else:
            if sys.platform == "win32":
                os.startfile(str(abs_p))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(abs_p)])
            else:
                subprocess.Popen(["xdg-open", str(abs_p)])
    except Exception as e:
        raise HTTPException(500, f"无法打开本地文件: {e}") from e
    return {"ok": True, "path": str(abs_p), "action": action}


@app.get("/api/output/file")
def route_output_file_read(file: str = Query(..., description="output 目录内 basename")):
    """读取 output 内 Markdown 正文与选区标记（预览/编辑用）。"""
    from .services.output_file_io import read_output_file

    try:
        return read_output_file(file)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/doc/export-md")
def route_doc_export_md_read(path: str = Query(..., description="output 目录内导出 MD 绝对路径")):
    """读取多模态导出 MD（含 mm_exports 子目录），供前端 picture 预览。"""
    from .services.file_naming import is_under_output_dir

    p = Path(path).resolve()
    if not p.is_file():
        raise HTTPException(404, "文件不存在")
    if not is_under_output_dir(p):
        raise HTTPException(400, "仅允许读取 output 目录内文件")
    if p.suffix.lower() not in {".md", ".markdown", ".mdx", ".txt"}:
        raise HTTPException(400, "仅支持 Markdown/文本文件")
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        raise HTTPException(500, f"读取失败: {e}") from e
    return {"ok": True, "path": str(p), "content": content, "basename": p.name}


@app.post("/api/output/file/export")
async def route_output_file_export(request: Request):
    """导出完整 Markdown（含文末标记块），供浏览器另存到本机任意目录。"""
    from .services.output_file_io import export_output_markdown

    body = await request.json()
    content = body.get("content")
    if content is None:
        raise HTTPException(400, "缺少 content")
    marks = body.get("marks")
    try:
        full = export_output_markdown(str(content), marks if isinstance(marks, list) else [])
        return {"ok": True, "content": full}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/output/file/save")
async def route_output_file_save(request: Request):
    """保存 output 内 Markdown（覆盖或另存为）。"""
    from .services.output_file_io import save_output_file

    body = await request.json()
    name = (body.get("file") or body.get("name") or "").strip()
    content = body.get("content")
    if content is None:
        raise HTTPException(400, "缺少 content")
    save_as = (body.get("save_as") or body.get("saveAs") or "").strip()
    save_dir = (body.get("save_dir") or body.get("saveDir") or "").strip()
    abs_path = (body.get("path") or body.get("abs_path") or "").strip()
    marks = body.get("marks")
    if not name and not abs_path:
        raise HTTPException(400, "缺少 file 或 path")
    try:
        return save_output_file(
            name,
            str(content),
            save_as=save_as,
            save_dir=save_dir,
            marks=marks if isinstance(marks, list) else None,
            abs_path=abs_path,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except FileExistsError as e:
        raise HTTPException(409, str(e)) from e
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/output/file/marks")
def route_output_file_marks_get(file: str = Query(...)):
    """读取选区标记侧车 JSON。"""
    from .services.output_file_io import enrich_marks_with_labels, read_marks, resolve_output_file

    try:
        abs_p = resolve_output_file(file)
        marks = enrich_marks_with_labels(abs_p, read_marks(abs_p))
        return {"ok": True, "file": abs_p.name, "marks": marks}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/output/file/marks")
async def route_output_file_marks_put(request: Request):
    """写入选区标记侧车 JSON（兼容旧版行标记读取）。"""
    from .services.output_file_io import save_marks

    body = await request.json()
    name = (body.get("file") or body.get("name") or "").strip()
    marks = body.get("marks")
    if not name:
        raise HTTPException(400, "缺少 file")
    if not isinstance(marks, list):
        raise HTTPException(400, "marks 须为数组")
    try:
        return save_marks(name, marks)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/output/file/marks/remap")
async def route_output_file_marks_remap(request: Request):
    """编辑正文后按选区文本锚点传递标记。"""
    from .services.output_file_io import remap_marks_on_text_change, save_marks

    body = await request.json()
    name = (body.get("file") or body.get("name") or "").strip()
    old_text = body.get("old_text")
    new_text = body.get("new_text")
    marks = body.get("marks")
    if not name:
        raise HTTPException(400, "缺少 file")
    if old_text is None or new_text is None:
        raise HTTPException(400, "缺少 old_text / new_text")
    if not isinstance(marks, list):
        raise HTTPException(400, "marks 须为数组")
    try:
        remapped = remap_marks_on_text_change(str(old_text), str(new_text), marks)
        return save_marks(name, remapped)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/output/config")
async def route_output_config(request: Request):
    """将服务端输出根目录写入 `.web_output_dir.json`（须绝对路径）。"""
    body = await request.json()
    path = (body.get("path") or "").strip()
    if not path:
        raise HTTPException(400, "缺少 path")
    r = set_output_dir(path)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "无效路径"))
    return r


@app.post("/api/process/start")
async def route_process_start(req: ProcessRequest):
    link = coerce_pasted_link(req.link)
    if not link:
        raise HTTPException(400, "未能从输入中识别有效链接，请粘贴抖音/小红书/B站分享链接")
    platform = (req.platform or "").strip() or platform_from_url(link) or "小红书"
    comments_dict = req.comments.model_dump() if req.comments else {"enabled": False, "count": 10, "sort": "hot"}
    from .services.task_manager import reuse_or_enqueue_task
    from .services.task_source_meta import SOURCE_MANUAL, source_meta_kwargs

    src_meta = source_meta_kwargs(SOURCE_MANUAL, platform=platform)
    task_id, reused, conflict = reuse_or_enqueue_task(
        platform,
        link,
        req.user_prompt,
        comments_dict,
        action=req.action,
        fast_enqueue=True,
        importance=req.importance,
        task_note=req.task_note,
        task_keywords=req.task_keywords,
        dup_action=req.dup_action,
        **src_meta,
    )
    if conflict:
        raise HTTPException(409, detail={"conflict": "duplicate_completed", "existing": conflict})
    if reused:
        add_log(task_id, f"同链接复用本卡片继续处理 [{platform}] {link}")
    add_log(task_id, f"收到处理请求 [{platform}] {link}")
    if req.user_prompt:
        add_log(task_id, f"User Prompt: {req.user_prompt[:100]}..." if len(req.user_prompt) > 100 else f"User Prompt: {req.user_prompt}")
    if comments_dict.get("enabled"):
        add_log(task_id, f"评论读取: 启用，数量={comments_dict.get('count', 10)}, 排序={comments_dict.get('sort', 'hot')}")
    from .services.pipeline_scheduler import request_video_pipeline_async

    async def _schedule():
        try:
            from .services.pipeline_span_bridge import ensure_pipeline_span_task

            ensure_pipeline_span_task(task_id, link, platform)
        except Exception:
            pass
        await request_video_pipeline_async(task_id)

    asyncio.create_task(_schedule())
    t = get_task(task_id)
    return {
        "task_id": task_id,
        "status": (t or {}).get("status") or "pending",
        "reused": reused,
        "url_hash": (t or {}).get("url_hash"),
        "normalized_link": (t or {}).get("normalized_link"),
    }


@app.get("/api/process/status/{task_id}")
def route_task_status(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return {
        k: task.get(k)
        for k in [
            "task_id",
            "status",
            "platform",
            "link",
            "progress",
            "stage",
            "link_title",
            "doc_title",
            "content_type",
            "cover_url",
            "route_type",
            "doc_filename",
            "doc_path",
            "html_path",
            "html_status",
            "html_message",
            "error",
            "created_at",
            "pipeline_started_at",
            "total_duration_ms",
            "total_token_count",
            "author_name",
            "author_id",
            "source_label",
        ]
    }


@app.get("/api/process/logs/{task_id}")
async def route_stream_logs(task_id: str, request: Request):
    async def gen():
        sent = 0
        last_status = None
        while True:
            if await request.is_disconnected():
                break
            task = get_task(task_id)
            if not task:
                yield f"event: error\ndata: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            logs = task.get("logs", [])
            while sent < len(logs):
                yield f"event: log\ndata: {json.dumps(logs[sent], ensure_ascii=False)}\n\n"
                sent += 1
            cs = f"{task.get('status', '')}:{task.get('progress', 0)}"
            if cs != last_status:
                from .services.pipeline_finalize import apply_task_card_metrics

                metrics = apply_task_card_metrics(task_id, persist=False)
                progress_payload = {
                    "stage": task.get("stage", ""),
                    "progress": task.get("progress", 0),
                    "status": task.get("status", ""),
                }
                if task.get("status") == "completed":
                    progress_payload.update(metrics)
                yield f"event: progress\ndata: {json.dumps(progress_payload, ensure_ascii=False)}\n\n"
                last_status = cs
            if task.get("status") == "completed":
                from .services.pipeline_finalize import apply_task_card_metrics

                metrics = apply_task_card_metrics(task_id, persist=False)
                yield f"event: complete\ndata: {json.dumps({'ok': True, 'doc_filename': task.get('doc_filename'), 'task_id': task_id, 'html_status': task.get('html_status', ''), 'html_message': task.get('html_message', ''), **metrics}, ensure_ascii=False)}\n\n"
                break
            if task.get("status") == "failed":
                yield f"event: error\ndata: {json.dumps({'ok': False, 'error': task.get('error')}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.get("/api/tasks/query")
def route_tasks_query(
    session_id: str = Query("", description="按会话 ID 筛选"),
    task_id: str = Query("", description="按任务 ID 子串筛选"),
    status: str = Query("", description="按任务状态筛选"),
    task_kind: str = Query("all", description="all|main|pipeline"),
    name: str = Query("", description="按任务名/链接检索"),
    sort: str = Query("time_desc", description="time_desc|time_asc|id_asc|id_desc|name_asc|name_desc"),
    limit: int = Query(200, ge=1, le=500),
):
    """任务中心：Redis 优先聚合，MySQL 补全。"""
    from .services.task_registry_service import query_task_registry

    return query_task_registry(
        session_id=session_id,
        task_id=task_id,
        status=status,
        task_kind=task_kind,
        name=name,
        sort=sort,
        limit=limit,
    )


@app.get("/api/tasks/detail")
def route_tasks_detail(
    task_id: str = Query(..., description="任务 ID"),
    task_kind: str = Query("", description="main|pipeline，空则自动推断"),
):
    """任务中心详情：双快照子表 + 工具链 + SPAN 步骤。"""
    from .services.task_registry_service import get_task_registry_detail

    result = get_task_registry_detail(task_id, task_kind=task_kind)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error") or "任务不存在或无详情")
    return result


@app.post("/api/tasks/sync-mysql")
async def route_tasks_sync_mysql(request: Request):
    """手动将单条任务从 Redis/热缓存同步到 MySQL。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    tid = str(body.get("task_id") or "").strip()
    kind = str(body.get("task_kind") or "").strip()
    from .services.task_registry_service import sync_task_to_mysql

    result = sync_task_to_mysql(tid, task_kind=kind)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "同步失败")
    return result


@app.get("/api/history")
def route_history():
    # 使用新的 history_manager 获取历史记录
    tasks = list_history_tasks(limit=400)
    
    # 为每个 task 补充队列位置和当前阶段
    from .services.task_manager import list_tasks as _list_running
    from .services.pipeline_stages import pipeline_summary
    running = {t["link"]: t for t in _list_running()}
    
    from .services.pipeline_finalize import enrich_completed_task_metrics

    for i, task in enumerate(tasks):
        link = task.get("link", "")
        rt = running.get(link)
        if rt:
            task["queue_pos"] = "执行中" if rt.get("status") not in ("completed","failed") else ""
            task["current_stage"] = rt.get("stage", task.get("current_stage", ""))
            task["status"] = rt.get("status", task.get("status"))
            task["progress"] = rt.get("progress", task.get("progress", 0))
            for _k in (
                "link_title", "doc_title", "doc_filename", "doc_path", "html_path", "html_status", "html_message",
                "pipeline_route", "pipeline_stages", "failed_stage", "failed_stage_label",
                "resume_from", "resume_context", "error", "transcribe_error_code",
                "pipeline_started_at", "md_completed_at", "total_duration_ms", "total_token_count",
                "article_char_count", "summary_char_count",
                "import_source", "source_label", "author_name", "author_id", "read_status",
            ):
                if rt.get(_k) is not None:
                    task[_k] = rt.get(_k)
        else:
            task["queue_pos"] = ""
            task["current_stage"] = task.get("stage", "")
        route = task.get("pipeline_route") or task.get("route_type") or "video"
        task["pipeline_steps"] = pipeline_summary(task.get("pipeline_stages"), route)
        tasks[i] = enrich_completed_task_metrics(task)

    from .services.task_manager import apply_read_status_to_history_row
    from .services.task_source_meta import enrich_task_source_fields

    for i, task in enumerate(tasks):
        apply_read_status_to_history_row(task)
        tasks[i] = enrich_task_source_fields(task)

    return {"tasks": tasks, "total": len(tasks)}


@app.get("/api/history/logs/{task_id}")
def route_history_logs(task_id: str):
    """历史任务日志包：操作日志 + SPAN 链路 + 异常（JSONL / history / 内存合并）。"""
    bundle = build_task_log_bundle(task_id)
    if not bundle.get("ok"):
        raise HTTPException(404, "任务不存在或日志未持久化")
    mem = get_task(task_id)
    if mem:
        bundle["status"] = mem.get("status") or bundle.get("status")
        bundle["html_status"] = mem.get("html_status")
        bundle["html_message"] = mem.get("html_message")
    bundle["logs"] = bundle.get("text_logs") or []
    return bundle


@app.post("/api/history/delete")
async def route_history_delete(request: Request):
    body = await request.json()
    link = body.get("link", "")
    success = delete_history_task(link=link)
    return {"ok": success}


@app.post("/api/history/restart")
async def route_history_restart(request: Request):
    """恢复执行/重新执行：resume → 从 failed_stage 断点恢复；rerun/start → 全量重跑"""
    body = await request.json()
    link = body.get("link", "")
    platform = body.get("platform", "抖音")
    action = (body.get("action") or "resume").strip().lower()
    req_task_id = (body.get("task_id") or body.get("id") or "").strip()
    if not link:
        raise HTTPException(400, "缺少 link")

    from .services.history_manager import get_task_history
    from .services.task_manager import reuse_or_enqueue_task, get_task as _get_mem_task

    hist = get_task_history(link=link) or {}
    mem = _get_mem_task(req_task_id) if req_task_id else None

    resume_from = None
    resume_context = {}
    pipeline_stages = {}
    user_prompt = (mem or {}).get("user_prompt") or hist.get("user_prompt") or ""
    comments = (mem or {}).get("comments") if mem else hist.get("comments")
    pipeline_route = (mem or {}).get("pipeline_route") or hist.get("pipeline_route") or hist.get("route_type") or ""

    if action in ("resume", "start"):
        src = mem or hist
        if src.get("failed_stage") or src.get("resume_from"):
            resume_from = src.get("failed_stage") or src.get("resume_from")
            resume_context = src.get("resume_context") or {}
            pipeline_stages = src.get("pipeline_stages") or {}
            pipeline_route = src.get("pipeline_route") or pipeline_route
    elif action == "rerun":
        resume_from = None
        resume_context = {}
        pipeline_stages = {}

    try:
        task_id, reused, _ = reuse_or_enqueue_task(
            platform,
            link,
            user_prompt=user_prompt,
            comments=comments,
            task_id=req_task_id or (hist.get("id") if hist else None),
            resume_from=resume_from,
            resume_context=resume_context,
            pipeline_stages=pipeline_stages,
            pipeline_route=pipeline_route,
            action=action,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    from .services.task_manager import remove_duplicate_tasks, consolidate_queue_by_url_hash
    from .services.link_hash import url_hash as _uh_fn

    remove_duplicate_tasks(_uh_fn(link), task_id)
    consolidate_queue_by_url_hash()
    try:
        from .services.history_manager import consolidate_history_by_url_hash

        consolidate_history_by_url_hash()
    except Exception:
        pass
    try:
        from .services.pipeline_span_bridge import ensure_pipeline_span_task

        ensure_pipeline_span_task(task_id, link, platform)
    except Exception:
        pass
    mode = "断点恢复" if resume_from else "全量重跑"
    add_log(task_id, f"{mode}任务: {link}" + (f" · 从阶段 {resume_from} 继续" if resume_from else ""))
    from .services.pipeline_scheduler import request_video_pipeline_async

    asyncio.create_task(request_video_pipeline_async(task_id))
    return {
        "ok": True,
        "task_id": task_id,
        "reused": reused,
        "action": action,
        "resume_from": resume_from or "",
        "mode": mode,
    }


@app.post("/api/history/stop")
async def route_history_stop(request: Request):
    """停止正在执行的任务"""
    body = await request.json()
    link = body.get("link", "")
    from .services.task_manager import list_tasks as _list_running
    for t in _list_running():
        if t.get("link") == link:
            update_task(t["task_id"], status="cancelled", stage="已取消", error="用户取消")
            add_log(t["task_id"], "任务已取消", "WARNING")
    return {"ok": True}


@app.post("/api/history/move")
async def route_history_move(request: Request):
    """队列中上下移动（仅待处理任务）"""
    body = await request.json()
    link = body.get("link", "")
    direction = body.get("direction", "up")  # up / down
    # 当前由 task_manager 的简单队列管理
    from .services.task_manager import list_tasks as _list_running
    pending = [t for t in _list_running() if t.get("status") == "pending"]
    for i, t in enumerate(pending):
        if t.get("link") == link:
            if direction == "up" and i > 0:
                pending[i], pending[i-1] = pending[i-1], pending[i]
            elif direction == "down" and i < len(pending) - 1:
                pending[i], pending[i+1] = pending[i+1], pending[i]
            break
    return {"ok": True}


@app.post("/api/history/clear-completed")
def route_history_clear_completed():
    removed = clear_completed_history()
    return {"ok": True, "removed": removed}


@app.post("/api/history/restore")
def route_history_restore():
    """从仓库内备份 history.json 合并恢复主历史（链接去重，保留更完整记录）。"""
    result = restore_history_from_backups()
    result["discovered_sources"] = [str(p) for p in discover_history_backup_paths()]
    return result


# ═══════════════════════════════════════════════════════════════════
# 社媒博主订阅（MariaDB）
# ═══════════════════════════════════════════════════════════════════
from .services.creator_subscription_api import (
    health as sub_health,
    api_create_subscription,
    api_list_subscriptions,
    api_get_subscription,
    api_update_subscription,
    api_delete_subscription,
    api_trigger_sync,
    api_trigger_sync_all,
    api_list_sync_runs,
    api_get_digest,
    api_get_latest_digest,
    api_run_creator_profile,
    api_get_latest_creator_profile,
    api_get_creator_profile_run,
    api_seed_subscription_catalog,
    api_list_subscription_blog_notes,
)
from .services.creator_scheduler import get_scheduler_status


@app.get("/api/subscriptions/health")
def route_subscriptions_health():
    return sub_health()


@app.post("/api/subscriptions")
async def route_subscriptions_create(request: Request):
    body = await request.json()
    try:
        return api_create_subscription(body)
    except ValueError as ex:
        code = str(ex)
        if code == "SUB_DUPLICATE":
            raise HTTPException(409, detail={"error_code": code, "message": "该博主已订阅"})
        if code in ("SUB_INVALID_URL", "SUB_UNSUPPORTED_PLATFORM"):
            raise HTTPException(400, detail={"error_code": code, "message": "无效的小红书主页 URL"})
        raise HTTPException(400, detail={"error_code": code, "message": str(ex)})
    except RuntimeError as ex:
        msg = str(ex)
        if "SUB_FETCH_AUTH_FAILED" in msg:
            raise HTTPException(422, detail={"error_code": "SUB_FETCH_AUTH_FAILED", "message": "Cookie 无效或未登录"})
        if "SUB_PROFILE" in msg:
            raise HTTPException(422, detail={"error_code": "SUB_PROFILE_UNREACHABLE", "message": msg})
        raise HTTPException(422, detail={"error_code": "SUB_PROFILE_ERROR", "message": msg})
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise HTTPException(500, detail={"message": str(ex)})


@app.get("/api/subscriptions")
def route_subscriptions_list(
    platform: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    try:
        return api_list_subscriptions(platform=platform, status=status, page=page, page_size=page_size)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/subscriptions/{subscription_id}")
def route_subscriptions_get(subscription_id: str):
    try:
        row = api_get_subscription(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not row:
        raise HTTPException(404, "订阅不存在")
    return row


@app.patch("/api/subscriptions/{subscription_id}")
async def route_subscriptions_patch(subscription_id: str, request: Request):
    body = await request.json()
    try:
        row = api_update_subscription(subscription_id, body)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not row:
        raise HTTPException(404, "订阅不存在")
    return row


@app.delete("/api/subscriptions/{subscription_id}")
def route_subscriptions_delete(subscription_id: str):
    try:
        ok = api_delete_subscription(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@app.post("/api/subscriptions/{subscription_id}/sync")
async def route_subscriptions_sync(subscription_id: str):
    try:
        result = await api_trigger_sync(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not result.get("ok") and result.get("error_code") == "SUB_NOT_FOUND":
        raise HTTPException(404, result.get("error"))
    return result


@app.post("/api/subscriptions/sync-all")
async def route_subscriptions_sync_all():
    try:
        return await api_trigger_sync_all()
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/subscriptions/sync-runs/list")
def route_subscriptions_sync_runs(
    subscription_id: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    try:
        return api_list_sync_runs(subscription_id=subscription_id, page=page, page_size=page_size)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/subscriptions/digests/latest")
def route_subscriptions_digest_latest(subscription_id: str = Query(None)):
    try:
        row = api_get_latest_digest(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not row:
        raise HTTPException(404, "暂无 digest")
    return row


@app.get("/api/subscriptions/digests/{digest_id}")
def route_subscriptions_digest_get(digest_id: str):
    try:
        row = api_get_digest(digest_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not row:
        raise HTTPException(404, "digest 不存在")
    return row


@app.get("/api/subscriptions/scheduler/status")
def route_subscriptions_scheduler_status():
    try:
        from .services.scheduled_job_scheduler import get_scheduler_status as unified_sched_status

        st = unified_sched_status()
        if st.get("scheduler_running"):
            return {"scheduler_running": True, "unified": True, **st}
    except Exception:
        pass
    return get_scheduler_status()


@app.get("/api/scheduled-jobs")
def route_scheduled_jobs_list():
    from .services.scheduled_job_service import api_list_jobs

    return api_list_jobs()


@app.patch("/api/scheduled-jobs/{job_key}")
async def route_scheduled_jobs_update(job_key: str, request: Request):
    from .services.scheduled_job_service import api_update_job

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    result = api_update_job(job_key, body or {})
    if not result.get("ok"):
        raise HTTPException(404, detail={"error_code": result.get("error_code"), "message": result.get("error")})
    return result


@app.post("/api/scheduled-jobs/{job_key}/run")
async def route_scheduled_jobs_run(job_key: str):
    from .services.scheduled_job_service import api_run_job

    return await api_run_job(job_key, trigger="manual_test")


@app.get("/api/scheduled-jobs/active")
def route_scheduled_jobs_active():
    from .services.scheduled_job_service import api_list_active_cards

    return api_list_active_cards()


@app.post("/api/scheduled-jobs/runs/{run_id}/cancel")
def route_scheduled_jobs_run_cancel(run_id: str):
    from .services.scheduled_job_service import api_cancel_run

    result = api_cancel_run(run_id)
    if not result.get("ok"):
        raise HTTPException(400, detail={"error_code": result.get("error_code"), "message": result.get("error")})
    return result


@app.post("/api/scheduled-jobs/runs/{run_id}/retry")
async def route_scheduled_jobs_run_retry(run_id: str):
    from .services.scheduled_job_service import api_retry_run

    result = await api_retry_run(run_id)
    if not result.get("ok"):
        code = 404 if result.get("error_code") == "RUN_NOT_FOUND" else 400
        raise HTTPException(code, detail={"error_code": result.get("error_code"), "message": result.get("error")})
    return result


@app.get("/api/scheduled-jobs/runs")
def route_scheduled_jobs_runs(job_key: str = Query(""), limit: int = Query(50, ge=1, le=200)):
    from .services.scheduled_job_store import list_runs

    return {"ok": True, "runs": list_runs(job_key=job_key, limit=limit)}


@app.get("/api/scheduled-jobs/scheduler/status")
def route_scheduled_jobs_scheduler_status():
    from .services.scheduled_job_scheduler import get_scheduler_status

    return get_scheduler_status()


@app.post("/api/subscriptions/{subscription_id}/profile/run")
async def route_subscriptions_profile_run(subscription_id: str):
    try:
        result = await api_run_creator_profile(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not result.get("ok") and result.get("error_code") == "SUB_NOT_FOUND":
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/subscriptions/{subscription_id}/profile/latest")
def route_subscriptions_profile_latest(subscription_id: str):
    try:
        row = api_get_latest_creator_profile(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not row:
        raise HTTPException(404, "暂无 UP 画像")
    return row


@app.get("/api/subscriptions/profile-runs/{profile_run_id}")
def route_subscriptions_profile_run_get(profile_run_id: str):
    try:
        row = api_get_creator_profile_run(profile_run_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not row:
        raise HTTPException(404, "画像运行记录不存在")
    return row


@app.post("/api/subscriptions/{subscription_id}/catalog/seed")
async def route_subscriptions_catalog_seed(subscription_id: str, request: Request):
    """摘录 UP 主页链接到 seen（博客信息），可选 enqueue 入队链接流水线。"""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    try:
        result = await api_seed_subscription_catalog(subscription_id, body or {})
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not result.get("ok"):
        code = result.get("error_code") or "CATALOG_SEED_FAILED"
        if code == "SUB_NOT_FOUND":
            raise HTTPException(404, result.get("error"))
        raise HTTPException(422, detail={"error_code": code, "message": result.get("error")})
    return result


@app.post("/api/subscriptions/{subscription_id}/catalog/repair-links")
async def route_subscriptions_catalog_repair_links(subscription_id: str):
    """补全 seen 表中缺少 xsec_token 的裸 explore 链接。"""
    from .services.creator_catalog_seed import repair_subscription_note_links

    try:
        result = await repair_subscription_note_links(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not result.get("ok"):
        raise HTTPException(422, detail={"error_code": result.get("error_code"), "message": result.get("error")})
    return result


@app.post("/api/subscriptions/{subscription_id}/catalog/finalize")
async def route_subscriptions_catalog_finalize(subscription_id: str, request: Request):
    """订阅批次收尾：同步 seen、审计、失败重试、总量校验。"""
    from .services.subscription_batch_gate import finalize_subscription_batch

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    sync_run_id = str((body or {}).get("sync_run_id") or "").strip()
    expected_total = body.get("expected_total")
    wait_timeout_sec = int((body or {}).get("wait_timeout_sec") or 0)
    max_retry_rounds = int((body or {}).get("max_retry_rounds") or 2)
    task_ids = body.get("task_ids") if isinstance(body.get("task_ids"), list) else None
    try:
        result = await finalize_subscription_batch(
            subscription_id,
            sync_run_id,
            expected_total=expected_total,
            wait_timeout_sec=wait_timeout_sec,
            max_retry_rounds=max_retry_rounds,
            task_ids=task_ids,
        )
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    return result


@app.get("/api/subscriptions/{subscription_id}/catalog/audit")
def route_subscriptions_catalog_audit(
    subscription_id: str,
    sync_run_id: str = Query(""),
    expected_total: int = Query(None),
):
    """订阅批次审计（不重试）。"""
    from .services.subscription_batch_gate import audit_subscription_batch, sync_seen_from_history

    synced = sync_seen_from_history(subscription_id)
    report = audit_subscription_batch(
        subscription_id,
        sync_run_id=sync_run_id,
        expected_total=expected_total,
    )
    return {"ok": report.ok, "synced_from_history": synced, "audit": report.to_dict()}


@app.get("/api/subscriptions/{subscription_id}/blog-notes")
def route_subscriptions_blog_notes(
    subscription_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    analysis_status: str = Query(None),
):
    """订阅下已摘录的博客链接（seen 表）。"""
    try:
        result = api_list_subscription_blog_notes(
            subscription_id,
            page=page,
            page_size=page_size,
            analysis_status=analysis_status,
        )
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not result.get("ok"):
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/subscriptions/{subscription_id}/link-cards")
def route_subscription_link_cards(
    subscription_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """订阅链接简化卡片（MySQL seen+pipeline 一次 JOIN；无库时 Redis 回退）。"""
    from .services.subscription_link_api import api_list_subscription_link_cards

    return api_list_subscription_link_cards(subscription_id, page=page, page_size=page_size)


# ═══════════════════════════════════════════════════════════════════
# 小红书收藏夹订阅
# ═══════════════════════════════════════════════════════════════════
from .services.favorites_subscription_api import (
    api_ensure_favorites_subscription,
    api_get_favorites_digest,
    api_get_favorites_habit,
    api_get_favorites_catalog,
    api_get_favorites_latest_sync,
    api_import_favorite_ups,
    api_pull_favorite_up_authors,
    api_refresh_favorites_cookies,
    api_trigger_favorites_sync,
    health as fav_health,
)
from .services.follow_up_api import (
    api_list_follow_ups,
    api_profile_follow_up,
    api_pull_follow_ups,
    api_remove_follow_up,
    api_subscribe_follow_up,
)
from .services.favorites_scheduler import get_scheduler_status as get_favorites_scheduler_status


@app.get("/api/favorites/health")
def route_favorites_health():
    return fav_health()


@app.get("/api/favorites/subscription")
def route_favorites_subscription():
    try:
        return api_ensure_favorites_subscription()
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.post("/api/favorites/sync")
async def route_favorites_sync(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    force = int(body.get("force_analyze_latest") or 0)
    batch = int(body.get("sync_batch_size") or 0)
    try:
        return await api_trigger_favorites_sync(
            force_analyze_latest=force,
            sync_batch_size=batch,
        )
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.post("/api/favorites/refresh-cookies")
def route_favorites_refresh_cookies():
    try:
        return api_refresh_favorites_cookies()
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/favorites/habit")
def route_favorites_habit(subscription_id: str = Query(None)):
    try:
        return api_get_favorites_habit(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/favorites/digest/latest")
def route_favorites_digest_latest(subscription_id: str = Query(None)):
    try:
        row = api_get_favorites_digest(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise
    if not row:
        raise HTTPException(404, "暂无收藏 digest")
    return row


@app.get("/api/favorites/catalog")
def route_favorites_catalog(limit: int = Query(20, ge=1, le=80)):
    try:
        return api_get_favorites_catalog(limit=limit)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/favorites/sync/latest")
def route_favorites_sync_latest(subscription_id: str = Query(None)):
    try:
        return api_get_favorites_latest_sync(subscription_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/favorites/scheduler/status")
def route_favorites_scheduler_status():
    return get_favorites_scheduler_status()


@app.get("/api/follow-ups")
def route_follow_ups_list(
    q: str = Query("", description="筛选关键词（支持近义词扩展）"),
    subscribed: str = Query("all", description="all|yes|no"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    """关注 UP 列表查询（近义词 + 订阅状态筛选）。"""
    try:
        return api_list_follow_ups(query=q, subscribed=subscribed, page=page, page_size=page_size)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.post("/api/follow-ups/pull")
def route_follow_ups_pull(
    limit: int = Query(20, ge=1, le=20),
    fast: bool = Query(False),
    reset: bool = Query(False),
):
    """从收藏夹笔记按 cursor 分批拉取未入库博主（每批最多 20 个）。"""
    try:
        return api_pull_follow_ups(limit=limit, merge=True, fast=fast, reset=reset)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        msg = str(ex)
        if "SUB_OWNER" in msg or "SUB_FAVORITES" in msg or "FOLLOW_UP" in msg:
            raise HTTPException(400, detail={"error_code": "FOLLOW_UP_PULL_FAILED", "message": msg})
        raise


@app.post("/api/follow-ups/{creator_id}/subscribe")
async def route_follow_up_subscribe(creator_id: str, request: Request):
    """将关注列表中的 UP 加入订阅（用户显式操作）。"""
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    sync_after = bool(body.get("sync_after", False))
    try:
        return await api_subscribe_follow_up(creator_id, sync_after=sync_after)
    except ValueError as ex:
        raise HTTPException(404, detail={"error_code": str(ex), "message": "关注列表中无此 UP"})
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.post("/api/follow-ups/{creator_id}/profile")
async def route_follow_up_profile(creator_id: str):
    """对关注列表 UP 生成画像（无订阅时先建订阅，不自动同步作品）。"""
    try:
        return await api_profile_follow_up(creator_id)
    except ValueError as ex:
        raise HTTPException(404, detail={"error_code": str(ex), "message": "关注列表中无此 UP"})
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.delete("/api/follow-ups/{creator_id}")
def route_follow_up_delete(creator_id: str):
    """从关注列表移除（不删除已有订阅）。"""
    try:
        return api_remove_follow_up(creator_id)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.get("/api/favorites/up-authors")
def route_favorites_up_authors(limit: int = Query(40, ge=5, le=80)):
    """兼容：拉取并写入关注列表（不自动订阅）。"""
    try:
        return api_pull_favorite_up_authors(limit=limit)
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        msg = str(ex)
        if "SUB_OWNER" in msg or "SUB_FAVORITES" in msg:
            raise HTTPException(400, detail={"error_code": "FAV_UP_PULL_FAILED", "message": msg})
        raise


@app.post("/api/favorites/up/import")
async def route_favorites_up_import(request: Request):
    """将收藏 UP 批量加入 UP 订阅。"""
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return await api_import_favorite_ups(body)
    except ValueError as ex:
        raise HTTPException(400, detail={"error_code": str(ex), "message": "未选择可导入的收藏 UP"})
    except Exception as ex:
        from .services.creator_subscription_store import SubscriptionDbError

        if isinstance(ex, SubscriptionDbError):
            raise HTTPException(503, detail={"error_code": "SUB_DB_UNAVAILABLE", "message": str(ex)})
        raise


@app.post("/api/history/regenerate-html")
async def route_history_regenerate_html(request: Request):
    """重新生成HTML长页"""
    body = await request.json()
    link = body.get("link", "")
    doc_filename = body.get("doc_filename", "")
    
    if not link or not doc_filename:
        raise HTTPException(400, "缺少 link 或 doc_filename")
    
    # 获取平台信息
    platform = "抖音"
    if "xiaohongshu" in link or "xhslink" in link:
        platform = "小红书"
    elif "bilibili" in link or "b23.tv" in link:
        platform = "B站"
    
    # 构建MD文件路径
    md_path = _out_root / Path(doc_filename).name
    if not md_path.exists():
        raise HTTPException(404, "MD文件不存在")

    md_str = str(md_path)
    prev = get_task_history(link=link)
    existing = get_task_by_link(link)
    if not existing and prev:
        import_history_task_to_queue(prev)
        existing = get_task_by_link(link)

    task_id = (existing or {}).get("task_id") or ((prev or {}).get("id") if prev else None)
    if not task_id:
        task_id = create_task(platform, link)
        update_task(
            task_id,
            status="generating_html",
            stage="重新生成HTML",
            progress=80,
            doc_filename=md_str,
            doc_path=md_str,
        )
    else:
        if not get_task(task_id) and prev:
            import_history_task_to_queue({**prev, "id": task_id})
        keep_status = (get_task(task_id) or {}).get("status") or (prev or {}).get("status") or "completed"
        patch = {
            "html_status": "async_pending",
            "html_message": "HTML 重新生成中...",
            "html_path": "",
            "stage": "重新生成HTML",
            "doc_filename": md_str,
            "doc_path": md_str,
        }
        if keep_status in ("completed", "failed", "cancelled"):
            patch["status"] = keep_status
        update_task(task_id, **patch)

    add_log(task_id, f"重新生成HTML: {link}")

    # 启动HTML生成
    from .services.video_pipeline import start_html_generation
    start_html_generation(md_str, task_id, platform=platform, link=link)
    if prev:
        add_or_update_task_in_history(
            {
                "task_id": prev.get("id") or task_id,
                "link": link,
                "url_hash": prev.get("url_hash"),
                "platform": platform,
                "status": prev.get("status") or "completed",
                "doc_filename": md_str,
                "doc_path": md_str,
                "html_status": "async_pending",
                "html_message": "HTML 重新生成中...",
                "logs": prev.get("logs") or [],
            }
        )

    return {"ok": True, "task_id": task_id}


@app.get("/api/history/stats")
def route_history_stats():
    tasks = list_history_tasks(limit=1000)
    return {
        "total": len(tasks),
        "completed": sum(1 for t in tasks if t.get("status") == "completed"),
        "failed": sum(1 for t in tasks if t.get("status") == "failed"),
        "pending": sum(1 for t in tasks if t.get("status") == "pending"),
        "in_progress": sum(1 for t in tasks if t.get("status") not in ("completed","failed","pending","cancelled")),
    }


@app.get("/api/config/info")
def route_config_info():
    from .services.ai_chat import chat_llm_config_diagnostics, resolve_chat_api_credentials

    diag = chat_llm_config_diagnostics()
    creds = resolve_chat_api_credentials(CONFIG if CONFIG else {})
    return {
        "path": str(_CONFIG_PATH),
        "keys": sorted(CONFIG.keys()),
        "llm": {
            **diag,
            "provider": creds.get("provider"),
            "ready": bool(creds.get("api_key") and creds.get("model")),
        },
    }


# ═══════════════════════════════════════════════════════════════════
# TOOLS: SKILL 注册表（替代低代码编排主入口；编排 API 仍保留兼容）
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/skills")
def route_skills_list():
    from .services.board_usage_stats import get_stats_map

    return {"skills": skill_list(), "usage_stats": get_stats_map()}


@app.get("/api/skills/{skill_id}")
def route_skills_get(skill_id: str):
    s = skill_get(skill_id)
    if not s:
        raise HTTPException(404, "SKILL 不存在")
    out = dict(s)
    out.setdefault("version", "1.0.0")
    return out


@app.delete("/api/skills/{skill_id}")
def route_skills_delete(skill_id: str):
    if not skill_delete(skill_id):
        raise HTTPException(404, "SKILL 不存在")
    return {"ok": True}


@app.patch("/api/skills/{skill_id}")
async def route_skills_patch(skill_id: str, request: Request):
    """更新 SKILL 字段（命令映射、名称、描述、正文等）。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "须为 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body 须为对象")
    allowed = {"name", "description", "command", "body_md"}
    patch = {k: body[k] for k in body if k in allowed}
    if not patch:
        raise HTTPException(400, f"仅支持字段: {', '.join(sorted(allowed))}")
    row, err = skill_patch(skill_id, patch)
    if err:
        raise HTTPException(400, err)
    return {"ok": True, "skill": row}


@app.post("/api/skills/import")
async def route_skills_import_json(request: Request):
    body = await request.json()
    try:
        row = skill_import(
            name=body.get("name", ""),
            description=body.get("description", ""),
            body_md=body.get("body_md", ""),
            command=body.get("command", ""),
            source=body.get("source", "form"),
        )
        from .services.skill_flow_service import schedule_skill_flow

        schedule_skill_flow(
            row.get("id", ""),
            row.get("name", ""),
            row.get("description", ""),
            row.get("body_md", ""),
            command=row.get("command", ""),
        )
        return {"ok": True, "skill": row}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/skills/import-md")
async def route_skills_import_md(request: Request):
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        body = await request.json()
        raw = body.get("markdown") or body.get("content") or ""
    else:
        raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        row = skill_import_md(raw, source="md")
        from .services.skill_flow_service import schedule_skill_flow

        schedule_skill_flow(
            row.get("id", ""),
            row.get("name", ""),
            row.get("description", ""),
            row.get("body_md", ""),
            command=row.get("command", ""),
        )
        return {"ok": True, "skill": row}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/skills/import-batch")
async def route_skills_import_batch(request: Request):
    """从服务端路径批量导入 SKILL（默认 .cursor/skills + web_migration/skills_downloaded）。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    roots = body.get("roots")
    upsert = bool(body.get("upsert", True))
    result = skill_import_batch(roots=roots, upsert_by_name=upsert)
    for item in result.get("imported") or []:
        sid = item.get("id")
        if not sid:
            continue
        sk = skill_get(sid)
        if not sk:
            continue
        from .services.skill_flow_service import schedule_skill_flow

        schedule_skill_flow(
            sid,
            sk.get("name", ""),
            sk.get("description", ""),
            sk.get("body_md", ""),
            command=sk.get("command", ""),
        )
    return result


@app.post("/api/skills/import-bundle")
async def route_skills_import_bundle(request: Request):
    """浏览器文件夹上传：每项含 markdown + attachments。"""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body 须为对象")
    items = body.get("skills") or body.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(400, "skills 须为数组")
    upsert = bool(body.get("upsert", True))
    result = skill_import_bundle(items, upsert_by_name=upsert)
    for name in result.get("imported") or []:
        for s in skill_list():
            if s.get("name") == name:
                full = skill_get(s.get("id", ""))
                if full:
                    from .services.skill_flow_service import schedule_skill_flow

                    schedule_skill_flow(
                        full.get("id", ""),
                        full.get("name", ""),
                        full.get("description", ""),
                        full.get("body_md", ""),
                        command=full.get("command", ""),
                    )
                break
    return result


@app.post("/api/skills/commit-commands")
async def route_skills_commit_commands(request: Request):
    """批量提交工具页 SKILL 命令映射（未传 commands 时为空命令补默认 /name）。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    commands = body.get("commands")
    if commands is not None and not isinstance(commands, dict):
        raise HTTPException(400, "commands 须为对象")
    return skill_commit_commands(commands)


@app.post("/api/skills/tag-board")
async def route_skills_tag_board(request: Request):
    """为 SKILL 重新生成能力看板 AI 标签（全部或指定 id）。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    from .services.skill_board_tagger import tag_skills_by_ids

    force = bool(body.get("force", True))
    skill_id = (body.get("skill_id") or body.get("id") or "").strip()
    if skill_id:
        return tag_skills_by_ids([skill_id], force=force)
    ids = [s.get("id") for s in skill_list() if s.get("id")]
    return tag_skills_by_ids(ids, force=force)


@app.get("/api/skills/{skill_id}/flow-diagram")
def route_skills_flow_get(skill_id: str):
    from .services.skill_flow_service import get_flow_state

    return {"ok": True, **get_flow_state(skill_id)}


@app.post("/api/skills/{skill_id}/flow-diagram")
def route_skills_flow_generate(skill_id: str):
    from .services.skill_flow_service import get_flow_state, schedule_skill_flow
    from .services.skill_registry import get_skill

    sk = get_skill(skill_id)
    if not sk:
        raise HTTPException(404, "SKILL 不存在")
    schedule_skill_flow(
        skill_id,
        sk.get("name", ""),
        sk.get("description", ""),
        sk.get("body_md", ""),
        command=sk.get("command", ""),
    )
    return {"ok": True, **get_flow_state(skill_id)}


@app.get("/api/skills/{skill_id}/versions")
def route_skills_versions(skill_id: str):
    from .services.skill_registry import get_skill
    from .services.skill_version_service import ensure_initial_version, list_versions

    sk = get_skill(skill_id)
    if not sk:
        raise HTTPException(404, "SKILL 不存在")
    ensure_initial_version(sk)
    return {"ok": True, "versions": list_versions(skill_id), "current": sk.get("version") or "1.0.0"}


@app.get("/api/skills/{skill_id}/diff")
def route_skills_diff(skill_id: str, from_ver: str = Query(..., alias="from"), to_ver: str = Query(..., alias="to")):
    from .services.skill_registry import get_skill
    from .services.skill_version_service import diff_versions

    if not get_skill(skill_id):
        raise HTTPException(404, "SKILL 不存在")
    try:
        return {"ok": True, **diff_versions(skill_id, from_ver.strip(), to_ver.strip())}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/chat/slash-suggest")
def route_chat_slash_suggest(prefix: str = Query("", alias="prefix"), limit: int = Query(12, ge=1, le=30)):
    from app.services.skill_registry import slash_suggestions_with_total

    return slash_suggestions_with_total(prefix, limit=limit)


@app.post("/api/chat/complete-slash")
async def route_chat_complete_slash(request: Request):
    """SPEC API-S2：与 slash-suggest 同源，供 POST 客户端使用。"""
    body = await request.json()
    prefix = (body.get("prefix") or "").strip()
    lim = int(body.get("limit") or 12)
    from app.services.skill_registry import slash_suggestions_with_total

    out = slash_suggestions_with_total(prefix, limit=lim)
    return {"ok": True, **out}


@app.get("/api/tools/builtin")
def route_tools_builtin():
    """内置 Tool Call 清单（供工具页展示）。"""
    return {"ok": True, "tools": _list_builtin_tools()}


@app.get("/api/chat/tools-catalog")
async def route_chat_tools_catalog(read_comments: bool = False):
    """AI 对话页实际挂载的工具目录（内置 + MCP + SKILL，不按 Agent 过滤）。"""
    from .services.chat_tool_registry import load_all_chat_tools

    _tools, meta = await load_all_chat_tools(read_comments=read_comments)
    return {"ok": True, **meta}


@app.get("/api/chat/models")
def route_chat_models():
    """AI 问答页模型下拉：网关节点池活跃 endpoint（不含 api_key）。"""
    from .services.config import list_chat_model_options

    return list_chat_model_options()


@app.get("/api/chat/warmup")
async def route_chat_warmup(
    read_comments: bool = False,
    include_rag: bool = True,
    force: bool = False,
    wait: bool = True,
):
    """
    对话运行时预热：MCP 工具 + LangGraph 图 + 可选 RAG/Milvus。
    前端进入 AI 问答页或发送前调用；后端启动时亦后台执行。
    """
    from .services.chat_warmup import (
        get_warmup_status,
        run_chat_warmup,
        wait_for_chat_warmup,
    )

    st = get_warmup_status()
    if st.get("ready") and not force and st.get("tools_cached", {}).get(
        "read_comments" if read_comments else "default"
    ):
        return {"ok": True, **st}
    if st.get("warming") and not force:
        if wait:
            st = await wait_for_chat_warmup(
                read_comments=read_comments,
                include_rag=include_rag,
                force=force,
                timeout_sec=90.0,
            )
        return {"ok": True, **st}
    if wait:
        st = await run_chat_warmup(
            read_comments=read_comments,
            include_rag=include_rag,
            force=force,
        )
    else:
        asyncio.create_task(
            run_chat_warmup(
                read_comments=read_comments,
                include_rag=include_rag,
                force=force,
            )
        )
        st = get_warmup_status()
    return {"ok": True, **st}


@app.get("/api/chat/runtime-status")
async def route_chat_runtime_status():
    """轻量运行时探活：不探测 Milvus，供前端判断后端是否假死。"""
    from .services.chat_warmup import get_warmup_status

    t0 = time.perf_counter()
    st = get_warmup_status()
    return {
        "ok": True,
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "warmup": st,
    }


@app.get("/api/tools/mcp/config")
def route_tools_mcp_config_get():
    return {"ok": True, "servers": mcp_lc.load_mcp_server_dict(), "path": str(mcp_lc.mcp_config_path())}


@app.post("/api/tools/mcp/config")
async def route_tools_mcp_config_save(request: Request):
    body = await request.json()
    servers = body.get("servers")
    if not isinstance(servers, dict):
        raise HTTPException(400, "body 须包含对象字段 servers")
    mcp_lc.save_mcp_server_dict(servers)
    return {"ok": True}


@app.post("/api/tools/mcp/sync")
async def route_tools_mcp_sync():
    """使用 LangChain langchain-mcp-adapters 连接已配置 MCP 并枚举工具。"""
    return await mcp_lc.mcp_sync_list_tools()


@app.get("/api/tools/mcp/vendors")
def route_tools_mcp_vendors():
    """常见 MCP 厂商/官方包/文档入口（静态目录，与 TRAE 类「预设 + 插入配置」一致）。"""
    return {"ok": True, "items": list_mcp_vendor_presets()}


@app.get("/api/tools/mcp/server/{alias}")
def route_tools_mcp_server_get(alias: str):
    blk = mcp_lc.get_mcp_server_block(alias)
    if blk is None:
        raise HTTPException(404, "MCP 服务不存在")
    return {"ok": True, "alias": alias.strip(), "block": blk}


@app.put("/api/tools/mcp/server/{alias}")
async def route_tools_mcp_server_put(alias: str, request: Request):
    body = await request.json()
    block = body.get("block")
    if not isinstance(block, dict):
        raise HTTPException(400, "body 须包含对象字段 block")
    try:
        servers = mcp_lc.upsert_mcp_server(alias, block)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "servers": servers}


@app.delete("/api/tools/mcp/server/{alias}")
def route_tools_mcp_server_delete(alias: str):
    try:
        servers = mcp_lc.delete_mcp_server(alias)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "servers": servers}


@app.post("/api/tools/detail-narrative")
async def route_tools_detail_narrative(request: Request):
    """用已配置的网关模型为工具详情生成补充 HTML（与 AI 问答同源 provider_adapters）。"""
    body = await request.json()
    kind = (body.get("kind") or "").strip().lower()
    payload = body.get("payload")
    if kind not in ("builtin", "mcp", "skill") or not isinstance(payload, dict):
        raise HTTPException(400, "kind 须为 builtin|mcp|skill，且 payload 须为对象")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: generate_tool_detail_html(kind=kind, payload=payload))


# ═══════════════════════════════════════════════════════════════════
# PAGE 2: 任务编排
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/orchestration/nodes")
def route_orch_nodes():
    return {"nodes": list_workflow_nodes()}


@app.get("/api/orchestration/designer/workflows")
def route_orch_workflows():
    return {"workflows": list_workflow_definitions()}


@app.post("/api/orchestration/designer/save")
async def route_orch_save(request: Request):
    body = await request.json()
    return save_workflow_definition(body.get("name", ""), body.get("definition", {}))


@app.post("/api/orchestration/designer/delete")
async def route_orch_delete(request: Request):
    body = await request.json()
    return delete_workflow_definition(body.get("name", ""))


@app.get("/api/orchestration/scheduler/status")
def route_orch_scheduler():
    return get_workflow_state()


# ═══════════════════════════════════════════════════════════════════
# PAGE 3: AI问答 + 语音识别
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/chat/voice-to-text")
async def route_chat_voice_to_text(audio: UploadFile = File(...)):
    audio_dir = ROOT / "audio_uploads"
    audio_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    audio_path = audio_dir / f"voice_{ts}.webm"
    content = await audio.read()
    audio_path.write_bytes(content)

    # 调用已有的 speech_to_text（从 video_downloader 导入）
    try:
        import sys
        _ag_dir = (_AGENT_DIR).resolve()
        if str(_ag_dir) not in sys.path:
            sys.path.insert(0, str(_ag_dir))
        from video_downloader import speech_to_text

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: speech_to_text(str(audio_path)))
        text = result.get("full_text", "") if result else ""
        return {"ok": True, "text": text, "audio_path": str(audio_path), "file_size": len(content)}
    except Exception as e:
        return {"ok": True, "text": "", "audio_path": str(audio_path),
                "file_size": len(content), "note": f"语音识别失败: {e}"}


@app.get("/api/chat/orch-pipeline-nodes")
async def route_orch_pipeline_nodes():
    from .services.orch_pipeline_config import orch_node_meta_list, DEFAULT_ORCH_PIPELINE_NODES

    return {
        "ok": True,
        "nodes": orch_node_meta_list(),
        "defaults": dict(DEFAULT_ORCH_PIPELINE_NODES),
    }


@app.post("/api/chat/stream")
async def route_chat_stream(request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}
    orig = body.get("message", "").strip()
    sid = body.get("session_id", "default")
    if not orig:
        raise HTTPException(400, "消息不能为空")
    _LOG_CHAT.info(
        "[AI问答-流式|main.route_chat_stream|session:%s|Agent执行|接入] "
        "stream_accepted; msg_len=%s; rag_prefetch=%s",
        sid,
        len(orig),
        bool(body.get("rag_prefetch", False)),
    )
    msg, skill_meta = expand_message_with_skill_meta(orig)
    if skill_meta:
        _LOG_CHAT.info(
            "skill_mounted session=%s skill_id=%s command=%s name=%s",
            sid,
            skill_meta.get("skill_id"),
            skill_meta.get("command"),
            skill_meta.get("name"),
        )
    model = (body.get("model") or "").strip() or None
    agent_id = (body.get("agent_id") or "").strip() or None
    agent_profile = body.get("agent_profile")
    if not isinstance(agent_profile, dict):
        agent_profile = None
    user_id = getattr(request.state, "user_id", None)
    rag_prefetch = bool(body.get("rag_prefetch", False))
    web_search = bool(body.get("web_search", False))
    read_comments = bool(body.get("read_comments", False))
    include_rss = bool(body.get("include_rss", False))
    deep_think = bool(body.get("deep_think", False))
    chat_max_tool_rounds = body.get("chat_max_tool_rounds")
    chat_tool_timeout_sec = body.get("chat_tool_timeout_sec")
    chat_tool_max_retry = body.get("chat_tool_max_retry")
    chat_distinct_tool_fail_limit = body.get("chat_distinct_tool_fail_limit")
    client_cur_task = body.get("cur_task") if isinstance(body.get("cur_task"), dict) else None
    client_main_task_history = body.get("main_task_history") if isinstance(body.get("main_task_history"), list) else None
    orch_pipeline_nodes = body.get("orch_pipeline_nodes") if isinstance(body.get("orch_pipeline_nodes"), dict) else None

    from .services.chat_warmup import (
        get_cached_session_memory,
        get_warmup_status,
        refresh_or_prepare_session_memory,
        run_chat_warmup,
        store_session_memory_cache,
        wait_for_chat_warmup,
    )

    def _minimal_session_memory() -> Dict[str, Any]:
        return {
            "usage": {"pct": 0, "mode": "short"},
            "memory_meta": {"mode": "short"},
            "memory_mode": "short",
            "task_context_block": "",
            "task_redis": {},
            "task_repo": {},
            "task_group_seq": 0,
            "cur_task": client_cur_task if isinstance(client_cur_task, dict) else {},
            "main_task_history": client_main_task_history if isinstance(client_main_task_history, list) else [],
            "summary_text": "",
            "events": [],
            "force_new_session": False,
        }

    async def gen():
        try:
            st = get_warmup_status()
            warmup_task = None
            # 仅等待工具/MCP + LangGraph；RAG/Milvus 嵌入后台预热，不得阻塞 SSE 首包与普通问答
            need_tools = not st.get("ready")
            if need_tools or st.get("warming"):
                warmup_task = asyncio.create_task(
                    wait_for_chat_warmup(
                        read_comments=read_comments,
                        include_rag=False,
                        timeout_sec=25.0,
                    )
                )
            elif not st.get("warming") and not st.get("ready"):
                asyncio.create_task(
                    run_chat_warmup(
                        read_comments=read_comments,
                        include_rag=bool(rag_prefetch),
                    )
                )
            # 照搬 HaiChiAgent：SSE 注释 + 填充强制 TCP 立即 flush，避免 Nagle 缓冲首包
            yield ": " + (" " * 2048) + "\n\n"
            # 首包立刻返回，避免前端长时间停在「正在处理…」（会话记忆在后台继续准备）
            yield (
                "event: stream_open\n"
                + "data: "
                + json.dumps(
                    {
                        "session_id": sid,
                        "stage": "准备会话上下文",
                        "progress": 0,
                        "orchestration_engine": "pending",
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            import time as _time

            _mem_t0 = _time.perf_counter()
            _tid_lite = ""
            if isinstance(client_cur_task, dict):
                _tid_lite = str(client_cur_task.get("task_id") or "").strip()
            mem_task = asyncio.create_task(
                refresh_or_prepare_session_memory(
                    sid,
                    client_cur_task=client_cur_task,
                    client_history=client_main_task_history,
                    extra_tokens=max(32, len(msg) // 2),
                    lite=bool(_tid_lite),
                )
            )
            cached_mem = get_cached_session_memory(sid)
            if cached_mem:
                memory_prepared = dict(cached_mem)
                async def _refresh_mem_bg() -> None:
                    try:
                        refreshed = await mem_task
                        store_session_memory_cache(sid, refreshed)
                    except Exception:
                        pass
                asyncio.create_task(_refresh_mem_bg())
            else:
                waitables = [mem_task]
                if warmup_task is not None:
                    waitables.append(warmup_task)
                done, pending = await asyncio.wait(
                    waitables,
                    timeout=0.35,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if mem_task in done:
                    memory_prepared = mem_task.result()
                else:
                    memory_prepared = _minimal_session_memory()
                    async def _mem_fallback() -> None:
                        try:
                            refreshed = await mem_task
                            store_session_memory_cache(sid, refreshed)
                        except Exception:
                            pass
                    asyncio.create_task(_mem_fallback())
                # pending 中的 warmup_task / mem_task 继续在后台运行，勿对 Task 再 create_task
            _mem_ms = int((_time.perf_counter() - _mem_t0) * 1000)
            logging.getLogger("sba.main").info(
                "[AI问答-流式|main.route_chat_stream|session:%s|硬编执行|会话记忆] "
                "prepare_done; cost_ms=%s; pct=%s",
                sid,
                _mem_ms,
                (memory_prepared.get("usage") or {}).get("pct"),
            )
            if memory_prepared.get("force_new_session"):
                yield (
                    "event: context_memory\n"
                    + "data: "
                    + json.dumps(
                        {
                            "type": "context_force_switch",
                            "pct": memory_prepared.get("usage", {}).get("pct", 100),
                            "message": "上下文已满，请新建会话；主任务链已存档",
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            async for event in _svc_chat_stream(
                msg, sid, model=model, agent_id=agent_id, agent_profile=agent_profile, user_id=user_id,
                rag_prefetch=rag_prefetch, web_search=web_search, read_comments=read_comments,
                include_rss=include_rss,
                deep_think=deep_think,
                chat_max_tool_rounds=chat_max_tool_rounds,
                chat_tool_timeout_sec=chat_tool_timeout_sec,
                chat_tool_max_retry=chat_tool_max_retry,
                chat_distinct_tool_fail_limit=chat_distinct_tool_fail_limit,
                client_cur_task=client_cur_task or memory_prepared.get("cur_task"),
                client_main_task_history=client_main_task_history or memory_prepared.get("main_task_history"),
                memory_prepared=memory_prepared,
                orch_pipeline_nodes=orch_pipeline_nodes,
            ):
                yield event
        except Exception as exc:
            from .services.chat_error_handler import stream_user_error_sse

            async for event in stream_user_error_sse(
                exc,
                session_id=sid,
                stage="问答流",
                user_message=msg,
            ):
                yield event

    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.post("/api/chat/graph/resume")
async def route_chat_graph_resume(request: Request):
    """LangGraph HITL：用 Command(resume=...) 继续编排或进入执行段。"""
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}
    sid = (body.get("session_id") or body.get("thread_id") or "default").strip()
    hitl = body.get("hitl") if isinstance(body.get("hitl"), dict) else body

    from .services.chat_graph_runner import stream_langgraph_resume

    async def gen():
        try:
            async for event in stream_langgraph_resume(
                sid,
                hitl,
                model=(body.get("model") or "").strip() or None,
                agent_id=(body.get("agent_id") or "").strip() or None,
                agent_profile=body.get("agent_profile") if isinstance(body.get("agent_profile"), dict) else None,
                user_id=getattr(request.state, "user_id", None),
                rag_prefetch=bool(body.get("rag_prefetch", False)),
                web_search=bool(body.get("web_search", False)),
                read_comments=bool(body.get("read_comments", False)),
                deep_think=bool(body.get("deep_think", False)),
                orch_pipeline_nodes=body.get("orch_pipeline_nodes") if isinstance(body.get("orch_pipeline_nodes"), dict) else None,
            ):
                yield event
        except Exception as exc:
            from .services.chat_error_handler import stream_user_error_sse

            async for event in stream_user_error_sse(
                exc,
                session_id=sid,
                stage="HITL 恢复",
            ):
                yield event

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/chat/sessions")
def route_chat_sessions():
    return {"sessions": list_sessions()}


@app.post("/api/chat/sessions")
async def route_chat_create(request: Request):
    title = "新对话"
    try:
        body = await request.json()
        if isinstance(body, dict) and (body.get("title") or "").strip():
            title = (body.get("title") or "").strip()[:80]
    except Exception:
        pass
    session = create_session(title)
    return {"session_id": session["id"]}


@app.delete("/api/chat/sessions/{sid}")
def route_chat_delete(sid: str):
    delete_session(sid)
    return {"ok": True}


@app.get("/api/chat/sessions/{sid}")
def route_chat_get(sid: str):
    from .services.chat_context_memory import (
        normalize_session_document_for_storage,
        persist_normalized_session_document,
        session_doc_byte_size,
        session_document_has_full_orchestration_io,
        SESSION_DOC_SOFT_BYTES,
    )
    from .services.chat_session_store import get_session_document

    doc = get_session_document(sid)
    if not doc:
        raise HTTPException(404, "会话不存在")
    normalized, changed = normalize_session_document_for_storage(doc)
    if (
        changed
        or session_document_has_full_orchestration_io(doc)
        or session_doc_byte_size(doc) > SESSION_DOC_SOFT_BYTES
    ):
        normalized = persist_normalized_session_document(sid, normalized)
    return normalized


@app.patch("/api/chat/sessions/{sid}")
async def route_chat_rename(sid: str, request: Request):
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title 不能为空")
    if not rename_session(sid, title):
        raise HTTPException(404, "会话不存在")
    return {"ok": True, "title": title}


@app.put("/api/chat/sessions/{sid}/state")
async def route_chat_save_state(sid: str, request: Request):
    body = await request.json()
    messages = body.get("messages")
    if messages is not None and not isinstance(messages, list):
        raise HTTPException(400, "messages 须为数组")
    ok = save_session_state(
        sid,
        messages=messages,
        title=(body.get("title") or "").strip() or None,
        cur_task=body.get("cur_task") if isinstance(body.get("cur_task"), dict) else None,
        main_task_history=body.get("main_task_history")
        if isinstance(body.get("main_task_history"), list)
        else None,
        prefs=body.get("prefs") if isinstance(body.get("prefs"), dict) else None,
        status=(body.get("status") or "").strip() or None,
    )
    if not ok:
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@app.post("/api/chat/sessions/{sid}/close")
def route_chat_close(sid: str):
    save_session_state(sid, status="closed")
    return {"ok": True, "status": "closed"}


@app.get("/api/chat/sessions/{sid}/export-md")
def route_chat_export_md(sid: str):
    from fastapi.responses import PlainTextResponse

    md = export_session_markdown(sid)
    if not md.strip():
        raise HTTPException(404, "会话不存在")
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@app.post("/api/chat/sessions/{sid}/solidify-task")
async def route_chat_solidify_task(sid: str, request: Request):
    """主任务结案后：固化用户画像 / 规则 / 情境 Skill。"""
    body = await request.json()
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(400, "task_id 不能为空")
    cur_task = body.get("cur_task") if isinstance(body.get("cur_task"), dict) else None
    user_id = getattr(request.state, "user_id", None)
    from .services.chat_context_memory import solidify_task_on_closure

    result = solidify_task_on_closure(
        user_id,
        session_id=sid,
        task_id=task_id,
        cur_task=cur_task,
    )
    return result


@app.post("/api/chat/sessions/sync-redis")
def route_chat_sync_redis():
    from .services.chat_session_store import sync_all_dirty

    n = sync_all_dirty()
    return {"ok": True, "synced": n}


# ─── Agent 个性化（分层 Prompt + 版本库，仅追加 INSERT）───
@app.get("/api/agent-personalization/catalog")
def route_agent_personalization_catalog():
    from .services import agent_personalization_service as _apz

    return _apz.catalog()


@app.get("/api/agent-personalization/current")
def route_agent_personalization_current(template_key: str = Query(..., min_length=4)):
    from .services import agent_personalization_service as _apz

    return _apz.get_current_payload(template_key.strip())


@app.get("/api/agent-personalization/history")
def route_agent_personalization_history(
    template_key: str = Query(..., min_length=4),
    limit: int = Query(40, ge=1, le=200),
):
    from .services.agent_personalization_db import list_revisions

    return {"revisions": list_revisions(template_key.strip(), limit=limit)}


@app.post("/api/agent-personalization/save")
async def route_agent_personalization_save(request: Request):
    from .services import agent_personalization_service as _apz

    body = await request.json()
    tk = (body.get("template_key") or "").strip()
    layers = body.get("layers")
    if not tk or not isinstance(layers, dict):
        raise HTTPException(400, "template_key 与 layers 必填且 layers 须为对象")
    r = _apz.save_layers(tk, layers)
    if not r.get("ok"):
        raise HTTPException(400, {"detail": r.get("errors") or ["校验失败"]})
    return {"ok": True, "version": r.get("version")}


@app.post("/api/agent-personalization/custom")
async def route_agent_personalization_custom_create(request: Request):
    from .services import agent_personalization_service as _apz

    body = await request.json()
    layers = body.get("layers")
    if not isinstance(layers, dict):
        raise HTTPException(400, "layers 须为对象")
    r = _apz.create_custom_template(layers)
    if not r.get("ok"):
        raise HTTPException(400, {"detail": r.get("errors") or ["创建失败"]})
    return r


@app.post("/api/agent-personalization/migrate-legacy")
async def route_agent_personalization_migrate_legacy(request: Request):
    """将浏览器 localStorage 旧版扁平模板迁入版本库（仅追加，不删历史）。"""
    from .services import agent_personalization_service as _apz
    from .services.agent_personalization_models import layers_from_legacy_profile, normalize_layers

    body = await request.json()
    agents = body.get("agents")
    if not isinstance(agents, list):
        raise HTTPException(400, "agents 须为数组")
    created = []
    for a in agents:
        if not isinstance(a, dict):
            continue
        layers = normalize_layers(layers_from_legacy_profile(a))
        r = _apz.create_custom_template(layers)
        if r.get("ok"):
            created.append({"agent_id": r.get("agent_id"), "template_key": r.get("template_key")})
    return {"ok": True, "migrated": len(created), "items": created}


@app.post("/api/agent-personalization/deactivate")
async def route_agent_personalization_deactivate(request: Request):
    from .services.agent_personalization_db import deactivate_custom

    body = await request.json()
    aid = (body.get("agent_id") or "").strip()
    if not aid.startswith("c_"):
        raise HTTPException(400, "仅支持自定义 agent_id（c_ 前缀）")
    ok = deactivate_custom(f"custom:{aid}")
    return {"ok": ok}


# ─── AI 连接测试 ───
@app.post("/api/settings/test-connection")
async def route_test_connection(request: Request):
    """测试 AI 连接：返回连通状态 + 简单测试语句结果"""
    import time as _time
    body = await request.json()
    provider = body.get("provider", "ark").strip().lower()
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip()
    model = body.get("model", "").strip()

    if not api_key or not model:
        return {"ok": False, "error": "缺少 api_key 或 model", "status": "invalid_config"}

    try:
        import sys
        _ag_dir = str(_AGENT_DIR) if _AGENT_DIR.exists() else ""
        if _ag_dir and _ag_dir not in sys.path:
            sys.path.insert(0, _ag_dir)
        from provider_adapters import invoke_unified

        t0 = _time.perf_counter()
        result = invoke_unified(
            provider=provider, base_url=base_url, api_key=api_key,
            model=model,
            messages=[{"role": "user", "content": "请用一句话介绍你自己（不超过30字）"}],
            temperature=0.1, max_tokens=100, timeout=30.0,
        )
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        if result:
            return {"ok": True, "status": "connected", "elapsed_ms": elapsed_ms,
                    "test_response": result[:200], "model": model}
        return {"ok": False, "status": "empty_response", "elapsed_ms": elapsed_ms, "error": "返回空"}
    except ImportError:
        return {"ok": False, "status": "import_error", "error": "provider_adapters 不可用"}
    except Exception as e:
        return {"ok": False, "status": "failed", "error": str(e)[:300]}


# ─── SPAN 审计查询 ───
@app.get("/api/chat/spans/{session_id}")
def route_chat_spans(session_id: str):
    from .services.span_audit import list_tasks as _span_list
    tasks = _span_list(session_id)
    enriched = []
    for t in tasks or []:
        row = dict(t)
        steps = row.get("steps") or []
        type_counts: dict[str, int] = {}
        for s in steps:
            st = str(s.get("step_type") or "unknown")
            type_counts[st] = type_counts.get(st, 0) + 1
        row["step_type_counts"] = type_counts
        enriched.append(row)
    return {
        "ok": True,
        "session_id": session_id,
        "tasks": enriched,
        "task_count": len(enriched),
    }


@app.get("/api/chat/spans/{session_id}/{task_id}")
def route_chat_span_detail(session_id: str, task_id: str):
    from .services.span_audit import get_task as _span_get
    task = _span_get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "task": task}


# ═══════════════════════════════════════════════════════════════════
# RAG：逻辑库 + 服务端路径浏览
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/fs/browse")
def route_fs_browse(path: str = Query("", description="绝对路径；空则返回白名单根目录")):
    return fs_browse(path)


@app.get("/api/rag/libraries")
def route_rag_libraries_list():
    return {"ok": True, "libraries": rag_lib.list_libraries(), "active_id": rag_lib.get_active_id()}


@app.post("/api/rag/libraries")
async def route_rag_libraries_create(request: Request):
    body = await request.json()
    try:
        row = rag_lib.create_library(body.get("name", ""))
        return {"ok": True, "library": row}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/rag/libraries/active")
async def route_rag_libraries_active(request: Request):
    body = await request.json()
    lid = body.get("id", "")
    if not rag_lib.set_active(lid):
        raise HTTPException(400, "无效的库 id")
    return {"ok": True, "active_id": lid}


@app.post("/api/rag/libraries/{lid}/config")
async def route_rag_library_config(lid: str, request: Request):
    body = await request.json()
    try:
        row = rag_lib.update_library(
            lid,
            slice_method=body.get("slice_method"),
            metadata_json=body.get("metadata_json"),
            recall_filter_json=body.get("recall_filter_json"),
        )
        if not row:
            raise HTTPException(404, "库不存在")
        return {"ok": True, "library": row}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/rag/libraries/{lid}")
def route_rag_library_delete(lid: str):
    if not rag_lib.delete_library(lid):
        raise HTTPException(400, "不能删除默认库或库不存在")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════
# PAGE 4: 文档处理 + RAG 知识库管理
# ═══════════════════════════════════════════════════════════════════
_RAG_API_TIMEOUT_SEC = 14.0


@app.get("/api/doc/rag/stats")
async def route_doc_rag_stats(refresh: bool = Query(False, description="true 时强制直连 Milvus 重算切片聚合")):
    """RAG 统计：线程池执行并限时，避免 Milvus 不可达时拖死 worker 线程。"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(lambda r=refresh: kb_stats(refresh=r)),
            timeout=_RAG_API_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logging.getLogger("sba.kb_rag").warning(
            "[RAG-知识库|main.route_doc_rag_stats|kb_stats|硬编执行|超时] "
            "timeout_sec=%s",
            _RAG_API_TIMEOUT_SEC,
        )
        return {
            "ok": True,
            "degraded": True,
            "error": "rag_stats_timeout",
            "data": {
                "total_files": 0,
                "total_chunks": 0,
                "milvus_ok": False,
                "chunk_count_source": "timeout",
            },
        }


@app.get("/api/doc/rag/files")
async def route_doc_rag_files(
    page: int = Query(1),
    size: int = Query(50),
    refresh: bool = Query(False, description="true 时强制直连 Milvus 按切片属性聚合父文档切片数"),
):
    """RAG 文件列表：线程池执行并限时，避免并发 Milvus 快照占满线程池。"""
    try:
        payload = await asyncio.wait_for(
            asyncio.to_thread(lambda r=refresh: kb_list_files(refresh=r)),
            timeout=_RAG_API_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logging.getLogger("sba.kb_rag").warning(
            "[RAG-知识库|main.route_doc_rag_files|kb_list_files|硬编执行|超时] "
            "timeout_sec=%s; page=%s; size=%s",
            _RAG_API_TIMEOUT_SEC,
            page,
            size,
        )
        return {
            "ok": True,
            "degraded": True,
            "error": "rag_files_timeout",
            "files": [],
            "total": 0,
            "page": page,
            "list_chunk_sum": 0,
        }
    files = (payload or {}).get("files") or []
    total = len(files)
    start = (page - 1) * size
    list_sum = sum(int(f.get("chunk_count") or 0) for f in files)
    return {
        "ok": True,
        "files": files[start : start + size],
        "total": total,
        "page": page,
        "list_chunk_sum": list_sum,
        "chunk_agg_ms": (payload or {}).get("chunk_agg_ms"),
        "chunk_count_source": (payload or {}).get("chunk_count_source"),
    }


@app.post("/api/doc/rag/sync-chunk-counts")
def route_doc_rag_sync_chunk_counts():
    return kb_sync_chunk_counts()


@app.get("/api/doc/rag/persisted")
def route_doc_rag_persisted():
    """磁盘持久化层清单（file_records / file_cache / vector_index），不连 Milvus。"""
    return kb_persisted_inventory()


@app.post("/api/doc/rag/rebuild-catalog")
async def route_doc_rag_rebuild_catalog(request: Request):
    """从本地 file_cache + vector_index 重建 file_records；可选 reindex 写入 Milvus。"""
    reindex = False
    try:
        body = await request.json()
        if isinstance(body, dict):
            reindex = bool(body.get("reindex_milvus"))
    except Exception:
        pass
    return kb_rebuild_catalog_from_persisted(reindex_milvus=reindex)


@app.get("/api/doc/rag/file/text")
def route_doc_rag_file_text(path: str = Query(""), limit: int = Query(50000)):
    """读知识库文本：源文件优先，否则 vector_index 切片正文拼接。"""
    return kb_read_persisted_text(path, limit=limit)


@app.post("/api/doc/process")
async def route_doc_process(request: Request):
    body = await request.json()
    path_raw = (body.get("path") or "").strip()
    if not path_raw:
        raise HTTPException(400, "缺少 path")
    p = Path(path_raw).resolve()
    if not p.is_file():
        raise HTTPException(400, "路径无效或不是可读文件")
    export_txt = bool(body.get("export_txt", False))
    summarize = bool(body.get("summarize", body.get("enable_summary", False)))
    try:
        from .services.multimodal_export import export_multimodal_document

        slice_method = (body.get("slice_method") or body.get("chunk_mode") or "auto").strip()
        max_tokens = int(body.get("max_tokens") or 350)
        overlap = int(body.get("overlap") or 40)
        return export_multimodal_document(
            str(p),
            export_txt=export_txt,
            summarize=summarize,
            slice_method=slice_method,
            chunk_mode=slice_method,
            max_tokens=max_tokens,
            overlap=overlap,
        )
    except Exception as e:
        return {
            "ok": False,
            "doc_type": "",
            "text": "",
            "chunks": [],
            "chunk_stats": {"mode": body.get("slice_method") or body.get("chunk_mode") or "auto", "count": 0, "error": str(e)},
            "error": str(e),
            "file_path": str(p),
            "file_size": 0,
            "processing_time": 0.0,
            "md_path": "",
            "txt_path": "",
            "summarized": False,
            "export_txt": export_txt,
        }


@app.post("/api/doc/flowchart/score")
async def route_doc_flowchart_score(request: Request):
    """多模态 Web：PDF/图片 → 流程图 CV 分块 + 泳道横切 + 几何得分 + 叠图。"""
    body = await request.json()
    path_raw = (body.get("path") or "").strip()
    if not path_raw:
        raise HTTPException(400, "缺少 path")
    from .services.flowchart_scoring_service import run_flowchart_score

    return run_flowchart_score(
        path_raw,
        page=int(body.get("page") or 1),
        zoom=float(body.get("zoom") or 2.0),
        mineru_json=(body.get("mineru_json") or "").strip(),
        column_band_split=bool(body.get("column_band_split", True)),
        column_bands=int(body.get("column_bands") if body.get("column_bands") is not None else 0),
        min_band_h=int(body.get("min_band_h") or 48),
        skip_arrows=bool(body.get("skip_arrows", True)),
        artifact_subdir=(body.get("job_id") or "").strip(),
    )


@app.post("/api/doc/upload")
async def route_doc_upload(file: UploadFile = File(...)):
    """浏览器上传文件到服务端 output/mm_uploads，返回绝对路径供 /api/doc/process 使用。"""
    raw_name = Path(file.filename or "upload.bin").name
    if not raw_name or raw_name in (".", ".."):
        raise HTTPException(400, "无效文件名")
    suf = Path(raw_name).suffix.lower()
    if suf not in _MM_UPLOAD_SUFFIXES:
        raise HTTPException(400, f"不支持的扩展名: {suf or '(无)'}")
    try:
        _MM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(500, f"无法创建上传目录: {e}") from e
    data = await file.read()
    if len(data) > _MM_UPLOAD_MAX_BYTES:
        raise HTTPException(400, f"文件超过上限 {_MM_UPLOAD_MAX_BYTES // (1024 * 1024)}MB")
    safe = "".join(c for c in raw_name if c.isalnum() or c in "._- ()[]")[:200] or "file.bin"
    dest = (_MM_UPLOAD_DIR / f"{uuid.uuid4().hex[:14]}_{safe}").resolve()
    try:
        dest.write_bytes(data)
    except OSError as e:
        raise HTTPException(500, f"写入失败: {e}") from e
    return {"ok": True, "path": str(dest), "name": safe, "size": len(data)}


@app.post("/api/doc/rag/upload")
async def route_doc_rag_upload(
    file: UploadFile = File(...),
    relative_path: str = Form(""),
):
    """浏览器选择本地文件夹后上传 Markdown/文本到 output/kb_uploads，再入库向量库。"""
    rel = (relative_path or file.filename or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        rel = Path(file.filename or "upload.md").name
    parts: list[str] = []
    for p in rel.split("/"):
        if not p or p == ".":
            continue
        if p == "..":
            raise HTTPException(400, "非法相对路径")
        parts.append(p)
    if not parts:
        raise HTTPException(400, "无效文件名")
    rel = "/".join(parts)
    suf = Path(rel).suffix.lower()
    if suf not in _KB_UPLOAD_SUFFIXES:
        raise HTTPException(400, f"仅支持 {', '.join(sorted(_KB_UPLOAD_SUFFIXES))}，当前: {suf or '(无)'}")
    try:
        _KB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(500, f"无法创建上传目录: {e}") from e
    data = await file.read()
    if len(data) > _KB_UPLOAD_MAX_BYTES:
        raise HTTPException(400, f"文件超过上限 {_KB_UPLOAD_MAX_BYTES // (1024 * 1024)}MB")
    dest = (_KB_UPLOAD_DIR / rel).resolve()
    root = _KB_UPLOAD_DIR.resolve()
    if not str(dest).startswith(str(root)):
        raise HTTPException(400, "非法路径")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except OSError as e:
        raise HTTPException(500, f"写入失败: {e}") from e
    return {
        "ok": True,
        "path": str(dest),
        "name": dest.name,
        "relative_path": rel,
        "size": len(data),
    }


@app.get("/api/doc/rag/metadata/options")
def route_doc_rag_metadata_options():
    return kb_metadata_options()


@app.get("/api/doc/rag/file/detail")
def route_doc_rag_file_detail(path: str = Query("")):
    if not (path or "").strip():
        raise HTTPException(400, "缺少 path")
    return kb_file_detail(path)


@app.post("/api/doc/rag/metadata/auto")
async def route_doc_rag_metadata_auto(request: Request):
    body = await request.json()
    fp = (body.get("path") or "").strip()
    if not fp:
        raise HTTPException(400, "缺少 path")
    return kb_auto_metadata(fp, mode=body.get("mode") or "rule")


@app.get("/api/doc/rag/file/chunks")
def route_doc_rag_file_chunks(path: str = Query(""), limit: int = Query(30)):
    if not (path or "").strip():
        raise HTTPException(400, "缺少 path")
    return kb_file_chunks(path, limit=limit)


@app.put("/api/doc/rag/file/metadata")
async def route_doc_rag_file_metadata(request: Request):
    body = await request.json()
    fp = (body.get("path") or "").strip()
    if not fp:
        raise HTTPException(400, "缺少 path")
    meta = body.get("metadata")
    if not isinstance(meta, dict):
        raise HTTPException(400, "缺少 metadata 对象")
    return kb_update_file_metadata(fp, meta)


@app.post("/api/doc/rag/add-file")
async def route_doc_rag_add(request: Request):
    body = await request.json()
    return kb_add_file(
        body.get("path", ""),
        body.get("slice_method") or body.get("chunk_mode") or "auto",
        metadata=body.get("metadata"),
    )


@app.post("/api/doc/rag/add-folder")
async def route_doc_rag_add_folder(request: Request):
    body = await request.json()
    return kb_add_folder(
        body.get("path", ""),
        body.get("extensions", ".md,.txt,.markdown"),
        body.get("slice_method") or body.get("chunk_mode") or "auto",
        granularity=bool(body.get("granularity", False)),
    )


@app.post("/api/doc/rag/import-local-path")
async def route_doc_rag_import_local_path(request: Request):
    """服务端白名单路径批量导入，并按大/中/小粒度自动标注元数据。"""
    from .services.kb_rag import kb_import_local_folder
    from .services.kb_granularity_metadata import DEFAULT_INTERVIEW_IMPORT_PATH

    body = await request.json()
    path = (body.get("path") or DEFAULT_INTERVIEW_IMPORT_PATH).strip()
    return kb_import_local_folder(
        path,
        extensions=body.get("extensions") or ".md,.txt,.markdown",
        slice_method=body.get("slice_method") or body.get("chunk_mode") or "auto",
        granularity=body.get("granularity", True) is not False,
    )


@app.get("/api/doc/rag/metadata/vocabulary")
def route_doc_rag_metadata_vocabulary():
    from .services.rag_recall_filter import kb_metadata_vocabulary

    return {"ok": True, "vocabulary": kb_metadata_vocabulary()}


@app.post("/api/doc/rag/recall/propose")
async def route_doc_rag_recall_propose(request: Request):
    from .services.rag_recall_filter import propose_rag_filter_form

    body = await request.json()
    q = (body.get("query") or "").strip()
    if not q:
        raise HTTPException(400, "缺少 query")
    return {
        "ok": True,
        **propose_rag_filter_form(
            q,
            slot_snapshot=body.get("slot_snapshot") if isinstance(body.get("slot_snapshot"), dict) else None,
            enhancement_snapshot=body.get("enhancement_snapshot")
            if isinstance(body.get("enhancement_snapshot"), dict)
            else None,
        ),
    }


@app.post("/api/doc/rag/search")
async def route_doc_rag_search(request: Request):
    """语义检索（Milvus + 嵌入模型）；供知识库页与联调使用。"""
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "缺少 query")
    top_k = int(body.get("top_k") or 5)
    top_k = max(1, min(top_k, 20))
    filt = body.get("metadata_filter") if isinstance(body.get("metadata_filter"), dict) else None
    hits = kb_search(query, top_k=top_k, metadata_filter=filt)
    return {"ok": True, "query": query, "count": len(hits or []), "hits": hits or [], "metadata_filter": filt or {}}


@app.delete("/api/doc/rag/delete")
async def route_doc_rag_delete(request: Request):
    body = await request.json()
    return {"ok": True, "message": f"RAG 删除: {body.get('path', '')}"}


# ═══════════════════════════════════════════════════════════════════
# PAGE 5: Redis缓存
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/cache/query")
def route_cache_query(
    artifact: str = Query(""), source: str = Query(""),
    keyword: str = Query(""), group: str = Query(""),
    limit: int = Query(300),
):
    return cache_query(artifact=artifact, source=source, keyword=keyword, group=group, limit=limit)


@app.get("/api/cache/entry/{eid}")
def route_cache_entry(eid: str):
    entry = cache_get_entry(eid)
    if not entry:
        raise HTTPException(404, "条目不存在")
    return {"entry": entry}


@app.put("/api/cache/entry/{eid}")
async def route_cache_update(eid: str, request: Request):
    body = await request.json()
    try:
        return cache_update_entry(eid, body.get("data"))
    except LookupError:
        raise HTTPException(404, "条目不存在")


@app.post("/api/cache/entry")
async def route_cache_create(request: Request):
    body = await request.json()
    return cache_create_entry(
        artifact_name=body.get("artifact_name", ""),
        source=body.get("source", ""),
        producer=body.get("producer", ""),
        task_key=body.get("task_key", ""),
        data=body.get("data"),
    )


@app.post("/api/cache/export")
async def route_cache_export(request: Request):
    body = await request.json()
    tk = body.get("task_key", "").strip()
    if not tk:
        raise HTTPException(400, "task_key 不能为空")
    return cache_export_by_task(tk)


# ═══════════════════════════════════════════════════════════════════
# PAGE 6: Agent配置
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/settings/gateway-nodes")
def route_settings_gw_nodes():
    return {"nodes": get_gateway_nodes()}


@app.post("/api/settings/gateway-nodes/upsert")
async def route_settings_gw_upsert(request: Request):
    body = await request.json()
    return upsert_gateway_node(body)


@app.delete("/api/settings/gateway-nodes/{nid}")
def route_settings_gw_delete(nid: str):
    return delete_gateway_node(nid)


@app.post("/api/settings/gateway-nodes/reorder")
async def route_settings_gw_reorder(request: Request):
    body = await request.json()
    cfg = load_config()
    cfg["api_gateway_nodes"] = body.get("nodes", [])
    save_config(cfg)
    return {"ok": True}


@app.get("/api/settings/agent-routing")
def route_settings_ar():
    return {"rules": get_agent_routing()}


@app.post("/api/settings/agent-routing/save")
async def route_settings_ar_save(request: Request):
    body = await request.json()
    return save_agent_routing(body.get("rules", {}))


@app.get("/api/settings/workflow-instructions/{ak}")
def route_settings_wf(ak: str):
    return get_agent_prompt(ak)


@app.post("/api/settings/workflow-instructions/{ak}")
async def route_settings_wf_save(ak: str, request: Request):
    body = await request.json()
    return save_agent_prompt(ak, body.get("fields", {}))


@app.get("/api/settings/meta-extract-schema")
def route_meta_extract_schema(lib: str = Query("", description="知识库 ID，留空则返回默认字段")):
    """链接沉淀可配置元数据提取结构；支持从知识库 metadata_json 一键转换。"""
    from .link_meta_extract import DEFAULT_META_EXTRACT_FIELDS, fields_from_kb_metadata_json, normalize_meta_extract_fields
    from .config import load_config

    cfg = load_config()
    fields = normalize_meta_extract_fields(cfg.get("meta_extract_fields"))
    if lib.strip():
        try:
            from .services.rag_libraries import get_active_id, list_libraries

            libs = list_libraries()
            target = lib.strip()
            row = next((x for x in libs if x.get("id") == target), None)
            if not row and not target:
                aid = get_active_id()
                row = next((x for x in libs if x.get("id") == aid), None)
            if row:
                fields = fields_from_kb_metadata_json(str(row.get("metadata_json") or "{}"))
        except Exception:
            fields = list(DEFAULT_META_EXTRACT_FIELDS)
    return {
        "ok": True,
        "enabled": bool(cfg.get("meta_extract_enabled", True)),
        "fields": fields,
        "default_fields": DEFAULT_META_EXTRACT_FIELDS,
    }


@app.get("/api/settings/agents-md/{ak}")
def route_settings_md_get(ak: str):
    return get_agent_md(ak)


@app.post("/api/settings/agents-md/{ak}")
async def route_settings_md_save(ak: str, request: Request):
    body = await request.json()
    return save_agent_md(ak, body.get("content", ""))


@app.get("/api/settings/ai-config")
def route_settings_ai_config():
    cfg = {}
    if _AI_CHAT_CFG_PATH.exists():
        try:
            cfg = json.loads(_AI_CHAT_CFG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"config": cfg, "path": str(_AI_CHAT_CFG_PATH)}


@app.post("/api/settings/ai-config")
async def route_settings_ai_save(request: Request):
    body = await request.json()
    _AI_CHAT_CFG_PATH.write_text(json.dumps(body.get("config", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@app.get("/api/settings/link-pipeline-prefs")
def route_settings_link_pipeline_prefs_get():
    from .services.config import get_link_pipeline_prefs

    return get_link_pipeline_prefs()


@app.post("/api/settings/link-pipeline-prefs")
async def route_settings_link_pipeline_prefs_save(request: Request):
    from .services.config import save_link_pipeline_prefs

    body = await request.json()
    return save_link_pipeline_prefs(body if isinstance(body, dict) else {})


@app.get("/api/settings/feishu")
def route_settings_feishu():
    return feishu_get_config()


@app.post("/api/settings/feishu/save")
async def route_settings_feishu_save(request: Request):
    body = await request.json()
    return feishu_save_config(body)


@app.post("/api/feishu/events/webhook")
async def route_feishu_events_webhook(request: Request):
    """飞书事件订阅回调（群消息推送触发，无需轮询）。"""
    body = await request.json()
    result = feishu_handle_event(body)
    if "challenge" in result:
        return {"challenge": result["challenge"]}
    return result


@app.get("/api/settings/feishu/messages")
def route_settings_feishu_messages(limit: int = Query(50, ge=1, le=200)):
    return {"messages": feishu_list_records(limit=limit)}


@app.get("/api/settings/feishu/status")
def route_settings_feishu_status():
    return event_status()


@app.get("/api/settings/im-robots/platforms")
def route_im_robots_platforms():
    return im_robot_list_platforms()


@app.get("/api/settings/im-robots/wechat")
def route_im_robots_wechat_get():
    return im_robot_get_wechat()


@app.post("/api/settings/im-robots/wechat/save")
async def route_im_robots_wechat_save(request: Request):
    body = await request.json()
    return im_robot_save_wechat(body)


@app.post("/api/settings/im-robots/wechat/qr/start")
async def route_im_robots_wechat_qr_start():
    return await im_robot_wechat_qr_start()


@app.get("/api/settings/im-robots/wechat/qr/poll")
async def route_im_robots_wechat_qr_poll(session_id: str = Query(...)):
    return await im_robot_wechat_qr_poll(session_id)


@app.post("/api/settings/im-robots/wechat/refresh-status")
async def route_im_robots_wechat_refresh():
    return await im_robot_wechat_refresh_status()


@app.post("/api/settings/im-robots/wechat/disconnect")
async def route_im_robots_wechat_disconnect():
    return await im_robot_wechat_disconnect()


@app.post("/api/im-robots/wechat/inbound")
async def route_im_robots_wechat_inbound(request: Request):
    body = await request.json()
    return await im_robot_wechat_inbound(body)


@app.get("/api/settings/thread-config")
def route_settings_thread():
    cfg = load_config()
    return {
        "max_workers": cfg.get("max_workers", 8),
        "llm_workers": cfg.get("llm_workers", 256),
        "background_workers": cfg.get("background_workers", 256),
        "system_workers": cfg.get("system_workers", 4),
        "rag_workers": cfg.get("rag_workers", 2),
        "whisper_pool_core_size": cfg.get("whisper_pool_core_size", 4),
        "whisper_pool_size": cfg.get("whisper_pool_size", 16),
        "mineru_workers": cfg.get("mineru_workers", 2),
        "queue_max_size": cfg.get("queue_max_size", 50),
    }


@app.post("/api/settings/thread-config/save")
async def route_settings_thread_save(request: Request):
    body = await request.json()
    cfg = load_config()
    for key in (
        "max_workers",
        "llm_workers",
        "background_workers",
        "system_workers",
        "rag_workers",
        "whisper_pool_core_size",
        "whisper_pool_size",
        "mineru_workers",
        "queue_max_size",
    ):
        if key in body and body[key] is not None:
            try:
                cfg[key] = int(body[key])
            except (TypeError, ValueError):
                pass
    save_config(cfg)
    return {"ok": True}


# ─── MD 输出模板配置 ───
@app.get("/api/settings/template")
def route_settings_template():
    cfg = load_config()
    return {
        "output_template": cfg.get("output_template", ""),
        "file_naming_rule": cfg.get("file_naming_rule", ""),
        "description": "可用占位符: {platform} {datetime} {link} {transcript} {summary} {title} {article} {url_hash}",
    }


@app.post("/api/settings/template/save")
async def route_settings_template_save(request: Request):
    body = await request.json()
    cfg = load_config()
    if "output_template" in body:
        cfg["output_template"] = body["output_template"]
    if "file_naming_rule" in body:
        cfg["file_naming_rule"] = body["file_naming_rule"]
    save_config(cfg)
    return {"ok": True}


# ─── HTML 长页生成配置 ───
@app.get("/api/settings/html-config")
def route_settings_html():
    cfg = load_config()
    return {
        "longpage_html_enabled": cfg.get("longpage_html_enabled", True),
        "longpage_html_max_bytes": cfg.get("longpage_html_max_bytes", 20 * 1024 * 1024),
        "longpage_html_timeout_sec": cfg.get("longpage_html_timeout_sec", 60),
        "longpage_html_async_timeout_sec": cfg.get("longpage_html_async_timeout_sec", 600),
        "longpage_html_async_diagram_pipeline": cfg.get("longpage_html_async_diagram_pipeline", True),
    }


@app.post("/api/settings/html-config/save")
async def route_settings_html_save(request: Request):
    body = await request.json()
    cfg = load_config()
    for k in ("longpage_html_enabled", "longpage_html_max_bytes", "longpage_html_timeout_sec",
              "longpage_html_async_timeout_sec", "longpage_html_async_diagram_pipeline"):
        if k in body:
            cfg[k] = body[k]
    save_config(cfg)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════
# PAGE 7: OPS运维
# ═══════════════════════════════════════════════════════════════════
@app.get("/api/eval/status")
def route_eval_status():
    """Eval 接入状态：Tracing、包安装、最近跑批（不探测外网）。"""
    from app.eval.ops_service import eval_extended_status

    return {"ok": True, "data": eval_extended_status()}


@app.get("/api/eval/overview")
def route_eval_overview():
    from app.eval.ops_service import eval_get_overview

    return eval_get_overview()


@app.get("/api/eval/references")
def route_eval_references():
    from app.eval.ops_service import eval_get_references

    return eval_get_references()


@app.get("/api/eval/traces/recent")
def route_eval_traces_recent(limit: int = Query(50, ge=1, le=200), scope: str = Query("all")):
    from app.eval.ops_service import eval_list_traces

    return eval_list_traces(limit=limit, scope=scope)


@app.get("/api/eval/rag/status")
def route_eval_rag_status():
    from app.eval.ops_service import eval_rag_status

    return eval_rag_status()


@app.post("/api/eval/trajectory/strict")
async def route_eval_trajectory_strict(request: Request):
    """轨迹 strict 匹配（agentevals）；body: outputs, reference_outputs 消息数组。"""
    from app.eval.trajectory_eval import evaluate_trajectory_strict

    body = await request.json()
    out = body.get("outputs") or []
    ref = body.get("reference_outputs") or []
    if not isinstance(out, list) or not isinstance(ref, list):
        raise HTTPException(400, "outputs / reference_outputs 须为数组")
    return evaluate_trajectory_strict(out, ref)


@app.post("/api/eval/trajectory/run")
async def route_eval_trajectory_run(request: Request):
    """轨迹评测：支持 strict / unordered / subset / superset。"""
    from app.eval.ops_service import eval_run_trajectory

    body = await request.json()
    out = body.get("outputs") or []
    ref = body.get("reference_outputs") or []
    mode = body.get("mode") or "strict"
    if not isinstance(out, list) or not isinstance(ref, list):
        raise HTTPException(400, "outputs / reference_outputs 须为数组")
    return eval_run_trajectory(out, ref, mode=mode)


@app.post("/api/eval/trajectory/from-span/{task_id}")
async def route_eval_trajectory_from_span(task_id: str, request: Request):
    """从 SPAN 任务生成轨迹并评测（可选 reference_id / reference_outputs）。"""
    from app.eval.ops_service import eval_trajectory_from_span

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    return eval_trajectory_from_span(
        task_id,
        reference_id=body.get("reference_id") or "",
        reference_outputs=body.get("reference_outputs"),
        mode=body.get("mode") or "strict",
    )


@app.get("/api/eval/manifest")
def route_eval_manifest():
    """测试集规模、金标/BadCase 分层说明（manifest.json）。"""
    from app.eval.ops_service import eval_get_manifest

    return eval_get_manifest()


@app.get("/api/eval/baseline")
def route_eval_baseline():
    """基线→目标→当前指标（baseline_targets.json）。"""
    from app.eval.ops_service import eval_get_baseline

    return eval_get_baseline()


@app.get("/api/eval/gate-rubric")
def route_eval_gate_rubric():
    """GATE 规则 + RUBRIC 维度定义。"""
    from app.eval.ops_service import eval_get_gate_rubric_schema

    return eval_get_gate_rubric_schema()


@app.post("/api/eval/offline/run")
async def route_eval_offline_run(request: Request):
    """RegEval 离线跑批：检索 + GATE + RUBRIC（RAGAS 需 SBA_RAG_EVAL_ENABLED=1）。"""
    from app.eval.ops_service import eval_run_offline

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    return eval_run_offline(
        dataset_path=body.get("dataset_path") or "",
        include_ragas=bool(body.get("include_ragas")),
    )


@app.post("/api/eval/retrieval/metrics")
async def route_eval_retrieval_metrics(request: Request):
    """对 JSON 数组批量算 Recall/Precision/Hit/MRR/nDCG/Pass@K。"""
    from app.eval.ops_service import eval_retrieval_metrics

    body = await request.json()
    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise HTTPException(400, "rows 须为数组")
    k_list = body.get("k_list")
    return eval_retrieval_metrics(rows, k_list=k_list)


@app.post("/api/eval/gate/batch")
async def route_eval_gate_batch(request: Request):
    """批量 GATE 硬规则校验。"""
    from app.eval.ops_service import eval_gate_batch

    body = await request.json()
    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise HTTPException(400, "rows 须为数组")
    return eval_gate_batch(rows)


# ── 用户反馈（打分 + 意图准确率） ──

@app.post("/api/chat/feedback")
async def route_chat_feedback(request: Request):
    """提交 AI 回答反馈：1-5 星打分 + 意图点赞/踩 + 纠正意图。"""
    from .services.chat_feedback import save_feedback, INTENT_LABELS, MODE_LABELS

    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}
    sid = str(body.get("session_id") or "").strip()
    idx = int(body.get("message_index") or 0)
    if not sid:
        raise HTTPException(400, "缺少 session_id")
    rating_raw = body.get("rating")
    rating = max(1, min(5, int(rating_raw))) if rating_raw is not None else None
    intent_liked = body.get("intent_liked")
    if intent_liked is not None:
        intent_liked = bool(intent_liked)
    corrected_intent = str(body.get("corrected_intent") or "").strip() or None if "corrected_intent" in body else None
    corrected_intent_label = str(body.get("corrected_intent_label") or "").strip() or None if "corrected_intent_label" in body else None
    comment = body.get("comment") if "comment" in body else None
    row = save_feedback(
        session_id=sid,
        message_index=idx,
        rating=rating,
        intent_liked=intent_liked,
        detected_intent=body.get("detected_intent") if isinstance(body.get("detected_intent"), dict) else None,
        corrected_intent=corrected_intent,
        corrected_intent_label=corrected_intent_label,
        comment=str(comment)[:500] if comment is not None else None,
        user_id=str(getattr(request.state, "user_id", "") or ""),
    )
    return {"ok": True, "feedback": row}


@app.get("/api/chat/feedback/analytics")
async def route_chat_feedback_analytics(days: int = 30, full: bool = False):
    """意图识别准确率统计；full=true 时返回完整看板指标。"""
    if full:
        from .services.feedback_analytics import compute_feedback_dashboard

        return compute_feedback_dashboard(days=max(1, min(365, days)))
    from .services.chat_feedback import compute_intent_accuracy

    return compute_intent_accuracy(days=max(1, min(365, days)))


@app.get("/api/chat/feedback/session")
async def route_chat_feedback_session(session_id: str = ""):
    """按会话批量加载反馈（前端回显）。"""
    from .services.chat_feedback import list_feedback_for_session

    sid = str(session_id or "").strip()
    if not sid:
        raise HTTPException(400, "缺少 session_id")
    return {"ok": True, "items": list_feedback_for_session(sid)}


@app.get("/api/chat/intent-alternatives")
async def route_chat_intent_alternatives(
    session_id: str = "",
    message_index: int = 0,
    retrieval_terms: str = "",
):
    """意图纠偏备选（内置 + LLM 推测）。"""
    from .services.intent_suggest import build_intent_alternatives
    from .services.chat_feedback import get_feedback
    from .services.chat_session_store import get_session_document

    sid = str(session_id or "").strip()
    idx = int(message_index or 0)
    fb = get_feedback(sid, idx) if sid else None
    detected = (fb or {}).get("detected_intent") or {}
    detected_label = str(detected.get("domain") or detected.get("mode") or "通用")
    detected_code = str(detected.get("domain_code") or detected_label)
    question = ""
    answer = ""
    if sid:
        try:
            doc = get_session_document(sid) or {}
            msgs = doc.get("messages") or []
            user_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") == "user"]
            asst_msgs = [m for m in msgs if isinstance(m, dict) and m.get("role") == "assistant"]
            if idx < len(asst_msgs):
                answer = str(asst_msgs[idx].get("content") or "")
            if user_msgs:
                question = str(user_msgs[min(idx, len(user_msgs) - 1)].get("content") or "")
        except Exception:
            pass
    terms = [t.strip() for t in (retrieval_terms or "").split(",") if t.strip()]
    return build_intent_alternatives(
        question=question,
        answer=answer,
        detected_intent=detected_code,
        detected_label=detected_label,
        retrieval_terms=terms,
        include_llm=True,
    )


@app.get("/api/settings/pipeline-config")
def route_settings_pipeline_config_get():
    from .services.pipeline_llm import get_pipeline_llm, pipeline_settings, probe_ollama_health

    node = get_pipeline_llm()
    health = probe_ollama_health()
    cfg = pipeline_settings()
    return {
        "ok": True,
        "ollama_base_url": cfg["ollama_base_url"],
        "ollama_model": cfg["ollama_model"],
        "pipeline_gateway_llm_fallback": cfg["pipeline_gateway_llm_fallback"],
        "pipeline_concurrency": cfg["pipeline_concurrency"],
        "pipeline_timeout_sec": cfg["pipeline_timeout_sec"],
        "ollama_configured": bool(node),
        "health": health,
    }


@app.put("/api/settings/pipeline-config")
async def route_settings_pipeline_config_put(request: Request):
    import os

    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}
    if "ollama_model" in body:
        model = str(body.get("ollama_model") or "").strip()
        if model:
            os.environ["OLLAMA_MODEL"] = model
    if "ollama_base_url" in body:
        url = str(body.get("ollama_base_url") or "").strip()
        if url:
            os.environ["OLLAMA_BASE_URL"] = url
    if "pipeline_concurrency" in body:
        os.environ["OLLAMA_PIPELINE_CONCURRENCY"] = str(max(1, min(32, int(body.get("pipeline_concurrency") or 4))))
    return route_settings_pipeline_config_get()


@app.get("/api/chat/feedback/list")
async def route_chat_feedback_list(
    limit: int = 100,
    offset: int = 0,
    rating_min: Optional[int] = None,
    rating_max: Optional[int] = None,
    intent_liked: Optional[bool] = None,
    keyword: str = "",
):
    """反馈列表（管理页用）。"""
    from .services.chat_feedback import list_all_feedback

    return {
        "items": list_all_feedback(
            limit=min(500, max(1, limit)),
            offset=max(0, offset),
            rating_min=rating_min,
            rating_max=rating_max,
            intent_liked=intent_liked,
            keyword=keyword.strip(),
        ),
    }


@app.get("/api/ops/observability/overview")
def route_ops_overview():
    return ops_get_overview()


@app.get("/api/ops/observability/events")
def route_ops_events(limit: int = Query(120)):
    return ops_get_events(limit)


@app.get("/api/ops/spans/tasks")
def route_ops_span_tasks(limit: int = Query(80, ge=1, le=200)):
    return ops_list_span_tasks(limit=limit)


@app.get("/api/ops/spans/tasks/{task_id}")
def route_ops_span_task_detail(task_id: str):
    return ops_get_span_task_detail(task_id)


@app.get("/api/ops/spans/exceptions")
def route_ops_span_exceptions(limit: int = Query(100, ge=1, le=300)):
    return ops_list_span_exceptions(limit=limit)


@app.post("/api/ops/events")
async def route_ops_add(request: Request):
    body = await request.json()
    return ops_add_event(
        method=body.get("method", "POST"),
        path=body.get("path", "/"),
        status_code=body.get("status_code", 200),
        cost_ms=body.get("cost_ms", 0),
    )


@app.get("/api/ops/scheduled-jobs/events")
def route_ops_scheduled_job_events(limit: int = Query(100, ge=1, le=500)):
    from .services.ops import ops_get_scheduled_job_events

    return ops_get_scheduled_job_events(limit=limit)


@app.get("/api/ops/exception-catalog")
def route_ops_exception_catalog():
    from .services.ops_exception_proposals import list_exception_catalog

    return list_exception_catalog()


@app.get("/api/ops/exception-proposals")
def route_ops_exception_proposals(error: str = Query(""), context: str = Query("")):
    from .services.ops_exception_proposals import get_proposal_for_error

    return get_proposal_for_error(error, context=context)


@app.get("/api/ops/dashboard")
def route_ops_dashboard():
    return ops_get_dashboard()


@app.get("/api/ops/agent/status")
def route_ops_agent_status():
    return ops_get_status()


@app.get("/api/ops/memory")
def route_ops_memory(limit: int = Query(30, ge=1, le=100)):
    return ops_get_memory(limit)


@app.get("/api/ops/reports")
def route_ops_reports(limit: int = Query(40, ge=1, le=200)):
    return ops_list_reports(limit)


@app.get("/api/ops/reports/{report_id}")
def route_ops_report_detail(report_id: str):
    return ops_get_report(report_id)


@app.get("/api/ops/daily-stats")
def route_ops_daily_stats():
    return ops_get_daily_stats()


@app.post("/api/ops/analyze-logs")
async def route_ops_analyze_logs(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    use_llm = bool(body.get("use_llm", True))
    return ops_analyze_logs(use_llm=use_llm)


@app.post("/api/ops/monitor")
async def route_ops_monitor(request: Request):
    body = await request.json()
    return ops_monitor_task(
        link=body.get("link", ""),
        task_id=body.get("task_id", ""),
        status=body.get("status", "failed"),
        logs=body.get("logs"),
        error_info=body.get("error_info"),
    )


@app.post("/api/ops/route/mark-failed")
async def route_ops_mark(request: Request):
    body = await request.json()
    return ops_route_action("mark-failed", body)


@app.post("/api/ops/route/reconfigure")
async def route_ops_reconfig(request: Request):
    body = await request.json()
    return ops_route_action("reconfigure", body)


@app.get("/api/ops/route/suggestions")
def route_ops_suggest():
    return ops_get_suggestions()


@app.post("/api/ops/route/rollback-last")
async def route_ops_rollback(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    return ops_route_action("rollback-last", body)


# ─── WebReplay 浏览器自动化（脚本库 / 扩展桥接）───
from .services.webreplay_store import (
    append_run as webreplay_append_run,
    delete_script as webreplay_delete_script,
    export_scripts as webreplay_export_scripts,
    get_bridge as webreplay_get_bridge,
    get_script as webreplay_get_script,
    import_scripts as webreplay_import_scripts,
    list_scripts as webreplay_list_scripts,
    save_bridge as webreplay_save_bridge,
    upsert_script as webreplay_upsert_script,
)


def _webreplay_user_id(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    return str(uid) if uid else "anonymous"


@app.get("/api/webreplay/health")
def route_webreplay_health():
    return {"ok": True, "service": "webreplay", "storage": "local-json"}


@app.get("/api/webreplay/scripts")
def route_webreplay_list(request: Request):
    return {"scripts": webreplay_list_scripts(_webreplay_user_id(request))}


@app.get("/api/webreplay/scripts/{script_id}")
def route_webreplay_get(script_id: str, request: Request):
    row = webreplay_get_script(_webreplay_user_id(request), script_id)
    if not row:
        raise HTTPException(404, "脚本不存在")
    return {"script": row}


@app.post("/api/webreplay/scripts")
async def route_webreplay_create(request: Request):
    body = await request.json()
    row = webreplay_upsert_script(_webreplay_user_id(request), body)
    return {"ok": True, "script": row}


@app.put("/api/webreplay/scripts/{script_id}")
async def route_webreplay_update(script_id: str, request: Request):
    body = await request.json()
    body["id"] = script_id
    row = webreplay_upsert_script(_webreplay_user_id(request), body)
    return {"ok": True, "script": row}


@app.delete("/api/webreplay/scripts/{script_id}")
def route_webreplay_delete(script_id: str, request: Request):
    ok = webreplay_delete_script(_webreplay_user_id(request), script_id)
    if not ok:
        raise HTTPException(404, "脚本不存在")
    return {"ok": True}


@app.post("/api/webreplay/scripts/import")
async def route_webreplay_import(request: Request):
    body = await request.json()
    try:
        return webreplay_import_scripts(_webreplay_user_id(request), body)
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/webreplay/scripts/export/all")
def route_webreplay_export(request: Request):
    return webreplay_export_scripts(_webreplay_user_id(request))


@app.get("/api/webreplay/bridge")
def route_webreplay_bridge_get(request: Request):
    return webreplay_get_bridge(_webreplay_user_id(request))


@app.post("/api/webreplay/bridge")
async def route_webreplay_bridge_save(request: Request):
    body = await request.json()
    return webreplay_save_bridge(_webreplay_user_id(request), body)


@app.post("/api/webreplay/runs")
async def route_webreplay_run_log(request: Request):
    body = await request.json()
    script_id = str(body.get("scriptId") or "")
    if not script_id:
        raise HTTPException(400, "缺少 scriptId")
    webreplay_append_run(_webreplay_user_id(request), script_id, body)
    return {"ok": True}


from .services.webreplay_cdp import (
    cdp_status as webreplay_cdp_status,
    get_cdp_recording_status,
    media_file_path as webreplay_media_file_path,
    run_cdp_replay,
    start_cdp_recording,
    stop_cdp_recording,
)


@app.get("/api/webreplay/cdp/status")
def route_webreplay_cdp_status():
    return webreplay_cdp_status()


@app.post("/api/webreplay/cdp/record/start")
async def route_webreplay_cdp_record_start(request: Request):
    body = await request.json()
    name = str(body.get("name") or "").strip()
    tab_url_hint = str(body.get("tabUrlHint") or body.get("tab_url_hint") or "").strip()
    result = start_cdp_recording(_webreplay_user_id(request), name=name, tab_url_hint=tab_url_hint)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "启动录制失败")
    return result


@app.get("/api/webreplay/cdp/record/{session_id}")
def route_webreplay_cdp_record_poll(session_id: str, request: Request):
    result = get_cdp_recording_status(_webreplay_user_id(request), session_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error") or "会话不存在")
    return result


@app.post("/api/webreplay/cdp/record/{session_id}/stop")
def route_webreplay_cdp_record_stop(session_id: str, request: Request):
    uid = _webreplay_user_id(request)
    result = stop_cdp_recording(uid, session_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "停止录制失败")
    script = result.get("script") or {}
    saved = webreplay_upsert_script(uid, script)
    result["script"] = saved
    return result


@app.post("/api/webreplay/cdp/replay/{script_id}")
def route_webreplay_cdp_replay(script_id: str, request: Request):
    uid = _webreplay_user_id(request)
    row = webreplay_get_script(uid, script_id)
    if not row:
        raise HTTPException(404, "脚本不存在")
    result = run_cdp_replay(row)
    webreplay_append_run(
        uid,
        script_id,
        {
            "status": result.get("status") or ("success" if result.get("ok") else "failed"),
            "error": result.get("error"),
            "failedAtStep": result.get("failedAtStep"),
            "doneSteps": result.get("doneSteps"),
            "totalSteps": result.get("totalSteps"),
            "elapsedMs": result.get("elapsedMs"),
            "via": "cdp",
        },
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "重放失败")
    return result


@app.get("/api/webreplay/media/{session_id}/{filename}")
def route_webreplay_media(session_id: str, filename: str, request: Request):
    from fastapi.responses import FileResponse

    path = webreplay_media_file_path(_webreplay_user_id(request), session_id, filename)
    if not path:
        raise HTTPException(404, "媒体文件不存在")
    return FileResponse(path, media_type="image/png")


# ─── RSS 订阅阅读 ───
from .services.rss_reader import (
    add_feed as rss_add_feed,
    delete_feed as rss_delete_feed,
    export_opml as rss_export_opml,
    import_opml as rss_import_opml,
    list_feeds as rss_list_feeds,
    list_items as rss_list_items,
    rss_stats as rss_stats_fn,
    set_item_read as rss_set_item_read,
    set_item_starred as rss_set_item_starred,
    sync_all_feeds as rss_sync_all,
    sync_feed as rss_sync_feed,
    enqueue_item_document as rss_enqueue_item_document,
)
from .services.rss_scheduler import get_scheduler_status as rss_scheduler_status


def _rss_user_id(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    return str(uid) if uid else "anonymous"


@app.get("/api/rss/health")
def route_rss_health():
    return {"ok": True, "service": "rss", "storage": "local-json"}


@app.get("/api/rss/stats")
def route_rss_stats(request: Request):
    return rss_stats_fn(_rss_user_id(request))


@app.get("/api/rss/feeds")
def route_rss_list_feeds(request: Request):
    return {"feeds": rss_list_feeds(_rss_user_id(request))}


@app.post("/api/rss/feeds")
async def route_rss_add_feed(request: Request):
    body = await request.json()
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "缺少 url")
    try:
        feed = rss_add_feed(_rss_user_id(request), url)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True, "feed": feed}


@app.delete("/api/rss/feeds/{feed_id}")
def route_rss_delete_feed(feed_id: str, request: Request):
    ok = rss_delete_feed(_rss_user_id(request), feed_id)
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@app.post("/api/rss/feeds/{feed_id}/sync")
def route_rss_sync_one(feed_id: str, request: Request):
    try:
        feed = rss_sync_feed(_rss_user_id(request), feed_id)
    except ValueError as ex:
        msg = str(ex)
        code = 404 if "不存在" in msg else 400
        raise HTTPException(code, msg)
    return {"ok": True, "feed": feed}


@app.post("/api/rss/feeds/{feed_id}/sync/stream")
async def route_rss_sync_one_stream(feed_id: str, request: Request):
    """RSS 单源同步 SSE：阶段性展示拉取/解析/映射本地文档（与 AI 问答 thought_step 对齐）。"""
    from .services.rss_sync_stream import stream_sync_feed

    uid = _rss_user_id(request)

    async def gen():
        try:
            async for chunk in stream_sync_feed(uid, feed_id):
                yield chunk
        except Exception as ex:
            yield f"event: error\ndata: {json.dumps({'ok': False, 'error': str(ex)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/rss/sync")
def route_rss_sync_all(request: Request):
    return rss_sync_all(_rss_user_id(request))


@app.post("/api/rss/sync/stream")
async def route_rss_sync_all_stream(request: Request):
    """RSS 全部订阅源同步 SSE。"""
    from .services.rss_sync_stream import stream_sync_all_feeds

    uid = _rss_user_id(request)

    async def gen():
        try:
            async for chunk in stream_sync_all_feeds(uid):
                yield chunk
        except Exception as ex:
            yield f"event: error\ndata: {json.dumps({'ok': False, 'error': str(ex)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/rss/items")
def route_rss_items(
    request: Request,
    feed_id: str = Query("", alias="feed_id"),
    unread_only: bool = Query(False),
    starred_only: bool = Query(False),
    q: str = Query("", description="标题/摘要关键词"),
):
    return {
        "items": rss_list_items(
            _rss_user_id(request),
            feed_id=feed_id,
            unread_only=unread_only,
            starred_only=starred_only,
            query=q,
        )
    }


@app.post("/api/rss/items/{item_id}/read")
async def route_rss_item_read(item_id: str, request: Request):
    body = await request.json()
    read = bool(body.get("read", True))
    try:
        item = rss_set_item_read(_rss_user_id(request), item_id, read=read)
    except ValueError as ex:
        raise HTTPException(404, str(ex))
    return {"ok": True, "item": item}


@app.post("/api/rss/items/{item_id}/star")
async def route_rss_item_star(item_id: str, request: Request):
    body = await request.json()
    starred = bool(body.get("starred", True))
    try:
        item = rss_set_item_starred(_rss_user_id(request), item_id, starred=starred)
    except ValueError as ex:
        raise HTTPException(404, str(ex))
    return {"ok": True, "item": item}


@app.get("/api/rss/opml/export")
def route_rss_opml_export(request: Request):
    from fastapi.responses import Response

    xml_text = rss_export_opml(_rss_user_id(request))
    return Response(content=xml_text, media_type="application/xml; charset=utf-8")


@app.post("/api/rss/opml/import")
async def route_rss_opml_import(request: Request):
    body = await request.json()
    content = str(body.get("content") or body.get("opml") or "")
    try:
        result = rss_import_opml(_rss_user_id(request), content)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True, **result}


@app.get("/api/rss/scheduler/status")
def route_rss_scheduler_status():
    return rss_scheduler_status()


@app.post("/api/rss/items/{item_id}/document")
async def route_rss_item_document(item_id: str, request: Request):
    """RSS 文章全文抓取 → 摘要 MD → 链接沉淀任务卡片。"""
    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user_prompt = str((body or {}).get("user_prompt") or "")
    uid = _rss_user_id(request)
    try:
        meta = rss_enqueue_item_document(uid, item_id, user_prompt=user_prompt)
    except ValueError as ex:
        msg = str(ex)
        code = 404 if "不存在" in msg else 400
        raise HTTPException(code, msg) from ex

    from .services.pipeline_scheduler import request_pipeline_task_async
    from .services.rss_article_pipeline import process_rss_article_pipeline

    task_id = str(meta.get("task_id") or "")
    rss_iid = str(meta.get("item_id") or item_id)

    async def _run_rss_doc() -> None:
        await request_pipeline_task_async(
            task_id,
            lambda: process_rss_article_pipeline(
                task_id,
                rss_item_id=rss_iid,
                user_id=uid,
            ),
        )

    asyncio.create_task(_run_rss_doc())
    return {"ok": True, **meta}


# ═══════════════════════════════════════════════════════════════════
# 文本阅读器 + 辅助阅读 Agent
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/reader/import-local")
async def route_reader_import_local(request: Request):
    """本地 MD/TXT 导入 output 目录，与任务产物相同方式打开预览。"""
    from .services.output_file_io import import_local_text_to_output

    body = await request.json()
    name = (body.get("name") or body.get("file") or "local.md").strip()
    content = body.get("content")
    if content is None:
        raise HTTPException(400, "缺少 content")
    try:
        return import_local_text_to_output(name, str(content))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


def _reader_user_id(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    return str(uid or "default")


@app.get("/api/reader/recent")
def route_reader_recent_list(request: Request):
    """最近打开的 MD 列表（服务端持久化，按磁盘 mtime 降序）。"""
    from .services.reader_recent_store import list_recent

    return {"ok": True, "items": list_recent(_reader_user_id(request))}


@app.put("/api/reader/recent")
async def route_reader_recent_replace(request: Request):
    """合并客户端缓存与服务端最近列表（全量合并，不丢项）。"""
    from .services.reader_recent_store import replace_recent

    body = await request.json()
    items = body.get("items") if isinstance(body, dict) else None
    if items is not None and not isinstance(items, list):
        raise HTTPException(400, "items 须为数组")
    merged = replace_recent(_reader_user_id(request), items or [])
    return {"ok": True, "items": merged}


@app.post("/api/reader/recent/touch")
async def route_reader_recent_touch(request: Request):
    """记录打开文件：合并写入，mtime 取磁盘修改时间。"""
    from .services.reader_recent_store import touch_recent

    body = await request.json()
    file_name = (body.get("file") or body.get("name") or "").strip()
    if not file_name:
        raise HTTPException(400, "缺少 file")
    opened_at = body.get("opened_at")
    try:
        opened_ms = int(opened_at) if opened_at is not None else None
    except (TypeError, ValueError):
        opened_ms = None
    try:
        items = touch_recent(_reader_user_id(request), file_name, opened_at=opened_ms)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "items": items}


@app.get("/api/reader/recent/stat")
def route_reader_recent_stat(file: str = Query(..., description="output 目录内 basename")):
    """查询 output 文件磁盘修改时间（毫秒）。"""
    from .services.reader_recent_store import stat_recent_file

    try:
        return stat_recent_file(file)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/reader/sessions/{doc_id}")
def route_reader_session_get(doc_id: str):
    from .services.reader_session_store import get_session

    did = (doc_id or "").strip()
    if not did:
        raise HTTPException(400, "doc_id 不能为空")
    return get_session(did)


@app.put("/api/reader/sessions/{doc_id}")
async def route_reader_session_put(doc_id: str, request: Request):
    from .services.reader_session_store import upsert_session, flush_session

    did = (doc_id or "").strip()
    if not did:
        raise HTTPException(400, "doc_id 不能为空")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body 须为对象")
    messages = body.get("messages")
    if messages is not None and not isinstance(messages, list):
        raise HTTPException(400, "messages 须为数组")
    row = upsert_session(
        did,
        doc_name=str(body.get("doc_name") or "").strip(),
        messages=messages,
        prefs=body.get("prefs") if isinstance(body.get("prefs"), dict) else None,
    )
    flush_session(did)
    return {"ok": True, "session": row}


@app.get("/api/reader/agent-config")
def route_reader_agent_config():
    from .services.reader_agent import get_agent_config_payload

    return get_agent_config_payload()


@app.post("/api/reader/chat/stream")
async def route_reader_chat_stream(request: Request):
    from .services.reader_agent import stream_reader_chat
    from .services.reader_session_store import maybe_periodic_flush

    maybe_periodic_flush()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(400, "body 须为 JSON 对象")
    doc_id = str(body.get("doc_id") or "").strip()
    doc_name = str(body.get("doc_name") or "").strip()
    doc_text = str(body.get("doc_text") or "")
    message = str(body.get("message") or "").strip()
    if not doc_id:
        raise HTTPException(400, "缺少 doc_id")
    if not message:
        raise HTTPException(400, "缺少 message")
    if not doc_text.strip():
        raise HTTPException(400, "缺少 doc_text（请先打开文档）")

    async def gen():
        try:
            async for chunk in stream_reader_chat(
                doc_id=doc_id,
                doc_name=doc_name,
                doc_text=doc_text,
                message=message,
                rag_prefetch=bool(body.get("rag_prefetch", False)),
                web_search=bool(body.get("web_search", False)),
                deep_think=bool(body.get("deep_think", False)),
                model=str(body.get("model") or "").strip() or None,
            ):
                yield chunk
        except Exception as ex:
            from .services.chat_error_handler import stream_user_error_sse

            async for ev in stream_user_error_sse(ex, session_id=doc_id, stage="阅读器问答"):
                yield ev

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# SPA Frontend Routes (must be after all API routes)
@app.get("/{path:path}")
def spa_catch_all(path: str, request: Request):
    """Vue.js SPA fallback - return index.html for non-API routes"""
    # 注意：api/、assets/、vendor/、preview/ 路径应该由其他路由处理
    # 如果走到这里，说明这些路径没有匹配到任何路由，返回404
    file_path = FRONTEND_DIR / path
    if file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))
    if (FRONTEND_DIR / "index.html").exists():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
    raise HTTPException(404)
