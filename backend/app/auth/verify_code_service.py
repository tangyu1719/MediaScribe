"""验证码服务：生成、校验、防刷。"""
from __future__ import annotations

import logging
import os
import random
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Optional

from .auth_models import RbacVerifyCode, session_scope

_log = logging.getLogger("sba.auth.verify_code")

CODE_TTL_MINUTES = 5
RATE_LIMIT_SECONDS = 60
MAX_ATTEMPTS = 5
CODE_LENGTH = 6


def _random_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(CODE_LENGTH))


def can_send(target: str) -> tuple[bool, str]:
    """检查是否有发送频率限制。60s 内同目标只能发一次。"""
    cutoff = datetime.utcnow() - timedelta(seconds=RATE_LIMIT_SECONDS)
    with session_scope() as db:
        from sqlalchemy import select

        latest = db.execute(
            select(RbacVerifyCode)
            .where(RbacVerifyCode.target == target)
            .where(RbacVerifyCode.created_at > cutoff)
            .order_by(RbacVerifyCode.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest:
            remain = RATE_LIMIT_SECONDS - int(
                (datetime.utcnow() - latest.created_at).total_seconds()
            )
            return False, f"发送过于频繁，请 {remain} 秒后再试"
    return True, ""


def generate_code(target: str, code_type: str, purpose: str = "login") -> str:
    """生成验证码并写入数据库。"""
    code = _random_code()
    expires_at = datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES)
    with session_scope() as db:
        record = RbacVerifyCode(
            target=target,
            code=code,
            type=code_type,
            purpose=purpose,
            expires_at=expires_at,
            used=False,
            attempts=0,
        )
        db.add(record)
        db.commit()
    _log.info("验证码生成 target=%s type=%s code=%s 过期=%s", target, code_type, code, expires_at)
    return code


def verify_code(
    target: str, code_type: str, purpose: str, code: str
) -> tuple[bool, str, Optional[RbacVerifyCode]]:
    """校验验证码有效性。"""
    with session_scope() as db:
        from sqlalchemy import select

        record = db.execute(
            select(RbacVerifyCode)
            .where(RbacVerifyCode.target == target)
            .where(RbacVerifyCode.type == code_type)
            .where(RbacVerifyCode.purpose == purpose)
            .where(RbacVerifyCode.used == False)
            .order_by(RbacVerifyCode.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not record:
            return False, "未找到有效验证码，请重新获取", None

        if datetime.utcnow() > record.expires_at:
            return False, "验证码已过期，请重新获取", None

        record.attempts += 1

        if record.attempts > MAX_ATTEMPTS:
            record.used = True  # 作废
            db.commit()
            return False, "验证码尝试次数过多，已作废", None

        if record.code != code.strip():
            db.commit()
            remain = MAX_ATTEMPTS - record.attempts
            return False, f"验证码错误，剩余尝试 {remain} 次", None

        record.used = True
        db.commit()
        return True, "验证通过", record


def send_email_code(email: str, code: str) -> bool:
    """发送邮件验证码。未配置 SMTP 时打印到日志。"""
    smtp_host = (os.environ.get("SBA_SMTP_HOST") or "").strip()
    if not smtp_host:
        _log.warning("SMTP 未配置，验证码已输出到日志: email=%s code=%s", email, code)
        return True  # 开发模式，返回成功

    smtp_port = int(os.environ.get("SBA_SMTP_PORT") or "587")
    smtp_user = (os.environ.get("SBA_SMTP_USER") or "").strip()
    smtp_pass = (os.environ.get("SBA_SMTP_PASS") or "").strip()
    smtp_from = (os.environ.get("SBA_SMTP_FROM") or smtp_user).strip()
    smtp_tls = os.environ.get("SBA_SMTP_TLS", "1").strip() not in ("0", "false", "no")

    msg = MIMEText(
        f"您的 SuperBizAgent 验证码是：{code}\n"
        f"有效期 {CODE_TTL_MINUTES} 分钟，请勿泄露给他人。",
        "plain",
        "utf-8",
    )
    msg["Subject"] = "SuperBizAgent 验证码"
    msg["From"] = smtp_from
    msg["To"] = email

    try:
        if smtp_tls:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, [email], msg.as_string())
        server.quit()
        _log.info("邮件验证码已发送 email=%s", email)
        return True
    except Exception as e:
        _log.error("邮件发送失败 email=%s err=%s", email, e)
        return False


def send_sms_code(phone: str, code: str) -> bool:
    """短信验证码（暂未开通运营商服务，仅打印日志）。"""
    _log.warning("SMS 服务暂未开通，验证码: phone=%s code=%s", phone, code)
    return True
