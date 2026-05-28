"""IM 机器人集成 —— 多平台群聊 AI 接入（首期：个人微信扫码）"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from .config import load_config, save_config

logger = logging.getLogger(__name__)

# 平台元数据（与 DeskClaw 类 IM 设置页对齐）
IM_PLATFORMS: List[Dict[str, Any]] = [
    {
        "id": "feishu",
        "name": "飞书",
        "description": "接入飞书自建应用，群消息事件订阅推送触发（官方 Webhook，非轮询）",
        "available": True,
        "status": "configure",
    },
    {
        "id": "dingtalk",
        "name": "钉钉",
        "description": "接入钉钉机器人，群聊或私聊中与 AI 直接交互，高效处理工作指令",
        "available": False,
        "status": "coming_soon",
    },
    {
        "id": "qq",
        "name": "QQ",
        "description": "极简配置快速接入，在 QQ 中随时与 AI 便捷对话",
        "available": False,
        "status": "coming_soon",
    },
    {
        "id": "wework",
        "name": "企业微信",
        "description": "一键关联企微，群聊或私聊中与 AI 直接沟通协作",
        "available": False,
        "status": "coming_soon",
    },
    {
        "id": "wechat",
        "name": "微信",
        "description": "个人微信接入（需第三方桥接，暂不推荐）",
        "available": False,
        "status": "coming_soon",
    },
]

# 内存中的扫码会话（重启后需重新扫码）
_qr_sessions: Dict[str, Dict[str, Any]] = {}


def _default_wechat_cfg() -> Dict[str, Any]:
    return {
        "enabled": False,
        "bridge_url": "",
        "bridge_token": "",
        "connected": False,
        "wxid": "",
        "nickname": "",
        "avatar_url": "",
        "group_whitelist": [],
        "reply_mode": "mention_only",
        "agent_key": "qa_orchestrator_agent",
        "webhook_secret": "",
        "last_connected_at": "",
    }


def _get_im_robots_cfg(cfg: Optional[Dict] = None) -> Dict[str, Any]:
    base = cfg if cfg is not None else load_config()
    im = base.get("im_robots")
    if not isinstance(im, dict):
        im = {}
    if "wechat" not in im or not isinstance(im.get("wechat"), dict):
        im["wechat"] = _default_wechat_cfg()
    else:
        merged = _default_wechat_cfg()
        merged.update(im["wechat"])
        im["wechat"] = merged
    return im


def im_robot_list_platforms() -> Dict[str, Any]:
    """返回平台概览及连接状态。"""
    from .feishu_group_im import event_status

    cfg = load_config()
    im = _get_im_robots_cfg(cfg)
    wx = im["wechat"]
    fs_st = event_status()
    items = []
    for p in IM_PLATFORMS:
        item = dict(p)
        if p["id"] == "feishu":
            if fs_st.get("running"):
                item["status"] = "connected"
            elif fs_st.get("enabled"):
                item["status"] = "configured"
            else:
                item["status"] = "configure"
            item["connected"] = bool(fs_st.get("running"))
            item["nickname"] = fs_st.get("chat_id") or ""
        elif p["id"] == "wechat":
            if wx.get("connected"):
                item["status"] = "connected"
            elif wx.get("enabled"):
                item["status"] = "configured"
            else:
                item["status"] = "configure"
            item["connected"] = bool(wx.get("connected"))
            item["nickname"] = wx.get("nickname") or ""
        items.append(item)
    return {"platforms": items}


def im_robot_get_wechat() -> Dict[str, Any]:
    im = _get_im_robots_cfg()
    wx = im["wechat"]
    safe = {k: v for k, v in wx.items() if k != "bridge_token" and k != "webhook_secret"}
    safe["has_bridge_token"] = bool(wx.get("bridge_token"))
    safe["has_webhook_secret"] = bool(wx.get("webhook_secret"))
    return safe


def im_robot_save_wechat(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    im = _get_im_robots_cfg(cfg)
    wx = im["wechat"]
    field_map = {
        "enabled": "enabled",
        "bridge_url": "bridge_url",
        "group_whitelist": "group_whitelist",
        "reply_mode": "reply_mode",
        "agent_key": "agent_key",
    }
    for src, dst in field_map.items():
        if src in payload:
            wx[dst] = payload[src]
    if "bridge_token" in payload and payload["bridge_token"]:
        wx["bridge_token"] = str(payload["bridge_token"]).strip()
    if "webhook_secret" in payload and payload["webhook_secret"]:
        wx["webhook_secret"] = str(payload["webhook_secret"]).strip()
    im["wechat"] = wx
    cfg["im_robots"] = im
    save_config(cfg)
    logger.info(
        "[IM机器人-微信配置|im_robot.im_robot_save_wechat|wechat|硬编执行|保存] 配置已写入; enabled=%s; bridge=%s",
        wx.get("enabled"),
        bool(wx.get("bridge_url")),
    )
    return {"ok": True}


async def _bridge_request(
    wx: Dict[str, Any],
    method: str,
    path: str,
    *,
    params: Optional[Dict] = None,
    json_body: Optional[Dict] = None,
    timeout_sec: float = 15.0,
) -> Dict[str, Any]:
    base = (wx.get("bridge_url") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置微信桥接地址（如 GeWeChat / OpenClaw 网关）"}
    url = f"{base}{path}"
    headers = {"Content-Type": "application/json"}
    token = (wx.get("bridge_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    return {"ok": False, "error": f"桥接 HTTP {resp.status}: {text[:200]}"}
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    return {"ok": False, "error": f"桥接返回非 JSON: {text[:120]}"}
                return {"ok": True, "data": data}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "桥接请求超时"}
    except aiohttp.ClientError as exc:
        return {"ok": False, "error": f"桥接连接失败: {exc}"}


def _extract_qr_from_bridge(data: Any) -> Dict[str, str]:
    """兼容 GeWeChat / 常见网关 QR 响应结构。"""
    if not isinstance(data, dict):
        return {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    qr_b64 = (
        inner.get("qrDataUrl")
        or inner.get("qrImgBase64")
        or inner.get("qr_base64")
        or inner.get("qrcode")
        or ""
    )
    qr_url = inner.get("qrUrl") or inner.get("qr_url") or inner.get("url") or ""
    uid = inner.get("uuid") or inner.get("login_uuid") or inner.get("session_id") or ""
    if qr_b64 and not qr_b64.startswith("data:"):
        qr_b64 = f"data:image/png;base64,{qr_b64}"
    return {"qr_image": qr_b64 or qr_url, "uuid": uid}


async def im_robot_wechat_qr_start() -> Dict[str, Any]:
    im = _get_im_robots_cfg()
    wx = im["wechat"]
    bridge = await _bridge_request(wx, "GET", "/login/getLoginQrCode")
    if not bridge.get("ok"):
        # 备用路径
        bridge = await _bridge_request(wx, "POST", "/v2/login/getLoginQrCode")
    if not bridge.get("ok"):
        return {
            "ok": False,
            "error": bridge.get("error") or "无法从桥接获取二维码",
            "hint": "请先在下方填写桥接服务地址（如 http://127.0.0.1:2531），并确保网关已启动。",
        }
    qr_info = _extract_qr_from_bridge(bridge.get("data"))
    if not qr_info.get("qr_image"):
        return {"ok": False, "error": "桥接未返回有效二维码", "raw": bridge.get("data")}
    session_id = qr_info.get("uuid") or str(uuid.uuid4())
    _qr_sessions[session_id] = {
        "uuid": qr_info.get("uuid") or session_id,
        "created_at": time.time(),
        "status": "waiting_scan",
    }
    logger.info(
        "[IM机器人-微信扫码|im_robot.im_robot_wechat_qr_start|wechat|Agent执行|发起] 二维码已生成; session=%s",
        session_id[:8],
    )
    return {
        "ok": True,
        "session_id": session_id,
        "qr_image": qr_info["qr_image"],
        "expires_in_sec": 120,
    }


async def im_robot_wechat_qr_poll(session_id: str) -> Dict[str, Any]:
    sess = _qr_sessions.get(session_id)
    if not sess:
        return {"ok": False, "error": "扫码会话不存在或已过期，请重新获取二维码"}
    if time.time() - sess.get("created_at", 0) > 180:
        _qr_sessions.pop(session_id, None)
        return {"ok": False, "error": "二维码已过期", "status": "expired"}

    im = _get_im_robots_cfg()
    wx = im["wechat"]
    uid = sess.get("uuid") or session_id
    bridge = await _bridge_request(wx, "GET", "/login/checkLogin", params={"uuid": uid})
    if not bridge.get("ok"):
        bridge = await _bridge_request(wx, "GET", "/login/checkLoginStatus", params={"uuid": uid})

    status_code = -1
    nickname = ""
    wxid = ""
    if bridge.get("ok"):
        raw = bridge.get("data") or {}
        inner = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        status_code = int(inner.get("status", inner.get("loginState", -1)))
        nickname = inner.get("nickName") or inner.get("nickname") or ""
        wxid = inner.get("wxid") or inner.get("wxId") or ""

    # 常见状态码：0 待扫 / 1 已扫待确认 / 2 已确认
    if status_code in (2, 200, 1) and (nickname or wxid or status_code in (2, 200)):
        cfg = load_config()
        im_cfg = _get_im_robots_cfg(cfg)
        w = im_cfg["wechat"]
        w["connected"] = True
        w["enabled"] = True
        w["nickname"] = nickname or w.get("nickname") or "微信用户"
        w["wxid"] = wxid or w.get("wxid") or ""
        w["last_connected_at"] = datetime.now().isoformat(timespec="seconds")
        im_cfg["wechat"] = w
        cfg["im_robots"] = im_cfg
        save_config(cfg)
        _qr_sessions.pop(session_id, None)
        logger.info(
            "[IM机器人-微信扫码|im_robot.im_robot_wechat_qr_poll|wechat|Agent执行|完成] 登录成功; wxid=%s",
            (wxid or "")[:12],
        )
        return {
            "ok": True,
            "status": "connected",
            "nickname": w["nickname"],
            "wxid": w["wxid"],
        }

    label = {0: "waiting_scan", 1: "scanned", -1: "waiting_scan"}.get(status_code, "waiting_scan")
    sess["status"] = label
    return {"ok": True, "status": label}


async def im_robot_wechat_refresh_status() -> Dict[str, Any]:
    """从桥接同步在线状态。"""
    im = _get_im_robots_cfg()
    wx = im["wechat"]
    if not wx.get("bridge_url"):
        return {"ok": True, "connected": bool(wx.get("connected")), "source": "local"}
    bridge = await _bridge_request(wx, "GET", "/login/getLoginStatus")
    if not bridge.get("ok"):
        return {"ok": True, "connected": bool(wx.get("connected")), "source": "local", "bridge_error": bridge.get("error")}
    raw = bridge.get("data") or {}
    inner = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    online = bool(inner.get("online") or inner.get("loginState") == 1 or inner.get("status") == 1)
    nickname = inner.get("nickName") or inner.get("nickname") or wx.get("nickname")
    wxid = inner.get("wxid") or inner.get("wxId") or wx.get("wxid")
    if online != wx.get("connected") or nickname or wxid:
        cfg = load_config()
        im_cfg = _get_im_robots_cfg(cfg)
        w = im_cfg["wechat"]
        w["connected"] = online
        if nickname:
            w["nickname"] = nickname
        if wxid:
            w["wxid"] = wxid
        if online:
            w["enabled"] = True
            w["last_connected_at"] = datetime.now().isoformat(timespec="seconds")
        im_cfg["wechat"] = w
        cfg["im_robots"] = im_cfg
        save_config(cfg)
    return {"ok": True, "connected": online, "nickname": nickname, "wxid": wxid, "source": "bridge"}


async def im_robot_wechat_disconnect() -> Dict[str, Any]:
    im = _get_im_robots_cfg()
    wx = im["wechat"]
    if wx.get("bridge_url"):
        await _bridge_request(wx, "POST", "/login/logout")
    cfg = load_config()
    im_cfg = _get_im_robots_cfg(cfg)
    w = im_cfg["wechat"]
    w["connected"] = False
    w["wxid"] = ""
    w["nickname"] = ""
    w["avatar_url"] = ""
    im_cfg["wechat"] = w
    cfg["im_robots"] = im_cfg
    save_config(cfg)
    _qr_sessions.clear()
    logger.info("[IM机器人-微信断开|im_robot.im_robot_wechat_disconnect|wechat|硬编执行|完成] 已断开连接")
    return {"ok": True, "message": "已断开微信连接"}


async def im_robot_wechat_send_text(chat_id: str, content: str) -> Dict[str, Any]:
    im = _get_im_robots_cfg()
    wx = im["wechat"]
    body = {"chatId": chat_id, "content": content}
    bridge = await _bridge_request(wx, "POST", "/message/sendText", json_body=body)
    if not bridge.get("ok"):
        bridge = await _bridge_request(wx, "POST", "/v2_TRANSACTIONAL/message/sendText", json_body=body)
    return bridge


async def im_robot_wechat_inbound(payload: Dict[str, Any]) -> Dict[str, Any]:
    """桥接回调：群消息入站（校验 webhook_secret 后记录并可选回复）。"""
    im = _get_im_robots_cfg()
    wx = im["wechat"]
    secret = (wx.get("webhook_secret") or "").strip()
    if secret:
        incoming = (payload.get("secret") or payload.get("token") or "").strip()
        if incoming != secret:
            return {"ok": False, "error": "webhook 密钥无效"}

    if not wx.get("enabled") or not wx.get("connected"):
        return {"ok": False, "error": "微信机器人未启用或未连接"}

    chat_id = str(payload.get("chat_id") or payload.get("chatId") or payload.get("roomid") or "")
    sender = str(payload.get("sender") or payload.get("from") or "")
    text = str(payload.get("content") or payload.get("text") or "").strip()
    is_group = bool(payload.get("is_group") or payload.get("isGroup") or chat_id.endswith("@chatroom"))
    mention_only = wx.get("reply_mode") == "mention_only"

    whitelist = wx.get("group_whitelist") or []
    if whitelist and chat_id and chat_id not in whitelist:
        return {"ok": True, "action": "ignored", "reason": "not_in_whitelist"}

    if mention_only and is_group:
        at_list = payload.get("at_list") or payload.get("atList") or []
        self_wxid = wx.get("wxid") or ""
        if self_wxid and self_wxid not in at_list and "@所有人" not in text:
            # 也接受文本中包含 @机器人昵称
            nick = wx.get("nickname") or ""
            if nick and f"@{nick}" not in text:
                return {"ok": True, "action": "ignored", "reason": "mention_required"}

    if not text:
        return {"ok": True, "action": "ignored", "reason": "empty_content"}

    logger.info(
        "[IM机器人-微信入站|im_robot.im_robot_wechat_inbound|chat:%s|Agent执行|接收] 收到消息; sender=%s; len=%s",
        chat_id[:16] if chat_id else "-",
        sender[:12] if sender else "-",
        len(text),
    )

    # 简化回复：调用统一 LLM（非流式），生产可改为走 chat_stream 编排
    reply_text = await _generate_im_reply(text, wx.get("agent_key") or "qa_orchestrator_agent")
    if chat_id and reply_text:
        send_result = await im_robot_wechat_send_text(chat_id, reply_text)
        return {"ok": True, "action": "replied", "reply_length": len(reply_text), "send": send_result}
    return {"ok": True, "action": "processed", "reply_length": len(reply_text or "")}


async def _generate_im_reply(user_text: str, agent_key: str) -> str:
    """调用 src/agent 统一网关生成短回复。"""
    try:
        import sys
        from pathlib import Path

        here = Path(__file__).resolve()
        agent_dir = None
        for p in here.parents:
            cand = p / "src" / "agent"
            if cand.is_dir():
                agent_dir = cand
                break
        if agent_dir and str(agent_dir) not in sys.path:
            sys.path.insert(0, str(agent_dir))
        from provider_adapters import invoke_unified

        system = (
            "你是 IM 群聊助手，回复须简洁、可直接发送给同事。"
            "优先给出可执行结论，避免冗长 markdown 标题。"
        )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: invoke_unified(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
                task_type=agent_key,
                max_tokens=800,
            ),
        )
        if isinstance(result, dict):
            return (result.get("content") or result.get("text") or "").strip()
        return str(result or "").strip()
    except Exception as exc:
        logger.warning(
            "[IM机器人-微信回复|im_robot._generate_im_reply|llm|Agent执行|失败] 生成失败; error=%s",
            exc,
        )
        return "抱歉，AI 暂时无法回复，请稍后再试。"
