"""飞书集成服务 —— 文档上传 + 群消息事件订阅。"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional, Dict, Any

from .config import load_config, agent_config_path, resolve_agent_dir

_AGENT_DIR = resolve_agent_dir()
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from feishu_integration import FeishuKnowledgeBase

from .feishu_group_im import (
    handle_feishu_event_webhook,
    list_recent_messages,
    event_status,
    apply_feishu_group_settings,
)
from .feishu_ws_service import restart_feishu_ws, ws_status

_feishu_kb: Optional[FeishuKnowledgeBase] = None


def _get_feishu_kb() -> Optional[FeishuKnowledgeBase]:
    global _feishu_kb
    if _feishu_kb is not None:
        return _feishu_kb
    try:
        cfg = load_config()
        app_id = cfg.get("feishu_app_id", "")
        app_secret = cfg.get("feishu_app_secret", "")
        if app_id and app_secret:
            _feishu_kb = FeishuKnowledgeBase(app_id=app_id, app_secret=app_secret)
            return _feishu_kb
    except Exception:
        pass
    return None


def feishu_get_config() -> Dict:
    try:
        cfg = load_config()
        st = event_status()
        ws = ws_status()
        running = bool(st.get("running")) and (bool(ws.get("ws_running")) or st.get("recent_count", 0) >= 0)
        return {
            "enabled": bool(cfg.get("feishu_group_trigger_enabled", False)),
            "mode": "websocket" if ws.get("ws_running") else "event",
            "chat_id": cfg.get("feishu_group_chat_id", ""),
            "app_id": cfg.get("feishu_app_id", ""),
            "has_app_secret": bool(cfg.get("feishu_app_secret")),
            "has_verification_token": bool(cfg.get("feishu_verification_token")),
            "has_encrypt_key": bool(cfg.get("feishu_encrypt_key")),
            "auto_reply": bool(cfg.get("feishu_im_auto_reply")),
            "agent_key": cfg.get("feishu_im_agent_key") or "qa_orchestrator_agent",
            "running": running,
            "ws_running": bool(ws.get("ws_running")),
            "ws_last_error": ws.get("ws_last_error") or "",
            "config_path": str(agent_config_path()),
            "last_error": st.get("last_error") or ws.get("ws_last_error") or "",
            "last_event_at": st.get("last_event_at") or 0,
            "webhook_path": st.get("webhook_path"),
        }
    except Exception:
        return {"enabled": False, "mode": "event"}


def feishu_save_config(config: Dict) -> Dict:
    result = apply_feishu_group_settings(config)
    restart_feishu_ws()
    return result


def feishu_list_records(limit: int = 500) -> list:
    return list_recent_messages(limit=limit)


def feishu_handle_event(body: Dict[str, Any]) -> Dict[str, Any]:
    return handle_feishu_event_webhook(body)


def feishu_upload_document(title: str, md_content: str, folder_path: str = None) -> Dict:
    kb = _get_feishu_kb()
    if kb is None:
        return {"ok": False, "error": "飞书知识库未配置"}
    result = kb.upload_document(title, md_content, feishu_folder_path=folder_path)
    return {"ok": result is not None, "result": result}


def feishu_invite_bot_to_group(chat_id: Optional[str] = None) -> Dict[str, Any]:
    """尝试将当前飞书应用机器人拉入指定群（需 lark-cli 用户身份已授权 im:chat.members:write_only）。"""
    import subprocess
    import json as _json

    cfg = load_config()
    cid = (chat_id or cfg.get("feishu_group_chat_id") or "").strip()
    app_id = str(cfg.get("feishu_app_id") or "").strip()
    if not cid or not app_id:
        return {"ok": False, "error": "未配置群 Chat ID 或 App ID"}
    params = _json.dumps({"chat_id": cid, "member_id_type": "app_id"}, ensure_ascii=False)
    data = _json.dumps({"id_list": [app_id]}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [
                "lark-cli",
                "im",
                "chat.members",
                "create",
                "--params",
                params,
                "--data",
                data,
                "--as",
                "user",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        out = (proc.stdout or proc.stderr or "").strip()
        try:
            payload = _json.loads(out)
        except Exception:
            payload = {"raw": out}
        if proc.returncode == 0 and payload.get("ok", True) and payload.get("code", 0) in (0, None):
            return {"ok": True, "chat_id": cid, "detail": payload}
        err = payload.get("error") or payload.get("msg") or out
        return {"ok": False, "error": str(err), "chat_id": cid, "hint": "请在飞书群设置中手动添加机器人，或完成 lark-cli 用户授权后重试"}
    except FileNotFoundError:
        return {"ok": False, "error": "未安装 lark-cli", "chat_id": cid}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "chat_id": cid}
