"""Lightweight FastAPI entry point for link processing and Markdown reading.

Run this module with ``uvicorn app.lite_main:app``.  It deliberately reuses
the proven link-processing routes from ``app.main`` while replacing the full
application's startup lifecycle.  AI chat warm-up, RAG health checks, RSS and
subscription schedulers, browser automation, skill backfill, and Whisper
warm-up are therefore never started.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse


# Set these before importing the full route module.  The flags are also useful
# to services that may be imported lazily by the link-processing pipeline.
os.environ["SBA_LITE_MODE"] = "1"
os.environ["SUBSCRIPTION_SCHEDULER_ENABLED"] = "0"
os.environ["FAVORITES_SCHEDULER_ENABLED"] = "0"
os.environ["RSS_SCHEDULER_ENABLED"] = "0"
os.environ["SKILL_AUTO_SYNC_ON_START"] = "0"
os.environ["SKILL_INTEL_BACKFILL_ON_START"] = "0"

from . import main as _full  # noqa: E402  (environment must be set first)


app = _full.app
app.title = "SuperBizAgent Lite - 链接分析与 MD 识别器"

# The full app registers heavyweight startup hooks.  The lightweight entry
# point keeps only authentication and link queue/history restoration.
app.router.on_startup.clear()
app.router.on_shutdown.clear()


@app.on_event("startup")
def _startup_lite_services() -> None:
    log = logging.getLogger("sba.lite")
    _full.ensure_auth_ready()
    try:
        from .services.history_manager import init_history_persistence
        from .services.task_manager import init_queue_from_history

        history = init_history_persistence()
        restored = init_queue_from_history()
        log.info(
            "lite startup ready; history_enabled=%s; restored_queue=%s; "
            "disabled=chat,rag,rss,schedulers,automation,skill-backfill,whisper-warmup",
            history.get("enabled"),
            restored,
        )
    except Exception as exc:  # local queue fallback remains available
        log.warning("lite history restore skipped: %s", exc)


_ALLOWED_API_PREFIXES = (
    "/api/auth",
    "/api/health",
    "/api/lite/status",
    "/api/link/",
    "/api/process/",
    "/api/tasks/",
    "/api/history",
    "/api/platforms",
    "/api/workflow/selector",
    "/api/output/",
    "/api/doc/export-md",
    "/api/reader/",
    "/api/settings/link-pipeline-prefs",
    "/api/settings/meta-extract-schema",
)

_BLOCKED_READER_PREFIXES = (
    "/api/reader/chat",
    "/api/reader/agent-config",
)


@app.middleware("http")
async def _lite_api_gate(request: Request, call_next):
    """Actively ignore optional feature APIs in lightweight mode."""
    path = request.url.path
    # This endpoint is also the launcher's unauthenticated readiness probe.
    # Return before the full application's authentication middleware.
    if path == "/api/lite/status":
        return JSONResponse(content=_lite_status_payload())
    if path.startswith(_BLOCKED_READER_PREFIXES):
        return JSONResponse(
            status_code=404,
            content={"detail": "轻量模式未启动 Reader AI 对话"},
        )
    if path.startswith("/api/") and not path.startswith(_ALLOWED_API_PREFIXES):
        return JSONResponse(
            status_code=404,
            content={"detail": "该附加功能在轻量模式中已忽略"},
        )
    return await call_next(request)


@app.get("/api/lite/status")
def lite_status():
    return _lite_status_payload()


def _lite_status_payload():
    return {
        "ok": True,
        "mode": "lite",
        "enabled": ["link-analysis", "markdown-reader"],
        "disabled": [
            "ai-chat",
            "rag",
            "rss",
            "subscriptions",
            "scheduled-jobs",
            "browser-automation",
            "skill-backfill",
            "whisper-warmup",
        ],
    }


def _remove_full_spa_routes() -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in ("/", "/{path:path}")
    ]


_remove_full_spa_routes()


@app.get("/", response_class=HTMLResponse)
def lite_index():
    """Serve the existing UI with a tiny client-side lightweight-mode shell."""
    index_path = _full.FRONTEND_DIR / "index.html"
    if not index_path.is_file():
        return HTMLResponse("<h1>SuperBizAgent Lite</h1>", status_code=200)
    html = index_path.read_text(encoding="utf-8")
    marker = (
        '<link rel="stylesheet" href="/assets/css/lite-mode.css?v=1">'
        '<script src="/assets/js/lite-mode.js?v=1" defer></script>'
    )
    html = html.replace("</head>", marker + "</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/{path:path}", response_class=HTMLResponse)
def lite_spa_catch_all(path: str):
    """Keep the reader SPA route while applying the lightweight shell."""
    file_path = _full.FRONTEND_DIR / path
    if file_path.is_file():
        return HTMLResponse(file_path.read_text(encoding="utf-8"))
    return lite_index()
