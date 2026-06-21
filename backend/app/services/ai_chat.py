"""AI 聊天服务 v2 — SSE 思考过程 + SPAN 审计

SSE 事件序列（参照原项目 _call_ai_api_stream）：
  task_created → thinking_start → thought_step_start → thinking_delta
  → thought_step_end → ... → thinking_end → answer_start → answer_delta
  → answer_end → span_update
"""
from __future__ import annotations
import asyncio, hashlib, json, os, re, sys, time, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus, urlparse, parse_qs
from urllib.request import Request, urlopen

_BASE_DIR = Path(__file__).resolve().parents[2]


def _resolve_agent_dir() -> Path:
    """向上查找含 src/agent 的工程根（与 config.py / main.py 一致，勿用 web_rebuild_v2/src/agent）。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src" / "agent"
        if candidate.is_dir():
            return candidate.resolve()
    return (here.parents[4] / "src" / "agent").resolve()


_AGENT_DIR = _resolve_agent_dir()
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


def load_chat_llm_config() -> Dict[str, Any]:
    """加载问答/编排用 LLM 配置：与 Agent 配置页同源（src/agent/config.json）。"""
    # 1) 启动脚本可显式指定（避免 cwd/路径推断失败）
    env_path = (os.environ.get("SBA_AGENT_CONFIG") or os.environ.get("SBA_CONFIG_PATH") or "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                if cfg:
                    return cfg
            except Exception:
                pass
    # 2) 与 Agent 配置页同一 loader（向上查找 src/agent）
    try:
        from .config import load_config, _CONFIG_PATH

        cfg = load_config()
        if cfg:
            return cfg
        if _CONFIG_PATH and _CONFIG_PATH.is_file():
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if cfg:
                return cfg
    except Exception:
        pass
    # 3) 兜底：已解析的 agent 目录
    cfg: Dict[str, Any] = {}
    for cp in [_AGENT_DIR / "config.json", _BASE_DIR / "config.json"]:
        if not cp.is_file():
            continue
        try:
            cfg = json.loads(cp.read_text(encoding="utf-8"))
            if cfg:
                break
        except Exception:
            pass
    return cfg


def chat_llm_config_diagnostics() -> Dict[str, Any]:
    """供日志/健康检查：当前问答链路读到的配置路径与密钥是否就绪。"""
    cfg = load_chat_llm_config()
    creds = resolve_chat_api_credentials(cfg)
    try:
        from .config import _CONFIG_PATH as canonical_path
        path = str(canonical_path)
    except Exception:
        path = str(_AGENT_DIR / "config.json")
    return {
        "config_path": path,
        "config_exists": Path(path).is_file(),
        "agent_dir": str(_AGENT_DIR),
        "volcengine_api_key_set": bool(str(cfg.get("volcengine_api_key") or "").strip()),
        "ai_chat_model": str(cfg.get("ai_chat_model") or creds.get("model") or ""),
        "api_key_resolved": bool(creds.get("api_key")),
        "model_resolved": bool(creds.get("model")),
        "gateway_nodes": len(cfg.get("api_gateway_nodes") or []),
    }


def resolve_chat_api_credentials(cfg: Dict[str, Any]) -> Dict[str, str]:
    """从 config 与 api_gateway_nodes 解析 provider / api_key / base_url / 默认 model。"""
    provider = str(cfg.get("gateway_provider") or "ark").strip().lower()
    api_key = str(cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
    base_url = str(
        cfg.get("volcengine_base_url")
        or cfg.get("openai_base_url")
        or "https://ark.cn-beijing.volces.com/api/v3"
    ).strip()
    model = str(cfg.get("ai_chat_model") or "").strip()
    route_map = cfg.get("gateway_task_type_route") or {}
    if isinstance(route_map, dict):
        qa_ep = str(route_map.get("qa") or route_map.get("chat") or "").strip()
        if qa_ep:
            model = qa_ep
    for node in cfg.get("api_gateway_nodes") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("status") or "active").lower() not in ("active", ""):
            continue
        if not api_key:
            api_key = str(node.get("api_key") or "").strip()
        if not model:
            model = str(node.get("endpoint_id") or node.get("model") or "").strip()
        if api_key and model:
            break
    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }

from .task_states import (
    PARENT_CREATED,
    PARENT_EXECUTING,
    PARENT_PLANNING,
    PARENT_RESOLVED,
    PARENT_SUMMARIZING,
    PARENT_PAUSED,
    PARENT_ABNORMAL,
    PARENT_CLOSED,
    SUB_THINKING,
    SUB_ACTING,
    SUB_DONE,
)
from .span_audit import (
    create_task as _span_task, update_task as _span_update, get_task as _span_get,
    create_step as _span_step, start_step as _span_start, finish_step as _span_finish,
    patch_task_snapshot as _span_patch_snapshot,
)
from .span_tool_interceptor import begin_tool_span, end_tool_span
from .tool_output_schema import (
    build_flow_step_output,
    build_llm_step_output,
    build_tool_step_output,
    dumps_step_output,
    brief_from_payload,
    format_intent_result_brief_cn,
    brief_from_react_act_text,
    sanitize_user_visible_answer_text,
    parse_inline_tool_calls_from_content,
    clamp_result_brief_cn,
)
from .task_manager import create_task as _store_create, add_log

# AI 问答专用底座：config.json 的 system_prompt 归属 summary_agent，不得用于聊天
_CHAT_BASE_FALLBACK = (
    "你是 SuperBizAgent 通用对话助手。"
    "角色、语气、工具范围与禁止项以 agent.md 为准；用户领域与偏好以 user.md 为准。"
    "未发生真实工具调用时不得编造工具名、参数或执行结果。"
)


def resolve_chat_base_system(cfg: Dict[str, Any]) -> str:
    """聊天 system 底座：仅 ai_chat_system_prompt，禁止误用摘要链路的 system_prompt。"""
    explicit = str(cfg.get("ai_chat_system_prompt") or "").strip()
    if explicit:
        return explicit
    return _CHAT_BASE_FALLBACK


def resolve_chat_agent_md(
    agent_id: Optional[str],
    *,
    agent_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """加载 Agent 个性化 XML（含 agent_id=default / builtin:default）。"""
    agent_key = (agent_id or "default").strip().lower() or "default"
    try:
        from .agent_personalization_service import build_system_prompt_extension

        return build_system_prompt_extension(agent_key, legacy_profile=agent_profile)
    except Exception:
        agent_frag = {
            "doc": "你当前为「文档助手」：优先结构化输出、引用与改写规范。",
            "ops": "你当前为「运维助手」：优先可执行排障步骤与风险提示。",
        }.get(agent_key, "")
        bits: List[str] = []
        if agent_frag:
            bits.append(agent_frag)
        if agent_profile and isinstance(agent_profile, dict):
            lines: List[str] = []
            nm = str(agent_profile.get("name") or "").strip()
            if nm:
                lines.append(f"【自定义 Agent】{nm}")
            desc = str(agent_profile.get("description") or "").strip()
            if desc:
                lines.append(f"简介：{desc}")
            fw = str(agent_profile.get("framework") or "").strip()
            if fw:
                fw_label = {
                    "react": "ReAct（推理-行动交替）",
                    "plan_execute": "Plan-Execute（先计划再执行）",
                    "single_shot": "Single-shot（单轮直答）",
                }.get(fw.lower(), fw)
                lines.append(f"执行框架偏好：{fw_label}")
            tools = str(agent_profile.get("tools_scope") or "").strip()
            if tools:
                lines.append(f"工具与能力范围：{tools}")
            bounds = str(agent_profile.get("boundaries") or "").strip()
            if bounds:
                lines.append(f"动作与内容边界（须严格遵守）：{bounds}")
            if lines:
                bits.append("\n".join(lines))
        return "\n\n".join(bits) if bits else ""


def assemble_chat_system_prompt(
    cfg: Dict[str, Any],
    agent_id: Optional[str],
    *,
    agent_profile: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> str:
    base_system = resolve_chat_base_system(cfg)
    agent_md = resolve_chat_agent_md(agent_id, agent_profile=agent_profile)
    user_md = ""
    if user_id:
        try:
            from .user_portrait import load_user_md_text

            user_md = load_user_md_text(user_id).strip()
        except Exception:
            user_md = ""
    doc_chunks: List[str] = []
    if user_md:
        doc_chunks.append(f"## 文件：user.md（用户画像）\n\n{user_md}")
    if (agent_md or "").strip():
        doc_chunks.append(f"## 文件：agent.md（Agent 个性化）\n\n{agent_md.strip()}")
    if not doc_chunks:
        return base_system
    return base_system + "\n\n---\n\n" + "\n\n---\n\n".join(doc_chunks)


from .chat_session_store import (
    load_all as _store_load_all,
    persist_session as _store_persist,
    delete_local as _store_delete,
    export_markdown as _store_export_md,
    start_periodic_redis_sync,
)

_sessions: Dict[str, Dict] = {}
_messages: Dict[str, list] = {}
_store_bootstrapped = False


def _bootstrap_sessions():
    global _store_bootstrapped
    if _store_bootstrapped:
        return
    sessions, messages = _store_load_all()
    _sessions.update(sessions)
    _messages.update(messages)
    _store_bootstrapped = True


def init_chat_persistence():
    """应用启动时调用：加载本地会话并启动 Redis 定时同步。"""
    _bootstrap_sessions()
    start_periodic_redis_sync()


# ── 会话管理 ──
def ensure_session(session_id: str, title: str = "新对话") -> Dict:
    """确保会话存在（保留调用方传入的 session_id）。"""
    _bootstrap_sessions()
    if session_id in _sessions:
        return _sessions[session_id]
    now = datetime.now().isoformat(timespec="seconds")
    meta = {
        "id": session_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "status": "active",
    }
    _sessions[session_id] = meta
    _messages[session_id] = []
    _store_persist(session_id, meta, [], mark_dirty=True)
    return meta


def create_session(title="新对话"):
    _bootstrap_sessions()
    sid = _new_id("sess_")
    now = datetime.now().isoformat(timespec="seconds")
    meta = {"id": sid, "title": title, "created_at": now, "updated_at": now, "status": "active"}
    _sessions[sid] = meta
    _messages[sid] = []
    _store_persist(sid, meta, [], mark_dirty=True)
    return _sessions[sid]


def get_session(sid):
    _bootstrap_sessions()
    return _sessions.get(sid)


def list_sessions():
    _bootstrap_sessions()
    return sorted(
        _sessions.values(),
        key=lambda s: s.get("updated_at") or s.get("created_at") or "",
        reverse=True,
    )


def rename_session(sid, title):
    _bootstrap_sessions()
    if sid not in _sessions:
        return False
    _sessions[sid]["title"] = title
    _sessions[sid]["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _store_persist(sid, _sessions[sid], _messages.get(sid, []), mark_dirty=True)
    return True


def delete_session(sid):
    _bootstrap_sessions()
    _sessions.pop(sid, None)
    _messages.pop(sid, None)
    _store_delete(sid)
    return True


def get_session_messages(sid) -> List[Dict]:
    _bootstrap_sessions()
    return list(_messages.get(sid, []))


def _slim_ui_message(m: Dict) -> Dict:
    from .chat_context_memory import slim_message_for_storage

    return slim_message_for_storage(m)


def save_session_state(
    sid: str,
    *,
    messages: Optional[List] = None,
    title: Optional[str] = None,
    cur_task: Optional[Dict] = None,
    main_task_history: Optional[List] = None,
    prefs: Optional[Dict] = None,
    status: Optional[str] = None,
) -> bool:
    _bootstrap_sessions()
    if sid not in _sessions:
        ensure_session(sid, title or "新对话")
    if title:
        _sessions[sid]["title"] = title
    if status:
        _sessions[sid]["status"] = status
    slim_msgs = None
    slim_ct = cur_task
    slim_hist = main_task_history
    from .chat_context_memory import (
        guard_session_payload_for_persist,
        slim_cur_task_for_storage,
        slim_main_task_history_for_storage,
    )

    if messages is not None:
        slim_msgs, slim_ct, slim_hist = guard_session_payload_for_persist(
            messages, cur_task, main_task_history
        )
        _messages[sid] = slim_msgs
    else:
        if isinstance(cur_task, dict):
            slim_ct = slim_cur_task_for_storage(cur_task)
        if isinstance(main_task_history, list):
            slim_hist = slim_main_task_history_for_storage(main_task_history)
    hist = slim_hist if isinstance(slim_hist, list) else None
    msgs_for_hist = slim_msgs if slim_msgs is not None else _messages.get(sid, [])
    if isinstance(hist, list) and not hist and msgs_for_hist:
        try:
            from .chat_context_memory import rebuild_main_task_history_from_messages

            hist = slim_main_task_history_for_storage(
                rebuild_main_task_history_from_messages(msgs_for_hist)
            )
        except Exception:
            pass
    _store_persist(
        sid,
        _sessions[sid],
        msgs_for_hist,
        cur_task=slim_ct,
        main_task_history=hist,
        prefs=prefs,
        mark_dirty=True,
    )
    return True


def export_session_markdown(sid: str) -> str:
    return _store_export_md(sid)


def _persist_ui_messages(session_id: str, ui_messages: List[Dict], cur_task: Optional[Dict] = None):
    """落盘 UI 消息：仅 transcript + 步骤摘要（全量 IO 在 SPAN/Redis）。"""
    _bootstrap_sessions()
    if session_id not in _sessions:
        return
    from .chat_context_memory import guard_session_payload_for_persist

    slim, slim_ct, _ = guard_session_payload_for_persist(ui_messages, cur_task, None)
    _messages[session_id] = [
        {"role": x["role"], "content": x.get("content") or ""}
        for x in slim
        if x.get("role") in ("user", "assistant")
    ]
    _store_persist(session_id, _sessions[session_id], slim, cur_task=slim_ct, mark_dirty=True)


def _sse(event: str, data: Dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _new_id(prefix="") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat")


def _provider_supports_openai_style_tools(provider: str, base_url: str) -> bool:
    """Anthropic 本版未接 tools；其余 OpenAI 兼容网关（含 Ark/DeepSeek）默认支持 tool_calls。"""
    pv = (provider or "").strip().lower()
    if pv == "anthropic":
        return False
    return True


def _mcp_react_loop_enabled(
    framework: str,
    chat_lc_tools: list,
    provider: str,
    base_url: str,
) -> bool:
    """执行段走 OpenAI tool_calls 真 ReAct 环；每轮工具前仍须真实 Observe/Act LLM 步。"""
    fw = (framework or "react").strip().lower()
    if fw not in ("react", "plan_execute", ""):
        return False
    return bool(chat_lc_tools) and _provider_supports_openai_style_tools(provider, base_url)


def _parse_tool_result_obj(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _async_pipeline_ids_from_tool_outputs(tool_outputs: Any) -> List[str]:
    from .chat_context_memory import pipeline_ids_from_tool_outputs

    return pipeline_ids_from_tool_outputs(tool_outputs)


_PIPELINE_ACTIVE_STATUSES = frozenset({
    "pending", "started", "running", "downloading", "transcribing", "generating",
    "extracting", "ocr", "comments", "assembling", "consolidating", "feishu_upload",
    "generating_html", "in_progress",
})


async def _stream_await_link_pipelines(
    pipeline_ids: List[str],
    *,
    timeout_sec: float,
    trace_id: str,
    task_id: str,
    poll_sec: float = 4.0,
):
    """长等待 link_pipeline 后台任务完成（Coding Agent 式），超时前不进入终态回答。"""
    from .task_manager import get_task

    ids = [str(x).strip() for x in (pipeline_ids or []) if str(x).strip()]
    if not ids:
        return
    deadline = time.perf_counter() + max(30.0, float(timeout_sec or 600))
    yield _sse("pipeline_wait_start", {
        "trace_id": trace_id,
        "task_id": task_id or "",
        "pipeline_task_ids": ids,
        "timeout_sec": int(timeout_sec),
        "label": "等待链接文档化流水线完成",
    })
    last_emit = 0.0
    while time.perf_counter() < deadline:
        statuses: Dict[str, str] = {}
        any_active = False
        any_failed = False
        results: List[Dict[str, Any]] = []
        for pid in ids:
            row = get_task(pid) or {}
            st = str(row.get("status") or "unknown").lower()
            statuses[pid] = st
            if st in _PIPELINE_ACTIVE_STATUSES:
                any_active = True
            elif st in ("failed", "cancelled"):
                any_failed = True
            results.append({
                "task_id": pid,
                "status": st,
                "progress": row.get("progress"),
                "stage": row.get("stage"),
                "doc_filename": row.get("doc_filename"),
                "html_status": row.get("html_status"),
                "error": row.get("error"),
            })
        now = time.perf_counter()
        if now - last_emit >= poll_sec:
            last_emit = now
            yield _sse("pipeline_wait_progress", {
                "trace_id": trace_id,
                "task_id": task_id or "",
                "pipeline_task_ids": ids,
                "statuses": statuses,
                "results": results,
                "elapsed_ms": int((now - (deadline - timeout_sec)) * 1000),
            })
        if not any_active:
            yield _sse("pipeline_wait_end", {
                "trace_id": trace_id,
                "task_id": task_id or "",
                "ok": not any_failed,
                "timeout": False,
                "statuses": statuses,
                "results": results,
            })
            return
        await asyncio.sleep(min(poll_sec, max(0.5, deadline - time.perf_counter())))
    yield _sse("pipeline_wait_end", {
        "trace_id": trace_id,
        "task_id": task_id or "",
        "ok": False,
        "timeout": True,
        "pipeline_task_ids": ids,
        "statuses": {pid: (get_task(pid) or {}).get("status") for pid in ids},
    })


def _tool_result_to_jsonable_str(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj[:80000]
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)[:80000]
    except Exception:
        return str(obj)[:80000]


def _clean_html_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_title_and_text(html: str) -> Dict[str, str]:
    title = ""
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, flags=re.I)
    if m:
        title = _clean_html_text(m.group(1))
    body = _clean_html_text(html)
    if title and title in body:
        body = body.replace(title, "", 1).strip()
    return {"title": title[:160], "text": body[:4000]}


async def _async_iter_llm_token_stream(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.3,
    max_tokens: int = 1800,
    timeout: float = 120.0,
    thinking_enabled: bool = True,
) -> Any:
    """
    对齐 Claude/Cursor：从网关拉取 text_delta，异步产出 content 片段。
    参考 Anthropic content_block_delta.text_delta / OpenAI choices[].delta.content。
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _producer() -> None:
        try:
            from provider_adapters import invoke_stream_unified

            for item in invoke_stream_unified(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                thinking_enabled=thinking_enabled,
            ):
                loop.call_soon_threadsafe(q.put_nowait, ("chunk", item))
            loop.call_soon_threadsafe(q.put_nowait, ("end", None))
        except Exception as ex:
            loop.call_soon_threadsafe(q.put_nowait, ("err", ex))

    loop.run_in_executor(_executor, _producer)

    while True:
        kind, payload = await q.get()
        if kind == "end":
            return
        if kind == "err":
            raise payload
        if kind == "chunk" and isinstance(payload, dict):
            piece = str(payload.get("content") or "")
            if payload.get("type") == "content" and piece:
                yield piece


