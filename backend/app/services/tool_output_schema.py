"""统一工具/步骤输出 schema — SSE 步骤、SPAN 审计、会话持久化共用。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# 用户可见回答中须剥离的「伪工具调用」正文（模型未走 function calling 时常见）
_TOOL_CALL_MARKDOWN_RE = re.compile(
    r"(?:#{1,3}\s*工具调用请求\s*)?"
    r"```json\s*\{[\s\S]*?\}\s*```",
    re.IGNORECASE,
)
_TOOL_CALL_JSON_RE = re.compile(
    r'\{\s*"name"\s*:\s*"(?:web_search|rag_search|link_pipeline_start|scrape_comments|tools_catalog)[^"]*"'
    r'[\s\S]*?\}',
    re.IGNORECASE,
)
# 火山/豆包等网关未走 OpenAI tool_calls 时，模型在正文里输出的伪调用标记
_VOLC_INLINE_FC_RE = re.compile(
    r"<\|FunctionCallBegin\|>\s*(\[[\s\S]*?\])\s*<\|FunctionCallEnd\|>",
    re.IGNORECASE,
)

SCHEMA_VERSION = 1


def build_flow_step_output(
    *,
    phase: str,
    cost_ms: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """非工具步骤：仅流程状态，不含伪造工具结果。"""
    base: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool_call": False,
        "tool_name": "",
        "tool_args": {},
        "tool_result": None,
        "error": None,
        "cost_ms": int(cost_ms or 0),
        "phase": phase,
    }
    if extra:
        base.update(extra)
    return base


def build_orchestration_step_output(
    *,
    phase: str,
    cost_ms: int = 0,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """LangGraph 编排节点：输出须含本阶段真实字段（非统一空 tool 壳）。"""
    body = dict(payload or {})
    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "node_kind": "orchestration",
        "phase": phase,
        "tool_call": False,
        "cost_ms": int(cost_ms or 0),
    }
    out.update(body)
    if body.get("result_brief_cn") and not out.get("result_msg"):
        out["result_msg"] = str(body["result_brief_cn"])[:8000]
    return out


def extract_tool_result_msg(tool_result: Any, *, error: Optional[str] = None) -> str:
    """从工具原始返回中提取可展示的 result 文案（硬编码字段，禁止伪造）。"""
    if error and str(error).strip():
        return str(error).strip()[:8000]
    if tool_result is None:
        return ""
    if isinstance(tool_result, str):
        return tool_result.strip()[:8000]
    if isinstance(tool_result, dict):
        for key in ("result_msg", "message", "msg", "result_message", "detail", "error"):
            val = tool_result.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()[:8000]
        try:
            return json.dumps(tool_result, ensure_ascii=False, default=str)[:8000]
        except Exception:
            return str(tool_result)[:8000]
    return str(tool_result)[:8000]


def build_tool_step_output(
    *,
    tool_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
    tool_result: Any = None,
    error: Optional[str] = None,
    cost_ms: int = 0,
    phase: str = "tool",
) -> Dict[str, Any]:
    """真实工具调用：必须来自实际 invoke 返回或结构化错误。"""
    err = (error or "").strip() or None
    result_msg = extract_tool_result_msg(tool_result, error=err)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_call": True,
        "tool_name": (tool_name or "").strip(),
        "tool_args": dict(tool_args or {}),
        "tool_result": tool_result,
        "result_msg": result_msg,
        "error": err,
        "cost_ms": int(cost_ms or 0),
        "phase": phase,
    }


def dumps_step_output(payload: Dict[str, Any], max_len: int = 80000) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        text = str(payload)
    return text[:max_len]


def build_llm_step_output(*, answer: str, cost_ms: int = 0) -> Dict[str, Any]:
    """LLM 直答：输出仅含真实生成文本的 result_msg，不伪造工具返回。"""
    ans = (answer or "").strip()
    tr = {"result_msg": ans[:8000]} if ans else None
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_call": False,
        "tool_name": "",
        "tool_args": {},
        "tool_result": tr,
        "result_msg": ans[:8000],
        "error": None,
        "cost_ms": int(cost_ms or 0),
        "phase": "llm",
    }


def brief_from_react_act_text(act_text: str, max_len: int = 160) -> str:
    """从 Act 步 LLM 输出提取节点旁中文摘要。"""
    t = (act_text or "").strip()
    if not t:
        return "完成推理，尚未选定工具"
    for name in (
        "web_search",
        "rag_search",
        "link_pipeline_start",
        "scrape_comments",
        "tools_catalog",
    ):
        if name in t:
            return f"行动：调用 {name}"
    if "无需工具" in t or "不需要工具" in t or "直接回答" in t:
        return "行动：无需工具，进入最终回答"
    line = t.split("\n")[0].strip()
    return (line[:max_len] + "…") if len(line) > max_len else line


def summarize_orchestration_payload_cn(
    phase: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    role: str = "output",
) -> str:
    """将编排节点 JSON 输出转为中文可读摘要（供节点分析框与 IO 面板）。"""
    p = payload if isinstance(payload, dict) else {}
    lines: List[str] = []
    ph = (phase or p.get("phase") or "").strip().lower()

    def _list_items(key: str, label: str, max_n: int = 6) -> None:
        raw = p.get(key)
        if not isinstance(raw, list) or not raw:
            return
        for i, item in enumerate(raw[:max_n], 1):
            lines.append(f"{label}{i}：{str(item)[:120]}")
        if len(raw) > max_n:
            lines.append(f"…共 {len(raw)} 条")

    if ph == "intent":
        mode = str(p.get("mode") or p.get("intent_type") or "").strip()
        if mode:
            mode_cn = {
                "new_main": "新建主任务",
                "continue_main": "延续主任务",
                "simple": "简单问答",
                "task": "主任务",
                "simple_chat": "简单问答",
            }.get(mode, mode)
            lines.append(f"意图模式：{mode_cn}")
        if p.get("task_summary"):
            lines.append(f"任务摘要：{str(p.get('task_summary'))[:200]}")
        _list_items("query_keywords", "原问关键词")
        if p.get("needs_rag") is not None:
            lines.append(f"需要知识库：{'是' if p.get('needs_rag') else '否'}")
        if p.get("needs_web_search") is not None:
            lines.append(f"需要联网：{'是' if p.get('needs_web_search') else '否'}")
        if p.get("reason"):
            lines.append(f"判定原因：{str(p.get('reason'))[:200]}")
        qrd = str(p.get("query_rewrite_decision") or "").strip()
        if qrd:
            qrd_cn = {"skip": "跳过", "apply": "执行"}.get(qrd, qrd)
            lines.append(f"Query 改写：{qrd_cn}")
        if p.get("query_rewrite_skip_reason"):
            lines.append(f"改写说明：{str(p.get('query_rewrite_skip_reason'))[:200]}")
        tdd = str(p.get("task_decompose_decision") or "").strip()
        if tdd:
            tdd_cn = {"skip": "跳过", "apply": "执行"}.get(tdd, tdd)
            lines.append(f"意图分解：{tdd_cn}")
        if p.get("task_decompose_skip_reason"):
            lines.append(f"分解说明：{str(p.get('task_decompose_skip_reason'))[:200]}")
        tc = str(p.get("task_complexity") or "").strip()
        if tc:
            tc_cn = {"normal": "一般任务", "complex": "复杂任务"}.get(tc, tc)
            lines.append(f"任务复杂度：{tc_cn}")
    elif ph == "rewrite":
        if p.get("rewritten_query"):
            lines.append(f"改写后问题：{str(p['rewritten_query'])[:200]}")
        if p.get("query_summary"):
            lines.append(f"摘要：{str(p['query_summary'])[:120]}")
        _list_items("query_keywords", "原问关键词")
        _list_items("retrieval_terms", "内部术语")
    elif ph == "slot":
        if p.get("domain"):
            lines.append(f"业务域：{p.get('domain')}")
        if p.get("operation_type"):
            lines.append(f"操作类型：{p.get('operation_type')}")
        if p.get("needs_rag") is not None:
            lines.append(f"需要知识库：{'是' if p.get('needs_rag') else '否'}")
        _list_items("retrieval_terms", "检索词")
    elif ph == "decompose":
        subs = p.get("sub_tasks") or []
        if isinstance(subs, list):
            for i, st in enumerate(subs[:5], 1):
                if isinstance(st, dict):
                    lines.append(f"子任务{i}：{str(st.get('title') or st.get('name') or st)[:100]}")
                else:
                    lines.append(f"子任务{i}：{str(st)[:100]}")
    elif ph == "enhance":
        _list_items("retrieval_hints", "检索提示")
        _list_items("search_keyword_queries", "知识库检索词")
        _list_items("web_search_queries", "联网检索词")
        _list_items("verification_points", "核验要点")
        if p.get("search_objective"):
            lines.append(f"检索目标：{str(p['search_objective'])[:160]}")
    elif ph == "rag_decision":
        if p.get("needs_rag") is not None:
            lines.append(f"需要 RAG：{'是' if p.get('needs_rag') else '否'}")
        if p.get("prefetch_count") is not None:
            lines.append(f"预取条数：{p.get('prefetch_count')}")
        slices = p.get("rag_slices")
        if isinstance(slices, list) and slices:
            lines.append(f"文献切片：{len(slices)} 条（完整正文见下方切片框）")
        if p.get("prefetch_error"):
            lines.append(f"预取异常：{str(p['prefetch_error'])[:120]}")
    elif role == "input" and ph:
        if p.get("rewritten_query"):
            lines.append(f"输入问题：{str(p['rewritten_query'])[:200]}")
        elif p.get("user_message"):
            lines.append(f"用户原话：{str(p['user_message'])[:200]}")

    if not lines and p:
        for k in ("result_msg", "result_brief_cn", "message"):
            if p.get(k):
                lines.append(str(p[k])[:200])
                break
    return "\n".join(lines)[:1200]


def clamp_result_brief_cn(text: str, max_len: int = 50) -> str:
    """PRD：结果摘要不超过 max_len 个中文字符。"""
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def parse_inline_tool_calls_from_content(content: str) -> List[Dict[str, Any]]:
    """从正文 <|FunctionCallBegin|>…<|FunctionCallEnd|> 解析为 OpenAI 风格 tool_calls。"""
    m = _VOLC_INLINE_FC_RE.search(content or "")
    if not m:
        return []
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(arr):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        params = item.get("parameters")
        if params is None:
            params = item.get("arguments") or {}
        if not isinstance(params, dict):
            params = {}
        out.append(
            {
                "id": f"inline_fc_{i}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(params, ensure_ascii=False),
                },
            }
        )
    return out


def sanitize_user_visible_answer_text(text: str) -> str:
    """剥离模型误输出的工具 JSON / 工具调用请求段落，避免进入回答区。"""
    if not text:
        return ""
    out = _VOLC_INLINE_FC_RE.sub("", text)
    out = _TOOL_CALL_MARKDOWN_RE.sub("", out)
    out = _TOOL_CALL_JSON_RE.sub("", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def format_intent_result_brief_cn(
    *,
    simple: bool,
    framework: str = "",
    keywords: Optional[List[str]] = None,
    needs_rag: bool = False,
    continue_main: bool = False,
    task_id: str = "",
    task_title: str = "",
) -> str:
    """意图识别节点旁展示的中文结果摘要（非 JSON、非任务说明模板）。"""
    if continue_main:
        tid = str(task_id or "").strip()
        title = str(task_title or "").strip()[:12]
        if tid and title:
            return clamp_result_brief_cn(f"延续·{tid}·{title}", 15)
        if tid:
            return clamp_result_brief_cn(f"延续主任务·{tid}", 15)
        return clamp_result_brief_cn("延续主任务", 15)
    if simple:
        return clamp_result_brief_cn("识别为简单问答", 15)
    if needs_rag:
        return clamp_result_brief_cn("复杂任务·需检索", 15)
    return clamp_result_brief_cn("复杂任务·继续分析", 15)


def _looks_like_json_blob(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("{") or t.startswith("["):
        return True
    return '"objective"' in t and '"results"' in t


def _coerce_json_object(val: Any) -> Any:
    """工具 raw 返回常为 JSON 字符串（如 web_search 经 _json_result），统一解析为对象。"""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        t = val.strip()
        if not t or not (t.startswith("{") or t.startswith("[")):
            return val
        try:
            parsed = json.loads(t)
            return parsed if isinstance(parsed, (dict, list)) else val
        except Exception:
            return val
    return val


def _brief_web_search_dict(tr: Dict[str, Any], max_len: int = 120) -> str:
    """web_search 工具结果的中文精简摘要。"""
    results = tr.get("results")
    if not isinstance(results, list):
        results = []
    queries = tr.get("search_queries")
    if not isinstance(queries, list):
        queries = []
    q_one = str(tr.get("query") or (queries[0] if queries else "")).strip()
    if results:
        titles: List[str] = []
        for row in results[:2]:
            if isinstance(row, dict):
                t = str(row.get("title") or row.get("url") or "").strip()
                if t:
                    titles.append(t[:40])
        if queries:
            qline = "、".join(str(q)[:24] for q in queries[:3])
            line = f"关键词 {qline}，共 {len(results)} 条"
        elif q_one:
            line = f"「{q_one[:30]}」共 {len(results)} 条"
        else:
            line = f"共 {len(results)} 条"
        if titles:
            line += "：" + "；".join(titles)
        return clamp_result_brief_cn(line, max_len)
    err = str(tr.get("error") or "").strip()
    if err:
        return clamp_result_brief_cn(err, max_len)
    if q_one:
        return clamp_result_brief_cn(f"「{q_one[:40]}」无检索结果", max_len)
    return clamp_result_brief_cn("无检索结果", max_len)


def _brief_rag_dict(tr: Dict[str, Any], max_len: int = 15) -> str:
    """RAG 检索结果精简摘要：只报命中条数，禁止塞切片正文。"""
    if not isinstance(tr, dict):
        return clamp_result_brief_cn("无检索结果", max_len)
    for key in ("hits", "slices", "rag_slices"):
        val = tr.get(key)
        if isinstance(val, list) and val:
            return clamp_result_brief_cn(f"检索到 {len(val)} 处片段", max_len)
    err = str(tr.get("error") or "").strip()
    if err:
        return clamp_result_brief_cn(err, max_len)
    return clamp_result_brief_cn("无检索结果", max_len)


def _is_rag_tool_name(name: str) -> bool:
    from .tool_invoke_qualifier import is_rag_tool_name

    return is_rag_tool_name(name)


def brief_from_tool_payload(payload: Dict[str, Any], max_len: int = 15) -> str:
    """工具步骤绿框「结果」行：中文精简摘要，禁止整段 JSON。"""
    name = str(payload.get("tool_name") or "tool").strip() or "tool"
    if payload.get("error"):
        return clamp_result_brief_cn(str(payload["error"]), max_len)
    tr_raw = payload.get("tool_result")
    tr = _coerce_json_object(tr_raw)
    if _is_rag_tool_name(name) and isinstance(tr, dict):
        return _brief_rag_dict(tr, max_len)
    if name == "web_search" and isinstance(tr, dict):
        return _brief_web_search_dict(tr, max_len)
    msg = str(payload.get("result_brief_cn") or payload.get("result_msg") or "").strip()
    if msg.startswith(f"{name}:"):
        msg = msg.split(":", 1)[1].strip()
    if msg and not _looks_like_json_blob(msg):
        return clamp_result_brief_cn(msg, max_len)
    if isinstance(tr, dict):
        if tr.get("ok") is False and tr.get("error"):
            return clamp_result_brief_cn(str(tr["error"]), max_len)
        if isinstance(tr.get("results"), list):
            return _brief_web_search_dict(tr, max_len)
        if isinstance(tr.get("hits"), list) or isinstance(tr.get("slices"), list):
            return _brief_rag_dict(tr, max_len)
        for key in ("message", "detail", "summary", "result_msg"):
            val = tr.get(key)
            if val and str(val).strip() and not _looks_like_json_blob(str(val)):
                return clamp_result_brief_cn(str(val), max_len)
    if isinstance(tr, str) and tr.strip() and not _looks_like_json_blob(tr):
        return clamp_result_brief_cn(tr, max_len)
    msg_obj = _coerce_json_object(msg) if _looks_like_json_blob(msg) else None
    if isinstance(msg_obj, dict) and isinstance(msg_obj.get("results"), list):
        return _brief_web_search_dict(msg_obj, max_len)
    return clamp_result_brief_cn("执行完成，详见输入/输出", max_len)


def brief_from_payload(payload: Dict[str, Any], max_len: int = 200) -> str:
    if payload.get("tool_call"):
        return brief_from_tool_payload(payload, max_len=min(max_len, 120))
    phase = payload.get("phase") or "flow"
    if phase == "intent":
        if payload.get("result_brief_cn"):
            return str(payload["result_brief_cn"])[:max_len]
        simple = payload.get("task_kind") == "simple" or payload.get("intent_type") == "simple_chat"
        return format_intent_result_brief_cn(
            simple=simple,
            framework=str(payload.get("framework") or ""),
            keywords=payload.get("keywords") if isinstance(payload.get("keywords"), list) else None,
            needs_rag=bool(payload.get("needs_rag")),
        )
    if phase == "llm":
        msg = str(payload.get("result_msg") or "").strip()
        if msg:
            return f"LLM: {msg[:max_len]}"
        return "LLM 生成回答"
    return f"流程步骤 {phase}"
