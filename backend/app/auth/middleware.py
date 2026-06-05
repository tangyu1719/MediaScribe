"""FastAPI 认证中间件：JWT 校验 + Casbin RBAC 鉴权。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .dependencies import decode_token, get_token_from_request
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
    "/webreplay",
)
_WHITELIST_EXACT = frozenset({"/", "/login.html", *_SPA_PAGE_PATHS})


def _is_whitelisted(path: str) -> bool:
    if path in _WHITELIST_EXACT:
        return True
    # /output 挂载点：产物 MD/HTML 直链，任务卡片点击无需鉴权
    if path == "/output" or path.startswith("/output/"):
        return True
    for prefix in _WHITELIST_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _get_primary_role(payload: dict) -> str:
    roles = payload.get("roles", [])
    return roles[0] if roles else "viewer"


def _get_token(request: Request) -> Optional[str]:
    """从 Header 或 Query 参数中提取 Token（兼容 EventSource）。"""
    token = get_token_from_request(request)
    if token:
        return token
    # EventSource 不支持自定义 Header，走 Query 参数兜底
    return request.query_params.get("sba_token")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if _is_whitelisted(path):
            return await call_next(request)

        # MD 预览：output 内正文/标记读写（侧车与 ST3 互通，路径校验在 service 层）
        if path.startswith("/api/output/file") and request.method.upper() in ("GET", "PUT", "POST"):
            return await call_next(request)

        token = _get_token(request)
        if not token:
            return JSONResponse(
                {"detail": "未登录，请先登录"}, status_code=401
            )

        payload = decode_token(token)
        if not payload:
            return JSONResponse(
                {"detail": "登录已过期，请重新登录"}, status_code=401
            )

        role = _get_primary_role(payload)
        method = request.method.upper()

        roles = payload.get("roles") or []
        if _is_admin_only_path(path) and "admin" not in roles:
            return JSONResponse(
                {"detail": "仅管理员可访问服务端内部 Agent 与网关配置"},
                status_code=403,
            )

        if not enforce(role, path, method):
            _log.warning("权限拒绝 role=%s path=%s method=%s", role, path, method)
            return JSONResponse(
                {"detail": f"权限不足：{role} 无权执行 {method} {path}"},
                status_code=403,
            )

        request.state.user_id = payload.get("sub")
        request.state.user_roles = payload.get("roles", [])

        return await call_next(request)
