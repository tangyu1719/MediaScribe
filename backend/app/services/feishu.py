"""飞书集成服务 —— 文档上传 + 群消息事件订阅。"""
from __future__ import annotations
import sys
import json
from pathlib import Path
from typing import Optional, Dict, Any

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
for _p in _HERE.parents:
    _candidate = _p / "src" / "agent"
    if _candidate.is_dir():
        _AGENT_DIR = _candidate.resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from feishu_integration import FeishuKnowledgeBase

from .feishu_group_im import (
    handle_feishu_event_webhook,
    list_recent_messages,
    event_status,
    apply_feishu_group_settings,
)

_feishu_kb: Optional[FeishuKnowledgeBase] = None


def _get_feishu_kb() -> Optional[FeishuKnowledgeBase]:
    global _feishu_kb
    if _feishu_kb is not None:
        return _feishu_kb
    config_path = _AGENT_DIR / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            app_id = cfg.get("feishu_app_id", "")
            app_secret = cfg.get("feishu_app_secret", "")
            if app_id and app_secret:
                _feishu_kb = FeishuKnowledgeBase(app_id=app_id, app_secret=app_secret)
                return _feishu_kb
        except Exception:
            pass
    return None


def feishu_get_config() -> Dict:
    config_path = _AGENT_DIR / "config.json"
    if not config_path.exists():
        return {"enabled": False, "mode": "event"}
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        st = event_status()
        return {
            "enabled": bool(cfg.get("feishu_group_trigger_enabled", False)),
            "mode": "event",
            "chat_id": cfg.get("feishu_group_chat_id", ""),
            "app_id": cfg.get("feishu_app_id", ""),
            "has_app_secret": bool(cfg.get("feishu_app_secret")),
            "has_verification_token": bool(cfg.get("feishu_verification_token")),
            "has_encrypt_key": bool(cfg.get("feishu_encrypt_key")),
            "auto_reply": bool(cfg.get("feishu_im_auto_reply")),
            "agent_key": cfg.get("feishu_im_agent_key") or "qa_orchestrator_agent",
            "running": st.get("running"),
            "last_error": st.get("last_error") or "",
            "last_event_at": st.get("last_event_at") or 0,
            "webhook_path": st.get("webhook_path"),
        }
    except Exception:
        return {"enabled": False, "mode": "event"}


def feishu_save_config(config: Dict) -> Dict:
    return apply_feishu_group_settings(config)


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
