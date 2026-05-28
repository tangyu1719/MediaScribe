"""飞书群消息轮询 —— 官方 OpenAPI，无需第三方桥接/微信网关。"""
from __future__ import annotations

import json
import logging
import re
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

_poll_thread: Optional[threading.Thread] = None
_poll_stop = threading.Event()
_seen_msg_ids: Set[str] = set()
_recent_messages: List[Dict[str, Any]] = []
_last_poll_error: str = ""
_last_poll_at: float = 0.0
_lock = threading.Lock()


def _log(action: str, **kwargs: Any) -> None:
    parts = "; ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info("[IM机器人-飞书群轮询|feishu_group_poll|chat|Agent执行|%s] %s; %s", action, action, parts)


def _get_app_creds(cfg: Optional[Dict] = None) -> tuple[str, str]:
    c = cfg or load_config()
    return (str(c.get("feishu_app_id") or "").strip(), str(c.get("feishu_app_secret") or "").strip())


def _tenant_access_token(app_id: str, app_secret: str) -> str:
    if not requests:
        raise RuntimeError("未安装 requests，无法调用飞书 OpenAPI")
    if not app_id or not app_secret:
        raise RuntimeError("请先在 config.json 或设置页配置 feishu_app_id / feishu_app_secret（飞书自建应用）")
    url = f"{OPEN_API}/auth/v3/tenant_access_token/internal"
    r = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg') or data}")
    tok = data.get("tenant_access_token")
    if not tok:
        raise RuntimeError("飞书 token 响应为空")
    return str(tok)


def _extract_message_text(item: Dict[str, Any]) -> str:
    body = item.get("body") or {}
    content = body.get("content") if isinstance(body, dict) else ""
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
    return str(raw or item.get("message_type") or "")


def fetch_chat_messages(chat_id: str, page_size: int = 20) -> Dict[str, Any]:
    """拉取群聊历史消息（应用机器人须在群内）。"""
    cfg = load_config()
    app_id, app_secret = _get_app_creds(cfg)
    token = _tenant_access_token(app_id, app_secret)
    if not requests:
        raise RuntimeError("未安装 requests")
    url = f"{OPEN_API}/im/v1/messages"
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "page_size": max(1, min(int(page_size), 50)),
    }
    r = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 im/v1/messages 失败: {data.get('msg') or data}")
    return data