async def _yield_answer_replay_delta(
    trace_id: str,
    task_id: Optional[str],
    full_answer: str,
):
    """
    MCP/非流式降级：一次推送整段文本 + stream_mode=replay。
    对齐 Vercel smoothStream / Cursor：网络层不必逐字 SSE，由前端打字机按字数率消化。
    """
    text = full_answer or ""
    if not text:
        return
    yield _sse(
        "answer_delta",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "content": text,
            "kind": "body",
            "stream_mode": "replay",
        },
    )


async def _invoke_langchain_tool(lc_tool: Any, arguments: Dict[str, Any]) -> Any:
    if hasattr(lc_tool, "ainvoke"):
        return await lc_tool.ainvoke(arguments)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: lc_tool.invoke(arguments))


# ReAct 各步必须走真实 LLM，禁止硬编码 step_think_delta 冒充推理
_REACT_LLM_PHASES = frozenset({"observe", "act", "plan", "deep", "rewrite"})

def _strip_react_display_markers(text: str) -> str:
    """去掉 ### Thought / ### Action 等展示用标记，避免污染 UI。"""
    if not text:
        return ""
    t = str(text)
    for pat in (
        r"^#{1,3}\s*Thought\s*\n?",
        r"^#{1,3}\s*Action\s*\n?",
        r"^#{1,3}\s*Observation\s*\n?",
        r"^#thought\s*\n?",
    ):
        t = re.sub(pat, "", t, flags=re.I | re.M)
    return t.strip()


_REACT_STEP_SYSTEM: Dict[str, str] = {
    "observe": (
        "你是 SuperBizAgent 的 ReAct 推理步。根据用户问题、工具目录、编排结论，"
        "用简体中文简要说明：任务目标、已掌握信息、缺什么、本轮计划。"
        "禁止输出 ### Thought / ### Action 等 Markdown 标题；不得编造已执行的工具结果。"
    ),
    "act": (
        "你是 SuperBizAgent 的工具规划步。用一行或列表写明拟调用的工具英文名"
        "（如 web_search、rag_search）及参数要点。"
        "禁止输出 ### Thought / ### Action；真正执行由系统 function calling 完成。"
    ),
    "plan": (
        "你是 Plan-Execute 的【Plan】步。将任务拆为 3-6 条可执行子步骤（简体中文列表），"
        "每条标明是否需调用工具及工具名。"
    ),
    "deep": "你是【Deep Think】步。扩展推理链：依赖、风险、备选路径（简体中文，6-10 句）。",
    "rewrite": (
        "你是 SuperBizAgent 编排段【Query 改写】节点。"
        "在保留 query_keywords（原问实体，语义不失真）的前提下，"
        "将问题改写为利于知识库检索/工具执行的表述，并产出 retrieval_terms（内部业务术语映射词）。"
        "query_keywords 与 retrieval_terms 是两步：前者来自原问 NER，后者是业务改造后的检索词，均须保留。"
        "仅输出一行 JSON："
        '{"rewritten_query":"…","query_summary":"最精简任务摘要","query_keywords":["原问实体"],"retrieval_terms":["内部术语"]}'
        "禁止 Markdown。"
    ),
    "intent": (
        "你是 SuperBizAgent 编排段【意图识别】节点。判定顺序必须遵守："
        "① 任务归属：消息含 task_ 前缀 id、或「任务恢复/重新启动/之前执行」等 → 有历史则 continue_main 并填 task_id；"
        "无进行中任务时，仍须对照【最近主任务】（含已结案）判断是否续接，禁止因句长或含 RAG/MCP 词就 new_main；"
        "② 仅当明确新课题且用户显式开新话题时 new_main；"
        "③ 最后才判 simple（仅限寒暄/自我介绍/与业务无关短句）。"
        "硬规则：含 xiaohongshu.com 或「画像」「链接分析」且无续接 task_id → new_main；"
        "用户追问或恢复先前 task_id → 必须 continue_main，禁止 simple；"
        "用户明确要求查知识库/Milvus/内部文档 → needs_rag=true，needs_web_search=false；"
        "查工具清单/SKILL/MCP 说明 → needs_rag=false，needs_web_search=false（走工具目录，非联网）。"
        "needs_web_search 仅当用户已开启联网开关且任务确实需要公开网页资料时为 true。"
        "禁止在正文输出 <|FunctionCallBegin|> 或编造工具调用；本步不做内部术语映射。"
        "仅输出一行 JSON："
        '{"mode":"simple|continue_main|new_main","task_id":"续接时填","reason":"中文短句",'
        '"confidence":0.0-1.0,"task_summary":"最精简但涵盖细节的任务摘要",'
        '"query_keywords":["NER实体/关键词，保语义"],"needs_rag":true|false,'
        '"needs_web_search":false,"skip_nodes":[]}'
        "禁止输出 Markdown 或其它说明。"
    ),
    "decompose": (
        "你是 SuperBizAgent 编排段【任务分解】节点。将改写后问题拆为可执行子任务。"
        "仅输出 JSON："
        '{"decomposition_type":"stage|parallel","sub_tasks":[{"index":1,"title":"中文","task_type":"scope|retrieval|verification|synthesis"}],"dependencies":[{"from":1,"to":2}]}'
        "禁止 Markdown。"
    ),
    "enhance": (
        "你是 SuperBizAgent 编排段【意图增强】节点。根据用户开关生成检索提示与核验点。"
        "search_keyword_queries 专指 Milvus 知识库检索词；"
        "web_search_queries 仅当用户已开启联网且任务需要公开网页时才输出，否则必须为空数组。"
        "用户只查知识库时禁止生成 web_search_queries。"
        "仅输出 JSON："
        '{"retrieval_hints":["…"],"search_keyword_queries":["知识库检索词"],"web_search_queries":[],'
        '"verification_points":["…"],"risk_flags":["…"],"search_objective":"…"}'
        "禁止 Markdown。"
    ),
    "slot": (
        "你是 SuperBizAgent 编排段【业务对齐】节点。识别领域、模块、操作类型与实体。"
        "仅输出 JSON："
        '{"domain":"…","module":"…","operation_type":"查询|分析|对比|生成","entities":["…"],"retrieval_terms":["…"],"needs_rag":true|false,"state":"待补全|已对齐"}'
        "禁止 Markdown。"
    ),
    "slot_decompose_bundle": (
        "你是 SuperBizAgent 编排段【业务对齐+任务分解】合并节点。"
        "一次输出业务槽位与子任务拆解，仅 JSON："
        '{"domain":"…","module":"…","operation_type":"查询|分析|对比|生成",'
        '"entities":["…"],"retrieval_terms":["…"],"needs_rag":true|false,"state":"已对齐",'
        '"decomposition_type":"stage|parallel",'
        '"sub_tasks":[{"index":1,"title":"中文","task_type":"scope|retrieval|verification|synthesis"}],'
        '"dependencies":[{"from":1,"to":2}]}'
        "禁止 Markdown。"
    ),
}


def _tools_catalog_brief(meta: Dict[str, Any]) -> str:
    rows = meta.get("tools") or []
    if not rows:
        return "（工具目录尚未加载）"
    return "\n".join(
        f"- {r.get('name')} [{r.get('source')}]: {(r.get('description') or '')[:100]}"
        for r in rows[:35]
    )


def _format_react_memory(memory: List[Dict[str, str]]) -> str:
    if not memory:
        return ""
    parts = [
        f"[{m.get('phase', '?')}] {_strip_react_display_markers(m.get('text', '') or '')}"
        for m in memory
        if _strip_react_display_markers(m.get("text", "") or "")
    ]
    return "【ReAct 推理链】\n" + "\n\n".join(parts)


async def _iter_react_llm_tokens(
    *,
    phase: str,
    user_message: str,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    tools_meta: Dict[str, Any],
    react_memory: List[Dict[str, str]],
    intent_snapshot: Optional[Dict[str, Any]] = None,
    pending_tool_name: str = "",
    pending_tool_args: Optional[Dict[str, Any]] = None,
    max_tokens: int = 520,
    include_tools_catalog: bool = True,
    thinking_enabled: bool = True,
):
    """单步 ReAct LLM 推理，逐 token 产出供上层写入 step_think_delta。"""
    sys = _REACT_STEP_SYSTEM.get(phase, "用简体中文简要推理本步。")
    catalog = _tools_catalog_brief(tools_meta)
    prior = _format_react_memory(react_memory)
    user_parts = [f"用户问题：{user_message}"]
    if intent_snapshot:
        snap_bits = [
            f"rewritten={intent_snapshot.get('rewritten_query', '')}",
            f"query_keywords={intent_snapshot.get('query_keywords') or intent_snapshot.get('keywords', [])}",
            f"retrieval_terms={intent_snapshot.get('retrieval_terms', [])}",
            f"task_summary={intent_snapshot.get('query_summary') or intent_snapshot.get('task_summary', '')}",
            f"needs_rag={intent_snapshot.get('needs_rag')}",
        ]
        user_parts.append("意图快照：" + "; ".join(snap_bits))
    if pending_tool_name:
        args_preview = json.dumps(pending_tool_args or {}, ensure_ascii=False)[:600]
        user_parts.append(
            f"本轮待执行工具（模型已选定，请说明为何调用）：{pending_tool_name}\n"
            f"参数要点：{args_preview}"
        )
    if prior:
        user_parts.append(prior)
    if include_tools_catalog and phase not in (
        "intent", "rewrite", "decompose", "enhance", "slot", "slot_decompose_bundle",
    ):
        user_parts.append(f"已挂载工具目录：\n{catalog}")
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    async for piece in _async_iter_llm_token_stream(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=0.35,
        max_tokens=max_tokens,
        timeout=90.0,
        thinking_enabled=thinking_enabled,
    ):
        yield piece


