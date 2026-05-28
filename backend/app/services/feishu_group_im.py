"""飞书群 IM —— 事件订阅推送触发（官方 Webhook，非轮询）。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

from .config import load_config, save_config

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None  # type: ignore

OPEN_API = "https://open.feishu.cn/open-apis"

_seen_msg_ids: Set[str] = set()
_recent_messages: List[Dict[str, Any]] = []
_last_event_at: float = 0.0
_last_event_error: str = ""
_lock = __import__("threading").Lock()


def _log(action: str, **kwargs: Any) -> None:
    parts = "; ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info("[IM机器人-飞书事件|feishu_group_im|chat|Agent执行|%s] %s; %s", action, action, parts)


def _get_app_creds(cfg: Optional[Dict] = None) -> tuple[str, str]:
    c = cfg or load_config()
    return (str(c.get("feishu_app_id") or "").strip(), str(c.get("feishu_app_secret") or "").strip())


def _tenant_access_token(app_id: str, app_secret: str) -> str:
    if not requests:
        raise RuntimeError("未安装 requests")
    if not app_id or not app_secret:
        raise RuntimeError("未配置 feishu_app_id / feishu_app_secret")
    url = f"{OPEN_API}/auth/v3/tenant_access_token/internal"
    r = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg') or data}")
    tok = data.get("tenant_access_token")
    if not tok:
        raise RuntimeError("飞书 token 响应为空")
    return str(tok)


def _extract_text_from_content(content: Any) -> str:
    raw = ""
    if isinstance(content, str):
        raw = content
    elif isinstance(content, dict):
        raw = json.dumps(content, ensure_ascii=False)
    if raw:
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                if "text" in j:
                    return str(j.get("text") or "")
                if "title" in j:
                    return str(j.get("title") or "")
                return json.dumps(j, ensure_ascii=False)
        except Exception:
            pass
    return str(raw or "")


def _decrypt_event(encrypt_key: str, encrypted: str) -> Dict[str, Any]:
    """飞书 Encrypt Key 解密（AES-CBC）。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
    except ImportError as exc:
        raise RuntimeError("解密需要 cryptography 库") from exc
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    buf = base64.b64decode(encrypted)
    iv, cipher_bytes = buf[:16], buf[16:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(cipher_bytes) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plain.decode("utf-8"))


