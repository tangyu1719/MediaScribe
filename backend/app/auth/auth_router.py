"""认证路由：登录、注册、验证码、登出、当前用户。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from .dependencies import create_token, get_current_user
from .enforcer import add_role_for_user
from .user_service import (
    change_password,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_login_identifier,
    get_user_by_phone,
    get_user_by_username,
    to_dict,
    update_profile,
    user_exists,
    verify_password,
)
from .init_admin import DEFAULT_ADMIN, ensure_default_admin_row
from .verify_code_service import (
    can_send,
    generate_code,
    send_email_code,
    send_sms_code,
    verify_code,
)

_log = logging.getLogger("sba.auth.router")
router = APIRouter(prefix="/api/auth", tags=["auth"])

# ─── Request models ───


class LoginRequest(BaseModel):
    login_type: str = "password"  # password | sms
    identifier: str = ""  # 用户名 / 手机号 / 邮箱
    credential: str = ""  # 密码 或 短信验证码


class SendCodeRequest(BaseModel):
    target: str  # 手机号 或 邮箱
    code_type: str = "email"  # sms | email
    purpose: str = "login"  # login | register


class RegisterRequest(BaseModel):
    email: str
    password: str
    code: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ProfileUpdateRequest(BaseModel):
    nickname: str = ""
    phone: str = ""


class UserPortraitUpdateRequest(BaseModel):
    """个人画像字段（写入 user.md + portrait.json）。"""

    display_name: str = ""
    timezone: str = ""
    occupation: str = ""
    tech_stack: str = ""
    communication_style: str = ""
    interests_projects: str = ""
    notes: str = ""
    language_pref: str = ""


# ─── Routes ───


@router.post("/login")
def route_login(body: LoginRequest):
    """统一登录：支持密码和短信验证码两种方式。"""
    identifier = (body.identifier or "").strip()
    if not identifier:
        raise HTTPException(400, "请输入用户名/手机号/邮箱")

    user = get_user_by_login_identifier(identifier)
    if not user and identifier.strip().lower() == DEFAULT_ADMIN:
        # 启动时 Casbin/其它步骤失败可能导致未写入 admin；登录前再幂等补一行
        ensure_default_admin_row()
        user = get_user_by_login_identifier(identifier)
    if not user:
        raise HTTPException(400, "账号不存在")

    if user.status != 1:
        raise HTTPException(403, "账号已被禁用")

    if body.login_type == "sms":
        # 短信验证码登录：identifier 应为手机号
        ok, msg, _ = verify_code(
            user.phone or identifier, "sms", "login", body.credential
        )
        if not ok:
            raise HTTPException(400, msg)
    else:
        if not verify_password(body.credential, user.password_hash):
            raise HTTPException(400, "密码错误")

    from .enforcer import get_roles_for_user
    roles = get_roles_for_user(user.id)
    if not roles:
        roles = ["admin" if user.username == "admin" else "viewer"]

    token = create_token(user.id, roles)
    _log.info("用户登录 user=%s roles=%s", user.username, roles)
    return {"access_token": token, "user": {**to_dict(user), "roles": roles}}


@router.post("/send-code")
def route_send_code(body: SendCodeRequest):
    """发送验证码（邮箱或短信）。"""
    target = (body.target or "").strip()
    if not target:
        raise HTTPException(400, "请输入手机号或邮箱")

    allowed, msg = can_send(target)
    if not allowed:
        raise HTTPException(429, msg)

    code = generate_code(target, body.code_type, body.purpose)

    if body.code_type == "email":
        send_email_code(target, code)
    else:
        send_sms_code(target, code)

    return {"ok": True, "message": "验证码已发送"}


@router.post("/register")
def route_register(body: RegisterRequest):
    """邮箱注册：验证码 + 邮箱 + 密码。"""
    email = (body.email or "").strip().lower()
    password = (body.password or "").strip()
    code = (body.code or "").strip()

    if not email or "@" not in email:
        raise HTTPException(400, "请输入有效的邮箱地址")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if not code:
        raise HTTPException(400, "请输入验证码")

    if get_user_by_email(email):
        raise HTTPException(400, "该邮箱已被注册")

    ok, msg, _ = verify_code(email, "email", "register", code)
    if not ok:
        raise HTTPException(400, msg)

    # 从邮箱提取默认用户名
    username = email.split("@")[0]
    base = username
    counter = 1
    while user_exists(username):
        username = f"{base}{counter}"
        counter += 1

    user = create_user(username=username, password=password, email=email)
    add_role_for_user(user.id, "viewer")

    token = create_token(user.id, ["viewer"])
    _log.info("用户注册 user=%s email=%s", username, email)
    return {"access_token": token, "user": {**to_dict(user), "roles": ["viewer"]}}


@router.get("/me")
def route_me(request: Request):
    """获取当前登录用户信息。"""
    payload = get_current_user(request)
    user_id = payload["sub"]
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    from .enforcer import get_roles_for_user
    roles = get_roles_for_user(user_id)
    if not roles:
        roles = ["admin" if user.username == "admin" else "viewer"]
    return {"user": {**to_dict(user), "roles": roles}}


@router.patch("/profile")
def route_profile_update(body: ProfileUpdateRequest, request: Request):
    """更新当前用户个人信息（昵称、绑定手机号）。"""
    payload = get_current_user(request)
    user_id = payload["sub"]
    ok, err = update_profile(user_id, nickname=body.nickname, phone=body.phone)
    if not ok:
        raise HTTPException(400, err)
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    from .enforcer import get_roles_for_user
    roles = get_roles_for_user(user_id)
    if not roles:
        roles = ["admin" if user.username == "admin" else "viewer"]
    return {"ok": True, "user": {**to_dict(user), "roles": roles}}


@router.get("/user-portrait")
def route_user_portrait_get(request: Request):
    """读取当前用户的个人画像（表单字段 + 已生成的 Markdown）。"""
    from ..services.user_portrait import build_user_md, load_portrait, load_user_md_text, user_md_path

    payload = get_current_user(request)
    user_id = payload["sub"]
    fields = load_portrait(user_id)
    md = load_user_md_text(user_id).strip() or build_user_md(fields)
    return {
        "ok": True,
        "fields": fields,
        "markdown": md,
        "path_hint": str(user_md_path(user_id)),
    }


@router.put("/user-portrait")
def route_user_portrait_put(body: UserPortraitUpdateRequest, request: Request):
    """保存个人画像：落盘 portrait.json 与 user.md。"""
    from ..services.user_portrait import save_portrait

    payload = get_current_user(request)
    user_id = payload["sub"]
    patch = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    fields, md = save_portrait(user_id, patch)
    return {"ok": True, "fields": fields, "markdown": md}


@router.post("/logout")
def route_logout():
    """登出（JWT 无状态，前端清除 token 即可；后续可扩展 Redis 黑名单）。"""
    return {"ok": True, "message": "已登出"}


@router.post("/change-password")
def route_change_password(body: ChangePasswordRequest, request: Request):
    """修改当前用户密码。"""
    payload = get_current_user(request)
    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(404, "用户不存在")
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    change_password(user.id, body.new_password)
    return {"ok": True, "message": "密码已修改"}
