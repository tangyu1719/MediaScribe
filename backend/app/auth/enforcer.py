"""Casbin Enforcer 单例，策略存储于 MySQL casbin_rule 表。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import casbin
from casbin_sqlalchemy_adapter import Adapter as CasbinAdapter


_enforcer: Optional[casbin.Enforcer] = None
_DEFAULT_SQLITE = (
    Path(__file__).resolve().parents[2] / "output" / "sba_casbin.sqlite3"
)


def _db_url() -> str:
    u = (os.environ.get("SBA_DATABASE_URL") or "").strip()
    if u:
        return u
    _DEFAULT_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_SQLITE.as_posix()}"


def get_enforcer() -> casbin.Enforcer:
    global _enforcer
    if _enforcer is not None:
        return _enforcer

    url = _db_url()
    adapter = CasbinAdapter(url)
    model_path = str(Path(__file__).resolve().parent / "casbin_model.conf")
    _enforcer = casbin.Enforcer(model_path, adapter)
    _enforcer.load_policy()
    return _enforcer


def enforce(role: str, resource: str, action: str) -> bool:
    """检查指定角色是否有权限执行操作。"""
    return bool(get_enforcer().enforce(role, resource, action))


def add_policy(role: str, resource: str, action: str) -> bool:
    return bool(get_enforcer().add_policy(role, resource, action))


def add_role_inheritance(child_role: str, parent_role: str) -> bool:
    return bool(get_enforcer().add_grouping_policy(child_role, parent_role))


def get_roles_for_user(user_id: str) -> list[str]:
    return get_enforcer().get_roles_for_user(user_id)


def add_role_for_user(user_id: str, role: str) -> bool:
    return bool(get_enforcer().add_role_for_user(user_id, role))