def _verify_token(body: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    expected = (cfg.get("feishu_verification_token") or "").strip()
    if not expected:
        return True
    token = str(body.get("token") or body.get("header", {}).get("token") or "").strip()
    return token == expected


def _append_message(row: Dict[str, Any]) -> bool:
    global _last_event_at, _last_event_error
    msg_id = str(row.get("message_id") or "").strip()
    if not msg_id or msg_id in _seen_msg_ids:
        return False
    _seen_msg_ids.add(msg_id)
    if len(_seen_msg_ids) > 5000:
        _seen_msg_ids.clear()
        _seen_msg_ids.update({m["message_id"] for m in _recent_messages[-500:]})
    with _lock:
        _recent_messages.append(row)
        if len(_recent_messages) > 500:
            del _recent_messages[:-500]
    _last_event_at = time.time()
    _last_event_error = ""
    return True


def _should_accept_chat(chat_id: str, cfg: Dict[str, Any]) -> bool:
    target = (cfg.get("feishu_group_chat_id") or "").strip()
    if not target:
        return True
    return chat_id == target


def _parse_im_message_event(event: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not cfg.get("feishu_group_trigger_enabled"):
        return None
    message = event.get("message") if isinstance(event.get("message"), dict) else event
    if not isinstance(message, dict):
        return None
    chat_id = str(message.get("chat_id") or "").strip()
    if not chat_id or not _should_accept_chat(chat_id, cfg):
        return None
    chat_type = str(message.get("chat_type") or "")
    if chat_type and chat_type not in ("group", "topic_group"):
        return None
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    if isinstance(sender, dict):
        stype = str(sender.get("sender_type") or "").lower()
        if stype in ("app", "bot"):
            return None
    msg_id = str(message.get("message_id") or "").strip()
    text = _extract_text_from_content(message.get("content"))
    sender_id = ""
    if isinstance(sender, dict):
        sid = sender.get("sender_id")
        if isinstance(sid, dict):
            sender_id = str(sid.get("open_id") or sid.get("user_id") or "")
        else:
            sender_id = str(sid or "")
    try:
        cts = int(message.get("create_time") or 0)
    except Exception:
        cts = int(time.time() * 1000)
    return {
        "message_id": msg_id,
        "create_time": cts,
        "text": text,
        "sender_id": sender_id,
        "chat_id": chat_id,
        "source": "event",
    }


def _auto_reply_async(row: Dict[str, Any]) -> None:
    """后台线程：LLM 生成回复并发送到群（避免阻塞 Webhook 响应）。"""
    cfg = load_config()
    if not cfg.get("feishu_im_auto_reply"):
        return
    text = (row.get("text") or "").strip()
    chat_id = (row.get("chat_id") or "").strip()
    if not text or not chat_id:
        return
    try:
        import sys
        from pathlib import Path

        here = Path(__file__).resolve()
        for p in here.parents:
            cand = p / "src" / "agent"
            if cand.is_dir() and str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
        from provider_adapters import invoke_unified

        system = "你是飞书群聊助手，回复须简洁，可直接在群里阅读，避免过长。"
        result = invoke_unified(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            task_type=cfg.get("feishu_im_agent_key") or "qa_orchestrator_agent",
            max_tokens=800,
        )
        reply = ""
        if isinstance(result, dict):
            reply = (result.get("content") or result.get("text") or "").strip()
        else:
            reply = str(result or "").strip()
        if reply:
            send_chat_text(chat_id, reply[:4000])
            _log("自动回复", ok=True, chat_id=chat_id[:16], reply_len=len(reply))
    except Exception as exc:
        _log("自动回复失败", ok=False, error_message=str(exc)[:200])


def handle_feishu_event_webhook(raw_body: Dict[str, Any]) -> Dict[str, Any]:
    """处理飞书事件订阅 HTTP 回调。"""
    global _last_event_error
    cfg = load_config()

    body = raw_body
    if "encrypt" in body and body.get("encrypt"):
        enc_key = (cfg.get("feishu_encrypt_key") or "").strip()
        if not enc_key:
            _last_event_error = "收到加密事件但未配置 feishu_encrypt_key"
            return {"ok": False, "error": _last_event_error}
        try:
            body = _decrypt_event(enc_key, str(body["encrypt"]))
        except Exception as exc:
            _last_event_error = f"事件解密失败: {exc}"
            _log("解密失败", ok=False, error_message=str(exc)[:120])
            return {"ok": False, "error": _last_event_error}

    # URL 校验（配置事件订阅时飞书会 POST challenge）
    if body.get("type") == "url_verification":
        if not _verify_token(body, cfg):
            return {"ok": False, "error": "verification token 不匹配"}
        challenge = body.get("challenge")
        _log("URL校验", ok=True)
        return {"challenge": challenge}

    schema = body.get("schema")
    header = body.get("header") if isinstance(body.get("header"), dict) else {}
    event_type = str(header.get("event_type") or body.get("event", {}).get("type") or "")

    # schema 2.0
    if schema == "2.0":
        if not _verify_token(body, cfg):
            return {"ok": False, "error": "verification token 不匹配"}
        if event_type == "im.message.receive_v1":
            event = body.get("event") if isinstance(body.get("event"), dict) else {}
            row = _parse_im_message_event(event, cfg)
            if row and _append_message(row):
                _log("消息触发", ok=True, chat_id=row.get("chat_id", "")[:16], msg_id=row.get("message_id", "")[:12])
                threading.Thread(target=_auto_reply_async, args=(row,), daemon=True).start()
                return {"ok": True, "triggered": True, "message_id": row["message_id"]}
            return {"ok": True, "triggered": False}
        return {"ok": True, "ignored": True, "event_type": event_type}

    # 旧版 event_callback
    if body.get("type") == "event_callback":
        if not _verify_token(body, cfg):
            return {"ok": False, "error": "verification token 不匹配"}
        event = body.get("event") if isinstance(body.get("event"), dict) else {}
        et = str(event.get("type") or "")
        if et in ("im.message.receive_v1", "message"):
            row = _parse_im_message_event(event, cfg)
            if row and _append_message(row):
                _log("消息触发", ok=True, chat_id=row.get("chat_id", "")[:16])
                threading.Thread(target=_auto_reply_async, args=(row,), daemon=True).start()
                return {"ok": True, "triggered": True}
        return {"ok": True, "ignored": True, "event_type": et}

    return {"ok": True, "ignored": True, "reason": "unknown_payload"}


def list_recent_messages(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        return list(reversed(_recent_messages[-limit:]))


def event_status() -> Dict[str, Any]:
    cfg = load_config()
    return {
        "mode": "event",
        "enabled": bool(cfg.get("feishu_group_trigger_enabled")),
        "chat_id": cfg.get("feishu_group_chat_id") or "",
        "has_app_creds": bool(_get_app_creds(cfg)[0] and _get_app_creds(cfg)[1]),
        "has_verification_token": bool((cfg.get("feishu_verification_token") or "").strip()),
        "has_encrypt_key": bool((cfg.get("feishu_encrypt_key") or "").strip()),
        "running": bool(cfg.get("feishu_group_trigger_enabled")),
        "last_event_at": _last_event_at,
        "last_error": _last_event_error,
        "recent_count": len(_recent_messages),
        "webhook_path": "/api/feishu/events/webhook",
    }


def apply_feishu_group_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    key_map = {
        "feishu_group_trigger_enabled": "feishu_group_trigger_enabled",
        "feishu_group_chat_id": "feishu_group_chat_id",
        "feishu_app_id": "feishu_app_id",
        "feishu_app_secret": "feishu_app_secret",
        "feishu_verification_token": "feishu_verification_token",
        "feishu_encrypt_key": "feishu_encrypt_key",
        "feishu_im_auto_reply": "feishu_im_auto_reply",
        "feishu_im_agent_key": "feishu_im_agent_key",
        "feishu_im_mode": "feishu_im_mode",
    }
    for src, dst in key_map.items():
        if src in config and config[src] is not None:
            val = config[src]
            if src in ("feishu_app_secret", "feishu_encrypt_key", "feishu_verification_token") and val == "":
                continue
            cfg[dst] = val
    cfg["feishu_im_mode"] = "event"
    save_config(cfg)
    _log("配置已保存", ok=True, enabled=bool(cfg.get("feishu_group_trigger_enabled")))
    return {"ok": True}


def send_chat_text(chat_id: str, text: str) -> Dict[str, Any]:
    cfg = load_config()
    app_id, app_secret = _get_app_creds(cfg)
    token = _tenant_access_token(app_id, app_secret)
    if not requests:
        raise RuntimeError("未安装 requests")
    url = f"{OPEN_API}/im/v1/messages"
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    r = requests.post(
        url,
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书发消息失败: {data.get('msg') or data}")
    return data
