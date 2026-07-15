"""上下文记忆：短期全量 + 任务 Redis 主链 / 会话摘要层（LLM FIFO）/ 结案固化。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .chat_session_store import get_session_document, persist_session
from .task_states import PARENT_TERMINAL

_log = logging.getLogger("sba.chat_context_memory")

DEFAULT_MAX_TOKENS = 128_000
DEFAULT_WARN_RATIO = 0.80
DEFAULT_FORCE_RATIO = 1.0
MEMORY_MODE_SHORT = "short"
MEMORY_MODE_SUMMARY = "summary"
KEEP_RECENT_MESSAGES = 8
MIN_MESSAGES_TO_COMPRESS = 12
MAX_FOLD_CHARS = 14_000
# 落盘与运行时 guard（UI thinking/span 不计入 LLM，但会撑爆 JSON 与 event loop）
MAX_PERSISTED_MESSAGES = 40
MAX_MAIN_TASK_HISTORY_STORED = 32
SESSION_DOC_SOFT_BYTES = 450_000
MAX_STORED_CONTENT_CHARS = 12_000
MAX_STORED_STEP_BRIEF_CHARS = 280
MAX_STORED_RESULT_MSG_CHARS = 6000
MAX_STORED_THINK_DESC_CHARS = 240
# 编排 IO / SPAN 详情走 Redis·SPAN 任务链，禁止写入会话 JSON
_SPAN_STORAGE_KEYS = frozenset({
    "task_id", "trace_id", "task_kind", "ephemeral", "persist_main_task", "status",
})

_NEW_TASK_HINTS = (
    "新任务", "新问题", "换个问题", "另外", "重新开", "不要继续", "另起", "开新",
)
_CONTINUE_HINTS = (
    "继续", "接着", "然后", "还有", "下一步", "那个", "这个", "刚才", "上面", "补充",
    "再查", "再搜", "再来", "往下", "现在呢", "怎么样了", "进度", "好了吗", "结果呢",
    "完成了吗", "搞定了吗", "出来了吗", "好了没",
    "怎么回事", "啥情况", "什么情况", "任务呢", "分析的任务", "要你分析", "之前的",
    "刚才的", "上面的", "还没", "怎么还没",
)
# 禁止子串盲目命中（如「分析这个人物的画像」中的「这个」）
_CONTINUE_HINTS_AMBIGUOUS = frozenset({"这个", "那个", "还有", "然后", "上面", "补充"})


def _has_explicit_continue_hint(message: str) -> bool:
    """仅匹配明确的续接/进度短语，避免普通新任务句中的「这个/那个」误触续接。"""
    m = (message or "").strip()
    if not m:
        return False
    for h in _CONTINUE_HINTS:
        if h in _CONTINUE_HINTS_AMBIGUOUS:
            continue
        if h in m:
            return True
    if len(m) <= 14:
        for h in _CONTINUE_HINTS_AMBIGUOUS:
            if m == h or m.startswith(h + "呢") or m.startswith(h + "？"):
                return True
    return False


def _is_unrelated_new_work(message: str, task_row: Optional[Dict[str, Any]] = None) -> bool:
    """当前句与主任务摘要明显无关 → 应开新任务，禁止续接旧 task_id。"""
    msg = (message or "").strip()
    if not msg:
        return False
    if _looks_like_new_task(msg):
        return True
    if _has_link_analysis_intent(msg):
        anchor = str(
            (task_row or {}).get("user_query") or (task_row or {}).get("query_summary") or ""
        ).strip()
        if not anchor:
            return True
        social = ("小红书", "画像", "主页", "用户分析", "红人", "博主", "xhs", "red_id")
        kb = ("知识库", "mcp", "rag", "检索", "文档总结", "文档进行总结", "总结反馈")
        msg_l = msg.lower()
        anchor_l = anchor.lower()
        msg_social = any(k in msg or k in msg_l for k in social)
        anchor_kb = any(k in anchor or k in anchor_l for k in kb)
        if msg_social and anchor_kb and not any(k in msg or k in msg_l for k in kb):
            return True
    return False


def extract_profile_query_keywords(message: str) -> List[str]:
    """从原问抽取可展示关键词（昵称、平台 ID、业务词）。"""
    msg = (message or "").strip()
    if not msg:
        return []
    keys: List[str] = []
    seen: set = set()

    def _add(v: str) -> None:
        t = str(v or "").strip()
        if not t or t in seen:
            return
        seen.add(t)
        keys.append(t)

    try:
        from .link_doc_routing import extract_xhs_numeric_id

        xhs_id = extract_xhs_numeric_id(msg)
        if xhs_id:
            _add(xhs_id)
    except Exception:
        pass
    for m in re.finditer(r"小红书号[：:\s]*([0-9]{5,12})", msg):
        _add(m.group(1))
    for m in re.finditer(r"([^\s，,。；;\n：:]{2,16})[ \t\n]*小红书号", msg):
        _add(m.group(1))
    for m in re.finditer(r"主页[：:\s]*([^\s，,。；;\n：:]{2,16})", msg):
        _add(m.group(1))
    for token in ("小红书", "人物画像", "画像", "主页", "用户分析"):
        if token in msg:
            _add(token)
    if len(keys) < 2:
        for m in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9_]{2,12}", msg):
            w = m.group(0)
            if w in ("可以", "帮我", "分析", "一下", "这个", "人物", "不用", "记录", "订阅", "模块", "只需", "简单"):
                continue
            _add(w)
            if len(keys) >= 6:
                break
    return keys[:8]


def build_profile_task_summary(message: str, keywords: Optional[List[str]] = None) -> str:
    """社媒画像类任务摘要（规则，毫秒级）。"""
    msg = (message or "").strip()
    qk = keywords if isinstance(keywords, list) else extract_profile_query_keywords(msg)
    nick = ""
    xhs_id = ""
    for k in qk:
        if re.fullmatch(r"[0-9]{5,12}", str(k)):
            xhs_id = str(k)
        elif len(str(k)) >= 2 and not re.fullmatch(r"(小红书|人物画像|画像|主页|用户分析)", str(k)):
            if not nick:
                nick = str(k)
    if nick and xhs_id:
        return f"分析小红书用户「{nick}」（ID:{xhs_id}）的人物画像"
    if xhs_id:
        return f"分析小红书用户（ID:{xhs_id}）的人物画像"
    if nick:
        return f"分析小红书用户「{nick}」的人物画像"
    return (msg or "社媒用户画像分析")[:120]


def build_fast_new_main_intent(
    message: str,
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    main_task_history: Optional[List] = None,
) -> Optional[Dict[str, Any]]:
    """
    与当前/最近主任务明显无关的新课题：规则秒级意图，免编排 LLM。
    典型：MCP 知识库任务后的小红书用户画像。
    """
    msg = (message or "").strip()
    if not msg:
        return None
    if extract_task_id_from_message(msg) or _looks_like_task_resume(msg):
        return None
    recent = _get_recent_main_task(cur_task, main_task_history)
    anchor = cur_task if isinstance(cur_task, dict) and cur_task.get("task_id") else recent
    if not anchor:
        return None
    if not _is_unrelated_new_work(msg, anchor):
        return None
    qk = extract_profile_query_keywords(msg)
    task_summary = build_profile_task_summary(msg, qk)
    needs_rag = any(k in msg.lower() for k in ("知识库", "mcp", "rag", "检索", "文档"))
    return {
        "mode": "new_main",
        "task_id": "",
        "skip_orchestration": False,
        "reason": "规则快径：新主任务（与当前主任务主题无关，原问要素已足够清晰）",
        "llm_powered": False,
        "task_summary": task_summary,
        "query_keywords": qk,
        "needs_rag": needs_rag,
        "needs_web_search": False,
        "task_complexity": "normal",
    }


def annotate_intent_preprocess_plan(
    snap: Dict[str, Any],
    message: str,
    *,
    orch_pipeline_nodes: Optional[Dict[str, Any]] = None,
    domain: str = "",
) -> Dict[str, Any]:
    """
    在意图识别输出中记录 Query 改写 / 术语映射 / 任务分解 的判定（含跳过原因）。
    """
    out = dict(snap or {})
    nodes = orch_pipeline_nodes if isinstance(orch_pipeline_nodes, dict) else {}
    msg = (message or "").strip()
    qk = out.get("query_keywords") or out.get("keywords") or []
    if not isinstance(qk, list):
        qk = []
    qk = [str(x).strip() for x in qk if str(x).strip()]
    dom = str(domain or out.get("domain") or "").strip()
    is_xhs = dom == "社媒分析" or _has_link_analysis_intent(msg)
    has_clear_ids = bool(qk) and any(re.fullmatch(r"[0-9]{5,12}", str(k)) for k in qk)
    needs_rag = bool(out.get("needs_rag"))
    rewrite_enabled = bool(nodes.get("query_rewrite", True))

    if not rewrite_enabled:
        out["query_rewrite_decision"] = "skip"
        out["query_rewrite_skip_reason"] = "编排节点 query_rewrite 未启用"
    elif is_xhs and has_clear_ids and not needs_rag:
        out["query_rewrite_decision"] = "skip"
        out["query_rewrite_skip_reason"] = (
            "原问含明确平台 ID/昵称，关键词可直映射为内部检索词，无需指代消解与术语转换"
        )
        out["rewrite_state"] = "rewrite_hold"
    elif qk and not needs_rag:
        out["query_rewrite_decision"] = "skip"
        out["query_rewrite_skip_reason"] = "原问关键词已足够清晰，且无知识库检索需求"
        out["rewrite_state"] = "rewrite_hold"
    else:
        out["query_rewrite_decision"] = "apply"
        out["query_rewrite_skip_reason"] = ""

    decompose_enabled = bool(nodes.get("task_decompose", False))
    complexity = str(out.get("task_complexity") or ("normal" if is_xhs else "complex")).strip()
    out["task_complexity"] = complexity
    if not decompose_enabled:
        out["task_decompose_decision"] = "skip"
        out["task_decompose_skip_reason"] = "编排节点 task_decompose 未启用"
    elif complexity == "normal" or is_xhs:
        out["task_decompose_decision"] = "skip"
        out["task_decompose_skip_reason"] = "一般复杂度任务，单步 ReAct+工具可满足，无需意图分解"
    else:
        out["task_decompose_decision"] = "apply"
        out["task_decompose_skip_reason"] = ""

    return out


_TASK_RECALL_HINTS = (
    "怎么回事", "啥情况", "任务呢", "分析的任务", "要你分析", "上面的任务", "之前说的",
    "当前任务", "当前的任务", "任务是啥", "任务是什么", "什么任务", "哪个任务",
    "忘记了", "忘了", "啥来着", "记不得",
)
_STATUS_INQUIRY_HINTS = (
    "好了吗", "完成了吗", "结果呢", "进度", "怎么样了", "搞定了吗", "出来了吗", "好了没",
    "分析好了", "处理好了", "完成了没",
    "执行到哪", "进行到哪", "做到哪", "到哪了", "到哪一步", "执行状态", "任务状态",
)
_TASK_RESUME_HINTS = (
    "任务恢复", "恢复说明", "重新启动", "重启检索", "恢复检索", "恢复流程", "重新执行",
    "续执行", "继续执行", "接着执行", "之前执行", "当前将重新", "出现代码报错", "报错后",
    "重新走", "重新跑", "接着做", "未完成", "继续任务", "重新启动检索",
)
# 不用前导 \b：中文与 task_ 连写时（如「执行task_554…」）Word boundary 会失效
_TASK_ID_IN_MESSAGE_RE = re.compile(r"(task_[a-z0-9]{8,})\b", re.I)

_SESSION_SUMMARY_SYSTEM = """你是会话上下文压缩助手（类似 Claude Code / Cursor 的 context compact）。
你的输出是【会话摘要】，供后续轮次恢复对话脉络，不是主任务执行日志。

必须遵守：
1. 用中文 Markdown，简洁但信息密度高。
2. 保留：用户目标、已达成结论、关键事实/数字/链接、未决问题、用户偏好。
3. 主任务仅写 task_id 指针与一句话标签，禁止展开工具 IO / ReAct 步骤（那些在 Redis 主任务链里）。
4. 若存在「先前会话摘要」，将其与新消息融合为一份更新摘要，去重，不要两套并列。
5. 不要编造未出现的内容；不确定处标「待确认」。