def send_chat_text(chat_id: str, text: str) -> Dict[str, Any]:
    cfg = load_config()
    app_id, app_secret = _get_app_creds(cfg)
    token = _tenant_access_token(app_id, app_secret)
    if not requests:
        raise RuntimeError("未安装 requests")
    url = f"{OPEN_API}/im/v1/messages"
    params = {"receive_id_type": "chat_id"}
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    r = requests.post(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书发消息失败: {data.get('msg') or data}")
    return data


def poll_feishu_group_once() -> Dict[str, Any]:
    """执行一次群消息轮询，返回本轮新增消息。"""
    global _last_poll_error, _last_poll_at
    cfg = load_config()
    chat_id = (cfg.get("feishu_group_chat_id") or "").strip()
    if not chat_id:
        return {"ok": False, "error": "未配置 feishu_group_chat_id（群 Chat ID）"}
    try:
        page_size = int(cfg.get("feishu_group_poll_page_size") or cfg.get("feishu_group_page_size") or 20)
    except Exception:
        page_size = 20
    try:
        last_ts = int(cfg.get("feishu_group_last_timestamp") or 0)
    except Exception:
        last_ts = 0

    try:
        payload = fetch_chat_messages(chat_id, page_size=page_size)
        items = ((payload or {}).get("data") or {}).get("items") or []
        if not isinstance(items, list):
            items = []

        parsed: List[tuple] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            msg_id = str(it.get("message_id") or "").strip()
            if not msg_id or msg_id in _seen_msg_ids:
                continue
            try:
                cts = int(it.get("create_time") or 0)
            except Exception:
                cts = 0
            text = _extract_message_text(it)
            sender = it.get("sender") or {}
            sender_id = ""
            if isinstance(sender, dict):
                sender_id = str(sender.get("id") or sender.get("sender_id") or "")
            parsed.append((cts, msg_id, text, sender_id, it))

        parsed.sort(key=lambda x: x[0] or 0)
        new_rows: List[Dict[str, Any]] = []
        max_ts = last_ts
        for cts, msg_id, text, sender_id, raw in parsed:
            if cts and cts <= last_ts:
                _seen_msg_ids.add(msg_id)
                continue
            row = {
                "message_id": msg_id,
                "create_time": cts,
                "text": text,
                "sender_id": sender_id,
                "chat_id": chat_id,
            }
            new_rows.append(row)
            _seen_msg_ids.add(msg_id)
            if len(_seen_msg_ids) > 3000:
                _seen_msg_ids.clear()
                _seen_msg_ids.update({r["message_id"] for r in _recent_messages[-500:]})
            if cts > max_ts:
                max_ts = cts

        if max_ts > last_ts:
            cfg = load_config()
            cfg["feishu_group_last_timestamp"] = max_ts
            save_config(cfg)

        if new_rows:
            with _lock:
                _recent_messages.extend(new_rows)
                if len(_recent_messages) > 500:
                    del _recent_messages[:-500]

        _last_poll_at = time.time()
        _last_poll_error = ""
        _log("轮询完成", ok=True, new_count=len(new_rows), chat_id=chat_id[:16])
        return {"ok": True, "new_count": len(new_rows), "messages": new_rows}
    except Exception as exc:
        _last_poll_error = str(exc)
        _last_poll_at = time.time()
        _log("轮询失败", ok=False, error_type=type(exc).__name__, error_message=str(exc)[:200])
        return {"ok": False, "error": str(exc)}


def list_recent_messages(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        return list(reversed(_recent_messages[-limit:]))


def poll_status() -> Dict[str, Any]:
    cfg = load_config()
    alive = _poll_thread is not None and _poll_thread.is_alive()
    return {
        "enabled": bool(cfg.get("feishu_group_trigger_enabled")),
        "chat_id": cfg.get("feishu_group_chat_id") or "",
        "poll_interval_sec": cfg.get("feishu_group_poll_interval_sec", 60),
        "page_size": cfg.get("feishu_group_poll_page_size") or cfg.get("feishu_group_page_size", 20),
        "has_app_creds": bool(_get_app_creds(cfg)[0] and _get_app_creds(cfg)[1]),
        "running": alive and bool(cfg.get("feishu_group_trigger_enabled")),
        "last_poll_at": _last_poll_at,
        "last_error": _last_poll_error,
        "recent_count": len(_recent_messages),
    }


def _poll_loop() -> None:
    while not _poll_stop.is_set():
        cfg = load_config()
        if not cfg.get("feishu_group_trigger_enabled"):
            break
        poll_feishu_group_once()
        try:
            interval = int(cfg.get("feishu_group_poll_interval_sec") or 10)
        except Exception:
            interval = 10
        interval = max(3, interval)
        if _poll_stop.wait(interval):
            break


def start_feishu_group_polling() -> None:
    global _poll_thread
    cfg = load_config()
    if not cfg.get("feishu_group_trigger_enabled"):
        stop_feishu_group_polling()
        return
    if _poll_thread and _poll_thread.is_alive():
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="feishu-group-poll")
    _poll_thread.start()
    _log("后台轮询已启动", ok=True)


def stop_feishu_group_polling() -> None:
    _poll_stop.set()


def apply_feishu_group_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """保存配置并重启轮询线程。"""
    cfg = load_config()
    key_map = {
        "feishu_group_trigger_enabled": "feishu_group_trigger_enabled",
        "feishu_group_chat_id": "feishu_group_chat_id",
        "feishu_group_poll_interval_sec": "feishu_group_poll_interval_sec",
        "feishu_group_page_size": "feishu_group_poll_page_size",
        "feishu_group_poll_page_size": "feishu_group_poll_page_size",
        "feishu_app_id": "feishu_app_id",
        "feishu_app_secret": "feishu_app_secret",
    }
    for src, dst in key_map.items():
        if src in config and config[src] is not None:
            cfg[dst] = config[src]
    save_config(cfg)
    stop_feishu_group_polling()
    if cfg.get("feishu_group_trigger_enabled"):
        start_feishu_group_polling()
    return {"ok": True}
