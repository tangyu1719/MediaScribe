"""FastAPI 认证中间件：JWT 校验 + Casbin RBAC 鉴权（纯 ASGI，不缓冲 StreamingResponse）。"""
from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import parse_qs

from .dependencies import decode_token
from .enforcer import enforce

_log = logging.getLogger("sba.auth.middleware")

# 仅管理员可访问（含 GET，避免普通用户读取密钥与内部提示词）
_ADMIN_ONLY_PREFIXES = (
    "/api/settings/gateway-nodes",
    "/api/settings/agent-routing",
    "/api/settings/workflow-instructions",
    "/api/settings/agents-md",
    "/api/settings/template",
    "/api/settings/html-config",
    "/api/settings/ai-config",
    "/api/settings/thread-config",
    "/api/settings/feishu",
    "/api/settings/im-robots",
    "/api/settings/test-connection",
)


def _is_admin_only_path(path: str) -> bool:
    return any(path.startswith(p) for p in _ADMIN_ONLY_PREFIXES)


# 无需认证的路径前缀
_WHITELIST_PREFIXES = (
    "/api/health",
    "/api/auth/",
    "/api/im-robots/wechat/inbound",
    "/api/feishu/events/webhook",
    "/assets/",
    "/vendor/",
    "/preview/",
    "/output/",  # 产物 MD/HTML 静态直链，任务卡片点击无需再鉴权
    "/favicon",
)

# 精确白名单（根路径 + 登录页 + 所有前端 SPA 页面路由；鉴权由前端 RBAC 遮罩/API 401 处理）
_SPA_PAGE_PATHS = (
    "/video",
    "/orch",
    "/chat",
    "/tasks",
    "/agpz",
    "/rag",
    "/rss",
    "/multimodal",
    "/cache",
    "/ops",
    "/profile",
    "/settings",
    "/iag",
    "/subscribe",
    "/webreplay",
    "/reader",
)
_WHITELIST_EXACT = frozenset({"/", "/login.html", *_SPA_PAGE_PATHS})


def _is_whitelisted(path: str) -> bool:
    if path in _WHITELIST_EXACT:
        return True
    if path == "/output" or path.startswith("/output/"):
        return True
    for prefix in _WHITELIST_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _path_bypasses_auth(path: str, method: str) -> bool:
    m = method.upper()
    if path.startswith("/api/output/file") and m in ("GET", "PUT", "POST"):
        return True
    if path.startswith("/api/reader/") and m in ("GET", "PUT", "POST"):
        return True
    if path.startswith("/api/fs/browse") and m == "GET":
        return True
    return False


def _get_primary_role(payload: dict) -> str:
    roles = payload.get("roles", [])
    return roles[0] if roles else "viewer"


def _headers_dict(scope) -> dict[str, str]:
    return {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in (scope.get("headers") or [])
    }


def _token_from_scope(scope) -> Optional[str]:
    headers = _headers_dict(scope)
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    qs = (scope.get("query_string") or b"").decode("latin-1", errors="replace")
    if qs:
        params = parse_qs(qs, keep_blank_values=False)
        rows = params.get("sba_token") or []
        if rows and rows[0]:
            return rows[0]
    return None


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


class AuthMiddleware:
    """纯 ASGI 鉴权中间件，避免 BaseHTTPMiddleware 缓冲 SSE 流。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()

        if _is_whitelisted(path) or _path_bypasses_auth(path, method):
            await self.app(scope, receive, send)
            return

        token = _token_from_scope(scope)
        if not token:
            await _send_json(send, 401, {"detail": "未登录，请先登录"})
            return

        payload = decode_token(token)
        if not payload:
            await _send_json(send, 401, {"detail": "登录已过期，请重新登录"})
            return

        role = _get_primary_role(payload)
        roles = payload.get("roles") or []
        if _is_admin_only_path(path) and "admin" not in roles:
            await _send_json(send, 403, {"detail": "仅管理员可访问服务端内部 Agent 与网关配置"})
            return

        if not enforce(role, path, method):
            _log.warning("权限拒绝 role=%s path=%s method=%s", role, path, method)
            await _send_json(
                send,
                403,
                {"detail": f"权限不足：{role} 无权执行 {method} {path}"},
            )
            return

        state = scope.setdefault("state", {})
        state["user_id"] = payload.get("sub")
        state["user_roles"] = roles
        await self.app(scope, receive, send)