输出结构（必须包含这些二级标题）：
## 对话脉络
## 用户诉求与结论
## 待续事项
## 关联主任务（指针）
"""


def memory_prefs_from_doc(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    prefs = (doc or {}).get("prefs") or {}
    cp = prefs.get("chatPrefs") if isinstance(prefs.get("chatPrefs"), dict) else {}
    max_tok = int(cp.get("contextMaxTokens") or DEFAULT_MAX_TOKENS)
    warn_pct = float(cp.get("contextWarnPct") or DEFAULT_WARN_RATIO * 100)
    if warn_pct > 1:
        warn_pct = warn_pct / 100.0
    return {
        "context_max_tokens": max(8000, max_tok),
        "context_warn_ratio": min(0.95, max(0.5, warn_pct)),
        "context_force_ratio": DEFAULT_FORCE_RATIO,
    }


def _json_size(obj: Any) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return len(str(obj))


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """估算 messages 占用（含 thinking/span，与落盘体积一致）。"""
    chars = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        chars += _json_size(m)
    return max(1, chars // 2)


def estimate_session_doc_tokens(doc: Optional[Dict[str, Any]]) -> int:
    """整份会话文档 token 粗估（用于压缩阈值，避免只算 content 导致永不压缩）。"""
    if not doc:
        return 1
    parts = [
        doc.get("messages") or [],
        doc.get("cur_task") or {},
        doc.get("main_task_history") or [],
        (doc.get("memory_meta") or {}).get("summary_text") or "",
    ]
    chars = sum(_json_size(p) for p in parts)
    return max(1, chars // 2)


async def load_session_document_async(session_id: str) -> Dict[str, Any]:
    """大 JSON 读盘走线程池，避免阻塞 event loop。"""
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    raw = await asyncio.to_thread(get_session_document, sid)
    return dict(raw) if isinstance(raw, dict) else {}


def slim_thinking_step_for_storage(step: Any) -> Dict[str, Any]:
    """用户视角落盘：做了啥(what) + 结果(result)；状态类字段由 UI 实时展示，不入 session。"""
    if not isinstance(step, dict):
        return {}
    out: Dict[str, Any] = {}
    if step.get("step_id"):
        out["step_id"] = step.get("step_id")
    kind = step.get("kind")
    if not kind and step.get("node_kind") == "tool_call":
        kind = "tool"
    if kind:
        out["kind"] = kind
    what = str(step.get("what") or step.get("step_name") or step.get("operation") or "").strip()
    result = str(step.get("result") or step.get("result_brief") or step.get("description") or "").strip()
    if what:
        out["what"] = what[:MAX_STORED_STEP_BRIEF_CHARS]
    if result:
        out["result"] = result[:MAX_STORED_RESULT_MSG_CHARS]
    return out


def slim_span_for_storage(span: Any) -> Dict[str, Any]:
    if not isinstance(span, dict) or not span:
        return {}
    return {k: span.get(k) for k in _SPAN_STORAGE_KEYS if span.get(k) is not None}


def slim_message_for_storage(msg: Any) -> Dict[str, Any]:
    """
    会话 JSON 只保留 UI 可渲染的摘要 + 正文；完整工具 IO / ReAct 链在 SPAN·Redis。
    """
    if not isinstance(msg, dict):
        return {}
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return {}
    content = str(msg.get("content") or "")
    if len(content) > MAX_STORED_CONTENT_CHARS:
        content = content[: MAX_STORED_CONTENT_CHARS - 20] + "\n\n…（正文已截断）"
    thinking_raw = msg.get("thinking") or []
    thinking: List[Dict[str, Any]] = []
    if isinstance(thinking_raw, list):
        thinking = [slim_thinking_step_for_storage(t) for t in thinking_raw if isinstance(t, dict)]
        thinking = [t for t in thinking if t]
    out: Dict[str, Any] = {
        "role": role,
        "content": content,
        "thinking": thinking or None,
        "span": slim_span_for_storage(msg.get("span")),
        "task_id": msg.get("task_id"),
        "result_status": msg.get("result_status"),
        "thinkingExpanded": bool(msg.get("thinkingExpanded", False)),
    }
    if msg.get("task_audit"):
        ta = msg.get("task_audit")
        if isinstance(ta, dict):
            out["task_audit"] = {
                k: ta.get(k)
                for k in ("task_id", "task_kind", "status", "ephemeral", "user_query", "query_summary")
                if ta.get(k) is not None
            }
    if msg.get("ephemeral"):
        out["ephemeral"] = True
    if msg.get("task_kind"):
        out["task_kind"] = msg.get("task_kind")
    return out


def slim_cur_task_for_storage(cur_task: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(cur_task, dict) or not cur_task:
        return None
    steps_in = cur_task.get("steps") or []
    steps: List[Dict[str, Any]] = []
    if isinstance(steps_in, list):
        for s in steps_in[-16:]:
            if isinstance(s, dict):
                steps.append(slim_thinking_step_for_storage(s))
    out = {
        k: cur_task.get(k)
        for k in (
            "task_id", "user_query", "query_summary", "status", "task_kind",
            "sub_plan_id", "group_seq",
        )
        if cur_task.get(k) is not None
    }
    if steps:
        out["steps"] = steps
    return out


def slim_main_task_history_for_storage(hist: Any) -> List[Dict[str, Any]]:
    if not isinstance(hist, list):
        return []
    slim: List[Dict[str, Any]] = []
    for h in hist[-MAX_MAIN_TASK_HISTORY_STORED:]:
        if not isinstance(h, dict) or not h.get("task_id"):
            continue
        slim.append(
            {
                k: h.get(k)
                for k in (
                    "task_id", "user_query", "query_summary", "status", "task_kind",
                    "result_status", "async_pipeline_pending",
                )
                if h.get(k) is not None
            }
        )
    return slim


def guard_session_payload_for_persist(
    messages: Optional[List[Any]],
    cur_task: Optional[Dict[str, Any]],
    main_task_history: Optional[List[Any]],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """前端 PUT /state 必经：瘦身 + 条数上限（不在此调 LLM，避免流式期间反复摘要）。"""
    slim_msgs = [
        slim_message_for_storage(m)
        for m in (messages or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    if len(slim_msgs) > MAX_PERSISTED_MESSAGES:
        slim_msgs = slim_msgs[-MAX_PERSISTED_MESSAGES:]
    slim_ct = slim_cur_task_for_storage(cur_task) if cur_task else None
    slim_hist = slim_main_task_history_for_storage(main_task_history or [])
    return slim_msgs, slim_ct, slim_hist


def session_doc_byte_size(doc: Dict[str, Any]) -> int:
    return _json_size(doc)


def session_document_has_full_orchestration_io(doc: Optional[Dict[str, Any]]) -> bool:
    """是否仍含应存 SPAN/Redis 的全量编排 IO（不应留在 session JSON）。"""
    if not isinstance(doc, dict):
        return False
    for m in doc.get("messages") or []:
        if not isinstance(m, dict):
            continue
        for t in m.get("thinking") or []:
            if not isinstance(t, dict):
                continue
            if any(str(t.get(k) or "").strip() for k in ("input_text", "output_text", "think_text")):
                return True
            if (t.get("what") or t.get("result")) and not any(
                t.get(k) for k in ("status", "phase", "node_kind", "result_brief", "step_name")
            ):
                continue
            if t.get("step_name") or t.get("result_brief") or t.get("status"):
                return True
        span = m.get("span")
        if isinstance(span, dict) and span.get("search_results"):
            return True
    return False


def normalize_session_document_for_storage(doc: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """
    会话落盘定案：transcript + 步骤摘要 + 指针。
    返回 (瘦身后的 doc, 相对原文是否应回写磁盘)。
    """
    if not isinstance(doc, dict) or not doc:
        return {}, False
    pre_bytes = session_doc_byte_size(
        {
            "messages": doc.get("messages"),
            "cur_task": doc.get("cur_task"),
            "main_task_history": doc.get("main_task_history"),
        }
    )
    out = dict(doc)
    msgs = [
        slim_message_for_storage(m)
        for m in (doc.get("messages") or [])
        if isinstance(m, dict)
    ]
    msgs = [m for m in msgs if m]
    if len(msgs) > MAX_PERSISTED_MESSAGES:
        msgs = msgs[-MAX_PERSISTED_MESSAGES:]
    out["messages"] = msgs
    if isinstance(doc.get("cur_task"), dict):
        out["cur_task"] = slim_cur_task_for_storage(doc["cur_task"])
    else:
        out["cur_task"] = doc.get("cur_task")
    out["main_task_history"] = slim_main_task_history_for_storage(doc.get("main_task_history"))
    post_bytes = session_doc_byte_size(
        {
            "messages": out.get("messages"),
            "cur_task": out.get("cur_task"),
            "main_task_history": out.get("main_task_history"),
        }
    )
    changed = (
        post_bytes < pre_bytes
        or session_document_has_full_orchestration_io(doc)
        or len(msgs) < len(doc.get("messages") or [])
    )
    return out, changed


def persist_normalized_session_document(session_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """瘦身并落盘；供 GET 迁移与 prepare 回写共用。"""
    sid = str(session_id or "").strip()
    normalized, _ = normalize_session_document_for_storage(doc)
    if not sid:
        return normalized
    sm = dict(normalized.get("session") or {})
    sm["id"] = sid
    persist_session(
        sid,
        sm,
        normalized.get("messages") or [],
        cur_task=normalized.get("cur_task"),
        main_task_history=normalized.get("main_task_history") or [],
        prefs=normalized.get("prefs"),
        memory_meta=normalized.get("memory_meta"),
        mark_dirty=True,
    )
    return normalized


def maybe_migrate_session_document_async(session_id: str) -> None:
    """后台瘦身超大/含全量 IO 的会话，不阻塞 GET 首屏。"""
    sid = str(session_id or "").strip()
    if not sid:
        return
    doc = get_session_document(sid)
    if not doc:
        return
    normalized, changed = normalize_session_document_for_storage(doc)
    if changed or session_document_has_full_orchestration_io(doc):
        try:
            persist_normalized_session_document(sid, normalized)
            _log.info(
                "[AI问答-会话读|chat_context_memory.maybe_migrate_session_document_async|session:%s|硬编执行|后台瘦身] ok=true",
                sid,
            )
        except Exception as ex:
            _log.warning(
                "[AI问答-会话读|chat_context_memory.maybe_migrate_session_document_async|session:%s|硬编执行|后台瘦身] "
                "ok=false; error_type=%s; error_message=%s",
                sid,
                type(ex).__name__,
                str(ex)[:200],
            )


def context_usage(
    doc: Optional[Dict[str, Any]],
    *,
    extra_tokens: int = 0,
    prefs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    mp = prefs or memory_prefs_from_doc(doc)
    max_tok = int(mp["context_max_tokens"])
    messages = (doc or {}).get("messages") or []
    memory_meta = (doc or {}).get("memory_meta") or {}
    summary_tok = int(memory_meta.get("summary_tokens_est") or 0)
    msg_tok = estimate_messages_tokens(messages)
    doc_tok = estimate_session_doc_tokens(doc)
    total = max(msg_tok, doc_tok) + summary_tok + max(0, int(extra_tokens))
    pct = min(100.0, round(total / max_tok * 100, 1))
    warn_line = int(mp["context_warn_ratio"] * 100)
    force_line = int(mp["context_force_ratio"] * 100)
    mode = str(memory_meta.get("mode") or MEMORY_MODE_SHORT)
    if pct >= force_line:
        mode = MEMORY_MODE_SUMMARY
    elif pct >= warn_line and mode == MEMORY_MODE_SHORT:
        mode = MEMORY_MODE_SUMMARY
    return {
        "tokens_est": total,
        "max_tokens": max_tok,
        "pct": pct,
        "mode": mode,
        "warn_pct": warn_line,
        "force_pct": force_line,
        "should_pre_summarize": pct >= warn_line and len(messages) >= MIN_MESSAGES_TO_COMPRESS,
        "should_force_archive": pct >= force_line,
    }


def _load_llm_cfg() -> Dict[str, Any]:
    try:
        from .config import load_config

        cfg = load_config()
        if cfg:
            return cfg
    except Exception:
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src" / "agent" / "config.json"
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _collect_task_pointers(messages: List[Dict[str, Any]], main_history: Optional[List] = None) -> List[str]:
    seen: set = set()
    lines: List[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        tid = str(m.get("task_id") or "").strip()
        if tid and tid not in seen:
            seen.add(tid)
            lines.append(f"- `{tid}`")
    for h in main_history or []:
        if not isinstance(h, dict):
            continue
        tid = str(h.get("task_id") or "").strip()
        if tid and tid not in seen:
            seen.add(tid)
            qs = str(h.get("query_summary") or h.get("user_query") or "")[:80]
            lines.append(f"- `{tid}`: {qs}" if qs else f"- `{tid}`")
    return lines


def _format_messages_for_llm(messages: List[Dict[str, Any]]) -> str:
    """将待折叠消息格式化为 LLM 输入（不含 thinking 细节）。"""
    parts: List[str] = []
    used = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = re.sub(r"\s+", " ", str(m.get("content") or "").strip())
        if not content:
            continue
        label = "用户" if role == "user" else "助手"
        tid = str(m.get("task_id") or "").strip()
        prefix = f"[{label}]"
        if tid:
            prefix += f"(task_id={tid})"
        chunk = content[:2000] + ("…" if len(content) > 2000 else "")
        line = f"{prefix}: {chunk}"
        if used + len(line) > MAX_FOLD_CHARS:
            parts.append("…（更早消息已截断，详见先前会话摘要）")
            break
        parts.append(line)
        used += len(line)
    return "\n".join(parts)


def _invoke_session_summary_llm(
    *,
    prior_summary: str,
    messages_to_fold: List[Dict[str, Any]],
    task_pointers: List[str],
    reason: str = "threshold",
) -> Dict[str, Any]:
    """真实 LLM 生成/更新会话摘要（Claude Code 式递归压缩）。"""
    cfg = _load_llm_cfg()
    api_key = (cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
    base_url = (
        cfg.get("volcengine_base_url") or cfg.get("openai_base_url") or "https://ark.cn-beijing.volces.com/api/v3"
    ).strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    model = (cfg.get("ai_chat_model") or "").strip()
    if not api_key or not model:
        return {"ok": False, "error": "未配置 LLM 网关", "llm_powered": False}

    agent_dir = Path(__file__).resolve().parents[2].parent / "src" / "agent"
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    try:
        from provider_adapters import invoke_chat_completion_raw, _extract_openai_message_dict
    except ImportError as ex:
        return {"ok": False, "error": f"provider_adapters 不可用: {ex}", "llm_powered": False}

    fold_text = _format_messages_for_llm(messages_to_fold)
    if not fold_text.strip() and not (prior_summary or "").strip():
        return {"ok": False, "error": "无消息可摘要", "llm_powered": False}

    user_parts = [
        f"压缩原因: {reason}",
        f"待折叠消息轮数: {len(messages_to_fold)}",
    ]
    if prior_summary.strip():
        user_parts.append("## 先前会话摘要\n" + prior_summary.strip()[:6000])
    user_parts.append("## 待折叠进摘要的对话\n" + fold_text)
    if task_pointers:
        user_parts.append("## 主任务指针（仅引用，勿展开 IO）\n" + "\n".join(task_pointers[:20]))
    user_prompt = "\n\n".join(user_parts)

    last_err = ""
    for attempt in (1, 2):
        try:
            data = invoke_chat_completion_raw(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=[
                    {"role": "system", "content": _SESSION_SUMMARY_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=1800,
                timeout=90.0,
                thinking_enabled=False,
                tools=None,
            )
            msg = _extract_openai_message_dict(data)
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            text = content.strip()
            if not text:
                raise RuntimeError("LLM 返回空摘要")
            header = (
                f"<!-- session_summary llm model={model} at={datetime.now().isoformat(timespec='seconds')} -->\n"
            )
            return {
                "ok": True,
                "content": header + text,
                "model": model,
                "llm_powered": True,
                "attempt": attempt,
            }
        except Exception as ex:
            last_err = str(ex)
            _log.warning(
                "[上下文记忆|chat_context_memory._invoke_session_summary_llm|session_summary|Agent执行|重试] "
                "attempt=%s; error_type=%s; error_message=%s",
                attempt,
                type(ex).__name__,
                last_err[:200],
            )
    return {"ok": False, "error": last_err[:500], "llm_powered": False}


def _build_session_summary_llm(
    messages: List[Dict[str, Any]],
    *,
    prior_summary: str = "",
    main_task_history: Optional[List] = None,
    reason: str = "threshold",
) -> Tuple[str, int, Dict[str, Any]]:
    """LLM 会话摘要；失败时返回降级标记（非冒充 LLM 正文）。"""
    pointers = _collect_task_pointers(messages, main_task_history)
    llm = _invoke_session_summary_llm(
        prior_summary=prior_summary,
        messages_to_fold=messages,
        task_pointers=pointers,
        reason=reason,
    )
    if llm.get("ok") and llm.get("content"):
        text = str(llm["content"])
        return text, max(1, len(text) // 2), {
            "llm_powered": True,
            "summary_model": llm.get("model"),
            "summary_source": "llm",
        }
    # 降级：仅结构占位，并显式标注非 LLM
    fallback = (
        f"## 会话摘要（LLM 不可用，结构占位）\n"
        f"- 时间: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- 原因: {(llm.get('error') or 'unknown')[:200]}\n"
        f"- 已折叠消息数: {len(messages)}\n"
        f"- 主任务指针: {', '.join(pointers[:5]) or '无'}\n"
        f"- 说明: 详细执行链请读 Redis 主任务缓存；请检查网关后点刷新或继续对话以重试 LLM 摘要。\n"
    )
    if prior_summary.strip():
        fallback = prior_summary.strip() + "\n\n" + fallback
    return fallback, max(1, len(fallback) // 2), {
        "llm_powered": False,
        "summary_source": "degraded",
        "summary_error": llm.get("error"),
    }


def _task_active(cur_task: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(cur_task, dict):
        return False
    if cur_task.get("async_pipeline_pending"):
        return True
    pids = cur_task.get("pipeline_task_ids")
    if isinstance(pids, list) and any(str(x or "").strip() for x in pids):
        return True
    st = str(cur_task.get("status") or "").strip().lower()
    if not st:
        return bool(cur_task.get("task_id"))
    # resolved/closed 仍可能续接，但不算 active 执行中；续接由 resolve_intent_mode 的 continuation 分支处理
    return st not in PARENT_TERMINAL


def _looks_like_new_task(message: str) -> bool:
    m = (message or "").strip()
    return any(h in m for h in _NEW_TASK_HINTS)


def _looks_like_task_recall(message: str) -> bool:
    m = (message or "").strip()
    return any(h in m for h in _TASK_RECALL_HINTS)


def _looks_like_task_status_inquiry(message: str) -> bool:
    m = (message or "").strip()
    return any(h in m for h in _STATUS_INQUIRY_HINTS)


def _looks_like_format_or_render_fix(message: str) -> bool:
    """用户追问排版/换行/Markdown 渲染，非重新抓取内容。"""
    m = (message or "").strip()
    if len(m) < 24:
        return False
    hints = (
        "换行", "格式", "排版", "渲染", "markdown", "md文档", "md 文档",
        "\\n", "失效", "不太对", "显示问题", "代码块",
    )
    return any(h in m.lower() if h.isascii() else h in m for h in hints)


def _has_link_analysis_intent(message: str) -> bool:
    m = (message or "").strip().lower()
    if "xiaohongshu.com" in m or "xhslink.com" in m:
        return True
    if "小红书" in m and any(k in m for k in ("profile", "用户", "主页", "画像", "分析", "链接")):
        return True
    return False


def _is_follow_up_user_query(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) <= 3:
        return True
    if any(h in t for h in _STATUS_INQUIRY_HINTS):
        return True
    if any(h in t for h in _CONTINUE_HINTS) and len(t) < 48:
        return True
    return False


def _origin_user_query_before(messages: List[Dict[str, Any]], assist_idx: int) -> str:
    for j in range(assist_idx - 1, -1, -1):
        m = messages[j]
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        t = str(m.get("content") or "").strip()
        if t and not _is_follow_up_user_query(t):
            return t
    for j in range(assist_idx - 1, -1, -1):
        m = messages[j]
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "").strip()
    return ""


def rebuild_main_task_history_from_messages(
    messages: List[Dict[str, Any]],
    *,
    existing: Optional[List] = None,
) -> List[Dict[str, Any]]:
    """从会话消息 task_id / task_audit 重建主任务历史（每 task_id 一条，追问不单独成主任务）。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for h in existing or []:
        if isinstance(h, dict) and h.get("task_id"):
            by_id[str(h["task_id"])] = dict(h)
    last_user = ""
    for i, m in enumerate(messages or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            last_user = str(m.get("content") or "").strip()
            continue
        if m.get("role") != "assistant":
            continue
        if m.get("ephemeral") or m.get("task_kind") == "simple":
            continue
        audit = m.get("task_audit") if isinstance(m.get("task_audit"), dict) else {}
        tid = str(m.get("task_id") or audit.get("task_id") or "").strip()
        if not tid or not tid.startswith("task_"):
            continue
        origin = _origin_user_query_before(messages, i) or (
            "" if _is_follow_up_user_query(last_user) else last_user
        )
        st = str(audit.get("status") or m.get("result_status") or "executing").lower()
        row: Dict[str, Any] = {
            "task_id": tid,
            "user_query": (origin or "")[:500],
            "query_summary": (origin or tid)[:80],
            "status": st,
            "task_kind": "main",
            "result_msg_index": i,
            "async_pipeline_pending": bool(audit.get("async_pipeline_pending")),
        }
        prev = by_id.get(tid)
        if not prev:
            by_id[tid] = row
            continue
        if origin and not _is_follow_up_user_query(origin) and _is_follow_up_user_query(
            str(prev.get("user_query") or "")
        ):
            prev["user_query"] = row["user_query"]
            prev["query_summary"] = row["query_summary"]
        prev["status"] = row["status"]
        prev["result_msg_index"] = i
    return list(by_id.values())


def resolve_preserved_task_queries(
    *,
    task_id: str = "",
    cur_task: Optional[Dict[str, Any]] = None,
    fallback_message: str = "",
) -> tuple[str, str]:
    """续接主任务时取真相源 user_query / query_summary，禁止用追问句覆盖。"""
    tid = str(task_id or (cur_task or {}).get("task_id") or "").strip()
    uq = str((cur_task or {}).get("user_query") or "").strip()
    qs = str((cur_task or {}).get("query_summary") or "").strip()
    rs = (cur_task or {}).get("rewrite_snapshot")
    if isinstance(rs, dict):
        if not uq:
            uq = str(rs.get("rewritten_query") or rs.get("query") or "").strip()
        if not qs:
            qs = str(rs.get("query_summary") or rs.get("task_summary") or "").strip()
    if tid:
        try:
            from .span_audit import get_task

            mt = get_task(tid) or {}
            if not uq:
                uq = str(mt.get("user_query") or "").strip()
            if not qs:
                qs = str(mt.get("query_summary") or "").strip()
        except Exception:
            pass
    if _is_follow_up_user_query(uq):
        uq = ""
    if _is_follow_up_user_query(qs):
        qs = ""
    fb = str(fallback_message or "").strip()
    if not uq and fb and not _is_follow_up_user_query(fb):
        uq = fb
    if not qs:
        qs = (uq or fb or tid)[:80]
    return uq[:500], qs[:80]


def infer_needs_rag_from_task_text(text: str) -> bool:
    """从主任务原问推断是否需知识库检索。"""
    t = str(text or "").strip()
    if not t:
        return False
    keys = (
        "知识库", "文档", "资料", "检索", "召回", "RAG", "rag",
        "MCP", "mcp", "向量", "milvus", "出处", "来源",
    )
    return any(k in t or k.lower() in t.lower() for k in keys)


def enrich_snapshot_for_continue_main(
    snap: Dict[str, Any],
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    rag_prefetch: bool = False,
) -> Dict[str, Any]:
    """续接主任务：用锁定原问覆盖「继续」类短句，并继承 needs_rag。"""
    out = dict(snap or {})
    uq, qs = resolve_preserved_task_queries(
        task_id=task_id,
        cur_task=cur_task,
        fallback_message="",
    )
    if uq:
        out["rewritten_query"] = uq
        out["query"] = uq
        out["task_summary"] = qs or uq[:120]
        out["query_summary"] = qs or uq[:120]
        if not out.get("query_keywords"):
            kws = []
            for token in re.split(r"[\s，,、；;：:]+", uq):
                token = token.strip()
                if len(token) >= 2 and token not in kws:
                    kws.append(token)
            if kws:
                out["query_keywords"] = kws[:8]
                out["keywords"] = kws[:8]
    out["needs_rag"] = bool(
        out.get("needs_rag") or infer_needs_rag_from_task_text(uq)
    ) and bool(rag_prefetch)
    return out


def upsert_session_main_task_history(
    session_id: str,
    *,
    task_id: str,
    user_query: str = "",
    query_summary: str = "",
    status: str = "executing",
    task_kind: str = "main",
    async_pipeline_pending: bool = False,
    pipeline_task_ids: Optional[List[str]] = None,
    cur_task: Optional[Dict[str, Any]] = None,
) -> None:
    """主任务创建/推进时写入会话 JSON，避免仅依赖前端 PUT。"""
    tid = str(task_id or "").strip()
    if not tid or not session_id:
        return
    doc = get_session_document(session_id) or {}
    hist = list(doc.get("main_task_history") or [])
    entry = {
        "task_id": tid,
        "session_id": session_id,
        "user_query": (user_query or "")[:500],
        "query_summary": (query_summary or user_query or "")[:80],
        "status": status,
        "task_kind": task_kind,
        "async_pipeline_pending": async_pipeline_pending,
    }
    if pipeline_task_ids:
        entry["pipeline_task_ids"] = list(pipeline_task_ids)[:10]
    idx = next((i for i, h in enumerate(hist) if isinstance(h, dict) and h.get("task_id") == tid), -1)
    if idx >= 0:
        prev = hist[idx] if isinstance(hist[idx], dict) else {}
        merged = {**prev, **entry}
        prev_uq = str(prev.get("user_query") or "").strip()
        if prev_uq and not _is_follow_up_user_query(prev_uq):
            merged["user_query"] = prev_uq
            merged["query_summary"] = str(prev.get("query_summary") or prev_uq)[:80]
        elif _is_follow_up_user_query(entry.get("user_query") or "") and prev_uq:
            merged["user_query"] = prev_uq
            merged["query_summary"] = str(prev.get("query_summary") or prev_uq)[:80]
        hist[idx] = merged
    else:
        hist.append(entry)
    ct = cur_task if isinstance(cur_task, dict) else {
        "task_id": tid,
        "user_query": entry["user_query"],
        "query_summary": entry["query_summary"],
        "status": status,
        "task_kind": task_kind,
        "async_pipeline_pending": async_pipeline_pending,
        "pipeline_task_ids": pipeline_task_ids or [],
    }
    persist_session(
        session_id,
        doc.get("session") or {"id": session_id, "title": "对话"},
        doc.get("messages") or [],
        cur_task=ct,
        main_task_history=hist,
        prefs=doc.get("prefs"),
        memory_meta=doc.get("memory_meta"),
        mark_dirty=True,
    )


def extract_task_id_from_message(message: str) -> str:
    """从用户句中提取显式 task_id（如 task_554272e197cc）。"""
    m = _TASK_ID_IN_MESSAGE_RE.search((message or "").strip())
    return m.group(1).lower() if m else ""


def _looks_like_task_resume(message: str) -> bool:
    m = (message or "").strip()
    if not m:
        return False
    if extract_task_id_from_message(m):
        return True
    return any(h in m for h in _TASK_RESUME_HINTS)


def _find_main_task_row(
    task_id: str,
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    main_task_history: Optional[List] = None,
) -> Optional[Dict[str, Any]]:
    tid = str(task_id or "").strip()
    if not tid:
        return None
    if isinstance(cur_task, dict) and str(cur_task.get("task_id") or "") == tid:
        return dict(cur_task)
    for h in reversed(main_task_history or []):
        if isinstance(h, dict) and str(h.get("task_id") or "") == tid:
            return dict(h)
    return {"task_id": tid, "task_kind": "main"}


def _get_recent_main_task(
    cur_task: Optional[Dict[str, Any]],
    main_task_history: Optional[List] = None,
) -> Optional[Dict[str, Any]]:
    if isinstance(cur_task, dict):
        tid = str(cur_task.get("task_id") or "").strip()
        if tid and str(cur_task.get("task_kind") or "main") != "simple":
            return dict(cur_task)
    for h in reversed(main_task_history or []):
        if not isinstance(h, dict):
            continue
        tid = str(h.get("task_id") or "").strip()
        if tid and str(h.get("task_kind") or "main") != "simple":
            return dict(h)
    return None


def _message_belongs_to_task_row(message: str, task_row: Dict[str, Any]) -> bool:
    """判断当前句是否指向给定主任务（含已结案）。"""
    msg = (message or "").strip()
    if not msg or not isinstance(task_row, dict):
        return False
    try:
        from .ai_chat import _is_simple_intent

        if _is_simple_intent(msg):
            return False
    except Exception:
        pass
    tid = str(task_row.get("task_id") or "").strip()
    if tid and tid in msg:
        return True
    if _looks_like_task_resume(msg):
        return True
    if _looks_like_task_status_inquiry(msg) or _looks_like_task_recall(msg):
        return True
    if _looks_like_continuation(msg, task_row):
        return True
    anchor = str(task_row.get("user_query") or task_row.get("query_summary") or "").strip()
    if anchor and len(anchor) >= 4:
        tokens = [w for w in re.split(r"[\s，,、；;：:]+", anchor) if len(w) >= 2][:8]
        hits = sum(1 for w in tokens if w in msg)
        if hits >= 2 or (len(tokens) == 1 and tokens[0] in msg):
            return True
    return False


def hydrate_client_task_context(
    session_id: str,
    *,
    client_cur_task: Optional[Dict[str, Any]] = None,
    client_main_task_history: Optional[List] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    合并前端指针与会话持久化：避免简单直答后 cur_task 被清空导致「继续」误判 simple。
    """
    cur = dict(client_cur_task) if isinstance(client_cur_task, dict) else None
    hist: List[Dict[str, Any]] = [
        dict(h) for h in (client_main_task_history or []) if isinstance(h, dict) and h.get("task_id")
    ]
    sid = str(session_id or "").strip()
    doc: Dict[str, Any] = {}
    if sid:
        try:
            doc = get_session_document(sid) or {}
        except Exception:
            doc = {}
    if not cur and isinstance(doc.get("cur_task"), dict):
        cur = dict(doc["cur_task"])
    if not hist:
        raw_hist = doc.get("main_task_history")
        if isinstance(raw_hist, list) and raw_hist:
            hist = [dict(h) for h in raw_hist if isinstance(h, dict) and h.get("task_id")]
    if not hist:
        hist = rebuild_main_task_history_from_messages(doc.get("messages") or [], existing=hist)
    if not cur and hist:
        for h in reversed(hist):
            if str(h.get("task_kind") or "main") != "simple":
                cur = dict(h)
                break
    return cur, hist


def resolve_task_affiliation(
    message: str,
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    main_task_history: Optional[List] = None,
) -> Optional[Dict[str, Any]]:
    """
    任务归属（优先于 simple/复杂分流）：
    1) 消息显式 task_id；2) 当前主任务指针；3) 最近一轮主任务（含已结案）。
    命中则 continue_main，否则返回 None。
    """
    msg = (message or "").strip()
    hist = main_task_history if isinstance(main_task_history, list) else []

    explicit_tid = extract_task_id_from_message(msg)
    if explicit_tid:
        row = _find_main_task_row(explicit_tid, cur_task=cur_task, main_task_history=hist)
        return {
            "mode": "continue_main",
            "task_id": explicit_tid,
            "skip_orchestration": True,
            "reason": "用户消息显式指定主任务，续接该 task_id",
            "cur_task": row,
            "affiliation": "explicit_task_id",
        }

    if _looks_like_new_task(msg):
        return None

    recent = _get_recent_main_task(cur_task, hist)
    if not recent:
        return None

    if _is_unrelated_new_work(msg, recent):
        return None

    tid = str(recent.get("task_id") or "").strip()
    if not tid:
        return None

    belongs = _message_belongs_to_task_row(msg, recent)
    if not belongs and isinstance(cur_task, dict) and str(cur_task.get("task_id") or "") == tid:
        try:
            from .ai_chat import _is_simple_intent

            # 元问答/寒暄（你是谁、能做什么）不得因「有未结案主任务」强行续接
            if _is_simple_intent(msg):
                return None
        except Exception:
            pass
        if _looks_like_new_task(msg):
            return None
        # 仅显式续接/进度/恢复类短句才默认续接，禁止吞掉新意图
        if (
            _looks_like_task_resume(msg)
            or _looks_like_task_status_inquiry(msg)
            or _looks_like_task_recall(msg)
            or _has_explicit_continue_hint(msg)
            or _looks_like_continuation(msg, cur_task if isinstance(cur_task, dict) else recent)
        ):
            belongs = True
        if not belongs and _task_active(cur_task):
            progress_ask = (
                "当前任务", "当前的任务", "任务是啥", "执行到哪", "进行到哪",
                "做到哪", "忘了", "忘记了", "啥来着", "任务状态",
            )
            if any(h in msg for h in progress_ask):
                belongs = True

    if not belongs:
        return None

    ct = dict(recent)
    if isinstance(cur_task, dict) and str(cur_task.get("task_id") or "") == tid:
        ct = {**recent, **cur_task}
    return {
        "mode": "continue_main",
        "task_id": tid,
        "skip_orchestration": True,
        "reason": "判定属于当前/最近主任务，续接执行",
        "cur_task": ct,
        "affiliation": "current_or_recent_main",
    }


def _looks_like_continuation(message: str, cur_task: Optional[Dict[str, Any]]) -> bool:
    m = (message or "").strip()
    if not m:
        return False
    try:
        from .ai_chat import _is_simple_intent

        # 元问答/寒暄（你是谁、能做什么）不得因句短而判为续接
        if _is_simple_intent(m):
            return False
    except Exception:
        pass
    if _looks_like_new_task(m):
        return False
    if _has_explicit_continue_hint(m):
        return True
    if cur_task and len(m) <= 48 and not any(k in m for k in ("帮我", "请帮", "分析", "查询", "搜索")):
        qsum = str(cur_task.get("query_summary") or cur_task.get("user_query") or "")
        if qsum and any(w in qsum for w in m.split() if len(w) >= 2):
            return True
    # 禁止「短句且无问号」泛化续接：易把「你是谁」类新意图误判为延续主任务
    return False


def _resolve_continue_task_id(
    cur_task: Optional[Dict[str, Any]],
    main_task_history: Optional[List] = None,
) -> str:
    if isinstance(cur_task, dict):
        tid = str(cur_task.get("task_id") or "").strip()
        if tid:
            return tid
    hist = main_task_history if isinstance(main_task_history, list) else []
    for h in reversed(hist):
        if not isinstance(h, dict):
            continue
        tid = str(h.get("task_id") or "").strip()
        if tid and str(h.get("task_kind") or "main") != "simple":
            return tid
    return ""


async def llm_resolve_intent_mode(
    message: str,
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    main_task_history: Optional[List] = None,
    provider: str = "",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    tools_meta: Optional[Dict[str, Any]] = None,
    rag_prefetch: bool = False,
    web_search: bool = False,
    on_token: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """编排段意图识别：必须走 LLM；失败时返回 None 由规则兜底。"""
    if not (api_key and model):
        return None
    from .ai_chat import _iter_react_llm_tokens

    ctx_lines = [
        "判定顺序：① 是否属于当前/最近主任务（含已结案，含消息内 task_ 与任务恢复说明）→ continue_main；"
        "② 是否显式新课题 → new_main；③ 最后才 simple。"
    ]
    if isinstance(cur_task, dict) and cur_task.get("task_id"):
        ctx_lines.append(
            f"当前主任务 task_id={cur_task.get('task_id')}; "
            f"status={cur_task.get('status')}; "
            f"摘要={(cur_task.get('query_summary') or cur_task.get('user_query') or '')[:120]}"
        )
    recent = _get_recent_main_task(cur_task, main_task_history)
    if recent and (not isinstance(cur_task, dict) or not cur_task.get("task_id")):
        ctx_lines.append(
            f"最近主任务 task_id={recent.get('task_id')}; "
            f"status={recent.get('status')}; "
            f"摘要={(recent.get('query_summary') or recent.get('user_query') or '')[:120]}"
        )
    elif main_task_history:
        last = main_task_history[-1] if isinstance(main_task_history[-1], dict) else {}
        if last.get("task_id") and not recent:
            ctx_lines.append(
                f"最近主任务 task_id={last.get('task_id')}; "
                f"status={last.get('status')}; "
                f"摘要={(last.get('query_summary') or last.get('user_query') or '')[:120]}"
            )
    user_block = (message or "").strip()
    switch_line = f"用户开关：rag_prefetch={bool(rag_prefetch)}; web_search={bool(web_search)}"
    if ctx_lines:
        user_block = (
            "【主任务上下文】\n"
            + "\n".join(ctx_lines)
            + "\n\n"
            + switch_line
            + "\n\n【用户当前句】\n"
            + user_block
        )
    else:
        user_block = switch_line + "\n\n" + user_block
    text = ""
    t0 = time.perf_counter()
    try:
        async for piece in _iter_react_llm_tokens(
            phase="intent",
            user_message=user_block,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            tools_meta=tools_meta or {},
            react_memory=[],
            intent_snapshot=None,
            max_tokens=480,
            include_tools_catalog=False,
        ):
            text += piece
            if on_token and piece:
                try:
                    cb = on_token(piece)
                    if hasattr(cb, "__await__"):
                        await cb
                except Exception:
                    pass
    except Exception as ex:
        _log.warning(
            "[上下文记忆|chat_context_memory.llm_resolve_intent_mode|intent|Agent执行|失败] "
            "error_type=%s; error_message=%s; orch_llm_ms=%s",
            type(ex).__name__,
            ex,
            int((time.perf_counter() - t0) * 1000),
        )
        return None
    _log.info(
        "[上下文记忆|chat_context_memory.llm_resolve_intent_mode|intent|Agent执行|完成] "
        "orch_llm_ms=%s; text_len=%s",
        int((time.perf_counter() - t0) * 1000),
        len(text),
    )
    raw = (text or "").strip()
    if not raw:
        return None
    # 抽取 JSON
    j: Dict[str, Any] = {}
    try:
        j = json.loads(raw)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                j = json.loads(m.group(0))
            except Exception:
                j = {}
    mode = str(j.get("mode") or "").strip().lower()
    if mode not in ("simple", "continue_main", "new_main"):
        return None
    tid = str(j.get("task_id") or "").strip()
    if mode == "continue_main" and not tid:
        tid = _resolve_continue_task_id(cur_task, main_task_history)
    qk = j.get("query_keywords")
    if not isinstance(qk, list):
        qk = []
    qk = [str(x).strip() for x in qk if str(x).strip()]
    skip_nodes = j.get("skip_nodes")
    if not isinstance(skip_nodes, list):
        skip_nodes = []
    skip_nodes = [str(x).strip() for x in skip_nodes if str(x).strip()]
    llm_needs_rag = j.get("needs_rag")
    llm_needs_web = j.get("needs_web_search")
    return {
        "mode": mode,
        "task_id": tid,
        "skip_orchestration": mode in ("simple", "continue_main"),
        "reason": str(j.get("reason") or "LLM 意图识别"),
        "llm_powered": True,
        "confidence": float(j.get("confidence") or 0.0),
        "llm_raw": raw,
        "task_summary": str(j.get("task_summary") or "").strip(),
        "query_keywords": qk,
        "needs_rag": bool(llm_needs_rag) if llm_needs_rag is not None else None,
        "needs_web_search": bool(llm_needs_web) if llm_needs_web is not None else False,
        "skip_nodes": skip_nodes,
    }


def resolve_intent_mode(
    message: str,
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    is_simple_heuristic: bool,
    main_task_history: Optional[List] = None,
) -> Dict[str, Any]:
    """
    意图分流（含主任务续接）：
    - 先 resolve_task_affiliation（当前/最近主任务，含已结案）
    - 再 simple / continue_main / new_main
    """
    from .ai_chat import _is_simple_intent

    msg = (message or "").strip()
    hist = main_task_history if isinstance(main_task_history, list) else []

    affiliated = resolve_task_affiliation(msg, cur_task=cur_task, main_task_history=hist)
    if affiliated:
        return affiliated

    # 元问答/寒暄：有未结案主任务也须先走 simple 分流（每轮 QUERY 先意图识别）
    try:
        from .ai_chat import _is_simple_intent

        if is_simple_heuristic and _is_simple_intent(msg):
            return {
                "mode": "simple",
                "task_id": "",
                "skip_orchestration": True,
                "reason": "元问答/寒暄，不续接未结案主任务",
            }
    except Exception:
        pass

    # 追问进度/结果优先于「含链接关键词」误判为新任务（如「链接分析好了吗」）
    if _looks_like_task_status_inquiry(msg) or _looks_like_task_recall(msg):
        tid = _resolve_continue_task_id(cur_task, hist)
        if tid:
            ct = dict(cur_task) if isinstance(cur_task, dict) else {"task_id": tid}
            ct.setdefault("task_id", tid)
            return {
                "mode": "continue_main",
                "task_id": tid,
                "skip_orchestration": True,
                "reason": "用户追问主任务进度/结果",
                "cur_task": ct,
            }

    if _has_link_analysis_intent(msg):
        return {
            "mode": "new_main",
            "task_id": "",
            "skip_orchestration": False,
            "reason": "含链接/小红书画像分析诉求，走主任务+工具链",
        }

    # 追问类短句优先续接主任务（即使上一轮回被误标 resolved）
    if _looks_like_continuation(msg, cur_task):
        tid = _resolve_continue_task_id(cur_task, hist)
        if tid:
            ct = dict(cur_task) if isinstance(cur_task, dict) else {"task_id": tid}
            ct.setdefault("task_id", tid)
            return {
                "mode": "continue_main",
                "task_id": tid,
                "skip_orchestration": True,
                "reason": "追问/续接上一主任务",
                "cur_task": ct,
            }

    active = _task_active(cur_task)
    tid = (cur_task or {}).get("task_id") or ""

    if _looks_like_new_task(msg):
        if is_simple_heuristic and not active:
            return {"mode": "simple", "task_id": "", "skip_orchestration": True, "reason": "显式新话题且为简单句"}
        return {"mode": "new_main", "task_id": "", "skip_orchestration": False, "reason": "用户要求开启新任务"}

    if active and tid:
        # 元问答/寒暄：未结案主任务也不得吞掉 simple 直答
        if is_simple_heuristic and _is_simple_intent(msg) and not _looks_like_continuation(msg, cur_task):
            return {
                "mode": "simple",
                "task_id": "",
                "skip_orchestration": True,
                "reason": "元问答/寒暄，不续接未结案主任务",
            }
        # 主任务未结案时：仅明确续接/同主题追问才延续，禁止吞掉明显新课题
        if not _is_unrelated_new_work(msg, cur_task) and (
            _looks_like_continuation(msg, cur_task) or not _looks_like_new_task(msg)
        ):
            return {
                "mode": "continue_main",
                "task_id": tid,
                "skip_orchestration": True,
                "reason": "当前主任务未结案，判定为延续执行",
                "cur_task": cur_task,
            }

    if is_simple_heuristic:
        # 元问答/寒暄：即使有未结案主任务也走 simple，不续接 MCP 主任务
        if _is_simple_intent(msg) and not _looks_like_continuation(msg, cur_task):
            return {
                "mode": "simple",
                "task_id": "",
                "skip_orchestration": True,
                "reason": "元问答/寒暄，不续接未结案主任务",
            }
        recent = _get_recent_main_task(cur_task, hist)
        if recent and not _looks_like_new_task(msg) and not _is_unrelated_new_work(msg, recent):
            tid = str(recent.get("task_id") or "").strip() or _resolve_continue_task_id(cur_task, hist)
            if tid:
                ct = dict(cur_task) if isinstance(cur_task, dict) else dict(recent)
                ct.setdefault("task_id", tid)
                return {
                    "mode": "continue_main",
                    "task_id": tid,
                    "skip_orchestration": True,
                    "reason": "存在最近主任务且非显式新话题，禁止落回 simple",
                    "cur_task": ct,
                }
        return {"mode": "simple", "task_id": "", "skip_orchestration": True, "reason": "简单问答，不建主任务"}

    if active and tid and not _looks_like_new_task(msg) and not _is_unrelated_new_work(msg, cur_task):
        return {
            "mode": "continue_main",
            "task_id": tid,
            "skip_orchestration": True,
            "reason": "存在进行中主任务，默认延续",
            "cur_task": cur_task,
        }

    return {"mode": "new_main", "task_id": "", "skip_orchestration": False, "reason": "复杂新任务"}


def pipeline_ids_from_tool_outputs(tool_outputs: Any) -> List[str]:
    """从 SPAN 工具轨迹提取后台 link_pipeline 任务 ID。"""
    ids: List[str] = []
    if not isinstance(tool_outputs, list):
        return ids
    seen: set = set()
    for rec in tool_outputs:
        if not isinstance(rec, dict) or str(rec.get("tool_name") or "") != "link_pipeline_start":
            continue
        tr = rec.get("tool_result")
        if isinstance(tr, str):
            try:
                tr = json.loads(tr)
            except Exception:
                tr = {}
        if not isinstance(tr, dict):
            continue
        if tr.get("ok") is not True and not tr.get("async"):
            continue
        tid = str(tr.get("task_id") or "").strip()
        if tid and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return ids


def pipeline_ids_from_answer_text(text: str) -> List[str]:
    """从回答正文提取可验证的后台流水线 task_id（须 task_manager 可查）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        from .task_manager import get_task
    except Exception:
        return []
    seen: set = set()
    ids: List[str] = []
    patterns = (
        r"(?:任务\s*ID|Task\s*ID|task_id|流水线)[：:\s=]+([a-zA-Z0-9_-]{8,32})",
        r"\b([a-f0-9]{12,24})\b",
    )
    for pat in patterns:
        for m in re.finditer(pat, raw, flags=re.I):
            tid = str(m.group(1) or "").strip()
            if not tid or tid in seen:
                continue
            row = get_task(tid) or {}
            if row and str(row.get("link") or row.get("url") or row.get("platform") or ""):
                seen.add(tid)
                ids.append(tid)
    return ids


