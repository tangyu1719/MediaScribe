"""FastAPI 依赖注入：获取当前用户、角色校验。"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from jose import JWTError, jwt

# JWT 密钥：优先环境变量；否则持久化到 output/.sba_jwt_secret，避免每次重启后端导致已登录 token 全部失效
_JWT_SECRET_FILE = Path(__file__).resolve().parents[3] / "output" / ".sba_jwt_secret"


def _load_or_create_jwt_secret() -> str:
    env = (os.environ.get("SBA_JWT_SECRET") or "").strip()
    if env:
        return env
    try:
        _JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _JWT_SECRET_FILE.exists():
            txt = _JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except Exception:
        pass
    key = secrets.token_urlsafe(48)
    try:
        _JWT_SECRET_FILE.write_text(key, encoding="utf-8")
    except Exception:
        pass
    return key


SECRET_KEY = _load_or_create_jwt_secret()
if not (os.environ.get("SBA_JWT_SECRET") or "").strip():
    os.environ["SBA_JWT_SECRET"] = SECRET_KEY

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24


def create_token(user_id: str, roles: list[str]) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def get_token_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def get_current_user(request: Request) -> dict:
    """从请求中提取 JWT 并返回 payload。未认证抛出 401。"""
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(401, "未登录，请先登录")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(401, "登录已过期，请重新登录")
    return payload


def get_current_user_or_none(request: Request) -> Optional[dict]:
    """从请求中提取 JWT，不强制要求登录。"""
    token = get_token_from_request(request)
    if not token:
        return None
    return decode_token(token)