async def _yield_react_reasoning_analysis(
    *,
    trace_id: str,
    task_id: str,
    session_id: str,
    message: str,
    provider: str,
    base_url: str,
    api_key: str,
    model_resolved: str,
    tools_meta: Dict[str, Any],
    react_memory: List[Dict[str, str]],
    intent_snapshot: Optional[Dict[str, Any]],
    sub_plan_id: str,
    sub_index: int,
    step_id: str,
    pending_tool_name: str = "",
    pending_tool_args: Optional[Dict[str, Any]] = None,
):
    """LangGraph handoff 后：流式「推理分析」(Observe+Act)，对齐老项目 thought_chain。"""
    observe_text = ""
    act_text = ""
    yield _sse("thought_step_start", {
        "trace_id": trace_id, "task_id": task_id,
        "step_id": step_id, "step_name": "ReAct 推理",
        "step_type": "llm_call", "status": SUB_ACTING,
        "status_text": "思考中…",
        "sub_plan_id": sub_plan_id, "sub_index": sub_index,
        "node_kind": "llm_call", "llm_powered": True,
        "step_lane": "execution", "phase": "react_round",
    })
    yield _sse("step_think_start", {
        "trace_id": trace_id, "task_id": task_id, "step_id": step_id,
        "step_name": "ReAct 推理",
        "sub_plan_id": sub_plan_id, "sub_index": sub_index,
        "llm_powered": True, "phase": "react_round",
    })
    if api_key and model_resolved:
        try:
            async for piece in _iter_react_llm_tokens(
                phase="observe",
                user_message=message,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model_resolved,
                tools_meta=tools_meta,
                react_memory=react_memory,
                intent_snapshot=intent_snapshot,
                pending_tool_name=pending_tool_name,
                pending_tool_args=pending_tool_args,
            ):
                observe_text += piece
                yield _sse("step_think_delta", {
                    "trace_id": trace_id, "step_id": step_id,
                    "content": piece, "llm_powered": True,
                })
        except Exception as ex:
            observe_text = f"[推理失败] {ex}"
            yield _sse("step_think_delta", {
                "trace_id": trace_id, "step_id": step_id,
                "content": observe_text, "llm_powered": True,
            })
        if observe_text.strip():
            observe_text = _strip_react_display_markers(observe_text)
            react_memory.append({"phase": "observe", "text": observe_text})
        try:
            async for piece in _iter_react_llm_tokens(
                phase="act",
                user_message=message,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model_resolved,
                tools_meta=tools_meta,
                react_memory=react_memory,
                intent_snapshot=intent_snapshot,
                pending_tool_name=pending_tool_name,
                pending_tool_args=pending_tool_args,
            ):
                act_text += piece
                yield _sse("step_think_delta", {
                    "trace_id": trace_id, "step_id": step_id,
                    "content": piece, "llm_powered": True,
                })
        except Exception as ex:
            act_text = f"[工具规划失败] {ex}"
            yield _sse("step_think_delta", {
                "trace_id": trace_id, "step_id": step_id,
                "content": act_text, "llm_powered": True,
            })
        if act_text.strip():
            act_text = _strip_react_display_markers(act_text)
            react_memory.append({"phase": "act", "text": act_text})
    yield _sse("step_think_end", {
        "trace_id": trace_id, "step_id": step_id, "llm_powered": True,
    })
    cost_ms = 0
    combined = _strip_react_display_markers(f"{observe_text}\n{act_text}".strip())
    result_brief = brief_from_react_act_text(act_text or observe_text)
    yield _sse("thought_step_end", {
        "trace_id": trace_id, "task_id": task_id,
        "step_id": step_id,
        "step_name": "ReAct 推理",
        "status": SUB_DONE,
        "elapsed_ms": cost_ms,
        "status_text": "完成",
        "description": result_brief,
        "result_brief": result_brief,
        "sub_plan_id": sub_plan_id, "sub_index": sub_index,
        "node_kind": "llm_call", "phase": "react_round",
        "think_text": combined,
        "success": True, "llm_powered": True,
        "token_count": max(12, len(combined) // 4),
        "step_lane": "execution",
    })


# ── 意图识别（必做，规则预检；复杂任务才进入主任务框架） ──

def _is_simple_intent(q: str) -> bool:
    """生活化闲聊 / 自我介绍类：一次性直答，不建主任务、不入任务历史。"""
    m = (q or "").strip()
    if not m:
        return True
    from .chat_context_memory import (
        _CONTINUE_HINTS,
        _has_link_analysis_intent,
        _looks_like_continuation,
        _looks_like_task_recall,
        _looks_like_task_resume,
        _looks_like_task_status_inquiry,
    )

    low = m.lower()
    meta_self_hints = (
        "你是谁", "你是什么", "你有什么能力", "你能做什么", "你可以做什么",
        "介绍一下你自己", "介绍你自己", "你有什么本事",
    )
    if any(h in m or h in low for h in meta_self_hints):
        return True

    # 续接/恢复/进度追问：不得在未判归属前标 simple（如「继续」「那你继续做啊」）
    if _looks_like_task_resume(m) or _looks_like_task_status_inquiry(m):
        return False
    if any(h in m for h in _CONTINUE_HINTS):
        return False
    if _looks_like_continuation(m, None):
        return False

    if _has_link_analysis_intent(m) or _looks_like_task_recall(m):
        return False
    simple_hints = (
        "你好", "您好", "嗨", "hello", "hi", "hey",
        "你是谁", "你是什么", "什么模型", "哪个模型", "谁开发", "谁做的",
        "你能做什么", "你可以帮", "你能帮", "介绍一下你自己", "介绍你自己",
        "能唱歌", "会唱歌", "能跳舞", "讲个笑话", "讲笑话",
        "谢谢", "感谢", "再见", "拜拜", "在吗",
        "帮助我做哪些", "能做什么", "怎么用", "介绍一下", "什么是",
        "你可以帮助", "你能做什么",
    )
    if len(m) <= 56 and any(h in m or h in low for h in simple_hints):
        return True
    # 短追问（含「现在呢」「好了吗」）不得判 simple；「什么」中的「么」不算追问
    if re.search(r"(吗|呢|咋|如何|怎么|怎样)", m):
        return False
    if "?" in m or "？" in m:
        if len(m) <= 32:
            return False
    # 短句仅在为明确寒暄/自我介绍时标 simple；「继续」等已在上方排除
    if len(m) <= 18 and "?" not in m and "？" not in m:
        if any(h in m or h in low for h in simple_hints):
            return True
        return False
    complex_hints = (
        "帮我", "请帮", "执行", "查询", "分析", "生成", "创建", "导出", "上传",
        "调用", "配置", "排查", "修复", "部署", "文档", "图例", "长页", "飞书",
        "mcp", "工具", "rag", "检索",
        "链接", "评论", "爬虫", "抓取", "小红书", "抖音", "文档化", "主页",
    )
    if len(m) > 24 and any(h in m for h in complex_hints):
        return False
    return len(m) <= 28




def _build_intent_rewrite_snapshot_for_message(query: str, link_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    link_ctx = link_ctx or {}
    query = (query or "").strip()
    rule_hit = len(query) <= 2 or query in {"1", "2", "3", "?", "？"}
    rewritten = query
    for src, dst in (("那个", "相关对象"), ("这个", "当前对象"), ("刚才说的", "上文提到的问题"), ("多少钱", "价格是多少")):
        rewritten = rewritten.replace(src, dst)
    needs_rag = bool(any(k in query for k in ("知识库", "文档", "资料", "参考", "出处", "来源", "检索", "召回", "RAG")))
    # 关键词/检索词仅在业务对齐节点生成，意图阶段不写 keywords
    return {
        "query": query,
        "rewritten_query": rewritten,
        "needs_rag": needs_rag,
        "confidence": 0.5 if rule_hit else 0.86,
        "rewrite_state": "rewrite_confirm" if not rule_hit else "rewrite_hold",
        "metadata": {
            "domain": "通用",
            "module": "未指定",
            "state": "待补全",
            "rule_hit": rule_hit,
            "rule_group": "noise" if rule_hit else "",
        },
    }

def _append_session_messages(session_id: str, user_message: str, assistant_content: str, *, ephemeral: bool = False, task_audit: Optional[Dict[str, Any]] = None) -> None:
    _messages.setdefault(session_id, []).append({"role": "user", "content": user_message})
    payload: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
    if ephemeral:
        payload["ephemeral"] = True
        payload["task_kind"] = "simple"
    if task_audit:
        payload["task_audit"] = task_audit
    _messages[session_id].append(payload)
    if session_id in _sessions:
        _sessions[session_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _store_persist(session_id, _sessions[session_id], _messages.get(session_id, []), mark_dirty=True)


def _skill_result_is_doc_only(raw_out: Any) -> bool:
    """SKILL 工具若仅返回 SKILL.md 正文，视为未实际执行（需本地运行时）。"""
    if not isinstance(raw_out, dict):
        return False
    if raw_out.get("execution_mode") == "documentation_only":
        return True
    body = str(raw_out.get("body_md") or "")
    markers = ("MANDATORY", "bootstrap", "Runtime Setup", "运行时")
    return bool(raw_out.get("ok")) and any(m in body for m in markers)


_RAG_RETRIEVAL_TOOL_NAMES = frozenset({
    "rag_search", "rag_retrieve", "kb_search", "vector_search", "knowledge_base_search",
})


def _filter_rag_tools_when_prefetched(
    tools: List[Any],
    meta: Dict[str, Any],
    *,
    rag_prefetch_done: bool,
    rag_slice_count: int,
) -> tuple[List[Any], Dict[str, Any]]:
    """编排/预取已命中切片时，执行段不再暴露检索类工具，避免重复 rag_search。"""
    if not rag_prefetch_done or rag_slice_count <= 0:
        return tools, meta
    kept = [
        t for t in tools
        if str(getattr(t, "name", "") or "").lower() not in _RAG_RETRIEVAL_TOOL_NAMES
    ]
    if len(kept) == len(tools):
        return tools, meta
    meta = dict(meta or {})
    catalog = [
        row for row in (meta.get("tools") or [])
        if str(row.get("name") or "").lower() not in _RAG_RETRIEVAL_TOOL_NAMES
    ]
    meta["tools"] = catalog
    meta["total"] = len(catalog)
    meta["rag_tools_filtered"] = True
    return kept, meta


def _filter_skill_tools_for_execution(
    tools: List[Any],
    meta: Dict[str, Any],
    message: str,
) -> tuple[List[Any], Dict[str, Any]]:
    """非 /command 挂载时，执行段不向 LLM 暴露 SKILL 工具，避免误调 agent-browser 等说明文档。"""
    if (message or "").strip().startswith("/"):
        return tools, meta
    kept = [t for t in tools if not str(getattr(t, "name", "") or "").startswith("skill_")]
    if len(kept) == len(tools):
        return tools, meta
    meta = dict(meta or {})
    catalog = [row for row in (meta.get("tools") or []) if row.get("source") != "skill"]
    meta["tools"] = catalog
    meta["total"] = len(catalog)
    meta["skill_count"] = 0
    meta["skill_tools_filtered"] = True
    return kept, meta


_GRAPH_EXEC_BOOTSTRAP: Optional[Dict[str, Any]] = None
def set_graph_execution_bootstrap(payload: Optional[Dict[str, Any]]) -> None:
    global _GRAPH_EXEC_BOOTSTRAP
    _GRAPH_EXEC_BOOTSTRAP = payload
def pop_graph_execution_bootstrap() -> Optional[Dict[str, Any]]:
    global _GRAPH_EXEC_BOOTSTRAP
    boot = _GRAPH_EXEC_BOOTSTRAP
    _GRAPH_EXEC_BOOTSTRAP = None
    return boot

# ── 核心 SSE 流（含 SPAN 审计） ──

async def chat_stream_v2(
    message: str,
    session_id: str = "default",
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_profile: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    rag_prefetch: bool = False,
    web_search: bool = False,
    read_comments: bool = False,
    include_rss: bool = False,
    deep_think: bool = False,
    chat_max_tool_rounds: Optional[int] = None,
    chat_tool_timeout_sec: Optional[float] = None,
    chat_tool_max_retry: Optional[int] = None,
    chat_distinct_tool_fail_limit: Optional[int] = None,
    graph_execution_boot: Optional[Dict[str, Any]] = None,
    _langgraph_orchestration_done: bool = False,
    client_cur_task: Optional[Dict[str, Any]] = None,
    client_main_task_history: Optional[List] = None,
    memory_prepared: Optional[Dict[str, Any]] = None,
    orch_pipeline_nodes: Optional[Dict[str, Any]] = None,
):
    """完整 SSE 流（默认 LangGraph 编排；handoff 时 _langgraph_orchestration_done=True）。"""
    graph_boot = graph_execution_boot if graph_execution_boot is not None else pop_graph_execution_bootstrap()
    if memory_prepared is None:
        from .chat_context_memory import prepare_session_memory

        memory_prepared = await prepare_session_memory(
            session_id,
            client_cur_task=client_cur_task,
            client_history=client_main_task_history,
            extra_tokens=max(32, len(message) // 2),
        )
    mem_ctx: Dict[str, Any] = dict(memory_prepared or {})
    if graph_boot and isinstance(graph_boot.get("memory_prepared"), dict):
        mem_ctx = {**mem_ctx, **graph_boot["memory_prepared"]}
    rag_context_block: Optional[str] = None
    rss_context_block: Optional[str] = None
    rag_citation_instruction: str = ""

    from .rss_reader import bind_chat_user, build_chat_context_block, message_wants_rss_context

    bind_chat_user(user_id)
    if include_rss or message_wants_rss_context(message):
        rss_context_block = build_chat_context_block(
            user_id,
            limit=12,
            query=(message or "") if message_wants_rss_context(message) and not include_rss else "",
            unread_only=bool(include_rss),
        )

    if session_id not in _sessions:
        ensure_session(session_id, message[:30] or "新对话")
    elif message and (_sessions[session_id].get("title") or "") in ("", "新对话"):
        _sessions[session_id]["title"] = message[:40]
        _sessions[session_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")

    cfg = load_chat_llm_config()

    if chat_max_tool_rounds is not None:
        cfg["chat_max_tool_rounds"] = chat_max_tool_rounds
    if chat_tool_timeout_sec is not None:
        cfg["chat_tool_timeout_sec"] = chat_tool_timeout_sec
    if chat_tool_max_retry is not None:
        cfg["chat_tool_max_retry"] = chat_tool_max_retry
    if chat_distinct_tool_fail_limit is not None:
        cfg["chat_distinct_tool_fail_limit"] = chat_distinct_tool_fail_limit

    creds = resolve_chat_api_credentials(cfg)
    provider = creds["provider"]
    api_key = creds["api_key"]
    base_url = creds["base_url"]
    model_resolved = (model or "").strip() or creds["model"]
    if not api_key or not model_resolved:
        import logging as _cfg_log

        diag = chat_llm_config_diagnostics()
        _cfg_log.getLogger(__name__).error(
            "[AI问答-配置|ai_chat.chat_stream_v2|config.json|硬编执行|加载] "
            "LLM 凭证未就绪; api_key=%s; model=%s; config_path=%s; agent_dir=%s; "
            "volc_in_json=%s; gateway_nodes=%s",
            bool(api_key),
            model_resolved or "(empty)",
            diag.get("config_path"),
            diag.get("agent_dir"),
            diag.get("volcengine_api_key_set"),
            diag.get("gateway_nodes"),
        )
    system_prompt = assemble_chat_system_prompt(
        cfg, agent_id, agent_profile=agent_profile, user_id=user_id
    )

    trace_id = _new_id("trace_")

    # LangGraph 编排段自行发送 stream_open / tools_discovered，避免重复且覆盖旧版单步意图
    if graph_boot is None and not _langgraph_orchestration_done:
        try:
            from .chat_graph_runner import langgraph_enabled, stream_langgraph_chat
            import logging as _lg_log

            if not langgraph_enabled():
                _lg_log.getLogger(__name__).warning(
                    "[AI问答-LangGraph|降级|chat_stream_v2|session:%s] CHAT_USE_LANGGRAPH=0，禁止走旧版单步意图",
                    session_id,
                )
                from .chat_error_handler import stream_user_error_sse

                async for line in stream_user_error_sse(
                    "LangGraph 编排未启用：请在环境变量中设置 CHAT_USE_LANGGRAPH=1 并重启后端",
                    session_id=session_id,
                    trace_id=trace_id,
                    stage="编排配置",
                    user_message=message,
                ):
                    yield line
                return
            async for _lg_ev in stream_langgraph_chat(
                message, session_id, model=model, agent_id=agent_id, agent_profile=agent_profile,
                user_id=user_id, rag_prefetch=rag_prefetch, web_search=web_search,
                read_comments=read_comments, deep_think=deep_think,
                chat_max_tool_rounds=chat_max_tool_rounds,
                chat_tool_timeout_sec=chat_tool_timeout_sec, chat_tool_max_retry=chat_tool_max_retry,
                chat_distinct_tool_fail_limit=chat_distinct_tool_fail_limit,
                client_cur_task=client_cur_task,
                client_main_task_history=client_main_task_history,
                memory_prepared=memory_prepared,
                orch_pipeline_nodes=orch_pipeline_nodes,
                _precomputed={
                    "cfg": cfg,
                    "provider": provider,
                    "api_key": api_key,
                    "base_url": base_url,
                    "model_resolved": model_resolved,
                    "system_prompt": system_prompt,
                },
            ):
                yield _lg_ev
            return
        except ImportError:
            import logging as _lg_log
            _lg_log.getLogger(__name__).warning('[AI问答-LangGraph|降级] langgraph 未安装 session=%s', session_id)
            from .chat_error_handler import stream_user_error_sse

            async for line in stream_user_error_sse(
                "LangGraph 未安装，无法执行固定编排链路",
                session_id=session_id,
                trace_id=trace_id,
                stage="编排依赖",
                user_message=message,
            ):
                yield line
            return
        except Exception as ex:
            import logging as _lg_log
            _lg_log.getLogger(__name__).exception(
                "[AI问答-LangGraph|异常|chat_stream_v2|session:%s] graph_stream_failed",
                session_id,
            )
            from .chat_error_handler import stream_user_error_sse

            async for line in stream_user_error_sse(
                ex,
                session_id=session_id,
                trace_id=trace_id,
                stage="LangGraph 编排",
                user_message=message,
            ):
                yield line
            return

    yield _sse("stream_open", {"session_id": session_id, "stage": "准备中", "progress": 1})

    from .link_doc_routing import analyze_link_doc_intent
    from .chat_tool_registry import (
        load_all_chat_tools,
        is_tools_inventory_query,
        format_tools_catalog_markdown,
    )

    link_ctx = analyze_link_doc_intent(message, read_comments=read_comments)
    if link_ctx.get("run_link_pipeline") and link_ctx.get("urls"):
        from .link_doc_routing import enqueue_link_pipeline_from_chat

        auto_pipe = await enqueue_link_pipeline_from_chat(
            link_ctx,
            user_prompt=message,
            read_comments=read_comments,
            session_id=session_id,
        )
        if auto_pipe.get("ok"):
            yield _sse("link_pipeline_auto_start", {**auto_pipe, "trace_id": trace_id, "session_id": session_id})
    tools_inventory_query = is_tools_inventory_query(message)

    chat_lc_tools_pre: List[Any] = []
    tools_meta_pre: Dict[str, Any] = {}
    if graph_boot is not None:
        chat_lc_tools_pre = list(graph_boot.get("chat_lc_tools") or [])
        tools_meta_pre = dict(graph_boot.get("tools_meta") or {})
        if not chat_lc_tools_pre:
            try:
                from .chat_warmup import get_cached_tools

                cached = get_cached_tools(read_comments=read_comments)
                if cached:
                    chat_lc_tools_pre, tools_meta_pre = cached
                else:
                    chat_lc_tools_pre, tools_meta_pre = await load_all_chat_tools(
                        read_comments=read_comments
                    )
            except Exception as _te:
                tools_meta_pre = dict(tools_meta_pre or {})
                tools_meta_pre.setdefault("mcp_error", str(_te))
    else:
        try:
            chat_lc_tools_pre, tools_meta_pre = await load_all_chat_tools(read_comments=read_comments)
        except Exception as _te:
            tools_meta_pre = {"total": 0, "tools": [], "mcp_error": str(_te)}
        yield _sse("tools_discovered", {
            "trace_id": trace_id,
            "task_id": "",
            "total": tools_meta_pre.get("total", 0),
            "builtin_count": tools_meta_pre.get("builtin_count", 0),
            "mcp_count": tools_meta_pre.get("mcp_count", 0),
            "skill_count": tools_meta_pre.get("skill_count", 0),
            "read_comments": read_comments,
            "tools": tools_meta_pre.get("tools", [])[:80],
            "mcp_error": tools_meta_pre.get("mcp_error") or "",
            "tools_inventory_query": tools_inventory_query,
        })

    time_start = time.perf_counter()
    total_tokens = 0
    step_idx = 0
    search_results = None
    group_seq = 0

    framework = ""
    if isinstance(agent_profile, dict):
        framework = str(agent_profile.get("framework") or "").strip().lower()
    # 强制硬编码：复杂任务固定走 ReAct；简单任务才允许单轮直答
    if not framework:
        framework = "react" if not _is_simple_intent(message) else "assistant"

    intent_task_kind = "simple" if _is_simple_intent(message) else "main"
    use_main_task = False
    task_id: Optional[str] = None
    intent_rewrite_snapshot: Dict[str, Any] = {}
    rewrite_state = "intent"
    react_memory: List[Dict[str, str]] = []

    def _build_intent_rewrite_snapshot() -> Dict[str, Any]:
        query = (message or "").strip()
        rule_hit = False
        rule_group = ""
        if len(query) <= 2 or query in {"1", "2", "3", "?", "？"}:
            rule_hit = True
            rule_group = "noise"
        business_keywords = []
        if any(k in query for k in ("运维", "故障", "排查", "修复", "日志", "告警")):
            business_keywords.append("运维")
        if any(k in query for k in ("订单", "退款", "支付", "商品", "用户", "客户", "业务")):
            business_keywords.append("业务")
        if any(k in query for k in ("RAG", "检索", "召回", "知识库", "文档", "向量")):
            business_keywords.append("检索/RAG")
        if link_ctx.get("link_doc_relevant") or any(
            k in query for k in ("链接", "评论", "爬虫", "小红书", "抖音", "文档化")
        ):
            business_keywords.append("链接文档化")
        if any(k in query for k in ("对比", "比较", "哪个好", "差异", "区别")):
            business_keywords.append("对比")
        if any(k in query for k in ("怎么", "如何", "为什么", "步骤", "流程", "方案")):
            business_keywords.append("推理/步骤")
        rewritten = query
        for src, dst in (("那个", "相关对象"), ("这个", "当前对象"), ("刚才说的", "上文提到的问题"), ("多少钱", "价格是多少")):
            rewritten = rewritten.replace(src, dst)
        needs_rag = bool(any(k in query for k in ("知识库", "文档", "资料", "参考", "出处", "来源", "检索", "召回", "RAG")))
        confidence = 0.5 if rule_hit else 0.86
        metadata = {
            "domain": business_keywords[0] if business_keywords else "通用",
            "module": business_keywords[1] if len(business_keywords) > 1 else (business_keywords[0] if business_keywords else "未指定"),
            "state": "待补全",
            "rule_hit": rule_hit,
            "rule_group": rule_group,
        }
        return {
            "query": query,
            "rewritten_query": rewritten,
            "keywords": business_keywords,
            "needs_rag": needs_rag,
            "confidence": confidence,
            "rewrite_state": "rewrite_confirm" if not rule_hit else "rewrite_hold",
            "metadata": metadata,
        }

    def _alloc_step_group() -> tuple[str, int]:
        nonlocal group_seq
        group_seq += 1
        if task_id:
            try:
                from .chat_context_memory import touch_task_group_seq

                touch_task_group_seq(str(task_id), group_seq)
            except Exception:
                pass
        return _new_id("subplan_"), group_seq

    def _build_post_intent_steps() -> List[tuple[str, str, str]]:
        """执行段前置：每步一组。ReAct 必须 observe+act 合并为一步，禁止拆成两个步骤组。"""
        post: List[tuple[str, str, str]] = []
        if deep_think:
            post.append(("deep", "深度推理", ""))
        if rag_prefetch and not _langgraph_orchestration_done:
            post.append(("rewrite", "Query 改写", ""))
        if framework == "plan_execute" and not _langgraph_orchestration_done:
            post.append(("plan", "任务分解", ""))
        elif (
            framework == "react"
            and not _langgraph_orchestration_done
            and not _mcp_react_loop_enabled(framework, chat_lc_tools_pre, provider, base_url)
        ):
            post.append(("react_round", "ReAct · 推理与行动", ""))
        return post


    skip_phase1 = bool(_langgraph_orchestration_done and graph_boot)
    continue_main_task = False
    slot_snapshot_exec: Dict[str, Any] = {}
    enhancement_snapshot_exec: Dict[str, Any] = {}
    plan_steps_boot: List[Any] = []
    if skip_phase1:
        boot = graph_boot or {}
        trace_id = str(boot.get("trace_id") or trace_id)
        task_id = boot.get("task_id")
        web_search = bool(web_search or boot.get("web_search"))
        rag_prefetch = bool(rag_prefetch or boot.get("rag_prefetch"))
        intent_task_kind = boot.get("intent_task_kind") or (
            "main" if boot.get("use_main_task") else "simple"
        )
        use_main_task = bool(boot.get("use_main_task"))
        continue_main_task = bool(boot.get("continue_main_task"))
        framework = str(boot.get("framework") or "react")
        intent_rewrite_snapshot = dict(boot.get("intent_rewrite_snapshot") or {})
        slot_snapshot_exec = dict(boot.get("slot_snapshot") or {})
        enhancement_snapshot_exec = dict(boot.get("enhancement_snapshot") or {})
        if boot.get("rewritten_query"):
            intent_rewrite_snapshot["rewritten_query"] = boot.get("rewritten_query")
        react_memory = list(boot.get("react_memory") or [])
        rewrite_state = intent_rewrite_snapshot.get("rewrite_state") or "rewrite_hold"
        step_idx = 1
        plan_steps_boot = list(graph_boot.get("plan_steps") or [])
        if plan_steps_boot:
            react_memory.append({
                "phase": "plan",
                "text": " → ".join(
                    str(s.get("title") or "")[:48] for s in plan_steps_boot[:6] if s.get("title")
                ),
            })
        group_seq = int((graph_boot or {}).get("group_seq") or group_seq)
        _boot_rag_ctx = str(boot.get("rag_context_block") or "").strip()
        if _boot_rag_ctx:
            rag_context_block = _boot_rag_ctx
        _boot_cite = str(boot.get("rag_citation_instruction") or "").strip()
        if _boot_cite:
            rag_citation_instruction = _boot_cite
    else:
        # ── Phase 1: 必做意图识别；通过后才建主任务并执行后续步骤 ──
        thinking_steps: List[tuple[str, str, str]] = [
            ("intent", "意图识别", f"分析用户意图: {message[:60]}"),
        ]

        yield _sse("thinking_start", {
            "trace_id": trace_id,
            "task_id": "",
            "session_id": session_id,
            "ephemeral": True,
        })

        _think_i = 0
        while _think_i < len(thinking_steps):
            step_id_code, step_name, step_default_text = thinking_steps[_think_i]
            _think_i += 1
            step_idx += 1
            sub_plan_id, sub_index = _alloc_step_group()
            node_kind = "tool_call" if step_id_code in ("rag_retrieve", "web") else "sub_task"
            span_step: Dict[str, Any] = {"step_id": _new_id("step_")}
            if task_id:
                span_step = _span_step(
                    task_id, session_id, "reasoning", f"{step_idx}. {step_name}",
                    parent_step_id=sub_plan_id,
                )
                _span_start(span_step["step_id"], input_payload={"query": message, "phase": step_id_code})

            if step_id_code == "react_round":
                step_begin = time.perf_counter()
                observe_text = ""
                act_text = ""
                yield _sse("thought_step_start", {
                    "trace_id": trace_id, "task_id": task_id or "",
                    "step_id": span_step["step_id"], "step_name": step_name,
                    "step_type": "llm_call", "status": SUB_ACTING, "status_text": "推理与选工具…",
                    "sub_plan_id": sub_plan_id, "sub_index": sub_index,
                    "node_kind": "llm_call", "operation": step_name, "target": message[:40],
                    "llm_powered": True,
                })
                if api_key and model_resolved:
                    yield _sse("step_think_start", {
                        "trace_id": trace_id, "task_id": task_id or "", "step_id": span_step["step_id"],
                        "step_name": step_name, "sub_plan_id": sub_plan_id, "sub_index": sub_index,
                        "llm_powered": True,
                    })
                    try:
                        async for piece in _iter_react_llm_tokens(
                            phase="observe",
                            user_message=message,
                            provider=provider,
                            base_url=base_url,
                            api_key=api_key,
                            model=model_resolved,
                            tools_meta=tools_meta_pre,
                            react_memory=react_memory,
                            intent_snapshot=intent_rewrite_snapshot or None,
                        ):
                            observe_text += piece
                            yield _sse("step_think_delta", {
                                "trace_id": trace_id, "step_id": span_step["step_id"],
                                "content": piece, "llm_powered": True,
                            })
                    except Exception as ex:
                        observe_text = f"[Observe 失败] {ex}"
                        yield _sse("step_think_delta", {
                            "trace_id": trace_id, "step_id": span_step["step_id"],
                            "content": observe_text, "llm_powered": True,
                        })
                    if observe_text.strip():
                        react_memory.append({"phase": "observe", "text": observe_text.strip()})
                    try:
                        async for piece in _iter_react_llm_tokens(
                            phase="act",
                            user_message=message,
                            provider=provider,
                            base_url=base_url,
                            api_key=api_key,
                            model=model_resolved,
                            tools_meta=tools_meta_pre,
                            react_memory=react_memory,
                            intent_snapshot=intent_rewrite_snapshot or None,
                        ):
                            act_text += piece
                            yield _sse("step_think_delta", {
                                "trace_id": trace_id, "step_id": span_step["step_id"],
                                "content": piece, "llm_powered": True,
                            })
                    except Exception as ex:
                        act_text = f"[Act 失败] {ex}"
                        yield _sse("step_think_delta", {
                            "trace_id": trace_id, "step_id": span_step["step_id"],
                            "content": act_text, "llm_powered": True,
                        })
                    if act_text.strip():
                        react_memory.append({"phase": "act", "text": act_text.strip()})
                    yield _sse("step_think_end", {
                        "trace_id": trace_id, "step_id": span_step["step_id"], "llm_powered": True,
                    })
                cost_ms = int((time.perf_counter() - step_begin) * 1000)
                result_brief = brief_from_react_act_text(act_text or observe_text)
                round_payload = build_flow_step_output(
                    phase="react_round",
                    cost_ms=cost_ms,
                    extra={
                        "observe_preview": (observe_text or "")[:500],
                        "act_preview": (act_text or "")[:500],
                        "llm_powered": True,
                    },
                )
                if act_text.strip():
                    step_output = dumps_step_output(
                        build_llm_step_output(
                            answer=f"【Observe】\n{observe_text}\n\n【Act】\n{act_text}",
                            cost_ms=cost_ms,
                        )
                    )
                else:
                    step_output = dumps_step_output(round_payload)
                yield _sse("thought_step_end", {
                    "trace_id": trace_id, "task_id": task_id or "", "step_id": span_step["step_id"],
                    "step_name": step_name, "status": SUB_DONE,
                    "elapsed_ms": cost_ms, "status_text": "完成",
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "description": result_brief,
                    "sub_plan_id": sub_plan_id, "sub_index": sub_index,
                    "node_kind": "llm_call",
                    "result_brief": result_brief,
                    "io_links": [],
                    "input_text": json.dumps({"query": message[:500], "phase": "react_round"}, ensure_ascii=False),
                    "output_text": step_output,
                    "phase": "react_round",
                    "success": True,
                    "confidence": 0.92,
                    "token_count": max(12, (len(observe_text) + len(act_text)) // 4),
                    "llm_powered": True,
                })
                if task_id:
                    _span_finish(
                        span_step["step_id"], status=SUB_DONE,
                        output_payload=round_payload,
                        open_layer={
                            "objective": result_brief,
                            "current_assessment": "ReAct 一轮推理完成",
                            "progress_percent": min(90, step_idx * 15),
                            "decision": "continue",
                        },
                    )
                    yield _sse("span_update", {
                        "task_id": task_id, "step_id": span_step["step_id"],
                        "step_name": step_name, "elapsed_ms": cost_ms, "status": SUB_DONE,
                        "parent_status": PARENT_EXECUTING,
                        "token_count": max(12, (len(observe_text) + len(act_text)) // 4),
                        "success": True, "confidence": 0.92,
                    })
                continue

            use_react_llm = (
                step_id_code in _REACT_LLM_PHASES
                and bool(api_key and model_resolved)
            )
            step_begin = time.perf_counter()
            llm_step_text = ""

            yield _sse("thought_step_start", {
                "trace_id": trace_id, "task_id": task_id or "",
                "step_id": span_step["step_id"], "step_name": step_name,
                "step_type": "llm_call" if use_react_llm else "reasoning",
                "status": SUB_ACTING, "status_text": "LLM 推理中…" if use_react_llm else "执行中…",
                "sub_plan_id": sub_plan_id, "sub_index": sub_index,
                "node_kind": "llm_call" if use_react_llm else node_kind,
                "operation": step_name, "target": message[:40],
            })

            if use_react_llm:
                yield _sse("step_think_start", {
                    "trace_id": trace_id, "task_id": task_id or "", "step_id": span_step["step_id"],
                    "step_name": step_name, "sub_plan_id": sub_plan_id, "sub_index": sub_index,
                    "llm_powered": True,
                })
                try:
                    async for piece in _iter_react_llm_tokens(
                        phase=step_id_code,
                        user_message=message,
                        provider=provider,
                        base_url=base_url,
                        api_key=api_key,
                        model=model_resolved,
                        tools_meta=tools_meta_pre,
                        react_memory=react_memory,
                        intent_snapshot=intent_rewrite_snapshot if intent_rewrite_snapshot else None,
                    ):
                        llm_step_text += piece
                        yield _sse("step_think_delta", {
                            "trace_id": trace_id, "step_id": span_step["step_id"],
                            "content": piece, "llm_powered": True,
                        })
                except Exception as ex:
                    from .chat_error_handler import llm_analyze_error_for_user

                    llm_step_text = await llm_analyze_error_for_user(
                        error_type=type(ex).__name__,
                        error_message=str(ex)[:400],
                        stage="ReAct 推理",
                        user_message=message,
                    )
                    yield _sse("step_think_delta", {
                        "trace_id": trace_id, "step_id": span_step["step_id"],
                        "content": llm_step_text,
                        "llm_powered": True,
                        "error_analyzed": True,
                    })
                yield _sse("step_think_end", {
                    "trace_id": trace_id, "step_id": span_step["step_id"], "llm_powered": True,
                })
                if llm_step_text.strip():
                    react_memory.append({"phase": step_id_code, "text": llm_step_text.strip()})
                    yield _sse("thinking_delta", {
                        "trace_id": trace_id,
                        "task_id": task_id or "",
                        "step_id": span_step["step_id"],
                        "content": llm_step_text,
                    })
            # 非 LLM 步骤（如意图识别）不伪造「思考」流式文案，结果见 thought_step_end.result_brief

            if step_id_code == "intent":
                from .chat_context_memory import hydrate_client_task_context, resolve_intent_mode

                _icur, _ihist = hydrate_client_task_context(
                    session_id,
                    client_cur_task=client_cur_task if isinstance(client_cur_task, dict) else None,
                    client_main_task_history=client_main_task_history
                    if isinstance(client_main_task_history, list)
                    else None,
                )
                _intent_dec = resolve_intent_mode(
                    message,
                    cur_task=_icur,
                    is_simple_heuristic=_is_simple_intent(message),
                    main_task_history=_ihist,
                )
                _imode = str(_intent_dec.get("mode") or "new_main")
                intent_task_kind = "simple" if _imode == "simple" else "main"
                use_main_task = _imode in ("new_main", "continue_main")
                continue_main_task = _imode == "continue_main"
                if continue_main_task and _intent_dec.get("task_id"):
                    task_id = str(_intent_dec["task_id"])
                intent_rewrite_snapshot = _build_intent_rewrite_snapshot()
                if intent_rewrite_snapshot.get("rewritten_query") != intent_rewrite_snapshot.get("query"):
                    yield _sse("thinking_delta", {
                        "trace_id": trace_id,
                        "task_id": task_id or "",
                        "step_id": span_step["step_id"],
                        "content": f"\n\n[query_rewrite] {intent_rewrite_snapshot['rewritten_query']}\n",
                    })
            cost_ms = int((time.perf_counter() - step_begin) * 1000)
            step_status = SUB_DONE
            step_input = json.dumps({"query": message[:500], "phase": step_id_code, "framework": framework}, ensure_ascii=False)
            flow_extra: Dict[str, Any] = {}
            result_brief = ""
            if step_id_code == "intent":
                if continue_main_task:
                    result_brief = (_intent_dec.get("reason") or "续接当前/最近主任务")[:120]
                else:
                    result_brief = format_intent_result_brief_cn(
                        simple=intent_task_kind == "simple",
                        needs_rag=bool(intent_rewrite_snapshot.get("needs_rag")),
                    )
                flow_extra = {
                    "intent_type": "simple_chat"
                    if intent_task_kind == "simple"
                    else ("task_continue" if continue_main_task else "task"),
                    "task_kind": intent_task_kind,
                    "use_main_task": use_main_task,
                    "continue_main_task": continue_main_task,
                    "needs_rag": bool(intent_rewrite_snapshot.get("needs_rag")),
                    "needs_plan_execute": use_main_task and framework == "plan_execute",
                    "result_brief_cn": result_brief,
                }
            if use_react_llm and llm_step_text:
                flow_extra["llm_output_preview"] = llm_step_text[:500]
                flow_extra["llm_powered"] = True
            out_payload = build_flow_step_output(phase=step_id_code, cost_ms=cost_ms, extra=flow_extra)
            if use_react_llm and llm_step_text:
                step_output = dumps_step_output(
                    build_llm_step_output(answer=llm_step_text, cost_ms=cost_ms)
                )
                result_brief = (llm_step_text[:120] + "…") if len(llm_step_text) > 120 else llm_step_text
            else:
                step_output = dumps_step_output(out_payload)
                if not result_brief:
                    result_brief = brief_from_payload(out_payload)

            yield _sse("thought_step_end", {
                "trace_id": trace_id, "task_id": task_id or "", "step_id": span_step["step_id"],
                "step_name": step_name, "status": step_status,
                "elapsed_ms": cost_ms, "status_text": "完成",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "description": result_brief,
                "sub_plan_id": sub_plan_id, "sub_index": sub_index,
                "node_kind": "llm_call" if use_react_llm else node_kind,
                "result_brief": result_brief,
                "io_links": [],
                "input_text": step_input,
                "output_text": step_output,
                "phase": step_id_code,
                "success": step_status == SUB_DONE,
                "confidence": 0.92 if step_status == SUB_DONE else 0.0,
                "token_count": max(12, len(llm_step_text or step_default_text) // 4),
                "llm_powered": use_react_llm,
            })

            if step_id_code == "intent":
                # 复杂任务：意图通过后必须创建主任务（续接已有 task_id 时不新建）
                if use_main_task and not task_id and not continue_main_task:
                    task_id = _new_id("task_")
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    rewritten_q = (intent_rewrite_snapshot.get("rewritten_query") or message or "").strip()
                    _span_task(session_id, message, task_id=task_id)
                    _span_update(
                        task_id,
                        status=PARENT_CREATED,
                        started_at=now,
                        query_summary=(message or "")[:120],
                        rewritten_query=rewritten_q[:500],
                        snapshot_json={
                            "fixed": {"session_id": session_id, "task_id": task_id},
                            "open": {
                                "objective": rewritten_q[:200] or (message or "")[:200],
                                "current_assessment": "意图识别完成，进入主任务执行",
                                "decision": "continue",
                                "metadata": intent_rewrite_snapshot.get("metadata", {}),
                                "keywords": intent_rewrite_snapshot.get("keywords", []),
                                "needs_rag": intent_rewrite_snapshot.get("needs_rag", False),
                                "rewrite_state": intent_rewrite_snapshot.get("rewrite_state") or "rewrite_confirm",
                                "rewrite_confidence": intent_rewrite_snapshot.get("confidence", 0.0),
                            },
                        },
                    )
                    _span_update(task_id, status=PARENT_SUMMARIZING)
                    _span_update(task_id, status=PARENT_PLANNING)
                    yield _sse("task_created", {
                        "task_id": task_id,
                        "session_id": session_id,
                        "user_query": message,
                        "status": PARENT_PLANNING,
                        "task_kind": "main",
                        "persist_main_task": True,
                        "stage": "分析任务中",
                        "progress": 8,
                        "rewrite_snapshot": intent_rewrite_snapshot,
                        "query_summary": (message or "")[:120],
                    })
                    _span_update(task_id, status=PARENT_EXECUTING)
                    span_step = _span_step(
                        task_id, session_id, "reasoning", f"{step_idx}. {step_name}",
                        parent_step_id=sub_plan_id,
                    )
                    _span_start(span_step["step_id"], input_payload={"query": message, "phase": step_id_code})
                yield _sse("intent_resolved", {
                    "trace_id": trace_id,
                    "task_id": task_id or "",
                    "task_kind": intent_task_kind,
                    "sub_plan_id": sub_plan_id,
                    "is_simple": intent_task_kind == "simple",
                    "persist_main_task": use_main_task,
                    "continue_main_task": continue_main_task,
                    "task_action": _imode,
                    "preserve_task_identity": bool(continue_main_task and task_id),
                    "rewrite_snapshot": intent_rewrite_snapshot if use_main_task else None,
                    "user_query": message if use_main_task else "",
                    "query_summary": (message or "")[:120] if use_main_task else "",
                    "intent_reason": str(_intent_dec.get("reason") or ""),
                })
                thinking_steps.extend(_build_post_intent_steps())
                if task_id and intent_rewrite_snapshot:
                    rewrite_state = intent_rewrite_snapshot.get("rewrite_state") or "rewrite_confirm"
                    _span_patch_snapshot(
                        task_id,
                        open_layer={
                            "objective": intent_rewrite_snapshot.get("rewritten_query") or message[:200],
                            "current_assessment": "已完成 query 意图识别、改写、关键词提取与 RAG 判定",
                            "decision": "continue",
                            "metadata": intent_rewrite_snapshot.get("metadata", {}),
                            "keywords": intent_rewrite_snapshot.get("keywords", []),
                            "needs_rag": intent_rewrite_snapshot.get("needs_rag", False),
                            "rewrite_state": rewrite_state,
                            "rewrite_confidence": intent_rewrite_snapshot.get("confidence", 0.0),
                        },
                    )

            if task_id:
                _span_finish(
                    span_step["step_id"], status=step_status,
                    output_payload=out_payload,
                    open_layer={
                        "objective": f"完成{step_name}", "current_assessment": "正常",
                        "progress_percent": min(95, step_idx * 20), "decision": "continue",
                        "tool_io_brief": {"phase": step_id_code, "tool_call": False},
                    },
                )
                yield _sse("span_update", {
                    "task_id": task_id, "step_id": span_step["step_id"],
                    "step_name": step_name, "elapsed_ms": cost_ms, "status": step_status,
                    "parent_status": PARENT_EXECUTING,
                    "token_count": max(12, len(step_default_text) // 4),
                    "success": step_status == SUB_DONE,
                    "confidence": 0.92 if step_status == SUB_DONE else 0.0,
                })
            if step_id_code == "intent" and not use_main_task:
                break

        yield _sse("thinking_end", {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "ephemeral": not use_main_task,
            "bundle": {
                "task_kind": intent_task_kind,
                "use_main_task": use_main_task,
                "needs_rag": bool(
                    rag_prefetch
                    and use_main_task
                    and bool((intent_rewrite_snapshot or {}).get("needs_rag"))
                ),
                "react_memory": react_memory,
                "framework": framework,
            },
        })

    react_context_block = _format_react_memory(react_memory)
    if use_main_task and task_id:
        from .chat_context_memory import hydrate_react_memory_from_repo

        react_memory = hydrate_react_memory_from_repo(str(task_id), react_memory)
        react_context_block = _format_react_memory(react_memory)

    prefetch_sub_plan_id: Optional[str] = None
    prefetch_sub_index: int = 0
    # LangGraph handoff：常规编排已在 rag_decision 预取；延续主任务跳过编排须在执行段补 RAG
    _mcp_react_loop = _mcp_react_loop_enabled(
        framework, chat_lc_tools_pre, provider, base_url
    )
    _graph_boot = graph_boot if isinstance(graph_boot, dict) else {}
    _boot_has_rag = bool(
        (_graph_boot.get("rag_slices") or [])
        or str(_graph_boot.get("rag_context_block") or "").strip()
    )
    # LangGraph 编排段（含延续主任务快径）已做过 RAG 预取时，执行段勿再 rag_retrieve
    _boot_rag_already = bool(
        _boot_has_rag or bool(_graph_boot.get("rag_prefetch_done"))
    )
    skip_react_prefetch = bool(
        (skip_phase1 and not continue_main_task)
        or (_mcp_react_loop and not continue_main_task)
        or _boot_has_rag
        or bool(_graph_boot.get("rag_prefetch_done"))
    )
    will_prefetch_rag = bool(
        rag_prefetch and use_main_task and task_id and not _boot_rag_already
    )
    will_prefetch_web = bool(
        web_search
        and use_main_task
        and task_id
        and not skip_react_prefetch
        and not link_ctx.get("skip_web_search")
        and not tools_inventory_query
    )
    if will_prefetch_rag or will_prefetch_web:
        yield _sse(
            "prefetch_segment_start",
            {
                "trace_id": trace_id,
                "task_id": task_id or "",
                "label": "检索预取",
                "stage": "prefetch",
                "progress": 66,
                "detail": "固定编排已完成，在 ReAct 推理前执行联网/RAG 预取",
            },
        )
    if (rag_prefetch or web_search) and use_main_task and task_id:
        prefetch_sub_plan_id, prefetch_sub_index = _alloc_step_group()

    if will_prefetch_rag:
        from .chat_graph_nodes import _safe_kb_search
        from .web_search_plan import build_rag_retrieve_query

        rag_sub_plan_id, rag_sub_index = prefetch_sub_plan_id, prefetch_sub_index
        rag_query = build_rag_retrieve_query(
            rewritten_query=(
                intent_rewrite_snapshot.get("rewritten_query") or message or ""
            ).strip(),
            original_query=message,
            slot_snapshot=slot_snapshot_exec,
            enhancement_snapshot=enhancement_snapshot_exec,
        ).strip()
        rag_handle = begin_tool_span(
            task_id=task_id,
            session_id=session_id,
            tool_name="rag_retrieve",
            tool_args={"query": rag_query, "top_k": 5},
            step_name="RAG 检索",
            react_round=0,
            sub_plan_id=rag_sub_plan_id,
            phase="rag",
        )
        from .tool_invoke_qualifier import INVOKE_FIXED, attach_invoke_to_payload

        yield _sse("thought_step_start", attach_invoke_to_payload({
            "trace_id": trace_id, "task_id": task_id, "step_id": rag_handle.step_id,
            "step_name": "RAG 检索", "step_type": "tool_call", "status": "running", "status_text": "执行中…",
            "input_text": json.dumps({"query": rag_query}, ensure_ascii=False),
            "operation": "RAG 检索", "target": rag_query[:80], "node_kind": "tool_call",
            "sub_plan_id": rag_sub_plan_id, "sub_index": rag_sub_index,
            "step_lane": "prefetch", "phase": "rag",
        }, mode=INVOKE_FIXED, tool_name="rag_retrieve", action_label="知识库检索",
           purpose="执行段预取", query=rag_query))
        rag_err: Optional[str] = None
        rag_hits: List[Any] = []
        try:
            rag_hits, rag_err = await _safe_kb_search(rag_query, top_k=5)
            rag_raw = {
                "ok": not rag_err,
                "query": rag_query,
                "hits": rag_hits,
                "count": len(rag_hits or []),
                "error": rag_err or None,
            }
        except Exception as ex:
            rag_raw = {"ok": False, "query": rag_query, "hits": [], "error": str(ex)}
            rag_err = str(ex)
        if rag_hits:
            lines = ["本地知识库检索结果（请综合后回答）："]
            for idx, hit in enumerate(rag_hits[:5], start=1):
                if isinstance(hit, dict):
                    title = hit.get("title") or hit.get("file") or hit.get("source") or f"片段{idx}"
                    snippet = hit.get("snippet") or hit.get("content") or hit.get("text") or ""
                    lines.append(f"{idx}. {title}")
                    if snippet:
                        lines.append(f"   {str(snippet)[:400]}")
            rag_context_block = "\n".join(lines)
        rag_payload = end_tool_span(
            rag_handle,
            tool_args={"query": rag_query, "top_k": 5},
            raw_out=rag_raw,
            tool_err=rag_err,
            phase="rag",
        )
        rag_cost_ms = int(rag_payload.get("cost_ms") or 0)
        rag_output_text = dumps_step_output(rag_payload)
        rag_brief = brief_from_payload(rag_payload)
        yield _sse("thought_step_end", attach_invoke_to_payload({
            "trace_id": trace_id, "task_id": task_id, "step_id": rag_handle.step_id,
            "step_name": "RAG 检索", "status": "completed" if not rag_err else "failed",
            "elapsed_ms": rag_cost_ms, "status_text": "完成" if not rag_err else "失败",
            "timestamp": datetime.now().strftime("%H:%M:%S"), "description": rag_brief,
            "input_text": json.dumps({"query": rag_query}, ensure_ascii=False),
            "output_text": rag_output_text, "phase": "rag", "success": not rag_err,
            "confidence": 0.9 if not rag_err else 0.0,
            "token_count": max(12, len(rag_output_text) // 4),
            "sub_plan_id": rag_sub_plan_id, "sub_index": rag_sub_index, "node_kind": "tool_call",
            "step_lane": "prefetch",
        }, mode=INVOKE_FIXED, tool_name="rag_retrieve", action_label="知识库检索",
           purpose="执行段预取", query=rag_query))
        yield _sse("span_update", {
            "task_id": task_id, "step_id": rag_handle.step_id, "step_name": "RAG 检索",
            "elapsed_ms": rag_cost_ms, "status": "completed" if not rag_err else "failed",
            "parent_status": PARENT_EXECUTING,
            "token_count": max(12, len(rag_output_text) // 4), "success": not rag_err,
        })

    link_guidance_block = None
    if use_main_task and link_ctx.get("guidance"):
        link_guidance_block = {"guidance": link_ctx["guidance"], "link_doc_relevant": True}

    web_block = None
    if web_search and use_main_task and not tools_inventory_query:
        import logging as _lg_ws

        _ws_log = _lg_ws.getLogger(__name__)
        if link_ctx.get("skip_web_search"):
            _ws_log.info(
                "[AI问答-执行段|ai_chat.chat_stream_v2|web_search|硬编执行|跳过] "
                "链接文档化路由跳过联网预取; link_doc_relevant=true"
            )
        elif not task_id:
            _ws_log.warning(
                "[AI问答-执行段|ai_chat.chat_stream_v2|web_search|硬编执行|跳过] "
                "缺少 task_id，无法执行联网预取"
            )
    if (
        web_search
        and use_main_task
        and task_id
        and not link_ctx.get("skip_web_search")
        and not tools_inventory_query
    ):
        from .web_search_plan import build_web_search_plan

        task_user_q = ""
        if task_id:
            try:
                from .span_audit import get_task

                mt = get_task(str(task_id)) or {}
                task_user_q = str(mt.get("user_query") or mt.get("query_summary") or "")
            except Exception:
                pass
        web_plan = build_web_search_plan(
            rewritten_query=(
                intent_rewrite_snapshot.get("rewritten_query") or message or ""
            ).strip(),
            original_query=message,
            task_user_query=task_user_q,
            continue_main=continue_main_task,
        )
        if web_plan.get("skip_web_search"):
            _LOG.info(
                "[AI问答-执行段|ai_chat.chat_stream_v2|web_search|硬编执行|跳过] "
                "追问句无检索实体; message_len=%s",
                len(message or ""),
            )
        elif not (web_plan.get("search_queries") or []):
            _LOG.info(
                "[AI问答-执行段|ai_chat.chat_stream_v2|web_search|硬编执行|跳过] "
                "未生成有效联网检索词",
            )
        else:
            from .web_search import web_search_multi_for_chat

            web_search_queries = list(web_plan.get("search_queries") or [])
            rewritten_q = str(web_plan.get("rewritten_query") or message or "").strip()
            if prefetch_sub_plan_id:
                web_sub_plan_id, web_sub_index = prefetch_sub_plan_id, prefetch_sub_index
            else:
                web_sub_plan_id, web_sub_index = _alloc_step_group()
                prefetch_sub_plan_id, prefetch_sub_index = web_sub_plan_id, web_sub_index
            web_input_doc = {
                "objective": web_plan.get("objective"),
                "rewritten_query": rewritten_q,
                "search_queries": web_search_queries,
                "original_query": message[:200],
                "keyword_source": web_plan.get("keyword_source"),
                "task_user_query": task_user_q[:200] if task_user_q else "",
            }
            web_handle = begin_tool_span(
                task_id=task_id,
                session_id=session_id,
                tool_name="web_search",
                tool_args={
                    "objective": web_plan.get("objective"),
                    "search_queries": web_search_queries,
                    "max_results": 5,
                },
                step_name="联网搜索",
                react_round=0,
                sub_plan_id=web_sub_plan_id,
                phase="web",
            )
            web_target = (web_search_queries[0] if web_search_queries else rewritten_q)[:80]
            from .tool_invoke_qualifier import INVOKE_FIXED, attach_invoke_to_payload

            yield _sse("thought_step_start", attach_invoke_to_payload({
                "trace_id": trace_id, "task_id": task_id, "step_id": web_handle.step_id,
                "step_name": "调用 web_search", "step_type": "tool_call", "status": "running", "status_text": "执行中…",
                "input_text": json.dumps(web_input_doc, ensure_ascii=False),
                "operation": "联网搜索", "target": web_target, "node_kind": "tool_call",
                "sub_plan_id": web_sub_plan_id, "sub_index": web_sub_index,
                "step_lane": "prefetch", "phase": "web",
            }, mode=INVOKE_FIXED, tool_name="web_search", action_label="联网搜索",
               purpose="执行段预取", query=web_target))
            try:
                web_block = await asyncio.get_running_loop().run_in_executor(
                    _executor,
                    lambda: web_search_multi_for_chat(
                        web_search_queries,
                        max_results_per_query=3,
                        objective=str(web_plan.get("objective") or ""),
                    ),
                )
            except Exception as e:
                web_block = {
                    "objective": web_plan.get("objective"),
                    "search_queries": web_search_queries,
                    "query": web_target,
                    "results": [],
                    "error": str(e),
                    "provider": "",
                }
            search_results = web_block
            web_provider = str((web_block or {}).get("provider") or "bing-html")
            web_results = (web_block or {}).get("results") or []
            web_err = str((web_block or {}).get("error") or "").strip() or None
            if web_results:
                web_err = None
            web_payload = end_tool_span(
                web_handle,
                tool_args={
                    "objective": web_plan.get("objective"),
                    "search_queries": web_search_queries,
                    "provider": web_provider,
                    "max_results": 5,
                },
                raw_out=web_block,
                tool_err=web_err,
                phase="web",
            )
            web_cost_ms = int(web_payload.get("cost_ms") or 0)
            web_output_text = dumps_step_output(web_payload)
            web_brief = brief_from_payload(web_payload)
            yield _sse("thinking_delta", {"trace_id": trace_id, "task_id": task_id, "step_id": web_handle.step_id, "content": "联网搜索结果已返回…"})
            yield _sse("thought_step_end", attach_invoke_to_payload({
                "trace_id": trace_id, "task_id": task_id, "step_id": web_handle.step_id,
                "step_name": "调用 web_search", "status": "completed", "elapsed_ms": web_cost_ms, "status_text": "完成",
                "timestamp": datetime.now().strftime("%H:%M:%S"), "description": web_brief, "result_brief": web_brief,
                "input_text": json.dumps(web_input_doc, ensure_ascii=False),
                "output_text": web_output_text, "phase": "web", "success": not web_err, "confidence": 0.88,
                "token_count": max(12, len(web_output_text) // 4),
                "sub_plan_id": web_sub_plan_id, "sub_index": web_sub_index, "node_kind": "tool_call",
                "step_lane": "prefetch",
            }, mode=INVOKE_FIXED, tool_name="web_search", action_label="联网搜索",
               purpose="执行段预取", query=web_target))
            yield _sse("span_update", {
                "task_id": task_id, "step_id": web_handle.step_id, "step_name": "联网搜索",
                "elapsed_ms": web_cost_ms, "status": "completed",
                "parent_status": PARENT_EXECUTING,
                "token_count": max(12, len(web_output_text) // 4), "success": not web_err, "confidence": 0.88,
                "search_results": web_block,
            })

    # ── 工具清单类问题：直接返回注册表，禁止联网搜索 + 禁止 LLM 编造工具名 ──
    if tools_inventory_query and tools_meta_pre.get("total", 0) >= 0:
        catalog_md = format_tools_catalog_markdown(tools_meta_pre)
        yield _sse("answer_start", {"trace_id": trace_id, "task_id": task_id or "", "ephemeral": not use_main_task})
        if use_main_task and task_id:
            yield _sse("answer_preface", {
                "trace_id": trace_id, "task_id": task_id,
                "content": "以下为本系统**真实注册**的 Tool Call / MCP / SKILL 清单：\n\n",
                "stage": "工具清单", "progress": 90,
            })
        for pos in range(0, len(catalog_md), 48):
            yield _sse("answer_delta", {
                "trace_id": trace_id, "task_id": task_id or "",
                "content": catalog_md[pos:pos + 48], "kind": "body", "stream_mode": "token",
            })
            await asyncio.sleep(0.008)
        yield _sse("answer_end", {
            "trace_id": trace_id, "task_id": task_id or "",
            "token_usage": {"prompt": 0, "completion": max(1, len(catalog_md) // 4)},
            "tools_catalog": tools_meta_pre,
        })
        total_ms_cat = int((time.perf_counter() - time_start) * 1000)
        yield _sse("task_completed", {
            "task_id": task_id or "",
            "status": PARENT_EXECUTING if use_main_task else "ephemeral",
            "persist_main_task": use_main_task,
            "total_duration_ms": total_ms_cat,
            "tool_outputs": [{"tool_name": "tools_catalog", "result": tools_meta_pre}],
            "task_kind": intent_task_kind,
            "ephemeral": not use_main_task,
            "user_resolved_allowed": False,
        })
        _messages.setdefault(session_id, []).append({"role": "user", "content": message})
        _messages[session_id].append({
            "role": "assistant",
            "content": catalog_md,
            "tools_catalog": tools_meta_pre,
        })
        if session_id in _sessions:
            _sessions[session_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _store_persist(session_id, _sessions[session_id], _messages.get(session_id, []), mark_dirty=True)
        return

    # ── Phase 2: 执行段 ReAct（工具循环）→ 最终回答 ──
    answer_started = False
    llm_sub_plan_id: Optional[str] = None
    llm_sub_index = 0
    llm_step: Dict[str, Any] = {"step_id": _new_id("step_")}
    show_llm_as_subtask = not use_main_task
    if show_llm_as_subtask:
        llm_sub_plan_id, llm_sub_index = _alloc_step_group()
    if task_id:
        llm_step = _span_step(task_id, session_id, "llm_call", f"{step_idx + 1}. LLM生成回答")
        _span_start(llm_step["step_id"], input_payload={"query": message, "model": model_resolved, "provider": provider})

    answer_begin = time.perf_counter()
    full_answer = ""
    token_count = 0
    streamed_tokens = False

    from .execution_framework import ExecutionContext, get_execution_strategy

    exec_strategy = get_execution_strategy(framework)
    _rag_slices_boot = list((_graph_boot.get("rag_slices") or []))
    _rag_slice_count = len(_rag_slices_boot)
    if not _rag_slice_count and rag_context_block:
        _rag_slice_count = len(re.findall(r"^\d+\.\s", rag_context_block, re.M))
    _rag_prefetch_done_flag = bool(
        _boot_rag_already or bool(_graph_boot.get("rag_prefetch_done"))
    )
    exec_ctx = ExecutionContext(
        framework=framework,
        use_main_task=use_main_task,
        web_search=web_search,
        read_comments=read_comments,
        web_block=web_block if isinstance(web_block, dict) else None,
        plan_steps=list(plan_steps_boot) if skip_phase1 else [],
        enhancement_snapshot=dict(enhancement_snapshot_exec) if enhancement_snapshot_exec else {},
        react_memory=react_memory,
        min_tool_rounds=1,
        rag_prefetch_done=_rag_prefetch_done_flag,
        rag_slice_count=_rag_slice_count,
    )

    if skip_phase1 and use_main_task and task_id:
        yield _sse("execution_segment_start", {
            "trace_id": trace_id,
            "task_id": task_id,
            "label": "ReAct 执行",
            "stage": "executing",
            "progress": 74,
            "framework": framework,
            "detail": (
                "延续主任务，直接进入 ReAct 推理与工具调用"
                if continue_main_task
                else "编排已完成，进入 ReAct 推理与工具调用（联网/RAG 由模型按需调用）"
            ),
        })
        if _boot_rag_already and _rag_slice_count > 0:
            yield _sse(
                "pipeline_progress",
                {
                    "trace_id": trace_id,
                    "task_id": task_id,
                    "stage": "RAG 已注入",
                    "progress": 72,
                    "detail": f"编排段已预取 {_rag_slice_count} 条知识库切片，执行段跳过重复 rag_retrieve",
                },
            )
        # 每轮 function calling 前由 _yield_react_reasoning_analysis 产出真实 Observe/Act，禁止裸调工具

    if show_llm_as_subtask:
        yield _sse("thought_step_start", {
            "trace_id": trace_id, "task_id": task_id or "", "step_id": llm_step["step_id"],
            "step_name": "LLM生成回答", "step_type": "llm_call", "status": "running", "status_text": "执行中…",
            "input_text": message[:4000], "node_kind": "llm_call",
            "sub_plan_id": llm_sub_plan_id, "sub_index": llm_sub_index,
            "step_lane": "execution",
        })
    if api_key and model_resolved:
        try:
            from provider_adapters import (
                invoke_unified,
                invoke_chat_completion_raw,
                _extract_openai_message_dict,
            )

            from .chat_context_memory import prepare_llm_context

            extra_blocks: List[str] = [
                (
                    "最终回答须面向用户：禁止输出「工具调用请求」、禁止粘贴 ```json 工具参数。"
                    "需要工具时请通过 function calling 调用目录中的具名工具，勿在正文里伪造 JSON。"
                ),
            ]
            if react_context_block:
                extra_blocks.append(react_context_block)
            if rag_citation_instruction:
                extra_blocks.append(rag_citation_instruction)
            if rag_context_block:
                extra_blocks.append(rag_context_block)
            if rss_context_block:
                extra_blocks.append(rss_context_block)
            if rag_context_block and re.search(r"MCP|知识库|mcp", message or "", re.I):
                extra_blocks.append(
                    "【MCP 知识库总结 · 硬性要求】须基于上方「预检索文献」切片作答；"
                    "正文按句为单位，每句句末用阿拉伯数字编号引用（1、2、3…）；"
                    "正文后必须输出「## 文献切片明细」（含父文档、切片全文、父文档路径）"
                    "与「## 注释」（每条「n 处逻辑链路」+ 置信度，与正文编号一一对应）；"
                    "正文须明确写出至少 2 个来自文档的术语，例如："
                    "「Model Context Protocol（模型上下文协议）」「MCP 服务器」「MCP 客户端」。"
                    "禁止在正文输出 FunctionCallBegin、```json 工具参数或伪工具调用。"
                )
            if link_guidance_block:
                extra_blocks.append(
                    "【链接文档化助手 · 路由说明】\n"
                    + link_guidance_block["guidance"]
                    + "\n勿建议用户去 App 内搜索替代本产品的链接+评论抓取能力。"
                )
            if read_comments:
                extra_blocks.append(
                    "用户已在对话页勾选「读取评论」。若有合法作品链接，可调用 scrape_comments；"
                    "调用 link_pipeline_start 时须传 read_comments_flag=true 才会在流水线中读评论。"
                )
            if include_rss or rss_context_block:
                extra_blocks.append(
                    "用户已启用 RSS 订阅上下文。回答资讯/订阅相关问题时须基于上方 RSS 条目或调用 rss_list_recent；"
                    "禁止编造未出现在 RSS 列表中的文章标题或链接。"
                )
            if continue_main_task and task_id:
                from .chat_context_memory import known_pipeline_ids_for_main_task

                _pids = known_pipeline_ids_for_main_task(str(task_id))
                if _pids:
                    extra_blocks.append(
                        "【续接主任务 · 禁止重新分析】已有流水线 ID: "
                        + "、".join(_pids[:5])
                        + "。用户追问进度/缓存/「好了吗」时必须先 cache_query(keyword=上述 ID 或账号)，"
                        "禁止 link_pipeline_start，禁止向用户声称「已重新提交」或给出新 task_id。"
                    )
                extra_blocks.append(
                    "【续接主任务 · 联网搜索】禁止用编排段业务映射检索词；"
                    "若需联网，检索词仅来自主任务原问抽词；"
                    "纯进度追问（好了吗）禁止 web_search，应查 cache_query/流水线。"
                )
            if continue_main_task and task_id:
                from .chat_context_memory import _looks_like_format_or_render_fix

                if _looks_like_format_or_render_fix(message):
                    extra_blocks.append(
                        "【续接主任务 · 格式/排版修正】用户仅修正 Markdown 换行或排版。"
                        "流水线若已完成，禁止 cache_query(主任务ID)、rag_search、web_search；"
                        "直接基于用户粘贴正文或既有 doc_filename 产出修正版，勿重新抓取笔记。"
                    )
            if web_block and web_block.get("results"):
                web_hint_lines = ["联网搜索结果如下（请综合后回答，注意标注来源）："]
                for idx, item in enumerate(web_block.get("results", [])[:5], start=1):
                    web_hint_lines.append(f"{idx}. {item.get('title') or '未命名'}")
                    if item.get("url"):
                        web_hint_lines.append(f"   来源：{item.get('url')}")
                    if item.get("snippet"):
                        web_hint_lines.append(f"   摘要：{item.get('snippet')}")
                extra_blocks.append("\n".join(web_hint_lines))
            if skip_phase1 and enhancement_snapshot_exec.get("search_keyword_queries"):
                hints = enhancement_snapshot_exec.get("search_keyword_queries") or []
                extra_blocks.append(
                    "【编排段产物·仅 RAG】业务映射检索词 "
                    + "、".join(str(x) for x in hints[:5])
                    + "。仅供 rag_search；联网 web_search 禁止使用此列表，须用原问抽词。"
                )
            if mem_ctx.get("task_redis") and mem_ctx["task_redis"].get("async_pipeline_pending"):
                pids = mem_ctx["task_redis"].get("pipeline_task_ids") or []
                extra_blocks.append(
                    "【后台流水线执行中】已提交 link_pipeline，任务 ID: "
                    + "、".join(str(x) for x in pids[:5])
                    + "。须告知用户等待后台产出，不得声称已全部完成。"
                )

            llm_ctx = await prepare_llm_context(
                session_id,
                message,
                task_id=str(task_id or ""),
                system_prompt=system_prompt,
                memory_prepared=mem_ctx,
                extra_system_blocks=extra_blocks,
                max_recent_turns=12,
            )
            messages = llm_ctx["messages"]
            mem_ctx = dict(llm_ctx.get("memory_prepared") or mem_ctx)
            if use_main_task and task_id:
                from .chat_context_memory import hydrate_react_memory_from_repo

                react_memory = hydrate_react_memory_from_repo(str(task_id), react_memory)
                react_context_block = _format_react_memory(react_memory)

            chat_lc_tools = chat_lc_tools_pre
            tools_meta = tools_meta_pre
            chat_lc_tools, tools_meta = _filter_skill_tools_for_execution(
                chat_lc_tools, tools_meta, message
            )
            chat_lc_tools, tools_meta = _filter_rag_tools_when_prefetched(
                chat_lc_tools,
                tools_meta,
                rag_prefetch_done=_rag_prefetch_done_flag,
                rag_slice_count=_rag_slice_count,
            )
            yield _sse("tools_discovered", {
                "trace_id": trace_id,
                "task_id": task_id or "",
                "total": tools_meta.get("total", 0),
                "builtin_count": tools_meta.get("builtin_count", 0),
                "mcp_count": tools_meta.get("mcp_count", 0),
                "skill_count": tools_meta.get("skill_count", 0),
                "read_comments": read_comments,
                "tools": tools_meta.get("tools", [])[:80],
                "mcp_error": tools_meta.get("mcp_error") or "",
                "phase": "answer",
            })

            use_tools = bool(chat_lc_tools) and _provider_supports_openai_style_tools(provider, base_url)

            loop = asyncio.get_running_loop()

            openai_tools = None
            if use_tools:
                try:
                    from langchain_core.utils.function_calling import convert_to_openai_tool

                    openai_tools = [convert_to_openai_tool(t) for t in chat_lc_tools]
                except Exception:
                    openai_tools = None

            catalog_lines = [
                f"- {row['name']} [{row['source']}]: {(row.get('description') or '')[:120]}"
                for row in (tools_meta.get("tools") or [])[:40]
            ]
            if openai_tools:
                messages[0] = {
                    "role": "system",
                    "content": system_prompt
                    + "\n\n【AI 对话页已挂载全部工具，不按 Agent 裁剪】内置 Tool Call + MCP + SKILL。"
                    + (f"\n共 {tools_meta.get('total', 0)} 个：内置 {tools_meta.get('builtin_count')}，"
                       f"MCP {tools_meta.get('mcp_count')}，SKILL {tools_meta.get('skill_count')}。")
                    + ("\n工具目录：\n" + "\n".join(catalog_lines) if catalog_lines else "")
                    + "\n请在需要时 function calling；未调用不得编造工具结果。"
                    + "\n**严禁**编造未在上表出现的工具（如「代码生成与调试工具」「合规网页搜索工具」等泛称）。"
                    + (" 评论工具仅用户勾选「读取评论」后可用。" if not read_comments else ""),
                }
            elif tools_meta.get("tools"):
                messages[0] = {
                    "role": "system",
                    "content": system_prompt
                    + "\n\n【已注册工具目录】\n" + "\n".join(catalog_lines)
                    + "\n未列出的能力不得声称拥有。",
                }

            if openai_tools:
                by_name = {getattr(t, "name", ""): t for t in chat_lc_tools if getattr(t, "name", "")}
                working: List[Dict[str, Any]] = list(messages)
                if _rag_prefetch_done_flag and _rag_slice_count > 0 and not web_search:
                    working.append({
                        "role": "system",
                        "content": exec_strategy.continuation_system_hint(
                            exec_ctx, reason="rag_prefetched",
                        ),
                    })
                max_rounds = max(1, int(cfg.get("chat_max_tool_rounds", 15) or 15))
                _exec_thinking = not (
                    _rag_prefetch_done_flag and _rag_slice_count > 0 and not web_search
                ) and not deep_think
                tool_timeout_sec = float(cfg.get("chat_tool_timeout_sec", 60) or 60)
                max_retry_per_tool = max(1, int(cfg.get("chat_tool_max_retry", 3) or 3))
                distinct_fail_limit = max(1, int(cfg.get("chat_distinct_tool_fail_limit", 3) or 3))
                tool_round = 0
                react_round_idx = 0
                react_tools_seen: set[str] = set()
                failed_tool_names: set[str] = set()
                pipeline_poll_sec = float(cfg.get("chat_pipeline_poll_sec") or 4.0)
                pipeline_wait_sec = float(cfg.get("chat_pipeline_wait_sec") or 0) or max(
                    float(cfg.get("chat_tool_timeout_sec", 60) or 60) * 5,
                    600.0,
                )
                for _rnd in range(max_rounds):
                    if task_id:
                        from .chat_context_memory import (
                            has_active_pipeline_tasks,
                            sync_working_repo_context,
                        )

                        sync_working_repo_context(working, str(task_id))

                    def _call_raw():
                        return invoke_chat_completion_raw(
                            provider=provider,
                            base_url=base_url,
                            api_key=api_key,
                            model=model_resolved,
                            messages=working,
                            temperature=0.3,
                            max_tokens=1800,
                            timeout=120.0,
                            thinking_enabled=_exec_thinking,
                            tools=openai_tools,
                            tool_choice="auto",
                        )

                    try:
                        data = await loop.run_in_executor(_executor, _call_raw)
                    except Exception as e:
                        from .chat_error_handler import llm_analyze_error_for_user

                        full_answer = await llm_analyze_error_for_user(
                            error_type=type(e).__name__,
                            error_message=str(e)[:500],
                            stage="MCP 工具链",
                            user_message=message,
                        )
                        break

                    msg_o = _extract_openai_message_dict(data)
                    content = msg_o.get("content") or ""
                    if isinstance(content, str):
                        pass
                    else:
                        content = ""
                    tool_calls = msg_o.get("tool_calls")
                    tool_calls = exec_strategy.normalize_tool_calls(content, tool_calls)

                    if tool_calls:
                        think_text = _strip_react_display_markers((content or "").strip())
                        if think_text:
                            react_memory.append({
                                "phase": f"react_plan_{react_round_idx + 1}",
                                "text": think_text,
                            })

                        assistant_record: Dict[str, Any] = {
                            "role": "assistant",
                            "content": content if content else None,
                            "tool_calls": tool_calls,
                        }
                        working.append(assistant_record)

                        for tc in tool_calls:
                            tool_round += 1
                            fn = (tc.get("function") or {}).get("name") or ""
                            tid = tc.get("id") or _new_id("tc_")
                            raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                            try:
                                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                            except Exception:
                                args = {}
                            react_round_idx += 1
                            tool_sub_plan_id, tool_sub_index = _alloc_step_group()
                            react_step_id = _new_id("step_")
                            yield _sse(
                                "execution_round_start",
                                {
                                    "trace_id": trace_id,
                                    "task_id": task_id,
                                    "sub_plan_id": tool_sub_plan_id,
                                    "sub_index": tool_sub_index,
                                    "react_round": react_round_idx,
                                    "tool_name": fn,
                                    "phase": "react_round",
                                    "step_lane": "execution",
                                },
                            )
                            async for _react_ev in _yield_react_reasoning_analysis(
                                trace_id=trace_id,
                                task_id=str(task_id or ""),
                                session_id=session_id,
                                message=message,
                                provider=provider,
                                base_url=base_url,
                                api_key=api_key,
                                model_resolved=model_resolved,
                                tools_meta=tools_meta,
                                react_memory=react_memory,
                                intent_snapshot=intent_rewrite_snapshot or None,
                                sub_plan_id=tool_sub_plan_id,
                                sub_index=tool_sub_index,
                                step_id=react_step_id,
                                pending_tool_name=fn,
                                pending_tool_args=args,
                            ):
                                yield _react_ev
                            span_handle = (
                                begin_tool_span(
                                    task_id=task_id,
                                    session_id=session_id,
                                    tool_name=fn,
                                    tool_args=args,
                                    step_name=f"MCP 工具: {fn}",
                                    react_round=react_round_idx,
                                    sub_plan_id=tool_sub_plan_id,
                                    phase="tool",
                                )
                                if task_id
                                else None
                            )
                            step_id_tool = span_handle.step_id if span_handle else _new_id("step_")
                            from .tool_invoke_qualifier import (
                                attach_invoke_to_payload,
                                resolve_react_invoke_mode,
                            )

                            tool_call_label = f"调用 {fn}"
                            _tool_query_hint = str(
                                args.get("query") or args.get("q") or ""
                            ).strip()
                            _react_invoke_mode = resolve_react_invoke_mode(
                                tool_name=fn,
                                rag_prefetch_done=_rag_prefetch_done_flag,
                                rag_slice_count=_rag_slice_count,
                                react_round=react_round_idx,
                                seen_tools=react_tools_seen,
                            )
                            _tool_invoke = attach_invoke_to_payload(
                                {
                                    "trace_id": trace_id,
                                    "task_id": task_id,
                                    "step_id": step_id_tool,
                                    "step_name": tool_call_label,
                                    "step_type": "tool_call",
                                    "status": "running",
                                    "status_text": "工具执行中…",
                                    "input_text": json.dumps(
                                        {
                                            "schema_version": 1,
                                            "tool_call": True,
                                            "tool_name": fn,
                                            "tool_args": args,
                                        },
                                        ensure_ascii=False,
                                    )[:4000],
                                    "sub_plan_id": tool_sub_plan_id,
                                    "sub_index": tool_sub_index,
                                    "node_kind": "tool_call",
                                    "phase": "tool",
                                    "step_lane": "execution",
                                },
                                mode=_react_invoke_mode,
                                tool_name=fn,
                                query=_tool_query_hint,
                            )
                            yield _sse("thought_step_start", _tool_invoke)
                            tool_obj = by_name.get(fn)
                            tool_err: Optional[str] = None
                            raw_out: Any = None
                            from .react_async_wait import (
                                build_pipeline_wait_result,
                                extract_async_pipeline_ids,
                                pipeline_snapshot_row,
                            )
                            if fn == "web_search" and task_id:
                                from .web_search_plan import resolve_web_search_plan_for_tool

                                task_user_q = ""
                                try:
                                    from .span_audit import get_task

                                    _mt = get_task(str(task_id)) or {}
                                    task_user_q = str(
                                        _mt.get("user_query") or _mt.get("query_summary") or ""
                                    )
                                except Exception:
                                    pass
                                _ws_plan = resolve_web_search_plan_for_tool(
                                    tool_query=str(args.get("query") or args.get("q") or message),
                                    tool_search_queries=None,
                                    current_message=message,
                                    task_user_query=task_user_q,
                                    rewritten_query=(
                                        intent_rewrite_snapshot.get("rewritten_query") or message
                                    ),
                                    continue_main=continue_main_task,
                                )
                                if _ws_plan.get("skip_web_search"):
                                    raw_out = {
                                        "ok": False,
                                        "skipped": True,
                                        "reason": _ws_plan.get("reason")
                                        or "追问进度不宜联网搜索，请查 cache_query 或流水线状态",
                                        "search_queries": [],
                                    }
                                    tool_err = None
                                else:
                                    from .web_search import web_search_multi_for_chat

                                    _qs = list(_ws_plan.get("search_queries") or [])
                                    raw_out = web_search_multi_for_chat(
                                        _qs,
                                        max_results_per_query=3,
                                        objective=str(_ws_plan.get("objective") or "")[:160],
                                    )
                                    if isinstance(raw_out, dict):
                                        raw_out["search_queries"] = _qs
                                        raw_out["keyword_source"] = _ws_plan.get(
                                            "keyword_source"
                                        )
                                    tool_err = None
                            if fn == "link_pipeline_start" and task_id:
                                from .chat_context_memory import guard_link_pipeline_start

                                guarded = guard_link_pipeline_start(
                                    main_task_id=str(task_id),
                                    user_message=message,
                                    tool_args=args,
                                    continue_main=continue_main_task,
                                )
                                if guarded is not None:
                                    raw_out = guarded
                                    tool_err = None
                            if raw_out is None and task_id:
                                from .chat_context_memory import guard_tool_call

                                _tg = guard_tool_call(
                                    main_task_id=str(task_id),
                                    tool_name=fn,
                                    tool_args=args,
                                    user_message=message,
                                    continue_main=continue_main_task,
                                )
                                if _tg is not None:
                                    raw_out = _tg
                                    tool_err = None if _tg.get("skipped") else str(
                                        _tg.get("reason") or _tg.get("error") or ""
                                    )
                            if raw_out is None:
                                for _try in range(max_retry_per_tool):
                                    try:
                                        if tool_obj is None:
                                            raw_out = {"ok": False, "error": f"未知工具: {fn}"}
                                            tool_err = raw_out["error"]
                                            break
                                        raw_out = await asyncio.wait_for(
                                            _invoke_langchain_tool(tool_obj, args),
                                            timeout=tool_timeout_sec,
                                        )
                                        tool_err = None
                                        if isinstance(raw_out, dict) and raw_out.get("error"):
                                            tool_err = str(raw_out.get("error"))
                                        if not tool_err:
                                            break
                                    except asyncio.TimeoutError:
                                        raw_out = {"ok": False, "error": f"工具超时（>{int(tool_timeout_sec)}s）"}
                                        tool_err = raw_out["error"]
                                    except Exception as ex:
                                        raw_out = {"ok": False, "error": f"工具执行异常: {ex}"}
                                        tool_err = str(ex)
                                    if _try < max_retry_per_tool - 1 and tool_err:
                                        await asyncio.sleep(0.15)
                            if tool_err and fn:
                                failed_tool_names.add(fn)
                                if len(failed_tool_names) >= distinct_fail_limit and task_id:
                                    _span_update(task_id, status=PARENT_ABNORMAL)
                                    yield _sse("span_update", {
                                        "task_id": task_id,
                                        "step_id": step_id_tool,
                                        "parent_status": PARENT_ABNORMAL,
                                        "status": "failed",
                                    })
                                    full_answer = (
                                        f"工具调用多次失败（不同工具累计 {len(failed_tool_names)} 次），"
                                        "任务已标记为异常，请检查权限或更换工具后重试。"
                                    )
                                    break
                            if tool_err is None and isinstance(raw_out, dict) and raw_out.get("error"):
                                tool_err = str(raw_out.get("error"))
                            if not tool_err and fn and not str(fn).startswith("skill_"):
                                try:
                                    from .board_usage_stats import record_tool_usage_by_name

                                    record_tool_usage_by_name(str(fn), event="invoke")
                                except Exception:
                                    pass
                            if fn.startswith("skill_") and _skill_result_is_doc_only(raw_out):
                                tool_err = (
                                    "SKILL 仅返回说明文档，未实际执行；"
                                    "需安装运行时或改用 web_search / 其他工具"
                                )
                                yield _sse("hitl_required", {
                                    "trace_id": trace_id,
                                    "task_id": task_id or "",
                                    "hitl_kind": "tool_exception",
                                    "payload": {
                                        "tool_name": fn,
                                        "message": tool_err,
                                        "options": [
                                            {"id": "switch_web_search", "label": "改用联网搜索"},
                                            {"id": "install_runtime", "label": "我先安装运行时"},
                                            {"id": "pause", "label": "暂停任务"},
                                        ],
                                    },
                                })
                            pipeline_ids_wait = extract_async_pipeline_ids(fn, raw_out)
                            if span_handle and not pipeline_ids_wait:
                                tool_payload = end_tool_span(
                                    span_handle,
                                    tool_args=args,
                                    raw_out=raw_out,
                                    tool_err=tool_err,
                                    phase="tool",
                                )
                            elif not span_handle:
                                tool_payload = build_tool_step_output(
                                    tool_name=fn,
                                    tool_args=args,
                                    tool_result=raw_out,
                                    error=tool_err,
                                    cost_ms=0,
                                    phase="tool",
                                )
                            else:
                                tool_payload = build_tool_step_output(
                                    tool_name=fn,
                                    tool_args=args,
                                    tool_result=raw_out,
                                    error=tool_err,
                                    cost_ms=0,
                                    phase="tool",
                                )
                            cost_tool = int(tool_payload.get("cost_ms") or 0)

                            if pipeline_ids_wait:
                                yield _sse(
                                    "thought_step_end",
                                    {
                                        "trace_id": trace_id,
                                        "task_id": task_id,
                                        "step_id": step_id_tool,
                                        "step_name": tool_call_label,
                                        "status": "running",
                                        "elapsed_ms": cost_tool,
                                        "status_text": "后台流水线执行中，等待产出…",
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "description": "已提交链接文档化，轮询中（Cursor 式等待）",
                                        "result_brief": "等待后台流水线",
                                        "sub_plan_id": tool_sub_plan_id,
                                        "sub_index": tool_sub_index,
                                        "node_kind": "tool_call",
                                        "step_lane": "execution",
                                        "phase": "tool_wait",
                                    },
                                )
                                wait_t0 = time.perf_counter()
                                async for ev in _stream_await_link_pipelines(
                                    pipeline_ids_wait,
                                    timeout_sec=pipeline_wait_sec,
                                    trace_id=trace_id,
                                    task_id=task_id or "",
                                    poll_sec=pipeline_poll_sec,
                                ):
                                    yield ev
                                rows_wait = [pipeline_snapshot_row(pid) for pid in pipeline_ids_wait]
                                any_active = any(
                                    str(r.get("status") or "").lower()
                                    in _PIPELINE_ACTIVE_STATUSES
                                    for r in rows_wait
                                )
                                timed_out = (time.perf_counter() - wait_t0) >= pipeline_wait_sec - 1
                                raw_out = build_pipeline_wait_result(
                                    pipeline_ids_wait,
                                    timeout=timed_out and any_active,
                                    rows=rows_wait,
                                )
                                cost_tool = int((time.perf_counter() - wait_t0) * 1000)
                                if span_handle:
                                    tool_payload = end_tool_span(
                                        span_handle,
                                        tool_args=args,
                                        raw_out=raw_out,
                                        tool_err=None if raw_out.get("ok") else str(raw_out.get("hint") or ""),
                                        phase="tool",
                                    )
                                else:
                                    tool_payload = build_tool_step_output(
                                        tool_name=fn,
                                        tool_args=args,
                                        tool_result=raw_out,
                                        error=None if raw_out.get("ok") else str(raw_out.get("hint") or ""),
                                        cost_ms=cost_tool,
                                        phase="tool",
                                    )
                                tool_err = None if raw_out.get("ok") else str(raw_out.get("hint") or "")

                            out_s = dumps_step_output(tool_payload)
                            tool_brief = brief_from_payload(tool_payload)

                            from .chat_context_memory import append_tool_observation_memory

                            append_tool_observation_memory(
                                react_memory,
                                tool_name=fn,
                                tool_payload=tool_payload,
                                react_round=react_round_idx,
                            )
                            yield _sse(
                                "thought_step_end",
                                attach_invoke_to_payload(
                                    {
                                        "trace_id": trace_id,
                                        "task_id": task_id,
                                        "step_id": step_id_tool,
                                        "step_name": tool_call_label,
                                        "status": "completed" if not tool_err else "failed",
                                        "elapsed_ms": cost_tool,
                                        "status_text": "完成" if not tool_err else "失败",
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "description": tool_brief,
                                        "result_brief": tool_brief,
                                        "input_text": json.dumps(
                                            {
                                                "schema_version": 1,
                                                "tool_call": True,
                                                "tool_name": fn,
                                                "tool_args": args,
                                            },
                                            ensure_ascii=False,
                                        )[:4000],
                                        "output_text": out_s,
                                        "phase": "tool",
                                        "success": not tool_err,
                                        "sub_plan_id": tool_sub_plan_id,
                                        "sub_index": tool_sub_index,
                                        "node_kind": "tool_call",
                                        "step_lane": "execution",
                                    },
                                    mode=_react_invoke_mode,
                                    tool_name=fn,
                                    query=_tool_query_hint,
                                ),
                            )
                            react_tools_seen.add(fn)
                            yield _sse(
                                "span_update",
                                {
                                    "task_id": task_id,
                                    "step_id": step_id_tool,
                                    "step_name": f"MCP:{fn}",
                                    "elapsed_ms": cost_tool,
                                    "status": "completed",
                                    "parent_status": PARENT_EXECUTING,
                                    "token_count": 0,
                                    "react_round": react_round_idx,
                                    "sub_plan_id": tool_sub_plan_id,
                                },
                            )
                            working.append({
                                "role": "tool",
                                "tool_call_id": tid,
                                "content": _tool_result_to_jsonable_str(raw_out),
                            })
                        exec_ctx.react_round_idx = react_round_idx
                        exec_ctx.tool_round = tool_round
                        exec_ctx.web_block = web_block if isinstance(web_block, dict) else exec_ctx.web_block
                        continue

                    exec_ctx.react_round_idx = react_round_idx
                    if task_id and has_active_pipeline_tasks(str(task_id)):
                        working.append({
                            "role": "system",
                            "content": exec_strategy.continuation_system_hint(
                                exec_ctx, reason="async_pipeline_pending"
                            ),
                        })
                        continue
                    if exec_strategy.should_finalize_without_tools(
                        exec_ctx, content=content, tool_calls=tool_calls
                    ):
                        full_answer = sanitize_user_visible_answer_text(content or "")
                        break
                    reason = "no_tool_calls"
                    if task_id and has_active_pipeline_tasks(str(task_id)):
                        reason = "async_pipeline_pending"
                    elif exec_ctx.web_search and not ((exec_ctx.web_block or {}).get("results")):
                        reason = "web_pending"
                    elif parse_inline_tool_calls_from_content(content or ""):
                        reason = "inline_only"
                    working.append({
                        "role": "system",
                        "content": exec_strategy.continuation_system_hint(
                            exec_ctx, reason=reason
                        ),
                    })
                    continue

            if not full_answer and not use_tools:
                try:
                    async for piece in _async_iter_llm_token_stream(
                        provider=provider,
                        base_url=base_url,
                        api_key=api_key,
                        model=model_resolved,
                        messages=messages,
                        temperature=0.3,
                        max_tokens=1800,
                        timeout=120.0,
                    ):
                        streamed_tokens = True
                        piece_vis = sanitize_user_visible_answer_text(piece)
                        if not piece_vis:
                            continue
                        full_answer += piece_vis
                        yield _sse(
                            "answer_delta",
                            {
                                "trace_id": trace_id,
                                "task_id": task_id or "",
                                "content": piece_vis,
                                "kind": "body",
                                "stream_mode": "token",
                            },
                        )
                except Exception:
                    streamed_tokens = False
                    full_answer = ""
                if not streamed_tokens and not full_answer:
                    full_answer = (
                        await loop.run_in_executor(
                            _executor,
                            lambda: invoke_unified(
                                provider=provider,
                                base_url=base_url,
                                api_key=api_key,
                                model=model_resolved,
                                messages=messages,
                                temperature=0.3,
                                max_tokens=1800,
                                timeout=120.0,
                            ),
                        )
                    ) or ""

            if not full_answer and use_tools:
                # 工具链未产出最终文本时再降级一次无工具调用
                full_answer = (
                    await loop.run_in_executor(
                        _executor,
                        lambda: invoke_unified(
                            provider=provider,
                            base_url=base_url,
                            api_key=api_key,
                            model=model_resolved,
                            messages=messages,
                            temperature=0.3,
                            max_tokens=1800,
                            timeout=120.0,
                        )
                    )
                ) or ""

            if not full_answer:
                full_answer = "（未得到模型文本：请确认网关支持 function calling、MCP 可连，或稍后重试）"

            prompt_tokens = len(message) // 2
            completion_tokens = len(full_answer) // 2 if full_answer else 0
            token_count = prompt_tokens + completion_tokens
        except ImportError:
            full_answer = "provider_adapters 模块不可用，请检查 src/agent 路径"
        except Exception as e:
            from .chat_error_handler import llm_analyze_error_for_user

            full_answer = await llm_analyze_error_for_user(
                error_type=type(e).__name__,
                error_message=str(e)[:500],
                stage="LLM 调用",
                user_message=message,
            )
    else:
        missing = []
        if not api_key:
            missing.append("volcengine_api_key")
        if not model_resolved:
            missing.append("ai_chat_model（或请在问答页选择模型）")
        diag = chat_llm_config_diagnostics()
        cfg_hint = (
            f"配置路径: {diag.get('config_path')} "
            f"(存在={diag.get('config_exists')}, 节点池={diag.get('gateway_nodes')} 个)。"
        )
        if not diag.get("config_exists"):
            cfg_hint += (
                f" 未找到 config.json；请确认原项目路径存在: "
                f"{_AGENT_DIR / 'config.json'}"
            )
        else:
            cfg_hint += " 若刚改过配置请重启 start_backend.bat（释放 8000 端口）后再试。"
        full_answer = (
            f"未配置 LLM ({', '.join(missing)})。{cfg_hint} "
            f"请在 Agent 配置 > AI API 节点池中核对 Ark 节点。\n\n"
            f"收到您的问题：\"{message}\""
        )

    full_answer = sanitize_user_visible_answer_text(full_answer or "")
    llm_cost_ms = int((time.perf_counter() - answer_begin) * 1000)
    llm_payload = build_llm_step_output(answer=full_answer or "", cost_ms=llm_cost_ms)
    llm_output_text = dumps_step_output(llm_payload)
    if show_llm_as_subtask:
        yield _sse("thought_step_end", {
            "trace_id": trace_id, "task_id": task_id or "", "step_id": llm_step["step_id"],
            "step_name": "LLM生成回答", "status": "completed", "elapsed_ms": llm_cost_ms,
            "status_text": "完成", "timestamp": datetime.now().strftime("%H:%M:%S"),
            "description": clamp_result_brief_cn(brief_from_payload(llm_payload)),
            "input_text": message[:4000], "output_text": llm_output_text,
            "phase": "llm", "success": True, "confidence": 0.95, "token_count": max(12, len(full_answer) // 4),
            "sub_plan_id": llm_sub_plan_id, "sub_index": llm_sub_index, "node_kind": "llm_call",
        })
        yield _sse("step_think_end", {"trace_id": trace_id, "task_id": task_id or "", "step_id": llm_step["step_id"]})
    if task_id:
        _span_finish(
            llm_step["step_id"], status="completed", output_payload=llm_payload, token_count=token_count,
            open_layer={"objective": "生成回答", "decision": "stop", "confidence": 0.85, "progress_percent": 100},
        )
        yield _sse("span_update", {
            "task_id": task_id, "step_id": llm_step["step_id"],
            "step_name": "LLM生成回答", "elapsed_ms": llm_cost_ms, "status": "completed",
            "parent_status": PARENT_EXECUTING,
            "token_count": token_count, "success": True, "confidence": 0.95,
        })

    # ReAct 完成后才进入「生成回答」展示（避免固定编排刚结束就显示总结）
    if not answer_started:
        yield _sse("answer_start", {"trace_id": trace_id, "task_id": task_id or "", "ephemeral": not use_main_task})
        if use_main_task and task_id and not show_llm_as_subtask:
            yield _sse("answer_generating", {
                "trace_id": trace_id,
                "task_id": task_id or "",
                "label": "生成回答",
                "stage": "生成回答中",
            })
        answer_started = True

    # 未走 token 流时（MCP 整段返回等）：单次 replay delta，前端 smoothStream 逐字展示
    if not streamed_tokens and full_answer:
        async for ev in _yield_answer_replay_delta(trace_id, task_id, full_answer):
            yield ev

    total_tokens = token_count
    total_ms = int((time.perf_counter() - time_start) * 1000)

    # ── 后台 link_pipeline 长等待：未达目标或超时前不进入终态 ──
    from .chat_context_memory import pipeline_ids_from_answer_text, answer_implies_async_pipeline_pending

    pipeline_task_ids_pre: List[str] = []
    tool_outputs_pre: List[Any] = []
    if use_main_task and task_id:
        span_pre = _span_get(task_id) or {}
        snap_pre = span_pre.get("snapshot_json") if isinstance(span_pre.get("snapshot_json"), dict) else {}
        tool_outputs_pre = span_pre.get("tool_outputs") or snap_pre.get("tool_outputs") or []
        pipeline_task_ids_pre = _async_pipeline_ids_from_tool_outputs(tool_outputs_pre)
    if not pipeline_task_ids_pre and full_answer:
        pipeline_task_ids_pre = pipeline_ids_from_answer_text(full_answer)
    if pipeline_task_ids_pre:
        pipeline_wait_sec = float(cfg.get("chat_pipeline_wait_sec") or 0) or max(
            float(cfg.get("chat_tool_timeout_sec", 60) or 60)
            * max(1, int(cfg.get("chat_tool_max_retry", 3) or 3))
            * 5,
            600.0,
        )
        async for ev in _stream_await_link_pipelines(
            pipeline_task_ids_pre,
            timeout_sec=pipeline_wait_sec,
            trace_id=trace_id,
            task_id=task_id or "",
        ):
            yield ev

    yield _sse("answer_end", {
        "trace_id": trace_id, "task_id": task_id or "",
        "full_text": full_answer,
        "stream_mode": "token" if streamed_tokens else "replay",
        "token_usage": {"prompt": token_count // 2, "completion": token_count // 2},
        "search_results": search_results,
        "ephemeral": not use_main_task,
    })

    # ── Phase 3: 复杂任务落主任务状态；禁止自动标「已解决」，仅用户手动可 resolved ──
    final_status = PARENT_EXECUTING
    pause_reason = ""
    summary_expect = (message or "")[:200]
    snap_final: Dict[str, Any] = {}
    tool_outputs_final: List[Any] = list(tool_outputs_pre) if pipeline_task_ids_pre else []
    async_pipeline_pending = False
    pipeline_task_ids: List[str] = list(pipeline_task_ids_pre)
    answer_claims_pipeline = answer_implies_async_pipeline_pending(full_answer, message)
    if use_main_task and task_id:
        if full_answer and len(full_answer.strip()) < 8:
            final_status = PARENT_PAUSED
            pause_reason = "回答过短，未满足任务摘要中的输出要求"
        span_final = _span_get(task_id) or {}
        if not tool_outputs_final:
            snap_final = span_final.get("snapshot_json") if isinstance(span_final.get("snapshot_json"), dict) else {}
            tool_outputs_final = span_final.get("tool_outputs") or snap_final.get("tool_outputs") or []
        if not pipeline_task_ids:
            pipeline_task_ids = _async_pipeline_ids_from_tool_outputs(tool_outputs_final)
        if not pipeline_task_ids and full_answer:
            pipeline_task_ids = pipeline_ids_from_answer_text(full_answer)
        if pipeline_task_ids:
            from .task_manager import get_task as _get_pipe_task

            still_active = any(
                str((_get_pipe_task(pid) or {}).get("status") or "").lower() in _PIPELINE_ACTIVE_STATUSES
                for pid in pipeline_task_ids
            )
            async_pipeline_pending = still_active
            if still_active:
                final_status = PARENT_EXECUTING
                pause_reason = pause_reason or "链接文档化流水线仍在执行，请稍后追问进度"
        elif answer_claims_pipeline:
            async_pipeline_pending = True
            final_status = PARENT_EXECUTING
            pause_reason = pause_reason or "回答表明后台流水线仍在处理，主任务保持执行中"
        assessment = (
            "链接文档化流水线执行中，等待后台产出"
            if async_pipeline_pending
            else ("执行中，待用户确认结案" if final_status == PARENT_EXECUTING else pause_reason)
        )
        decision = "continue" if async_pipeline_pending or final_status == PARENT_EXECUTING else "escalate"
        if async_pipeline_pending:
            snap_fixed = dict(snap_final.get("fixed") or {})
            snap_fixed["pipeline_task_ids"] = pipeline_task_ids
            snap_fixed["async_pipeline_pending"] = True
            snap_final = {**snap_final, "fixed": snap_fixed}
        _span_patch_snapshot(
            task_id,
            fixed={"task_id": task_id, "session_id": session_id},
            open_layer={
                "objective": summary_expect,
                "current_assessment": assessment,
                "decision": decision,
            },
        )
        _span_update(
            task_id,
            status=final_status,
            ended_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            total_duration_ms=total_ms,
            total_token_count=total_tokens,
            total_steps=step_idx + 1,
            completed_steps=step_idx + 1,
            snapshot_json=snap_final if snap_final else {
                "fixed": {"task_id": task_id, "session_id": session_id},
                "open": {
                    "objective": summary_expect,
                    "current_assessment": assessment,
                    "decision": decision,
                },
            },
        )

    yield _sse("task_completed", {
        "task_id": task_id or "",
        "total_duration_ms": total_ms,
        "total_token_count": total_tokens,
        "total_steps": step_idx + 1,
        "status": final_status if use_main_task else "ephemeral",
        "pause_reason": pause_reason,
        "async_pipeline_pending": async_pipeline_pending,
        "pipeline_task_ids": pipeline_task_ids,
        "context_mode": "agent",
        "search_results": search_results,
        "tool_outputs": tool_outputs_final,
        "snapshot_json": snap_final,
        "persist_main_task": use_main_task,
        "task_kind": intent_task_kind,
        "ephemeral": not use_main_task,
        "user_resolved_allowed": False,
    })

    _messages.setdefault(session_id, []).append({"role": "user", "content": message})
    assistant_payload: Dict[str, Any] = {"role": "assistant", "content": full_answer}
    if search_results:
        assistant_payload["search_results"] = search_results
    if use_main_task and task_id:
        assistant_payload["task_audit"] = {
            "task_id": task_id,
            "status": final_status,
            "snapshot_json": snap_final,
            "tool_outputs": tool_outputs_final,
            "async_pipeline_pending": async_pipeline_pending,
            "pipeline_task_ids": pipeline_task_ids,
        }
    else:
        assistant_payload["ephemeral"] = True
        assistant_payload["task_kind"] = "simple"
    _messages[session_id].append(assistant_payload)
    if session_id in _sessions:
        _sessions[session_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        slim = [_slim_ui_message(m) for m in _messages.get(session_id, []) if isinstance(m, dict)]
        cur_task_persist = None
        hist_persist = None
        try:
            from .chat_context_memory import get_session_document, upsert_session_main_task_history

            if use_main_task and task_id:
                upsert_session_main_task_history(
                    session_id,
                    task_id=task_id,
                    user_query=message,
                    query_summary=(message or "")[:80],
                    status=final_status,
                    async_pipeline_pending=async_pipeline_pending,
                    pipeline_task_ids=pipeline_task_ids,
                )
            doc = get_session_document(session_id) or {}
            cur_task_persist = doc.get("cur_task")
            hist_persist = doc.get("main_task_history")
        except Exception:
            pass
        _store_persist(
            session_id,
            _sessions[session_id],
            slim,
            cur_task=cur_task_persist,
            main_task_history=hist_persist if isinstance(hist_persist, list) else None,
            mark_dirty=True,
        )


# ── 兼容旧版 chat_stream ──
async def chat_stream(
    message: str,
    session_id: str = "default",
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_profile: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    rag_prefetch: bool = False,
    web_search: bool = False,
    read_comments: bool = False,
    include_rss: bool = False,
    deep_think: bool = False,
    **orchestration_kwargs: Any,
):
    async for event in chat_stream_v2(
        message,
        session_id,
        model=model,
        agent_id=agent_id,
        agent_profile=agent_profile,
        user_id=user_id,
        rag_prefetch=rag_prefetch,
        web_search=web_search,
        read_comments=read_comments,
        include_rss=include_rss,
        deep_think=deep_think,
        **orchestration_kwargs,
    ):
        yield event