_PIPELINE_ACTIVE_STATUSES = frozenset({
    "pending", "started", "running", "downloading", "transcribing", "generating",
    "extracting", "ocr", "comments", "assembling", "consolidating", "feishu_upload",
    "generating_html", "in_progress", "async_pending",
})
_PIPELINE_DONE_STATUSES = frozenset({"completed", "ok", "done", "success"})


def known_pipeline_ids_for_main_task(main_task_id: str) -> List[str]:
    """主任务 SPAN 轨迹中已提交过的 link_pipeline task_id（去重保序）。"""
    if not main_task_id:
        return []
    repo = load_task_repo(main_task_id)
    ids = list(pipeline_ids_from_tool_outputs(repo.get("tool_outputs")))
    fixed = repo.get("snapshot_fixed") if isinstance(repo.get("snapshot_fixed"), dict) else {}
    for x in fixed.get("pipeline_task_ids") or []:
        s = str(x or "").strip()
        if s and s not in ids:
            ids.append(s)
    return ids


def _cache_rows_for_pipeline(pipeline_id: str, link: str = "") -> List[Dict[str, Any]]:
    """追问进度时附带 cache_query 结果，避免模型误判「无缓存」后重提交流水线。"""
    try:
        from .cache import cache_query
    except Exception:
        return []
    keys = [pipeline_id, pipeline_id[:12], (link or "")[:80]]
    seen: set = set()
    rows: List[Dict[str, Any]] = []
    for kw in keys:
        kw = (kw or "").strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        try:
            out = cache_query(keyword=kw, limit=30)
            for r in out.get("rows") or []:
                if isinstance(r, dict):
                    rows.append(r)
        except Exception:
            pass
    return rows[:15]


