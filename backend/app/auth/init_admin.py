"""首次启动初始化：创建管理员用户 + Casbin 默认策略。"""
from __future__ import annotations

import logging
import os

from sqlalchemy.exc import IntegrityError

from . import enforcer as _enf
from .user_service import change_password, create_user, get_user_by_username

_log = logging.getLogger("sba.auth.init")

DEFAULT_ADMIN = "admin"
# 首次建库时的默认口令（仅用于本地/演示；生产环境请登录后立即修改）
DEFAULT_ADMIN_PASSWORD = "admin"

# 默认 RBAC 策略
DEFAULT_POLICIES = [
    # 角色继承: admin 继承 viewer 所有权限
    ("g", "admin", "viewer"),
    # viewer: 全部 API 只读
    ("p", "viewer", "/api/*", "(GET|HEAD|OPTIONS)"),
    # viewer: AI 问答与会话写操作（SSE 为 POST /api/chat/stream）
    ("p", "viewer", "/api/chat/*", "(POST|PUT|PATCH|DELETE)"),
    # viewer: RSS 订阅管理（添加/同步/删除）
    ("p", "viewer", "/api/rss/*", "(POST|PUT|PATCH|DELETE)"),
    # admin: 全部功能
    ("p", "admin", "/api/*", ".*"),
]


def _seed_policies():
    """写入默认策略（幂等）。"""
    e = _enf.get_enforcer()
    existing_p = {(p[0], p[1], p[2]) for p in e.get_policy() if len(p) >= 3}
    existing_g = {(g[0], g[1], g[2]) for g in e.get_grouping_policy() if len(g) >= 3}
    for item in DEFAULT_POLICIES:
        ptype = item[0]
        if ptype == "p":
            sub, obj, act = item[1], item[2], item[3]
            if (sub, obj, act) not in existing_p:
                e.add_policy(sub, obj, act)
                _log.info("Casbin 策略写入: p, %s, %s, %s", sub, obj, act)
        elif ptype == "g":
            sub, obj = item[1], item[2]
            if (sub, obj, "") not in existing_g:
                e.add_grouping_policy(sub, obj)
                _log.info("Casbin 角色继承写入: g, %s, %s", sub, obj)


def ensure_default_admin_row() -> bool:
    """
    幂等：确保 rbac_user 中存在 admin / DEFAULT_ADMIN_PASSWORD。
    与 Casbin 解耦，避免因 Casbin 连库失败导致「账号不存在」。
    返回 True 表示本次新建了用户。
    """
    if get_user_by_username(DEFAULT_ADMIN):
        return False
    try:
        create_user(
            username=DEFAULT_ADMIN,
            password=DEFAULT_ADMIN_PASSWORD,
            nickname="系统管理员",
        )
        _log.warning(
            "已写入默认管理员: 用户名=%s 密码=%s",
            DEFAULT_ADMIN,
            DEFAULT_ADMIN_PASSWORD,
        )
        return True
    except IntegrityError:
        _log.info("admin 用户已存在（并发或重复初始化）")
        return False


def init_admin_user() -> str | None:
    """
    启动时：保证 admin 用户行存在，再尽力写入 Casbin。
    若本次新建 admin，返回默认密码；否则返回 None。
    """
    created = ensure_default_admin_row()
    existing = get_user_by_username(DEFAULT_ADMIN)
    if not existing:
        _log.error(
            "默认管理员仍不存在，请检查数据库连接与 SBA_DATABASE_URL（需可写）"
        )
        return None

    flag = os.environ.get("SBA_RESET_ADMIN_PASSWORD", "").strip().lower()
    if flag in ("1", "true", "yes"):
        change_password(existing.id, DEFAULT_ADMIN_PASSWORD)
        _log.warning(
            "SBA_RESET_ADMIN_PASSWORD 已生效：admin 口令已重置为默认值；请移除该变量并重启"
        )

    try:
        _seed_policies()
        e = _enf.get_enforcer()
        if "admin" not in e.get_roles_for_user(existing.id):
            e.add_role_for_user(existing.id, "admin")
            _log.info("补写 admin 角色关联: user=%s", existing.id)
    except Exception as exc:
        _log.exception(
            "Casbin 初始化失败（仍可用 admin 登录，接口权限走 JWT 角色兜底）: %s",
            exc,
        )

    if created:
        banner = (
            "\n"
            + "=" * 60
            + "\n"
            + "  SuperBizAgent 管理员账号已创建\n"
            + "  ─────────────────────────────\n"
            + f"  用户名:  {DEFAULT_ADMIN}\n"
            + f"  密码:    {DEFAULT_ADMIN_PASSWORD}\n"
            + f"  用户ID:  {existing.id}\n"
            + "  ─────────────────────────────\n"
            + "  生产环境请登录后立即修改密码。\n"
            + "=" * 60
        )
        print(banner)
        _log.warning("管理员默认密码为固定演示值，生产环境请尽快修改")

    return DEFAULT_ADMIN_PASSWORD if created else None


def ensure_auth_ready():
    """确保认证模块就绪：建表 + 种子数据。"""
    from .auth_models import get_engine

    get_engine()  # 建表
    init_admin_user()
