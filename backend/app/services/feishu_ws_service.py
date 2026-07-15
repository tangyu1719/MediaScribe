"""飞书 IM WebSocket 长连接 —— 本地免公网 Webhook，自动收群消息。"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from .config import load_config

logger = logging.getLogger(__name__)

_ws_thread: Optional[threading.Thread] = None
_ws_client: Any = None
_ws_running = False
_ws_last_error = ""
_ws_started_at = 0.0
_lock = threading.Lock()


def _log(action: str, **kwargs: Any) -> None:
    parts = "; ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info("[IM机器人-飞书WS|feishu_ws_service|ws|Agent执行|%s] %s; %s", action, action, parts)


def ws_status() -> Dict[str, Any]:
    cfg = load_config()
    with _lock:
        return {
            "ws_running": _ws_running,
            "ws_last_error": _ws_last_error,
            "ws_started_at": _ws_started_at,
            "ws_enabled": bool(cfg.get("feishu_group_trigger_enabled")),
            "transport": "websocket" if _ws_running else ("webhook" if cfg.get("feishu_im_mode") == "event" else "off"),
        }


def _on_p2_im_message_receive_v1(data: Any) -> None:
    """lark-oapi v2.0 消息事件回调。"""
    try:
        from .feishu_group_im import handle_feishu_event_webhook

        raw = data
        if hasattr(data, "event"):
            ev = data.event
            event_dict: Dict[str, Any] = {}
            if hasattr(ev, "__dict__"):
                event_dict = _to_plain(ev)
            elif isinstance(ev, dict):
                event_dict = ev
            body = {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": event_dict,
            }
        else:
            body = _to_plain(data) if not isinstance(data, dict) else data

        handle_feishu_event_webhook(body)
    except Exception as exc:
        _log("WS消息处理失败", ok=False, error_message=str(exc)[:200])


def _to_plain(obj: Any) -> Any:
    """递归将 lark SDK 对象转为 dict。"""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(x) for x in obj]
    if hasattr(obj, "to_dict"):
        try:
            return _to_plain(obj.to_dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        out: Dict[str, Any] = {}
        for k, v in vars(obj).items():
            if k.startswith("_"):
                continue
            out[k] = _to_plain(v)
        return out
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


def _ws_loop(app_id: str, app_secret: str) -> None:
    global _ws_running, _ws_last_error, _ws_started_at, _ws_client
    with _lock:
        _ws_started_at = time.time()
    try:
        import lark_oapi as lark

        def handler(data: Any) -> None:
            _on_p2_im_message_receive_v1(data)

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(handler)
            .build()
        )
        cli = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        with _lock:
            _ws_client = cli
            _ws_running = True
            _ws_last_error = ""
        _log("WS启动", ok=True, app_id=app_id[:12])
        cli.start()
    except Exception as exc:
        with _lock:
            _ws_running = False
            _ws_last_error = str(exc)[:300]
        _log("WS异常退出", ok=False, error_message=str(exc)[:200])
    finally:
        with _lock:
            _ws_running = False
            _ws_client = None


def start_feishu_ws_if_enabled() -> bool:
    """若已启用飞书 IM，则后台启动 WebSocket 长连接。"""
    global _ws_thread
    cfg = load_config()
    if not cfg.get("feishu_group_trigger_enabled"):
        stop_feishu_ws()
        return False
    app_id = str(cfg.get("feishu_app_id") or "").strip()
    app_secret = str(cfg.get("feishu_app_secret") or "").strip()
    if not app_id or not app_secret:
        _log("WS未启动", ok=False, reason="missing_app_creds")
        return False
    with _lock:
        if _ws_thread and _ws_thread.is_alive():
            return True
    _ws_thread = threading.Thread(
        target=_ws_loop,
        args=(app_id, app_secret),
        name="feishu-ws",
        daemon=True,
    )
    _ws_thread.start()
    return True


def stop_feishu_ws() -> None:
    global _ws_thread, _ws_client, _ws_running
    with _lock:
        cli = _ws_client
        _ws_client = None
        _ws_running = False
    if cli is not None:
        try:
            stop_fn = getattr(cli, "stop", None)
            if callable(stop_fn):
                stop_fn()
        except Exception:
            pass
    _ws_thread = None


def restart_feishu_ws() -> bool:
    stop_feishu_ws()
    time.sleep(0.5)
    return start_feishu_ws_if_enabled()