def _pipeline_row_brief(pid: str) -> Dict[str, Any]:
    try:
        from .task_manager import get_task
    except Exception:
        return {"task_id": pid, "status": "unknown"}
    row = get_task(pid) or {}
    return {
        "task_id": pid,
        "status": str(row.get("status") or "unknown"),
        "progress": row.get("progress"),
        "stage": row.get("stage"),
        "doc_filename": row.get("doc_filename"),
        "html_status": row.get("html_status"),
        "link": row.get("link") or row.get("url"),
        "error": row.get("error"),
    }


def guard_link_pipeline_start(
    *,
    main_task_id: str,
    user_message: str = "",
    tool_args: Optional[Dict[str, Any]] = None,
    continue_main: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    禁止续接/追问时对同一分析重复 link_pipeline_start。
    返回 dict 时调用方应直接作为 tool_result，不再真正提交新流水线。
    """
    known = known_pipeline_ids_for_main_task(main_task_id)
    if not known:
        return None

    msg = (user_message or "").strip()
    args = dict(tool_args or {})
    link = str(args.get("link") or "").strip()
    inquiry = (
        continue_main
        or _looks_like_task_status_inquiry(msg)
        or _looks_like_task_recall(msg)
        or _looks_like_continuation(msg, {"task_id": main_task_id})
    )
    explicit_new = _looks_like_new_task(msg) and not inquiry

    try:
        from .task_manager import link_url_hash
    except Exception:
        link_url_hash = None  # type: ignore

    target_pid = known[-1]
    if link and link_url_hash:
        uh = link_url_hash(link)
        for pid in reversed(known):
            row = _pipeline_row_brief(pid)
            plink = str(row.get("link") or "")
            if plink and link_url_hash(plink) == uh:
                target_pid = pid
                break

    st = str(_pipeline_row_brief(target_pid).get("status") or "").lower()
    active = st in _PIPELINE_ACTIVE_STATUSES

    # 追问/续接：一律不新建，只回既有流水线 + 缓存
    if inquiry and not explicit_new:
        cache_rows = _cache_rows_for_pipeline(target_pid, link)
        return {
            "ok": True,
            "blocked_duplicate": True,
            "async": active,
            "reused": True,
            "task_id": target_pid,
            "existing_pipeline_task_ids": known,
            "pipeline": _pipeline_row_brief(target_pid),
            "cache_rows": cache_rows,
            "cache_count": len(cache_rows),
            "hint": (
                "已拦截重复提交：用户为续接/追问，须基于既有流水线 "
                + target_pid
                + " 与 cache_query 结果作答，禁止向用户声称「重新提交分析」或给出新的任务 ID。"
                + (" 流水线仍在执行，请等待或说明进度。" if active else " 可查 cache_rows 或说明已完成产出。")
            ),
        }

    # 同链接且已有流水线（含进行中或已完成）：复用，不 create_task
    if link and link_url_hash:
        uh = link_url_hash(link)
        for pid in reversed(known):
            row = _pipeline_row_brief(pid)
            plink = str(row.get("link") or "")
            if plink and link_url_hash(plink) == uh:
                cache_rows = _cache_rows_for_pipeline(pid, link)
                return {
                    "ok": True,
                    "blocked_duplicate": True,
                    "async": str(row.get("status") or "").lower() in _PIPELINE_ACTIVE_STATUSES,
                    "reused": True,
                    "task_id": pid,
                    "existing_pipeline_task_ids": known,
                    "pipeline": row,
                    "cache_rows": cache_rows,
                    "cache_count": len(cache_rows),
                    "hint": "同链接已有流水线任务，已复用 task_id="
                    + pid
                    + "，未新建分析任务。",
                }
    return None


def guard_tool_call(
    *,
    main_task_id: str,
    tool_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
    user_message: str = "",
    continue_main: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    主任务 ReAct 段拦截不当工具调用。
    返回 dict 时调用方应直接作为 tool_result，不再执行真实工具。
    """
    fn = (tool_name or "").strip()
    if not fn or not main_task_id:
        return None

    msg = (user_message or "").strip()
    args = dict(tool_args or {})
    known = known_pipeline_ids_for_main_task(main_task_id)
    pipeline_done = False
    if known:
        st = str(_pipeline_row_brief(known[-1]).get("status") or "").lower()
        pipeline_done = st == "completed"

    format_fix = _looks_like_format_or_render_fix(msg)
    progress_q = (
        _looks_like_task_status_inquiry(msg)
        or _looks_like_task_recall(msg)
        or _looks_like_continuation(msg, {"task_id": main_task_id})
    )

    if continue_main and format_fix and pipeline_done:
        if fn == "rag_search":
            return {
                "ok": False,
                "skipped": True,
                "reason": (
                    "用户为格式/排版修正追问，流水线 MD 已产出；"
                    "禁止 rag_search，请直接基于 doc_filename 或已有正文改写。"
                ),
            }
        if fn == "web_search":
            return {
                "ok": False,
                "skipped": True,
                "reason": "格式修正追问禁止联网搜索；请基于既有流水线产物作答。",
            }
        if fn == "cache_query":
            kw = str(args.get("keyword") or "").strip()
            if kw == main_task_id or (kw and kw not in known):
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": (
                        "格式修正追问无需 cache_query(主任务 ID)；"
                        "若需查产物请用 pipeline task_id 或 document_analyze。"
                    ),
                }

    if fn == "cache_query" and continue_main and not progress_q:
        kw = str(args.get("keyword") or "").strip()
        if kw == main_task_id and pipeline_done:
            pid_hint = known[-1] if known else ""
            return {
                "ok": False,
                "skipped": True,
                "reason": (
                    "cache_query 的 keyword 应为流水线 task_id"
                    + (f"（如 {pid_hint}）" if pid_hint else "")
                    + "，非主任务 task_id；非进度追问时不应盲查缓存。"
                ),
            }

    if fn == "rag_search" and continue_main and format_fix:
        return {
            "ok": False,
            "skipped": True,
            "reason": "排版/换行类追问不需要知识库检索，请直接改写用户给出的正文。",
        }

    return None


