"""用户 CRUD 服务。"""
from __future__ import annotations

import uuid6
import bcrypt
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from .auth_models import RbacUser, session_scope


def _gen_user_id() -> str:
    return f"user_{uuid6.uuid7().hex}"


def hash_password(raw: str) -> str:
    """使用 bcrypt 原生库，避免 passlib 与 bcrypt>=4 不兼容导致无法建用户。"""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    if not raw or not hashed:
        return False
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_user(
    username: str,
    password: str,
    *,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    nickname: str = "",
) -> RbacUser:
    with session_scope() as db:
        user = RbacUser(
            id=_gen_user_id(),
            username=username,
            password_hash=hash_password(password),
            phone=phone or None,
            email=email or None,
            nickname=nickname or username,
            status=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def get_user_by_id(user_id: str) -> Optional[RbacUser]:
    with session_scope() as db:
        return db.get(RbacUser, user_id)


def get_user_by_username(username: str) -> Optional[RbacUser]:
    with session_scope() as db:
        return db.execute(
            select(RbacUser).where(RbacUser.username == username)
        ).scalar_one_or_none()


def get_user_by_phone(phone: str) -> Optional[RbacUser]:
    with session_scope() as db:
        return db.execute(
            select(RbacUser).where(RbacUser.phone == phone)
        ).scalar_one_or_none()


def get_user_by_email(email: str) -> Optional[RbacUser]:
    with session_scope() as db:
        return db.execute(
            select(RbacUser).where(RbacUser.email == email)
        ).scalar_one_or_none()


def get_user_by_login_identifier(identifier: str) -> Optional[RbacUser]:
    """根据用户名、手机号或邮箱查找用户。"""
    user = get_user_by_username(identifier)
    if user:
        return user
    user = get_user_by_phone(identifier)
    if user:
        return user
    return get_user_by_email(identifier)


def update_profile(user_id: str, *, nickname: str, phone: str) -> tuple[bool, str]:
    """更新昵称与绑定手机号。phone 可为空字符串表示解绑。"""
    nick = (nickname or "").strip()[:64]
    p = (phone or "").strip() or None
    if p and (not p.isdigit() or len(p) != 11 or not p.startswith("1")):
        return False, "手机号须为 11 位中国大陆号码"
    with session_scope() as db:
        user = db.get(RbacUser, user_id)
        if not user:
            return False, "用户不存在"
        if p:
            other = db.execute(
                select(RbacUser).where(RbacUser.phone == p, RbacUser.id != user_id)
            ).scalar_one_or_none()
            if other:
                return False, "该手机号已被其他账号使用"
        user.phone = p
        user.nickname = nick if nick else (user.username or "")
        user.updated_at = datetime.utcnow()
        db.commit()
    return True, ""


def change_password(user_id: str, new_password: str) -> bool:
    with session_scope() as db:
        user = db.get(RbacUser, user_id)
        if not user:
            return False
        user.password_hash = hash_password(new_password)
        user.updated_at = datetime.utcnow()
        db.commit()
        return True


def to_dict(user: RbacUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "phone": user.phone,
        "email": user.email,
        "nickname": user.nickname,
        "avatar_url": user.avatar_url,
        "status": user.status,
        "created_at": user.created_at.isoformat() if user.created_at else "",
    }


def user_exists(username: str) -> bool:
    return get_user_by_username(username) is not None


def count_users() -> int:
    with session_scope() as db:
        from sqlalchemy import func
        return db.query(func.count(RbacUser.id)).scalar() or 0
