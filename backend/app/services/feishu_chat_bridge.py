"""飞书 IM → Web Agent 对话框桥接：同 chat_stream_v2 / LangGraph 编排链路。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import load_config

_logger = logging.getLogger("sba.feishu_chat_bridge")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[3]
_MAP_PATH = _ROOT / "data" / "feishu_im_sessions.json"
_CHAT_LOCKS: Dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


def _log(action: str, **kwargs: Any) -> None:
    parts = "; ".join(f"{k}={v}" for k, v in kwargs.items())
    _logger.info(
        "[IM机器人-飞书Agent桥|feishu_chat_bridge|chat|Agent执行|%s] %s; %s",
        action,
        action,
        parts,
    )


def _chat_lock(chat_id: str) -> threading.Lock:
    cid = str(chat_id or "").strip()
    with _LOCK_GUARD:
        if cid not in _CHAT_LOCKS:
            _CHAT_LOCKS[cid] = threading.Lock()
        return _CHAT_LOCKS[cid]


def _ensure_map_dir() -> None:
    _MAP_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_mapping_store() -> Dict[str, Any]:
    _ensure_map_dir()
    if not _MAP_PATH.exists():
        return {"mappings": {}}
    try:
        data = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("mappings"), dict):
            return data
    except Exception:
        pass
    return {"mappings": {}}


def _save_mapping_store(store: Dict[str, Any]) -> None:
    _ensure_map_dir()
    _MAP_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def build_feishu_session_title(first_message: str) -> str:
    """会话标题：飞书聊天-XXX（XXX 为首句摘要/截取）。"""
    raw = re.sub(r"\s+", " ", (first_message or "").strip())
    if not raw:
        return "飞书聊天-新对话"
    snippet = raw[:24].rstrip()
    if len(raw) > 24:
        snippet = snippet.rstrip("，,。；;：: ") + "…"
    return f"飞书聊天-{snippet}"


def _new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"


def get_feishu_session_mapping(chat_id: str) -> Optional[Dict[str, Any]]:
    cid = str(chat_id or "").strip()
    if not cid:
        return None
    store = _load_mapping_store()
    row = (store.get("mappings") or {}).get(cid)
    return dict(row) if isinstance(row, dict) else None


def list_feishu_session_mappings() -> Dict[str, Any]:
    return _load_mapping_store()


def _update_mapping(
    chat_id: str,
    *,
    active_session_id: str,
    title: str,
    first_message: str,
    archived_session_id: Optional[str] = None,
) -> None:
    cid = str(chat_id or "").strip()
    if not cid:
        return
    store = _load_mapping_store()
    mappings = store.setdefault("mappings", {})
    row = dict(mappings.get(cid) or {})
    row["active_session_id"] = active_session_id
    row["title"] = title
    if first_message:
        row["first_message"] = first_message
    row["updated_at"] = datetime.now().isoformat(timespec="seconds")
    archived = list(row.get("archived_session_ids") or [])
    if archived_session_id and archived_session_id not in archived:
        archived.append(archived_session_id)
    row["archived_session_ids"] = archived[-20:]
    mappings[cid] = row
    _save_mapping_store(store)


def resolve_or_create_session(chat_id: str, user_text: str) -> Tuple[str, str, bool]:
    """
    解析飞书群 → Web session_id。
    返回 (session_id, title, is_new)。
    """
    from .ai_chat import ensure_session, get_session

    cid = str(chat_id or "").strip()
    text = (user_text or "").strip()
    mapping = get_feishu_session_mapping(cid)
    title = build_feishu_session_title(text)

    if mapping:
        sid = str(mapping.get("active_session_id") or "").strip()
        mapped_title = str(mapping.get("title") or "").strip()
        if sid:
            sess = get_session(sid)
            if sess and str(sess.get("status") or "active") != "archived":
                return sid, mapped_title or title, False
            # 活跃会话已归档：沿用原标题基线开新会话
            title = mapped_title or title
            first_msg = str(mapping.get("first_message") or text)
            if first_msg and not mapped_title:
                title = build_feishu_session_title(first_msg)

    sid = _new_session_id()
    from .ai_chat import ensure_session, _messages, _store_persist

    meta = ensure_session(sid, title)
    meta["channel"] = "feishu"
    meta["feishu_chat_id"] = cid
    _store_persist(sid, meta, _messages.get(sid, []), mark_dirty=True)
    first = text or "新对话"
    _update_mapping(cid, active_session_id=sid, title=title, first_message=first)
    _log("创建飞书会话", ok=True, chat_id=cid[:16], session_id=sid, title=title[:40])
    return sid, title, True


async def create_handoff_session(
    old_session_id: str,
    *,
    chat_id: str,
    title: str,
) -> str:
    """
    上下文满额归档后：新建会话并移交摘要 + 短期记忆最近轮。
    """
    from .ai_chat import _bootstrap_sessions, _messages, _sessions
    from .chat_context_memory import KEEP_RECENT_MESSAGES, persist_normalized_session_document
    from .chat_session_store import get_session_document, persist_session

    old_sid = str(old_session_id or "").strip()
    old_doc = get_session_document(old_sid) or {}
    memory_meta = dict(old_doc.get("memory_meta") or {})
    old_msgs = [m for m in (old_doc.get("messages") or []) if isinstance(m, dict)]
    recent = old_msgs[-KEEP_RECENT_MESSAGES:] if old_msgs else []
    cur_task = old_doc.get("cur_task") if isinstance(old_doc.get("cur_task"), dict) else None
    main_hist = old_doc.get("main_task_history") if isinstance(old_doc.get("main_task_history"), list) else []

    memory_meta["handoff_from"] = old_sid
    memory_meta["handoff_at"] = datetime.now().isoformat(timespec="seconds")
    memory_meta["handoff_reason"] = "context_full"

    new_sid = _new_session_id()
    now = datetime.now().isoformat(timespec="seconds")
    meta = {
        "id": new_sid,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "channel": "feishu",
        "feishu_chat_id": chat_id,
        "continued_from": old_sid,
    }
    new_doc = {
        "session": meta,
        "messages": recent,
        "cur_task": cur_task,
        "main_task_history": main_hist,
        "memory_meta": memory_meta,
    }
    persist_normalized_session_document(new_sid, new_doc)

    _bootstrap_sessions()
    _sessions[new_sid] = meta
    _messages[new_sid] = list(recent)
    if old_sid in _sessions:
        _sessions[old_sid]["status"] = "archived"
        _sessions[old_sid]["updated_at"] = now
        persist_session(
            old_sid,
            _sessions[old_sid],
            _messages.get(old_sid, old_msgs),
            cur_task=old_doc.get("cur_task"),
            main_task_history=main_hist,
            memory_meta=old_doc.get("memory_meta"),
            mark_dirty=True,
        )

    mapping = get_feishu_session_mapping(chat_id) or {}
    first_msg = str(mapping.get("first_message") or title)
    _update_mapping(
        chat_id,
        active_session_id=new_sid,
        title=title,
        first_message=first_msg,
        archived_session_id=old_sid,
    )
    _log(
        "上下文移交",
        ok=True,
        old_session=old_sid[:16],
        new_session=new_sid[:16],
        recent_msgs=len(recent),
        has_summary=bool(memory_meta.get("summary_text")),
    )
    return new_sid


def parse_sse_block(block: str) -> Tuple[str, Dict[str, Any]]:
    """解析单个 SSE 块。"""
    event = ""
    data_lines: List[str] = []
    for line in (block or "").split("\n"):
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    payload: Dict[str, Any] = {}
    if data_lines:
        raw = "\n".join(data_lines)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {"raw": raw}
    return event, payload


async def aggregate_chat_stream_to_text(stream) -> Tuple[str, Dict[str, Any]]:
    """消费 chat_stream_v2 SSE，提取最终回答文本。"""
    buffer = ""
    answer_parts: List[str] = []
    full_text = ""
    meta: Dict[str, Any] = {
        "session_id": "",
        "trace_id": "",
        "context_switched": False,
        "errors": [],
    }

    async for chunk in stream:
        if not chunk:
            continue
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event, data = parse_sse_block(block)
            if not event:
                continue
            if event == "stream_open" and data.get("session_id"):
                meta["session_id"] = str(data["session_id"])
            if event == "context_memory" and data.get("type") == "context_force_switch":
                meta["context_switched"] = True
            if event == "answer_delta":
                piece = str(data.get("content") or data.get("delta") or "")
                if piece:
                    answer_parts.append(piece)
            if event == "answer_end":
                ft = str(data.get("full_text") or "").strip()
                if ft:
                    full_text = ft
            if event == "stream_error":
                meta["errors"].append(str(data.get("message") or data.get("error") or "stream_error"))

    reply = full_text or "".join(answer_parts)
    return reply.strip(), meta


def _format_for_feishu(text: str, *, context_switched: bool = False) -> str:
    out = (text or "").strip()
    if context_switched:
        prefix = "【提示】上下文已满，已自动归档并切换新会话继续。\n\n"
        out = prefix + out if out else prefix.rstrip()
    if not out:
        out = "本次未生成有效回复，请稍后重试或把问题说得更具体一些。"
    # 飞书文本消息上限约 4000 字符
    return out[:3900]


async def run_feishu_agent_reply(
    *,
    chat_id: str,
    user_text: str,
    sender_id: str = "",
) -> str:
    """
    飞书消息走 Web 同源 Agent 链路（chat_stream_v2），返回可发送给飞书的文本。
    """
    from .ai_chat import chat_stream_v2
    from .chat_warmup import refresh_or_prepare_session_memory, wait_for_chat_warmup

    text = (user_text or "").strip()
    cid = (chat_id or "").strip()
    if not text or not cid:
        return ""

    cfg = load_config()
    agent_id = str(cfg.get("feishu_im_agent_key") or "qa_orchestrator_agent").strip() or None

    session_id, title, _is_new = resolve_or_create_session(cid, text)
    context_switched = False

    try:
        await wait_for_chat_warmup(read_comments=False, include_rag=False, timeout_sec=25.0)
    except Exception as exc:
        _log("预热等待失败", ok=False, error_message=str(exc)[:120])

    memory_prepared = await refresh_or_prepare_session_memory(
        session_id,
        extra_tokens=max(32, len(text) // 2),
    )

    if memory_prepared.get("force_new_session"):
        session_id = await create_handoff_session(
            session_id,
            chat_id=cid,
            title=title,
        )
        context_switched = True
        memory_prepared = await refresh_or_prepare_session_memory(
            session_id,
            client_cur_task=memory_prepared.get("cur_task"),
            client_history=memory_prepared.get("main_task_history"),
            extra_tokens=max(32, len(text) // 2),
        )

    client_cur_task = memory_prepared.get("cur_task") if isinstance(memory_prepared.get("cur_task"), dict) else None
    client_hist = memory_prepared.get("main_task_history") if isinstance(memory_prepared.get("main_task_history"), list) else None

    _log(
        "Agent流式开始",
        ok=True,
        chat_id=cid[:16],
        session_id=session_id[:16],
        msg_len=len(text),
        agent_id=agent_id or "",
        context_switched=context_switched,
    )

    stream = chat_stream_v2(
        text,
        session_id,
        agent_id=agent_id,
        user_id=sender_id or f"feishu:{cid[:12]}",
        memory_prepared=memory_prepared,
        client_cur_task=client_cur_task,
        client_main_task_history=client_hist,
    )
    reply, stream_meta = await aggregate_chat_stream_to_text(stream)
    if stream_meta.get("context_switched"):
        context_switched = True
    if stream_meta.get("errors") and not reply:
        reply = "抱歉，Agent 处理出错：" + "; ".join(stream_meta["errors"])[:200]

    formatted = _format_for_feishu(reply, context_switched=context_switched)
    _log(
        "Agent流式完成",
        ok=bool(reply),
        chat_id=cid[:16],
        session_id=(stream_meta.get("session_id") or session_id)[:16],
        reply_len=len(formatted),
        context_switched=context_switched,
    )
    return formatted


def run_feishu_agent_reply_sync(row: Dict[str, Any]) -> str:
    """供 feishu_group_im 后台线程调用（独立 event loop）。"""
    chat_id = str(row.get("chat_id") or "").strip()
    text = str(row.get("text") or "").strip()
    sender_id = str(row.get("sender_id") or "").strip()
    if not chat_id or not text:
        return ""
    lock = _chat_lock(chat_id)
    with lock:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                run_feishu_agent_reply(chat_id=chat_id, user_text=text, sender_id=sender_id)
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)