def has_active_pipeline_tasks(task_id: str) -> bool:
    """主任务是否仍有未结案的后台 link_pipeline。"""
    if not task_id:
        return False
    try:
        from .task_manager import get_task
    except Exception:
        return False
    for pid in known_pipeline_ids_for_main_task(task_id):
        st = str(_pipeline_row_brief(pid).get("status") or "").lower()
        if st in _PIPELINE_ACTIVE_STATUSES:
            return True
    return False


def answer_implies_async_pipeline_pending(answer: str, user_message: str = "") -> bool:
    """回答声称后台流水线仍在处理，但可能尚未写入 tool_outputs。"""
    a = (answer or "").strip()
    if not a:
        return False
    wait_hints = ("等待", "请稍后", "处理中", "1-3分钟", "1～3分钟", "后台", "流水线", "自动提取", "尚未完成")
    if not any(h in a for h in wait_hints):
        return False
    ctx = a + " " + (user_message or "")
    domain_hints = ("链接", "小红书", "文档化", "主页", "link_pipeline", "笔记", "账号")
    return any(h in ctx for h in domain_hints)


def resolve_task_group_seq(task_id: str) -> int:
    """主任务已展示的最大步骤组序号（续接时承接，避免从 #1 重计）。"""
    if not task_id:
        return 0
    try:
        from .span_audit import get_task

        task = get_task(task_id) or {}
    except Exception:
        return 0
    seq = 0
    snap = task.get("snapshot_json") if isinstance(task.get("snapshot_json"), dict) else {}
    fixed = snap.get("fixed") if isinstance(snap.get("fixed"), dict) else {}
    seq = max(seq, int(fixed.get("group_seq") or 0))
    for sp in task.get("sub_plans") or []:
        if isinstance(sp, dict):
            seq = max(seq, int(sp.get("sub_index") or 0))
    return seq


def touch_task_group_seq(task_id: str, group_seq: int) -> None:
    """步骤组递增后写回 fixed.group_seq。"""
    if not task_id or group_seq <= 0:
        return
    try:
        from .span_audit import patch_task_snapshot

        patch_task_snapshot(task_id, fixed={"group_seq": int(group_seq)})
    except Exception as ex:
        _log.warning(
            "[上下文记忆|chat_context_memory.touch_task_group_seq|task:%s|硬编执行|写回] "
            "失败; group_seq=%s; error_type=%s",
            task_id,
            group_seq,
            type(ex).__name__,
        )


def load_task_repo(task_id: str) -> Dict[str, Any]:
    """任务双快照 REPO（真相源：fixed/open + tool_outputs + steps）。"""
    if not task_id:
        return {}
    try:
        from .span_audit import get_task

        task = get_task(task_id) or {}
    except Exception as ex:
        _log.warning(
            "[上下文记忆|chat_context_memory.load_task_repo|task:%s|硬编执行|加载] "
            "失败; error_type=%s; error_message=%s",
            task_id,
            type(ex).__name__,
            ex,
        )
        return {}
    steps = task.get("steps") or []
    sub_plans = task.get("sub_plans") or []
    snap = task.get("snapshot_json") if isinstance(task.get("snapshot_json"), dict) else {}
    tool_outputs = task.get("tool_outputs") or snap.get("tool_outputs") or []
    if not isinstance(tool_outputs, list):
        tool_outputs = []
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "user_query": task.get("user_query"),
        "rewritten_query": task.get("rewritten_query"),
        "query_summary": task.get("query_summary"),
        "steps_count": len(steps),
        "sub_plans_count": len(sub_plans),
        "group_seq": resolve_task_group_seq(task_id),
        "snapshot_fixed": (snap.get("fixed") if isinstance(snap.get("fixed"), dict) else {}) or {},
        "snapshot_open": (snap.get("open") if isinstance(snap, dict) else {}) or {},
        "tool_outputs": tool_outputs,
        "recent_steps": [
            {
                "step_name": s.get("step_name"),
                "phase": s.get("phase"),
                "status": s.get("status"),
                "result_brief": (s.get("result_brief") or "")[:120],
            }
            for s in steps[-8:]
            if isinstance(s, dict)
        ],
    }


def load_task_redis_context(task_id: str) -> Dict[str, Any]:
    """兼容别名：等同 load_task_repo。"""
    return load_task_repo(task_id)


def format_task_tool_outputs_block(tool_outputs: Any, *, max_items: int = 8) -> str:
    """工具轨迹精简块（供 LLM Read REPO，非 UI 原始 JSON）。"""
    if not isinstance(tool_outputs, list) or not tool_outputs:
        return ""
    try:
        from .tool_output_schema import brief_from_tool_payload
    except Exception:
        brief_from_tool_payload = None  # type: ignore

    lines = ["[主任务工具轨迹 — 真相源]"]
    for rec in tool_outputs[-max_items:]:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("tool_name") or "tool")
        status = str(rec.get("status") or "")
        brief = ""
        if brief_from_tool_payload:
            try:
                brief = brief_from_tool_payload(
                    {
                        "tool_name": name,
                        "tool_result": rec.get("tool_result"),
                        "status": status,
                    },
                    max_len=100,
                )
            except Exception:
                brief = ""
        if not brief:
            tr = rec.get("tool_result")
            brief = str(tr)[:100] if tr is not None else ""
        lines.append(f"- {name} ({status}): {brief}")
    return "\n".join(lines)


REPO_CTX_TAG = "[任务 REPO 上下文 — 自动刷新]"


def hydrate_react_memory_from_repo(
    task_id: str,
    react_memory: List[Dict[str, str]],
    *,
    max_tools: int = 12,
) -> List[Dict[str, str]]:
    """续接/新轮执行前：把双快照 tool_outputs 灌入 ReAct 推理链，接上上一轮工具结果。"""
    if not task_id:
        return list(react_memory or [])
    repo = load_task_repo(task_id)
    tool_outputs = repo.get("tool_outputs") or []
    if not isinstance(tool_outputs, list):
        return list(react_memory or [])
    memory = list(react_memory or [])
    seen = {str(m.get("phase") or "") for m in memory}
    try:
        from .tool_output_schema import brief_from_tool_payload
    except Exception:
        brief_from_tool_payload = None  # type: ignore

    for i, rec in enumerate(tool_outputs[-max_tools:]):
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("tool_name") or "tool")
        phase = f"repo_{name}_{i}"
        if phase in seen:
            continue
        brief = ""
        if brief_from_tool_payload:
            try:
                brief = brief_from_tool_payload(
                    {
                        "tool_name": name,
                        "tool_result": rec.get("tool_result"),
                        "status": rec.get("status"),
                    },
                    max_len=200,
                )
            except Exception:
                brief = ""
        if not brief:
            brief = str(rec.get("status") or "已完成")
        memory.append({"phase": phase, "text": f"【上轮工具·{name}】{brief}"})
        seen.add(phase)
    return memory


def build_working_repo_system_block(task_id: str) -> str:
    """ReAct 每轮 LLM 调用前注入的任务真相源块。"""
    if not task_id:
        return ""
    repo = load_task_repo(task_id)
    if not repo:
        return ""
    parts: List[str] = []
    ctx = format_task_context_block(repo)
    if ctx:
        parts.append(ctx)
    tool_block = format_task_tool_outputs_block(repo.get("tool_outputs"), max_items=10)
    if tool_block:
        parts.append(tool_block)
    pids = known_pipeline_ids_for_main_task(task_id)
    if pids:
        lines = ["【已有链接文档化流水线 — 禁止重复 link_pipeline_start】"]
        for pid in pids[:5]:
            row = _pipeline_row_brief(pid)
            lines.append(
                f"- pipeline_task_id={pid}; status={row.get('status')}; "
                f"stage={row.get('stage') or '-'}; doc={row.get('doc_filename') or '-'}"
            )
        lines.append(
            "用户追问进度/缓存/「好了吗」时：必须先 cache_query(keyword=上述 task_id 或账号关键词)，"
            "或等待 Observation；严禁再次提交分析、严禁编造新的 task_id。"
        )
        active = [
            f"{pid}({str(_pipeline_row_brief(pid).get('status') or '')})"
            for pid in pids[:5]
            if str(_pipeline_row_brief(pid).get("status") or "").lower() in _PIPELINE_ACTIVE_STATUSES
        ]
        if active:
            lines.append("【执行中】" + "、".join(active))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def sync_working_repo_context(working: List[Dict[str, Any]], task_id: str) -> None:
    """在 working messages 末尾刷新 REPO 系统块（每轮 ReAct 前调用）。"""
    block = build_working_repo_system_block(task_id)
    if not block:
        return
    content = f"{REPO_CTX_TAG}\n{block}"
    for i in range(len(working) - 1, -1, -1):
        msg = working[i]
        if msg.get("role") == "system" and REPO_CTX_TAG in str(msg.get("content") or ""):
            working[i] = {"role": "system", "content": content}
            return
    working.append({"role": "system", "content": content})


def append_tool_observation_memory(
    react_memory: List[Dict[str, str]],
    *,
    tool_name: str,
    tool_payload: Dict[str, Any],
    react_round: int = 0,
) -> None:
    """工具完成后写入 ReAct 推理链，供后续轮次与编排 LLM 读取。"""
    try:
        from .tool_output_schema import brief_from_tool_payload

        brief = brief_from_tool_payload(tool_payload, max_len=240)
    except Exception:
        brief = str(tool_payload.get("tool_name") or tool_name)
    phase = f"obs_r{react_round}_{tool_name}" if react_round else f"obs_{tool_name}"
    react_memory.append(
        {
            "phase": phase,
            "text": f"【Observation·{tool_name}】{brief}",
        }
    )


def build_primary_task_anchor_block(task_ctx: Dict[str, Any], *, current_user_message: str = "") -> str:
    """LLM 一级上下文：双快照任务真相（优先于会话摘要与旧 messages）。"""
    if not task_ctx or not task_ctx.get("task_id"):
        return ""
    tid = str(task_ctx.get("task_id") or "")
    uq = str(task_ctx.get("user_query") or "")[:300]
    qs = str(task_ctx.get("query_summary") or uq)[:120]
    lines = [
        "[主任务真相 — 一级上下文 · 禁止串任务]",
        f"- 当前 task_id: {tid}",
        f"- 任务摘要（创建时锁定，勿被追问句或联网结果覆盖）: {qs}",
        f"- 用户原始目标: {uq}",
    ]
    if current_user_message.strip():
        lines.append(f"- 本轮用户句（二级，仅作进度/补充）: {current_user_message.strip()[:200]}")
    lines.append(
        "- 规则：作答须围绕上述 task_id 与原始目标；其它会话轮次、工具返回的平台介绍文案"
        "不得当作本任务目标；续接时禁止声称「重新提交」或替换 task_id。"
    )
    return "\n".join(lines)


def format_task_context_block(task_ctx: Dict[str, Any]) -> str:
    if not task_ctx or not task_ctx.get("task_id"):
        return ""
    lines = [
        "[主任务 Redis 执行链 — 标准上下文]",
        f"- task_id: {task_ctx.get('task_id')}",
        f"- 状态: {task_ctx.get('status')}",
        f"- 任务摘要: {(task_ctx.get('query_summary') or task_ctx.get('user_query') or '')[:120]}",
        f"- 用户目标: {(task_ctx.get('user_query') or '')[:200]}",
        f"- 改写目标: {(task_ctx.get('rewritten_query') or '')[:200]}",
    ]
    open_snap = task_ctx.get("snapshot_open") or {}
    if open_snap.get("objective"):
        lines.append(f"- 当前目标: {str(open_snap.get('objective'))[:200]}")
    if open_snap.get("current_assessment"):
        lines.append(f"- 当前评估: {str(open_snap.get('current_assessment'))[:200]}")
    tool_block = format_task_tool_outputs_block(task_ctx.get("tool_outputs"))
    if tool_block:
        lines.append(tool_block)
    recent = task_ctx.get("recent_steps") or []
    if recent:
        lines.append("- 最近步骤:")
        for s in recent:
            lines.append(
                f"  · [{s.get('phase') or '-'}] {s.get('step_name') or '步骤'} "
                f"({s.get('status')}) {s.get('result_brief') or ''}"
            )
    lines.append("（详细 IO 以 Redis/MariaDB 主任务快照为准；对话消息仅作情境辅助）")
    return "\n".join(lines)


def compress_session_fifo(
    session_id: str,
    doc: Dict[str, Any],
    *,
    reason: str = "threshold",
    keep_recent: int = KEEP_RECENT_MESSAGES,
) -> Dict[str, Any]:
    """超过阈值：旧消息 FIFO 折叠，由 LLM 递归更新【会话摘要】，保留最近消息全量。"""
    messages = list(doc.get("messages") or [])
    if len(messages) < MIN_MESSAGES_TO_COMPRESS:
        return doc
    keep_from = max(0, len(messages) - keep_recent)
    old = messages[:keep_from]
    recent = messages[keep_from:]
    if not old:
        return doc

    memory_meta = dict(doc.get("memory_meta") or {})
    prior = str(memory_meta.get("summary_text") or "")
    main_hist = doc.get("main_task_history") if isinstance(doc.get("main_task_history"), list) else []

    summary_text, summary_tok, sum_meta = _build_session_summary_llm(
        old,
        prior_summary=prior,
        main_task_history=main_hist,
        reason=reason,
    )

    layers = list(memory_meta.get("summary_layers") or [])
    layers.append(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "kind": "llm_session_summary",
            "messages_folded": len(old),
            "summary_preview": summary_text[:280],
            "llm_powered": bool(sum_meta.get("llm_powered")),
            "summary_model": sum_meta.get("summary_model"),
        }
    )
    memory_meta.update(
        {
            "mode": MEMORY_MODE_SUMMARY,
            "summary_text": summary_text,
            "summary_tokens_est": summary_tok,
            "summary_layers": layers[-8:],
            "summary_source": sum_meta.get("summary_source"),
            "summary_llm_powered": bool(sum_meta.get("llm_powered")),
            "summary_model": sum_meta.get("summary_model"),
            "summary_error": sum_meta.get("summary_error"),
            "fifo_kept_messages": len(recent),
            "last_compress_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    doc = dict(doc)
    doc["messages"] = [slim_message_for_storage(m) for m in recent if isinstance(m, dict)]
    doc["memory_meta"] = memory_meta
    sm = dict(doc.get("session") or {})
    sm["context_tokens_est"] = estimate_messages_tokens(recent) + summary_tok
    doc["session"] = sm
    sid = (sm.get("id") or session_id or "").strip()
    if sid:
        persist_session(
            sid,
            sm,
            recent,
            cur_task=doc.get("cur_task"),
            main_task_history=doc.get("main_task_history"),
            prefs=doc.get("prefs"),
            memory_meta=memory_meta,
            mark_dirty=True,
        )
    _log.info(
        "[上下文记忆|chat_context_memory.compress_session_fifo|session:%s|Agent执行|LLM会话摘要] "
        "folded=%s; kept=%s; llm_powered=%s; ok=true",
        session_id,
        len(old),
        len(recent),
        memory_meta.get("summary_llm_powered"),
    )
    return doc


def archive_session_full(session_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """100% 强制切换：LLM 补充会话摘要 + 存档（主任务链已持久化）。"""
    doc = compress_session_fifo(session_id, doc, reason="context_full", keep_recent=4)
    memory_meta = dict(doc.get("memory_meta") or {})
    memory_meta["archived_at"] = datetime.now().isoformat(timespec="seconds")
    memory_meta["archive_reason"] = "context_full"
    doc["memory_meta"] = memory_meta
    sm = dict(doc.get("session") or {})
    sm["status"] = "archived"
    doc["session"] = sm
    sid = (sm.get("id") or session_id or "").strip()
    if sid:
        persist_session(
            sid,
            sm,
            doc.get("messages") or [],
            cur_task=doc.get("cur_task"),
            main_task_history=doc.get("main_task_history"),
            prefs=doc.get("prefs"),
            memory_meta=doc.get("memory_meta"),
            mark_dirty=True,
        )
    return doc


async def prepare_session_memory(
    session_id: str,
    *,
    client_cur_task: Optional[Dict[str, Any]] = None,
    client_history: Optional[List] = None,
    extra_tokens: int = 0,
) -> Dict[str, Any]:
    """发送前：评估占用，必要时 LLM 会话摘要 / 强制存档。"""
    _t0 = time.perf_counter()
    doc = await load_session_document_async(session_id)
    if not doc:
        doc = {}
    if client_cur_task and isinstance(client_cur_task, dict):
        doc["cur_task"] = client_cur_task
    if isinstance(client_history, list) and client_history:
        doc["main_task_history"] = client_history
    msgs = doc.get("messages") or []
    if not doc.get("main_task_history"):
        doc["main_task_history"] = rebuild_main_task_history_from_messages(msgs)
    doc, _norm_changed = normalize_session_document_for_storage(doc)
    prefs = memory_prefs_from_doc(doc)
    usage = context_usage(doc, extra_tokens=extra_tokens, prefs=prefs)
    events: List[Dict[str, Any]] = []
    _pre_bytes = session_doc_byte_size(doc)

    if usage["should_pre_summarize"] and usage["mode"] == MEMORY_MODE_SUMMARY:
        doc = await asyncio.to_thread(
            compress_session_fifo, session_id, doc, reason="threshold"
        )
        usage = context_usage(doc, extra_tokens=extra_tokens, prefs=prefs)
        sm = doc.get("memory_meta") or {}
        llm_ok = bool(sm.get("summary_llm_powered"))
        events.append(
            {
                "type": "context_pre_summary",
                "pct": usage["pct"],
                "mode": usage["mode"],
                "llm_powered": llm_ok,
                "summary_model": sm.get("summary_model"),
                "message": (
                    f"上下文已达 {usage['pct']}%，已用 LLM 生成会话摘要"
                    if llm_ok
                    else f"上下文已达 {usage['pct']}%，会话摘要 LLM 失败（已降级占位）"
                ),
            }
        )

    force_new = False
    if usage["should_force_archive"]:
        doc = await asyncio.to_thread(archive_session_full, session_id, doc)
        usage = context_usage(doc, extra_tokens=0, prefs=prefs)
        force_new = True
        events.append(
            {
                "type": "context_force_switch",
                "pct": usage["pct"],
                "message": "上下文已满，当前会话已存档；请新建会话继续（主任务链已保留）",
            }
        )

    task_id = ""
    if isinstance(doc.get("cur_task"), dict):
        task_id = str(doc["cur_task"].get("task_id") or "")
    task_ctx: Dict[str, Any] = {}
    if task_id:
        cached_repo = doc.get("task_redis") if isinstance(doc.get("task_redis"), dict) else None
        if not cached_repo and isinstance(doc.get("task_repo"), dict):
            cached_repo = doc.get("task_repo")
        ct_status = str((doc.get("cur_task") or {}).get("status") or "").lower()
        if cached_repo and ct_status in ("executing", "planning", "running"):
            task_ctx = dict(cached_repo)
        else:
            task_ctx = await asyncio.to_thread(load_task_repo, task_id)
  # 若 SPAN 工具轨迹含未结案的后台 link_pipeline，标记给执行段/终态决策
    pipeline_ids: List[str] = []
    if task_id:
        try:
            from .span_audit import get_task

            span_task = get_task(task_id) or {}
            pipeline_ids = pipeline_ids_from_tool_outputs(
                span_task.get("tool_outputs")
                or (span_task.get("snapshot_json") or {}).get("tool_outputs")
            )
        except Exception:
            pipeline_ids = []
    if pipeline_ids:
        task_ctx["async_pipeline_pending"] = True
        task_ctx["pipeline_task_ids"] = pipeline_ids
    task_block = format_task_context_block(task_ctx)
    if pipeline_ids:
        task_block = (
            task_block
            + "\n- 后台流水线: 执行中（link_pipeline_start 已提交，勿将主任务标为已结案）"
            + "\n- pipeline_task_ids: "
            + ", ".join(pipeline_ids[:5])
        )
    memory_meta = dict(doc.get("memory_meta") or {})
    memory_meta["mode"] = usage["mode"]
    memory_meta["last_pct"] = usage["pct"]

    _post_bytes = session_doc_byte_size(
        {
            "messages": doc.get("messages"),
            "cur_task": doc.get("cur_task"),
            "main_task_history": doc.get("main_task_history"),
            "memory_meta": memory_meta,
        }
    )
    if _norm_changed or _post_bytes < _pre_bytes or _pre_bytes > SESSION_DOC_SOFT_BYTES:
        doc["memory_meta"] = memory_meta
        await asyncio.to_thread(persist_normalized_session_document, session_id, doc)

    _ms = int((time.perf_counter() - _t0) * 1000)
    _log.info(
        "[AI问答-会话记忆|chat_context_memory.prepare_session_memory|session:%s|硬编执行|完成] "
        "cost_ms=%s; pct=%s; compress_events=%s; task_id=%s",
        session_id,
        _ms,
        usage.get("pct"),
        len(events),
        (task_id or "")[:16],
    )
    return {
        "usage": usage,
        "memory_meta": memory_meta,
        "memory_mode": usage["mode"],
        "task_context_block": task_block,
        "task_redis": task_ctx,
        "task_repo": task_ctx,
        "task_group_seq": int(task_ctx.get("group_seq") or 0),
        "cur_task": doc.get("cur_task"),
        "main_task_history": doc.get("main_task_history") or [],
        "summary_text": memory_meta.get("summary_text") or "",
        "events": events,
        "force_new_session": force_new,
    }


def peek_continue_main_intent(
    message: str,
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    main_task_history: Optional[List] = None,
) -> bool:
    """发送前预判是否延续主任务（用于步骤组承接等）。"""
    try:
        from .ai_chat import _is_simple_intent

        simple_heur = _is_simple_intent(message)
    except Exception:
        simple_heur = len((message or "").strip()) <= 48
    decision = resolve_intent_mode(
        message,
        cur_task=cur_task,
        is_simple_heuristic=simple_heur,
        main_task_history=main_task_history,
    )
    return str(decision.get("mode") or "") == "continue_main"


def peek_fast_continue_eligible(
    message: str,
    *,
    cur_task: Optional[Dict[str, Any]] = None,
    main_task_history: Optional[List] = None,
) -> bool:
    """
    是否可走「延续主任务快径」：仅显式续接/恢复/进度追问，禁止元问答与新话题误续接。
    其余 QUERY 必须走意图识别节点（含 LLM）。
    """
    msg = (message or "").strip()
    if not msg:
        return False
    try:
        from .ai_chat import _is_simple_intent

        if _is_simple_intent(msg):
            return False
    except Exception:
        pass
    if _looks_like_new_task(msg):
        return False
    if _is_unrelated_new_work(msg, cur_task or _get_recent_main_task(cur_task, main_task_history)):
        return False
    if extract_task_id_from_message(msg):
        return True
    if _looks_like_task_resume(msg) or _looks_like_task_status_inquiry(msg) or _looks_like_task_recall(msg):
        return True
    if _has_explicit_continue_hint(msg):
        return True
    if _looks_like_continuation(msg, cur_task):
        return True
    return False


async def prepare_llm_context(
    session_id: str,
    user_message: str,
    *,
    task_id: str = "",
    system_prompt: str = "",
    memory_prepared: Optional[Dict[str, Any]] = None,
    extra_system_blocks: Optional[List[str]] = None,
    max_recent_turns: int = 12,
) -> Dict[str, Any]:
    """
    调 LLM 前统一 Read：Session 近轮/摘要 + 任务 REPO + 可选 system 块。
    冲突优先级：双快照 tool_outputs > 会话摘要 > 旧 messages。
    """
    mem = dict(memory_prepared or {})
    if not mem:
        mem = await prepare_session_memory(session_id, extra_tokens=max(32, len(user_message) // 2))
    tid = str(task_id or "").strip()
    if not tid and isinstance(mem.get("cur_task"), dict):
        tid = str(mem["cur_task"].get("task_id") or "")
    repo = load_task_repo(tid) if tid else {}
    task_block = format_task_context_block(repo) if repo else str(mem.get("task_context_block") or "")
    anchor_block = build_primary_task_anchor_block(repo, current_user_message=user_message) if repo else ""
    if repo.get("async_pipeline_pending"):
        pids = repo.get("pipeline_task_ids") or []
        task_block = (
            task_block
            + "\n- 后台流水线: 执行中（link_pipeline_start 已提交，勿将主任务标为已结案）"
            + "\n- pipeline_task_ids: "
            + ", ".join(str(x) for x in pids[:5])
        )
    mem = {**mem, "task_context_block": task_block, "task_repo": repo, "task_redis": repo}
    merged_extra: List[str] = []
    if anchor_block:
        merged_extra.append(anchor_block)
    for block in extra_system_blocks or []:
        txt = str(block or "").strip()
        if txt and txt not in merged_extra:
            merged_extra.append(txt)
    messages = build_agent_llm_messages(
        session_id=session_id,
        user_message=user_message,
        system_prompt=system_prompt,
        memory_prepared=mem,
        extra_system_blocks=merged_extra,
        max_recent_turns=max_recent_turns,
    )
    return {
        "messages": messages,
        "memory_prepared": mem,
        "task_repo": repo,
        "task_context_block": task_block,
        "group_seq": int(repo.get("group_seq") or mem.get("task_group_seq") or 0),
    }


def build_agent_llm_messages(
    *,
    session_id: str,
    user_message: str,
    system_prompt: str,
    memory_prepared: Optional[Dict[str, Any]] = None,
    extra_system_blocks: Optional[List[str]] = None,
    max_recent_turns: int = 12,
) -> List[Dict[str, Any]]:
    """
    Agent 响应模式（非裸 chat）：系统提示 + 会话摘要/主任务 Redis 链 + 近期对话 + 当前用户句。
    禁止把 UI thinking 链、工具原始 JSON 塞进 messages。
    """
    from .ai_chat import get_session_messages

    mem = memory_prepared or {}
    messages: List[Dict[str, Any]] = [{"role": "system", "content": (system_prompt or "").strip()}]

    appendix = build_context_system_appendix(
        memory_mode=str(mem.get("memory_mode") or MEMORY_MODE_SHORT),
        summary_text=str(mem.get("summary_text") or ""),
        task_context_block=str(mem.get("task_context_block") or ""),
    )
    if appendix:
        messages.append(
            {
                "role": "system",
                "content": (
                    "[Agent 上下文缓存 — 标准输入]\n"
                    "以下为会话摘要与主任务执行链指针，须结合当前用户句作答；"
                    "不得声称未调用过的工具已执行完成。\n\n"
                    + appendix
                ),
            }
        )

    hist = get_session_messages(session_id) or []
    recent: List[Dict[str, Any]] = []
    for h in hist:
        if not isinstance(h, dict) or h.get("role") not in ("user", "assistant"):
            continue
        content = str(h.get("content") or "").strip()
        if not content or content.startswith(("正在处理", "正在连接")):
            continue
        if content in ("正在准备…", "正在编排…"):
            continue
        tid = str(h.get("task_id") or "").strip()
        if tid and h.get("role") == "assistant":
            content = f"[主任务 {tid} 当轮回答]\n{content}"[:6000]
        recent.append({"role": h["role"], "content": content[:8000]})
    if len(recent) > max_recent_turns:
        recent = recent[-max_recent_turns:]

    for block in extra_system_blocks or []:
        txt = str(block or "").strip()
        if txt:
            messages.append({"role": "system", "content": txt})

    for m in recent:
        messages.append(m)

    messages.append({"role": "user", "content": (user_message or "").strip()})
    return messages


def build_context_system_appendix(
    *,
    memory_mode: str,
    summary_text: str,
    task_context_block: str,
) -> str:
    parts: List[str] = []
    if memory_mode == MEMORY_MODE_SUMMARY and summary_text:
        parts.append("[会话摘要层 — LLM 压缩的对话脉络（非主任务 IO）]\n" + summary_text.strip())
    if task_context_block:
        parts.append(task_context_block.strip())
    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)


def solidify_task_on_closure(
    user_id: Optional[str],
    *,
    session_id: str,
    task_id: str,
    cur_task: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    主任务结案后：Hermes 式用户画像/规则/情境 Skill 固化。
    """
    uid = (user_id or "unknown").strip() or "unknown"
    task_ctx = load_task_redis_context(task_id)
    ct = cur_task or {}
    objective = (
        task_ctx.get("rewritten_query")
        or task_ctx.get("user_query")
        or ct.get("user_query")
        or ""
    )[:300]
    status = str(task_ctx.get("status") or ct.get("status") or "closed")
    rule_line = f"任务 {task_id} 结案({status})：{objective[:120]}"
    skill_title = f"task-{task_id[-8:]}-experience"
    skill_body = {
        "task_id": task_id,
        "session_id": session_id,
        "objective": objective,
        "status": status,
        "steps_count": task_ctx.get("steps_count", 0),
        "solidified_at": datetime.now().isoformat(timespec="seconds"),
        "hint": "复用此任务的成功路径：先读 Redis 主任务链，再决定是否调用相同工具序列",
    }

    portrait_note = (
        f"\n\n### 任务结案 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"- 任务: `{task_id}` · 状态: {status}\n"
        f"- 目标: {objective[:200]}\n"
        f"- 步骤数: {task_ctx.get('steps_count', 0)}\n"
    )

    out: Dict[str, Any] = {"ok": True, "task_id": task_id, "user_id": uid}
    try:
        from .user_portrait import load_portrait, save_portrait

        portrait = load_portrait(uid)
        notes = str(portrait.get("notes") or "").strip()
        portrait["notes"] = (notes + portrait_note).strip()[:8000]
        save_portrait(uid, portrait)
        out["user_md_updated"] = True
    except Exception as ex:
        out["user_md_updated"] = False
        out["user_md_error"] = str(ex)[:200]

    try:
        from .user_portrait import user_md_path

        base = user_md_path(uid).parent
        base.mkdir(parents=True, exist_ok=True)
        rules_path = base / "task_rules.jsonl"
        with rules_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"rule": rule_line, "task_id": task_id}, ensure_ascii=False) + "\n")
        skills_path = base / "solidified_skills.json"
        skills: List[Any] = []
        if skills_path.is_file():
            skills = json.loads(skills_path.read_text(encoding="utf-8"))
        if not isinstance(skills, list):
            skills = []
        skills.append(skill_body)
        skills_path.write_text(json.dumps(skills[-40:], ensure_ascii=False, indent=2), encoding="utf-8")
        out["skill_solidified"] = skill_title
    except Exception as ex:
        out["skill_solidified"] = False
        out["skill_error"] = str(ex)[:200]

    _log.info(
        "[上下文记忆|chat_context_memory.solidify_task_on_closure|task:%s|硬编执行|结案固化] "
        "ok=true; user_md=%s; skill=%s",
        task_id,
        out.get("user_md_updated"),
        out.get("skill_solidified"),
    )
    return out
