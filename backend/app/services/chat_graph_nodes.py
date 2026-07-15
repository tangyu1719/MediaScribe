"""LangGraph 节点：编排段硬编码 + HITL interrupt；执行段 handoff 至 ai_chat。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from . import ai_chat
from .chat_graph_runtime import (
    ChatGraphRuntime,
    get_runtime_from_config,
    restore_runtime_from_state,
)
from .orch_pipeline_config import orch_node_enabled
from .orchestration_step_emit import emit_orchestration_step
from .tool_output_schema import (
    brief_from_payload,
    build_flow_step_output,
    build_llm_step_output,
    clamp_result_brief_cn,
    dumps_step_output,
    format_intent_result_brief_cn,
    summarize_orchestration_payload_cn,
)
from .chat_graph_state import (
    PHASE_ABNORMAL,
    PHASE_FINAL,
    PHASE_INTENT,
    PHASE_PAUSED,
    PHASE_PLAN,
    PHASE_DECOMPOSE,
    PHASE_ENHANCE,
    PHASE_RAG_DECISION,
    PHASE_REACT,
    PHASE_REWRITE_CONFIRM,
    PHASE_REWRITE_PROPOSE,
    PHASE_SIMPLE,
    PHASE_SLOT_CONFIRM,
    PHASE_SLOT_FILL,
)
from .task_states import (
    PARENT_ABNORMAL,
    PARENT_CREATED,
    PARENT_EXECUTING,
    PARENT_PLANNING,
    PARENT_SUMMARIZING,
    SUB_ACTING,
    SUB_DONE,
)

_LOG = logging.getLogger(__name__)


def _runtime_from_state_or_config(state: Dict[str, Any], config: RunnableConfig | None) -> ChatGraphRuntime:
    runtime = state.get("runtime_obj")
    if isinstance(runtime, ChatGraphRuntime):
        return runtime
    runtime_key = str(state.get("runtime_key") or state.get("session_id") or "").strip()
    if runtime_key:
        try:
            from .chat_graph_runner import _RUNTIME_REGISTRY

            runtime = _RUNTIME_REGISTRY.get(runtime_key)
            if isinstance(runtime, ChatGraphRuntime):
                return runtime
        except Exception:
            pass
    restored = restore_runtime_from_state(state)
    if isinstance(restored, ChatGraphRuntime):
        return restored
    if config is not None:
        return get_runtime_from_config(config)
    raise ValueError("缺少 LangGraph configurable.runtime")


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


_TERM_DICTIONARY: Dict[str, str] = {
    "SSD": "固态硬盘",
    "GPU": "图形处理器",
    "CPU": "中央处理器",
    "RAM": "内存",
    "API": "应用程序接口",
    "SDK": "软件开发工具包",
    "内存条": "内存",
    "显卡": "图形处理器",
}

_PRONOUNS = ("这个", "那个", "刚才说的", "之前提到的", "上文说的", "这种情况", "它")


def _extract_history_entity(link_ctx: Dict[str, Any]) -> str:
    history = link_ctx.get("history") or link_ctx.get("messages") or link_ctx.get("recent_messages") or []
    texts: List[str] = []
    if isinstance(history, list):
        for item in history[-5:]:
            if isinstance(item, dict):
                text = str(item.get("content") or item.get("message") or item.get("text") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                texts.append(text)
    elif isinstance(history, str):
        texts.append(history)
    for text in reversed(texts):
        for suffix in ("问题", "错误", "故障", "功能", "模块", "系统", "任务", "接口", "文档"):
            idx = text.find(suffix)
            if idx > 0:
                start = max(0, idx - 16)
                return text[start : idx + len(suffix)].strip(" ，。；;：:")
        if len(text) > 5:
            return text[:30]
    return ""


def _infer_operation_type(text: str) -> str:
    q = text or ""
    if any(x in q for x in ("对比", "比较", "哪个好", "差异", "区别")):
        return "对比"
    if any(x in q for x in ("执行", "创建", "删除", "更新", "修改", "生成", "帮我", "打开", "调用")):
        return "操作"
    if any(x in q for x in ("为什么", "原因", "分析", "推理", "判断", "是否", "怎么处理", "怎么解决")):
        return "推理"
    return "查询"


def _infer_domain_module(text: str, tools_meta: Optional[Dict[str, Any]] = None) -> tuple[str, str]:
    q = text or ""
    if any(x in q for x in ("知识库", "RAG", "召回", "检索", "向量", "Milvus", "Embedding")):
        return "知识库", "检索问答"
    if any(x in q for x in ("文档", "链接", "网页", "页面", "评论", "飞书")):
        return "资料处理", "文档分析"
    if any(x in q for x in ("接口", "API", "SDK", "代码", "报错", "异常", "日志")):
        return "研发运维", "接口/代码问题"
    if any(x in q for x in ("订单", "用户", "退款", "支付", "商品")):
        return "业务系统", "交易/用户问题"
    if any(x in q for x in ("小红书", "小红薯", "xhs", "笔记号")):
        return "社媒分析", "小红书账号/内容"
    return "通用", "未指定"


def _extract_entities(text: str) -> List[str]:
    q = (text or "").strip()
    entities: List[str] = []
    for marker in ("问题", "故障", "错误", "接口", "文档", "系统", "模块", "订单", "用户"):
        idx = q.find(marker)
        if idx > 0:
            start = max(0, idx - 18)
            ent = q[start : idx + len(marker)].strip(" ，。；;：:")
            if ent and ent not in entities:
                entities.append(ent)
    for token in q.replace("，", " ").replace("。", " ").replace("/", " ").split():
        token = token.strip(" ，。；;：:")
        if 2 <= len(token) <= 32 and any(ch.isdigit() for ch in token) and token not in entities:
            entities.append(token)
    return entities[:8]


def _extract_retrieval_terms(text: str, entities: Optional[List[str]] = None) -> List[str]:
    q = text or ""
    terms: List[str] = []
    for ent in entities or []:
        if ent and ent not in terms:
            terms.append(ent)
    normalized = q.replace("，", " ").replace("。", " ").replace("？", " ").replace("?", " ")
    for token in normalized.split():
        token = token.strip(" ，。；;：:")
        if len(token) >= 2 and token not in terms:
            terms.append(token)
    for term in _TERM_DICTIONARY:
        if term in q and term not in terms:
            terms.append(term)
    return terms[:10]


def _align_business_slots(query: str, tools_meta: Optional[Dict[str, Any]] = None, snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metadata = dict((snap or {}).get("metadata") or {})
    text = (query or "").strip()
    domain, module = _infer_domain_module(text, tools_meta)
    operation_type = _infer_operation_type(text)
    entities = _extract_entities(text)
    retrieval_terms = _extract_retrieval_terms(text, entities)
    needs_rag = bool((snap or {}).get("needs_rag")) or any(x in text for x in ("文档", "知识库", "资料", "检索", "RAG", "链接", "网页"))
    slots = {
        "query": text,
        "has_entities": bool(entities),
        "needs_rag": needs_rag,
    }
    return {
        "domain": metadata.get("domain") or domain,
        "module": metadata.get("module") or module,
        "operation_type": metadata.get("operation_type") or operation_type,
        "entities": metadata.get("entities") or entities,
        "slots": metadata.get("slots") or slots,
        "retrieval_terms": metadata.get("retrieval_terms") or retrieval_terms,
        "needs_rag": needs_rag,
        "rewritten_query": text,
    }


def _enhance_intent_by_rules(
    query: str,
    slot: Optional[Dict[str, Any]] = None,
    decomposition: Optional[Dict[str, Any]] = None,
    *,
    web_search: bool = False,
) -> Dict[str, Any]:
    """生成检索提示、假设性答案占位、核验点与风险标记。"""
    text = (query or "").strip()
    slot = slot or {}
    decomposition = decomposition or {}
    terms = [str(x).strip() for x in (slot.get("retrieval_terms") or []) if str(x).strip()]
    if not terms:
        terms = _extract_retrieval_terms(text, slot.get("entities") or [])
    sub_tasks = [task for task in (decomposition.get("sub_tasks") or []) if isinstance(task, dict)]
    task_titles = [str(task.get("title") or "").strip() for task in sub_tasks if str(task.get("title") or "").strip()]
    retrieval_hints = []
    for item in terms + task_titles:
        if item and item not in retrieval_hints:
            retrieval_hints.append(item)
    needs_rag = bool(slot.get("needs_rag"))
    domain = str(slot.get("domain") or "").strip()
    module = str(slot.get("module") or "").strip()
    operation_type = str(slot.get("operation_type") or _infer_operation_type(text))
    hypothetical_answer = ""
    if needs_rag:
        hypothetical_answer = f"假设资料中包含与{module or domain or '当前问题'}相关的原因、证据和处理建议。"
    verification_points = [
        "回答必须基于已检索或已读取资料",
        "无法从资料确认的内容需要明确说明",
    ]
    if operation_type == "对比":
        verification_points.append("对比结论需要分别覆盖每个对象")
    if domain == "知识库":
        verification_points.append("需要检查召回结果是否真正命中用户问题")
    risk_flags = []
    if hypothetical_answer:
        risk_flags.append("hypothesis_not_final_answer")
    if needs_rag:
        risk_flags.append("needs_source_check")
    if not retrieval_hints:
        risk_flags.append("missing_retrieval_terms")

    from .web_search_plan import build_rag_search_keyword_queries, _strip_conversational

    search_keyword_queries = build_rag_search_keyword_queries(
        text,
        original_query=text,
        slot_snapshot=slot,
        enhancement_snapshot={"retrieval_hints": retrieval_hints},
    )
    return {
        "hypothetical_answer": hypothetical_answer,
        "retrieval_hints": retrieval_hints[:12],
        "search_keyword_queries": search_keyword_queries,
        "web_search_queries": [] if not web_search else [],
        "search_objective": _strip_conversational(text)[:160] if text else "",
        "verification_points": verification_points,
        "risk_flags": risk_flags,
    }


def _decompose_task_by_rules(query: str, slot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按业务对齐结果做基础任务分解：对比类并行，多跳/分析类阶段执行。"""
    text = (query or "").strip()
    slot = slot or {}
    operation_type = str(slot.get("operation_type") or _infer_operation_type(text))
    entities = [str(x).strip() for x in (slot.get("entities") or []) if str(x).strip()]
    retrieval_terms = [str(x).strip() for x in (slot.get("retrieval_terms") or []) if str(x).strip()]
    needs_rag = bool(slot.get("needs_rag"))

    if operation_type == "对比":
        decomp_type = "parallel"
        compare_targets = entities[:4] or retrieval_terms[:4]
        if len(compare_targets) < 2:
            compare_targets = ["对象A", "对象B"]
        sub_tasks = [
            {"index": i, "title": f"核验{target}的关键信息", "task_type": "retrieval", "done": False}
            for i, target in enumerate(compare_targets[:4], start=1)
        ]
        sub_tasks.append(
            {
                "index": len(sub_tasks) + 1,
                "title": "汇总差异并给出对比结论",
                "task_type": "synthesis",
                "done": False,
            }
        )
        dependencies = [{"from": i, "to": len(sub_tasks)} for i in range(1, len(sub_tasks))]
    else:
        decomp_type = "stage"
        is_xhs = any(x in text for x in ("小红书", "小红薯", "笔记号"))
        xhs_id = ""
        for token in text.replace("：", ":").replace("，", " ").split():
            t = token.strip(" ，。；;：:")
            if t.isdigit() and 8 <= len(t) <= 15:
                xhs_id = t
                break
        if is_xhs and xhs_id:
            first_title = f"定位小红书账号/笔记（ID {xhs_id}）"
            second_title = "检索知识库与账号相关资料" if needs_rag else "检索公开资料与账号相关信息"
            third_title = "汇总信息并输出结构化分析"
        else:
            first_title = f"明确任务范围：{text[:80]}" if text else "明确任务范围"
            second_title = "检索或读取相关资料" if needs_rag else "整理已有上下文与约束"
            third_title = "核验资料并形成答复要点"
        sub_tasks = [
            {"index": 1, "title": first_title, "task_type": "scope", "done": False},
            {"index": 2, "title": second_title, "task_type": "retrieval" if needs_rag or is_xhs else "context", "done": False},
            {"index": 3, "title": third_title, "task_type": "verification", "done": False},
        ]
        dependencies = [{"from": 1, "to": 2}, {"from": 2, "to": 3}]

    return {
        "decomposition_type": decomp_type,
        "sub_tasks": sub_tasks,
        "dependencies": dependencies,
    }


def _rewrite_query_by_rules(query: str, link_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按改写需求文档实现基础规则层：术语标准化、指代消解、上下文补全。"""
    original = (query or "").strip()
    rewritten = original
    actions: List[str] = []
    link_ctx = link_ctx or {}

    for term in sorted(_TERM_DICTIONARY, key=len, reverse=True):
        full = _TERM_DICTIONARY[term]
        if term in rewritten and f"{term}({full})" not in rewritten and full not in rewritten:
            rewritten = rewritten.replace(term, f"{term}({full})")
            actions.append("term_normalization")

    history_entity = _extract_history_entity(link_ctx)
    if history_entity and any(p in rewritten for p in _PRONOUNS):
        for pronoun in _PRONOUNS:
            rewritten = rewritten.replace(pronoun, history_entity)
        actions.append("coreference_resolution")

    current_product = str(link_ctx.get("product") or link_ctx.get("current_product") or "").strip()
    current_issue = str(link_ctx.get("issue") or link_ctx.get("current_issue") or "").strip()
    if current_product and rewritten.startswith("多少钱"):
        rewritten = f"{current_product}{rewritten}"
        actions.append("context_completion")
    elif current_issue and (rewritten.startswith("怎么解决") or rewritten == "怎么弄"):
        rewritten = f"{current_issue}{rewritten}"
        actions.append("context_completion")

    summary = rewritten[:120] if rewritten else original[:120]
    if not actions:
        actions.append("no_rewrite_needed")
    return {
        "original_query": original,
        "rewritten_query": rewritten,
        "query_summary": summary,
        "rewrite_actions": actions,
    }


def _sse_events_from_runtime(runtime: ChatGraphRuntime) -> Dict[str, Any]:
    return {"sse_events": runtime.drain_sse()}


def _next_step_group(state: Dict[str, Any]) -> tuple[str, int]:
    group_seq = int(state.get("group_seq") or 0) + 1
    return _new_id("subplan_"), group_seq


def _route_after_enhance_chain(
    runtime: ChatGraphRuntime,
    slot: Dict[str, Any],
    framework: str,
) -> str:
    needs_rag = bool(slot.get("needs_rag"))
    if framework == "plan_execute":
        return "plan"
    if needs_rag and _orch_enabled(runtime, "rag_filter_confirm"):
        return "rag_filter_confirm"
    if _orch_enabled(runtime, "rag_decision"):
        return "rag_decision"
    return "rag_decision"


def _route_after_slot(runtime: ChatGraphRuntime, slot: Dict[str, Any], framework: str) -> str:
    if _orch_enabled(runtime, "task_decompose"):
        return "intent_decompose"
    if _orch_enabled(runtime, "intent_enhance"):
        return "intent_enhance"
    return _route_after_enhance_chain(runtime, slot, framework)


def _route_after_decompose(runtime: ChatGraphRuntime, slot: Dict[str, Any], framework: str) -> str:
    if _orch_enabled(runtime, "intent_enhance"):
        return "intent_enhance"
    return _route_after_enhance_chain(runtime, slot, framework)


def _orch_enabled(runtime: ChatGraphRuntime, node_id: str) -> bool:
    return orch_node_enabled(getattr(runtime, "orch_pipeline_nodes", None), node_id)


def _orch_rag_filter_hitl(runtime: ChatGraphRuntime) -> bool:
    """标准 RAG：从 Query 自动推导筛选；复杂问题：需用户 HITL 确认。"""
    if not _orch_enabled(runtime, "rag_filter_confirm"):
        return False
    return _orch_enabled(runtime, "rag_filter_confirm_hitl")


def _skip_orch_step(
    runtime: ChatGraphRuntime,
    state: Dict[str, Any],
    *,
    trace_id: str,
    task_id: str,
    step_name: str,
    phase: str,
    node_id: str,
    graph_route: str,
    passthrough: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """节点被禁用时：标记 skipped、不调用 LLM、原样传递 state。"""
    seq = _emit_orchestration_step(
        runtime,
        state,
        trace_id=trace_id,
        task_id=task_id,
        step_name=step_name,
        phase=phase,
        result_brief=f"已跳过（{step_name} 未启用）",
        input_payload={"node_id": node_id, "enabled": False},
        output_payload={"skipped": True, "node_id": node_id},
        executed=False,
        llm_powered=False,
    )
    out = dict(passthrough or {})
    out.update(
        {
            "graph_route": graph_route,
            "group_seq": seq.get("group_seq"),
            "orch_chain": seq.get("orch_chain", []),
        }
    )
    out.update(_sse_events_from_runtime(runtime))
    return out


def _merge_intent_llm_into_snapshot(
    snap: Dict[str, Any],
    intent_decision: Dict[str, Any],
    *,
    message: str,
    rag_prefetch: bool,
    web_search: bool,
) -> Dict[str, Any]:
    merged = dict(snap or {})
    task_summary = str(intent_decision.get("task_summary") or "").strip()
    qk = intent_decision.get("query_keywords")
    if isinstance(qk, list):
        qk = [str(x).strip() for x in qk if str(x).strip()]
    else:
        qk = []
    if task_summary:
        merged["task_summary"] = task_summary
        merged["query_summary"] = task_summary[:120]
    if qk:
        merged["query_keywords"] = qk
        merged["keywords"] = qk
    llm_rag = intent_decision.get("needs_rag")
    rule_rag = bool(merged.get("needs_rag"))
    # rag_prefetch 仅为用户开关：开启后仍须意图判定 needs_rag，禁止开关即强制检索
    merged["needs_rag"] = bool(rule_rag or llm_rag) and bool(rag_prefetch)
    merged["needs_web_search"] = bool(web_search and intent_decision.get("needs_web_search"))
    if not merged.get("rewritten_query"):
        merged["rewritten_query"] = message
    return merged


def _emit_orchestration_step(
    runtime: ChatGraphRuntime,
    state: Dict[str, Any],
    *,
    trace_id: str,
    task_id: str,
    step_name: str,
    phase: str,
    result_brief: str,
    input_payload: Optional[Dict[str, Any]] = None,
    output_payload: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
    executed: bool = True,
    think_text_override: Optional[str] = None,
    llm_powered: bool = False,
    pre_step_id: Optional[str] = None,
    think_streamed: bool = False,
) -> Dict[str, Any]:
    """编排节点：思考框 + 输入输出 + result_brief（PRD 5.1/5.10）。"""
    out_body = dict(output_payload or extra or {})
    inp = dict(input_payload or {"user_message": (runtime.message or "")[:500]})
    return emit_orchestration_step(
        runtime,
        state,
        trace_id=trace_id,
        task_id=task_id,
        step_name=step_name,
        phase=phase,
        result_brief=result_brief,
        input_payload=inp,
        output_payload=out_body,
        executed=executed,
        think_text_override=think_text_override,
        llm_powered=llm_powered,
        pre_step_id=pre_step_id,
        think_streamed=think_streamed,
    )


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    return {}


def _eligible_kb_orch_fast_lane(
    runtime: ChatGraphRuntime,
    message: str,
    snap: Dict[str, Any],
) -> bool:
    """知识库检索型主任务：合并业务对齐+任务分解为一次 LLM（仍保留独立 SSE 步骤）。"""
    if not runtime.rag_prefetch:
        return False
    if not (
        _orch_enabled(runtime, "slot_fill")
        and _orch_enabled(runtime, "task_decompose")
        and _orch_enabled(runtime, "query_rewrite")
    ):
        return False
    text = str(message or "")
    needs_rag = bool(snap.get("needs_rag")) or any(
        k in text for k in ("知识库", "MCP", "RAG", "检索", "文档", "milvus")
    )
    return needs_rag and _infer_domain_module(text)[0] == "知识库"


def _apply_slot_decompose_bundle(
    rewritten: str,
    snap: Dict[str, Any],
    combined_json: Dict[str, Any],
    *,
    tools_meta: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any], List[Any]]:
    slot = _align_business_slots(str(rewritten or ""), tools_meta, snap)
    for key in (
        "domain", "module", "operation_type", "entities",
        "retrieval_terms", "needs_rag", "state",
    ):
        val = combined_json.get(key)
        if val is not None and val != "":
            slot[key] = val
    qk = snap.get("query_keywords") or snap.get("keywords")
    if isinstance(qk, list) and qk:
        ents = [str(x).strip() for x in (slot.get("entities") or []) if str(x).strip()]
        for x in qk:
            s = str(x).strip()
            if s and s not in ents:
                ents.append(s)
        if ents:
            slot["entities"] = ents
    decomp = _decompose_task_by_rules(rewritten, slot)
    sub_tasks = combined_json.get("sub_tasks")
    if isinstance(sub_tasks, list) and sub_tasks:
        decomp = {
            **decomp,
            "decomposition_type": str(
                combined_json.get("decomposition_type") or decomp.get("decomposition_type") or "stage"
            ),
            "sub_tasks": sub_tasks,
            "dependencies": (
                combined_json.get("dependencies")
                if isinstance(combined_json.get("dependencies"), list)
                else decomp.get("dependencies")
            ),
        }
    return slot, decomp, list(decomp.get("sub_tasks") or [])


async def _orch_llm_invoke(
    runtime: ChatGraphRuntime,
    phase: str,
    user_message: str,
    *,
    intent_snapshot: Optional[Dict[str, Any]] = None,
    max_tokens: int = 520,
    step_id: str = "",
    stream_think: bool = False,
    trace_id: str = "",
    task_id: str = "",
    step_name: str = "",
    sub_plan_id: str = "",
    sub_index: int = 0,
) -> tuple[str, Dict[str, Any]]:
    """编排固定节点 LLM 调用；stream_think 时逐 token 推送 step_think_delta。"""
    if not (runtime.api_key and runtime.model_resolved):
        return "", {}
    text = ""
    t0 = time.perf_counter()
    _orch_no_think = phase in (
        "intent", "rewrite", "slot", "decompose", "enhance", "slot_decompose_bundle",
    )
    sid = (step_id or "").strip()
    if stream_think and sid:
        from .orchestration_step_emit import begin_stream_think

        begin_stream_think(
            runtime,
            trace_id=trace_id or runtime.trace_id,
            task_id=task_id,
            step_id=sid,
            step_name=step_name or phase,
            sub_plan_id=sub_plan_id,
            sub_index=sub_index,
            llm_powered=True,
        )
    try:
        async for piece in ai_chat._iter_react_llm_tokens(
            phase=phase,
            user_message=user_message,
            provider=runtime.provider,
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            model=runtime.model_resolved,
            tools_meta=runtime.tools_meta,
            react_memory=[],
            intent_snapshot=intent_snapshot,
            max_tokens=max_tokens,
            thinking_enabled=not _orch_no_think,
        ):
            text += piece
            if stream_think and sid and piece:
                from .orchestration_step_emit import emit_stream_think_delta

                emit_stream_think_delta(
                    runtime,
                    trace_id=trace_id or runtime.trace_id,
                    task_id=task_id,
                    step_id=sid,
                    content=piece,
                    llm_powered=True,
                )
    except Exception as ex:
        _LOG.warning(
            "[AI问答-LangGraph|chat_graph_nodes._orch_llm_invoke|%s|Agent执行|失败] "
            "error_type=%s; error_message=%s; orch_llm_ms=%s",
            phase,
            type(ex).__name__,
            ex,
            int((time.perf_counter() - t0) * 1000),
        )
        if stream_think and sid:
            from .orchestration_step_emit import end_stream_think

            end_stream_think(
                runtime,
                trace_id=trace_id or runtime.trace_id,
                step_id=sid,
                llm_powered=True,
            )
        return "", {}
    if stream_think and sid:
        from .orchestration_step_emit import end_stream_think

        end_stream_think(
            runtime,
            trace_id=trace_id or runtime.trace_id,
            step_id=sid,
            llm_powered=True,
        )
    raw = text.strip()
    orch_ms = int((time.perf_counter() - t0) * 1000)
    _LOG.info(
        "[AI问答-LangGraph|chat_graph_nodes._orch_llm_invoke|session:%s|Agent执行|完成] "
        "phase=%s; orch_llm_ms=%s; text_len=%s",
        runtime.session_id,
        phase,
        orch_ms,
        len(raw),
    )
    return raw, _parse_llm_json(raw)


def _emit_thought_step(
    runtime: ChatGraphRuntime,
    *,
    trace_id: str,
    task_id: str,
    step_id: str,
    step_name: str,
    phase: str,
    sub_plan_id: str,
    sub_index: int,
    output_text: str,
    use_llm: bool = False,
    node_kind: str = "sub_task",
    cost_ms: int = 0,
    result_brief: str | None = None,
) -> None:
    """与 ai_chat Phase1 对齐的 thought_step SSE（规则/LLM 结果摘要，非任务说明模板）。"""
    runtime.emit(
        "thought_step_start",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "step_id": step_id,
            "step_name": step_name,
            "step_type": "llm_call" if use_llm else "reasoning",
            "status": SUB_ACTING,
            "status_text": "LLM 推理中…" if use_llm else "执行中…",
            "sub_plan_id": sub_plan_id,
            "sub_index": sub_index,
            "node_kind": "llm_call" if use_llm else node_kind,
            "operation": step_name,
            "target": (runtime.message or "")[:40],
        },
    )
    if not result_brief:
        try:
            payload_obj = json.loads(output_text) if isinstance(output_text, str) else output_text
        except Exception:
            payload_obj = {"result_msg": (output_text or "")[:120]}
        result_brief = brief_from_payload(payload_obj if isinstance(payload_obj, dict) else {})
    result_brief = clamp_result_brief_cn(result_brief or "")
    runtime.emit(
        "thought_step_end",
        {
            "trace_id": trace_id,
            "task_id": task_id or "",
            "step_id": step_id,
            "step_name": step_name,
            "status": SUB_DONE,
            "elapsed_ms": cost_ms,
            "status_text": "完成",
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "description": result_brief,
            "sub_plan_id": sub_plan_id,
            "sub_index": sub_index,
            "node_kind": "llm_call" if use_llm else node_kind,
            "result_brief": result_brief,
            "io_links": [],
            "input_text": json.dumps(
                {"query": (runtime.message or "")[:500], "phase": phase},
                ensure_ascii=False,
            ),
            "output_text": output_text,
            "phase": phase,
            "success": True,
            "confidence": 0.92,
            "token_count": max(12, len(output_text) // 4),
            "llm_powered": use_llm,
        },
    )


async def fast_continue_main_to_handoff(
    state: Dict[str, Any],
    *,
    runtime: ChatGraphRuntime,
    message: str,
    session_id: str,
    trace_id: str,
    task_id: str,
    cur_task: Optional[Dict[str, Any]],
    main_hist: Optional[List],
    graph_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    延续主任务快径：规则意图 + 可选 RAG 预取，直接 handoff_execute。
    跳过 LangGraph 多节点（改写/槽位/HITL），避免长时间停在「正在编排任务」。
    """
    from .chat_context_memory import (
        enrich_snapshot_for_continue_main,
        resolve_preserved_task_queries,
        resolve_task_affiliation,
        resolve_intent_mode,
    )

    tid = str(task_id or "").strip()
    task_aff = resolve_task_affiliation(message, cur_task=cur_task, main_task_history=main_hist)
    gs = graph_state if isinstance(graph_state, dict) else state
    from .orchestration_step_emit import _next_step_group

    sub_plan_id, group_seq = _next_step_group(gs)
    gs["group_seq"] = group_seq
    if tid:
        try:
            from .chat_context_memory import touch_task_group_seq

            touch_task_group_seq(tid, group_seq)
        except Exception:
            pass
    intent_decision = dict(task_aff) if task_aff else resolve_intent_mode(
        message, cur_task=cur_task, is_simple_heuristic=False, main_task_history=main_hist
    )
    if str(intent_decision.get("mode") or "") != "continue_main":
        intent_decision = {
            **intent_decision,
            "mode": "continue_main",
            "task_id": tid,
            "reason": intent_decision.get("reason") or "快径：延续当前主任务",
        }

    snap = ai_chat._build_intent_rewrite_snapshot_for_message(message, runtime.link_ctx)
    snap = _merge_intent_llm_into_snapshot(
        snap,
        intent_decision,
        message=message,
        rag_prefetch=bool(runtime.rag_prefetch),
        web_search=bool(runtime.web_search),
    )
    snap = enrich_snapshot_for_continue_main(
        snap,
        cur_task=cur_task,
        task_id=tid,
        rag_prefetch=bool(runtime.rag_prefetch),
    )
    cont_uq, cont_qs = resolve_preserved_task_queries(
        task_id=tid, cur_task=cur_task, fallback_message=""
    )
    needs_rag = bool(snap.get("needs_rag")) and bool(runtime.rag_prefetch)
    intent_result_cn = intent_decision.get("reason") or "延续主任务，跳过重复编排"
    cont_title = str(cont_qs or snap.get("query_summary") or "").strip()
    intent_result_cn = format_intent_result_brief_cn(
        simple=False,
        continue_main=True,
        task_id=tid,
        task_title=cont_title,
    ) or intent_result_cn

    runtime.emit(
        "intent_resolved",
        {
            "task_id": tid,
            "task_kind": "main",
            "is_simple": False,
            "persist_main_task": True,
            "task_action": "continue_main",
            "continue_main_task": True,
            "rewrite_snapshot": snap,
            "user_query": cont_uq or message,
            "query_summary": cont_qs or (message or "")[:120],
            "preserve_task_identity": bool(cont_uq),
            "intent_reason": intent_decision.get("reason") or "",
        },
    )
    runtime.emit(
        "task_created",
        {
            "task_id": tid,
            "session_id": session_id,
            "user_query": cont_uq,
            "status": PARENT_EXECUTING,
            "task_kind": "main",
            "persist_main_task": True,
            "stage": "续接主任务",
            "progress": 12,
            "rewrite_snapshot": snap,
            "query_summary": cont_qs,
            "task_action": "continue",
            "preserve_task_identity": True,
        },
    )
    step_id = _new_id("step_")
    runtime.emit(
        "thought_step_start",
        {
            "trace_id": trace_id,
            "task_id": tid,
            "step_id": step_id,
            "step_name": "意图识别",
            "step_type": "reasoning",
            "status": SUB_ACTING,
            "status_text": "执行中…",
            "sub_plan_id": sub_plan_id,
            "sub_index": group_seq,
            "node_kind": "orchestration",
            "phase": "intent",
            "step_lane": "orchestration",
        },
    )
    runtime.emit(
        "thought_step_end",
        {
            "trace_id": trace_id,
            "task_id": tid,
            "step_id": step_id,
            "step_name": "意图识别",
            "status": SUB_DONE,
            "status_text": "完成",
            "result_brief": intent_result_cn[:120],
            "description": intent_result_cn[:120],
            "sub_plan_id": sub_plan_id,
            "sub_index": group_seq,
            "phase": "intent",
            "node_kind": "orchestration",
            "step_lane": "orchestration",
            "output_text": json.dumps(
                {
                    "mode": "continue_main",
                    "task_action": "continue_main",
                    "task_kind": "main",
                    "is_simple": False,
                    "continue_main_task": True,
                    "intent_type": "task_continue",
                    "result_brief_cn": intent_result_cn,
                    "reason": intent_result_cn,
                    "task_id": tid,
                    "query_summary": cont_qs or cont_title,
                },
                ensure_ascii=False,
            ),
        },
    )
    ai_chat._span_update(tid, status=PARENT_EXECUTING)

    rag_slices: List[Dict[str, Any]] = []
    rag_ctx = ""
    rag_cite = ""
    if needs_rag and tid:
        from .web_search_plan import build_rag_retrieve_query

        rag_query = build_rag_retrieve_query(
            rewritten_query=str(
                snap.get("rewritten_query") or message or ""
            ).strip(),
            original_query=message or "",
            slot_snapshot={},
            enhancement_snapshot={},
        ).strip()
        if rag_query:
            runtime.emit(
                "pipeline_progress",
                {
                    "task_id": tid,
                    "stage": "知识库检索",
                    "progress": 14,
                    "detail": rag_query[:120],
                },
            )
            t0 = time.perf_counter()
            rag_hits, rag_err = await _safe_kb_search(
                rag_query,
                top_k=5,
                span_ctx={"session_id": session_id, "task_id": tid, "trace_id": trace_id},
            )
            from .rag_slice_utils import build_rag_llm_blocks, normalize_rag_slices

            rag_slices = normalize_rag_slices(rag_hits)
            rag_ctx, rag_cite = build_rag_llm_blocks(
                rag_slices,
                prefetch_error=rag_err,
                rag_query=rag_query,
            )
            if rag_slices:
                runtime.emit(
                    "rag_prefetch_slices",
                    {
                        "trace_id": trace_id,
                        "task_id": tid,
                        "rag_query": rag_query[:300],
                        "slice_count": len(rag_slices),
                        "slices": rag_slices,
                        "prefetch_error": rag_err[:300] if rag_err else "",
                    },
                )
            rag_sub_plan_id, rag_group_seq = _next_step_group(gs)
            gs["group_seq"] = rag_group_seq
            if tid:
                try:
                    from .chat_context_memory import touch_task_group_seq

                    touch_task_group_seq(tid, rag_group_seq)
                except Exception:
                    pass
            from .tool_invoke_qualifier import INVOKE_FIXED, attach_invoke_to_payload

            rag_step_id = _new_id("step_")
            _rag_invoke = attach_invoke_to_payload(
                {
                    "trace_id": trace_id,
                    "task_id": tid,
                    "step_id": rag_step_id,
                    "step_name": "知识库检索",
                    "phase": "rag_decision",
                    "node_kind": "tool_call",
                    "step_lane": "prefetch",
                    "sub_plan_id": rag_sub_plan_id,
                    "sub_index": rag_group_seq,
                    "status": "running",
                },
                mode=INVOKE_FIXED,
                tool_name="rag_retrieve",
                action_label="知识库检索",
                purpose="续接预取",
                query=rag_query,
                phase="rag_decision",
            )
            runtime.emit("thought_step_start", _rag_invoke)
            runtime.emit(
                "thought_step_end",
                attach_invoke_to_payload(
                    {
                        "trace_id": trace_id,
                        "task_id": tid,
                        "step_id": rag_step_id,
                        "step_name": "知识库检索",
                        "phase": "rag_decision",
                        "node_kind": "tool_call",
                        "step_lane": "prefetch",
                        "sub_plan_id": rag_sub_plan_id,
                        "sub_index": rag_group_seq,
                        "status": "done",
                        "result_brief": f"命中 {len(rag_slices)} 条切片",
                        "output_text": f"query={rag_query[:80]}; hits={len(rag_slices)}",
                    },
                    mode=INVOKE_FIXED,
                    tool_name="rag_retrieve",
                    action_label="知识库检索",
                    purpose="续接预取",
                    query=rag_query,
                    phase="rag_decision",
                ),
            )
            _LOG.info(
                "[AI问答-LangGraph|chat_graph_nodes.fast_continue_main_to_handoff|session:%s|硬编执行|续接RAG] "
                "hit_count=%s; cost_ms=%s",
                session_id,
                len(rag_slices),
                int((time.perf_counter() - t0) * 1000),
            )

    runtime.emit("thinking_end", {"task_id": tid, "ephemeral": False, "bundle": {"task_kind": "main"}})
    _LOG.info(
        "[AI问答-LangGraph|chat_graph_nodes.fast_continue_main_to_handoff|session:%s|硬编执行|完成] "
        "task_id=%s; needs_rag=%s",
        session_id,
        tid[:16],
        needs_rag,
    )
    out = {
        "orchestration_phase": PHASE_REACT,
        "task_kind": "main",
        "use_main_task": True,
        "framework": "react",
        "intent_passed": True,
        "intent_rewrite_snapshot": snap,
        "needs_rag": needs_rag,
        "query_summary": cont_qs or str(snap.get("query_summary") or (message or "")[:120]),
        "rewritten_query": str(snap.get("rewritten_query") or message or ""),
        "task_id": tid,
        "continue_main_task": True,
        "graph_route": "handoff_execute",
        "execution_done": False,
        "react_round": 0,
        "rag_slices": rag_slices,
        "rag_context_block": rag_ctx,
        "rag_citation_instruction": rag_cite,
        "rag_prefetch_done": bool(needs_rag),
        "intent_result_brief": intent_result_cn,
        "group_seq": int(gs.get("group_seq") or group_seq),
    }
    out.update(_sse_events_from_runtime(runtime))
    return out


async def node_intent_recognition(state: Dict[str, Any], config: RunnableConfig | None = None) -> Dict[str, Any]:
    """意图识别：先任务归属，再 LLM/规则判定 simple / 续接 / 新主任务。"""
    from .chat_context_memory import (
        resolve_intent_mode,
        llm_resolve_intent_mode,
        resolve_task_affiliation,
        hydrate_client_task_context,
    )

    runtime = _runtime_from_state_or_config(state, config)
    message = state.get("message") or runtime.message
    trace_id = state.get("trace_id") or runtime.trace_id
    session_id = state.get("session_id") or runtime.session_id

    # Ollama / 网关预处理：领域意图 + Query 改写（毫秒～秒级，失败降级规则）
    pipeline_hist: List[Dict[str, Any]] = []
    if isinstance(state.get("history"), list):
        pipeline_hist = [h for h in state["history"] if isinstance(h, dict)][-8:]
    from .agent_pipeline import (
        merge_pipeline_into_snapshot,
        run_agent_pipeline,
        run_agent_pipeline_rules_only,
        should_skip_pipeline_llm,
    )

    if should_skip_pipeline_llm(message or ""):
        pipeline_result = await asyncio.to_thread(
            run_agent_pipeline_rules_only, message or "", pipeline_hist
        )
    else:
        pipeline_result = await asyncio.to_thread(run_agent_pipeline, message or "", pipeline_hist)
    runtime.emit(
        "pipeline_intent_preview",
        {
            "intent": pipeline_result.intent,
            "intent_label": pipeline_result.intent_label,
            "rewritten_query": pipeline_result.rewritten_query[:200],
            "pipeline_source": pipeline_result.pipeline_source,
            "llm_powered": pipeline_result.pipeline_source in ("llm", "llm_gateway"),
        },
    )

    runtime.emit(
        "pipeline_progress",
        {
            "task_id": str(state.get("task_id") or "").strip(),
            "stage": "意图识别",
            "progress": 6,
            "detail": "判定 simple / 续接 / 新主任务",
        },
    )
    _intent_tid = str(state.get("task_id") or "").strip()
    if not _intent_tid and isinstance(state.get("client_cur_task"), dict):
        _intent_tid = str(state["client_cur_task"].get("task_id") or "").strip()
    cur_task = state.get("client_cur_task") if isinstance(state.get("client_cur_task"), dict) else None
    main_hist = state.get("client_main_task_history") if isinstance(state.get("client_main_task_history"), list) else []
    cur_task, main_hist = hydrate_client_task_context(
        session_id,
        client_cur_task=cur_task,
        client_main_task_history=main_hist,
    )

    simple_heur = ai_chat._is_simple_intent(message)
    if not cur_task and not simple_heur:
        if any(k in (message or "") for k in ("分析", "排查", "为什么", "原因", "文档", "资料", "知识库", "RAG", "检索", "召回", "链接", "网页")):
            simple_heur = False

    intent_decision: Dict[str, Any] = {}
    intent_llm_powered = False
    intent_pre_step_id = ""
    intent_think_streamed = False
    task_aff = resolve_task_affiliation(message, cur_task=cur_task, main_task_history=main_hist)
    from .chat_context_memory import peek_fast_continue_eligible, build_fast_new_main_intent

    _fast_new = build_fast_new_main_intent(
        message, cur_task=cur_task, main_task_history=main_hist
    )

    async def _resolve_intent_with_stream() -> Dict[str, Any]:
        nonlocal intent_pre_step_id, intent_think_streamed, intent_llm_powered
        pre = _new_id("step_")
        from .orchestration_step_emit import (
            begin_stream_think,
            emit_stream_think_delta,
            end_stream_think,
        )

        begin_stream_think(
            runtime,
            trace_id=trace_id,
            task_id=str(state.get("task_id") or ""),
            step_id=pre,
            step_name="意图识别",
            sub_plan_id="",
            sub_index=0,
            llm_powered=True,
        )

        def _on_tok(piece: str) -> None:
            emit_stream_think_delta(
                runtime,
                trace_id=trace_id,
                task_id=str(state.get("task_id") or ""),
                step_id=pre,
                content=piece,
                llm_powered=True,
            )

        decision = await llm_resolve_intent_mode(
            message,
            cur_task=cur_task,
            main_task_history=main_hist,
            provider=runtime.provider,
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            model=runtime.model_resolved,
            tools_meta=runtime.tools_meta,
            rag_prefetch=bool(runtime.rag_prefetch),
            web_search=bool(runtime.web_search),
            on_token=_on_tok,
        ) or {}
        end_stream_think(
            runtime,
            trace_id=trace_id,
            step_id=pre,
            llm_powered=True,
        )
        intent_pre_step_id = pre
        intent_think_streamed = True
        intent_llm_powered = bool(decision.get("llm_powered"))
        return decision

    _skip_intent_llm = bool(task_aff) or peek_fast_continue_eligible(
        message, cur_task=cur_task, main_task_history=main_hist
    )
    if task_aff:
        intent_decision = dict(task_aff)
    elif _fast_new:
        intent_decision = dict(_fast_new)
        intent_llm_powered = False
    elif _skip_intent_llm:
        intent_decision = resolve_intent_mode(
            message, cur_task=cur_task, is_simple_heuristic=simple_heur, main_task_history=main_hist
        )
    elif _orch_enabled(runtime, "intent_recognition") and not cur_task and not task_aff and not simple_heur:
        _domain, _ = _infer_domain_module(message)
        _kb_hint = _domain == "知识库" and any(
            k in (message or "") for k in ("知识库", "MCP", "RAG", "检索", "文档")
        ) and runtime.rag_prefetch
        _xhs_hint = _domain == "社媒分析"
        if _kb_hint:
            intent_decision = resolve_intent_mode(
                message,
                cur_task=cur_task,
                is_simple_heuristic=False,
                main_task_history=main_hist,
            )
            intent_decision.setdefault("mode", "new_main")
            intent_decision.setdefault("reason", "规则快径：知识库检索型主任务")
            intent_llm_powered = False
        elif _xhs_hint:
            intent_decision = resolve_intent_mode(
                message,
                cur_task=cur_task,
                is_simple_heuristic=False,
                main_task_history=main_hist,
            )
            intent_decision.setdefault("mode", "new_main")
            intent_decision.setdefault("reason", "规则快径：社媒分析型主任务")
            intent_llm_powered = False
        else:
            intent_decision = await _resolve_intent_with_stream()
    elif _orch_enabled(runtime, "intent_recognition"):
        intent_decision = await _resolve_intent_with_stream()
    if not intent_decision:
        intent_decision = resolve_intent_mode(
            message, cur_task=cur_task, is_simple_heuristic=simple_heur, main_task_history=main_hist
        )
    # 任务归属优先于 LLM 误判（含已结案主任务续接）；元问答/simple 不得被覆盖为续接
    task_aff_final = resolve_task_affiliation(message, cur_task=cur_task, main_task_history=main_hist)
    from .chat_context_memory import _is_unrelated_new_work

    if (
        task_aff_final
        and str(intent_decision.get("mode") or "") != "simple"
        and not simple_heur
        and not ai_chat._is_simple_intent(message)
        and not _is_unrelated_new_work(message, cur_task or task_aff_final.get("cur_task"))
        and not _has_link_analysis_intent(message)
    ):
        intent_decision = {**intent_decision, **task_aff_final, "mode": "continue_main"}
    mode = intent_decision.get("mode") or "new_main"
    simple = mode == "simple"
    continue_main = mode == "continue_main"
    # 简单/复杂分流开关关闭时：禁止走 simple 捷径（续接主任务仍允许）
    if not _orch_enabled(runtime, "simple_intent_gate"):
        if simple and not continue_main:
            simple = False
            mode = "new_main"
            intent_decision = {
                **intent_decision,
                "mode": "new_main",
                "reason": (intent_decision.get("reason") or "") + "；已关闭简单/复杂分流",
            }
    # LLM 误判 simple 时：规则强制续接/主任务
    from .chat_context_memory import (
        _has_link_analysis_intent,
        _looks_like_continuation,
        _looks_like_task_recall,
        _looks_like_task_resume,
        _looks_like_task_status_inquiry,
        _resolve_continue_task_id,
        extract_task_id_from_message,
    )

    tid_fix = _resolve_continue_task_id(cur_task, main_hist)
    if _has_link_analysis_intent(message) and not (
        _looks_like_task_resume(message)
        or extract_task_id_from_message(message)
    ):
        simple = False
        continue_main = False
        mode = "new_main"
        intent_decision = {
            **intent_decision,
            "mode": "new_main",
            "task_id": "",
            "reason": "规则覆盖：社媒/链接画像分析为新主任务",
        }
    elif tid_fix and (
        _looks_like_task_status_inquiry(message)
        or _looks_like_task_recall(message)
        or _looks_like_task_resume(message)
        or _looks_like_continuation(message, cur_task)
    ):
        simple = False
        continue_main = True
        mode = "continue_main"
        intent_decision = {
            **intent_decision,
            "mode": "continue_main",
            "task_id": tid_fix,
            "reason": "规则覆盖：用户追问先前主任务",
        }
    if continue_main:
        rid = str(intent_decision.get("task_id") or "").strip()
        if not cur_task and rid:
            cur_task = {"task_id": rid}
            for h in reversed(main_hist):
                if isinstance(h, dict) and str(h.get("task_id") or "") == rid:
                    cur_task = {**h, **cur_task}
                    break
    task_id = (state.get("task_id") or intent_decision.get("task_id") or "").strip() or None
    if continue_main and cur_task:
        task_id = str(cur_task.get("task_id") or task_id or "").strip() or task_id
    use_main = mode in ("new_main", "continue_main")
    task_kind = "simple" if simple else "main"
    framework = "assistant" if simple else "react"
    if isinstance(runtime.agent_profile, dict):
        fw = str(runtime.agent_profile.get("framework") or "").strip().lower()
        if fw in ("react", "plan_execute", "single_shot"):
            framework = "assistant" if fw == "single_shot" else fw

    from .chat_context_memory import _task_active

    if (
        mode == "new_main"
        and not simple
        and _task_active(cur_task)
        and isinstance(cur_task, dict)
        and str(cur_task.get("task_id") or "").strip()
    ):
        cur_tid = str(cur_task.get("task_id") or "").strip()
        cur_sum = str(cur_task.get("query_summary") or cur_task.get("user_query") or "")[:80]
        switch_payload = {
            "kind": "task_switch_confirm",
            "message": (
                f"当前主任务「{cur_sum}」（{cur_tid}）尚未结案。"
                "识别到新问题，是否创建新任务执行？"
            ),
            "pending_query": message,
            "current_task_id": cur_tid,
            "current_query_summary": cur_sum,
        }
        runtime.emit(
            "hitl_required",
            {
                "task_id": cur_tid,
                "hitl_kind": "task_switch_confirm",
                "payload": switch_payload,
                "parent_status": PARENT_EXECUTING,
            },
        )
        user_switch = interrupt(switch_payload)
        if not isinstance(user_switch, dict):
            user_switch = {"action": "continue_main"}
        sw_act = str(user_switch.get("action") or "continue_main").strip().lower()
        if sw_act in ("switch_new", "new_main", "confirm_new"):
            mode = "new_main"
            simple = False
            continue_main = False
            use_main = True
            task_kind = "main"
            framework = "react"
            task_id = _new_id("task_")
            intent_decision = {
                **intent_decision,
                "mode": "new_main",
                "task_id": "",
                "reason": "用户确认创建新主任务",
            }
            from .chat_context_memory import annotate_intent_preprocess_plan, upsert_session_main_task_history

            _hitl_snap = ai_chat._build_intent_rewrite_snapshot_for_message(message, runtime.link_ctx)
            _hitl_snap = merge_pipeline_into_snapshot(_hitl_snap, pipeline_result)
            _hitl_snap = _merge_intent_llm_into_snapshot(
                _hitl_snap,
                intent_decision,
                message=message,
                rag_prefetch=bool(runtime.rag_prefetch),
                web_search=bool(runtime.web_search),
            )
            _hitl_snap = annotate_intent_preprocess_plan(
                _hitl_snap,
                message,
                orch_pipeline_nodes=getattr(runtime, "orch_pipeline_nodes", None) or {},
                domain=pipeline_result.intent_label,
            )
            _hitl_qs = str(
                _hitl_snap.get("task_summary") or _hitl_snap.get("query_summary") or message or ""
            )[:120]
            ai_chat._span_task(session_id, message, task_id=task_id)
            runtime.emit(
                "task_created",
                {
                    "task_id": task_id,
                    "session_id": session_id,
                    "user_query": message,
                    "status": PARENT_PLANNING,
                    "task_kind": "main",
                    "persist_main_task": True,
                    "stage": "分析任务中",
                    "progress": 18,
                    "rewrite_snapshot": _hitl_snap,
                    "query_summary": _hitl_qs,
                    "task_complexity": _hitl_snap.get("task_complexity") or "normal",
                },
            )
            runtime.emit(
                "intent_resolved",
                {
                    "task_id": task_id,
                    "task_kind": "main",
                    "is_simple": False,
                    "persist_main_task": True,
                    "task_action": "new_main",
                    "continue_main_task": False,
                    "rewrite_snapshot": _hitl_snap,
                    "user_query": message,
                    "query_summary": _hitl_qs,
                    "intent_reason": intent_decision.get("reason") or "",
                    "task_complexity": _hitl_snap.get("task_complexity") or "normal",
                    "detected_intent": {
                        "domain": pipeline_result.intent_label,
                        "domain_code": pipeline_result.intent,
                        "mode": "new_main",
                        "pipeline_source": pipeline_result.pipeline_source,
                    },
                },
            )
            try:
                upsert_session_main_task_history(
                    session_id,
                    task_id=task_id,
                    user_query=message,
                    query_summary=_hitl_qs,
                    status=PARENT_PLANNING,
                )
            except Exception:
                pass
            state["_hitl_early_emitted"] = True
            state["_hitl_early_snap"] = _hitl_snap
        elif sw_act in ("pause",):
            return {
                "orchestration_phase": PHASE_PAUSED,
                "paused": True,
                "graph_route": "paused",
                "hitl_kind": "task_switch_confirm",
                "user_hitl": user_switch,
                **_sse_events_from_runtime(runtime),
            }
        else:
            mode = "continue_main"
            simple = False
            continue_main = True
            use_main = True
            task_kind = "main"
            framework = "react"
            task_id = cur_tid
            intent_decision = {
                **intent_decision,
                "mode": "continue_main",
                "task_id": cur_tid,
                "reason": "用户选择继续当前主任务",
            }

    _hitl_early_emitted = bool(state.pop("_hitl_early_emitted", False))
    snap = state.pop("_hitl_early_snap", None)
    if not isinstance(snap, dict):
        snap = ai_chat._build_intent_rewrite_snapshot_for_message(
            message, runtime.link_ctx
        )
        snap = merge_pipeline_into_snapshot(snap, pipeline_result)
        snap = _merge_intent_llm_into_snapshot(
            snap,
            intent_decision,
            message=message,
            rag_prefetch=bool(runtime.rag_prefetch),
            web_search=bool(runtime.web_search),
        )
    else:
        snap = dict(snap)
        snap = _merge_intent_llm_into_snapshot(
            snap,
            intent_decision,
            message=message,
            rag_prefetch=bool(runtime.rag_prefetch),
            web_search=bool(runtime.web_search),
        )
    from .chat_context_memory import annotate_intent_preprocess_plan

    snap = annotate_intent_preprocess_plan(
        snap,
        message,
        orch_pipeline_nodes=getattr(runtime, "orch_pipeline_nodes", None) or {},
        domain=pipeline_result.intent_label,
    )
    if continue_main:
        from .chat_context_memory import enrich_snapshot_for_continue_main

        snap = enrich_snapshot_for_continue_main(
            snap,
            cur_task=cur_task,
            task_id=str(task_id or intent_decision.get("task_id") or ""),
            rag_prefetch=bool(runtime.rag_prefetch),
        )
    rewrite_state = snap.get("rewrite_state") or "rewrite_confirm"
    skip_confirm = rewrite_state == "rewrite_hold" or not _orch_enabled(runtime, "rewrite_confirm")

    step_idx = int(state.get("step_idx") or 0) + 1
    sub_plan_id, group_seq = _next_step_group(state)
    step_id = _new_id("step_")
    t0 = time.perf_counter()
    from .chat_context_memory import resolve_preserved_task_queries

    preserved_uq, preserved_qs = ("", "")
    if continue_main and use_main:
        preserved_uq, preserved_qs = resolve_preserved_task_queries(
            task_id=str(task_id or ""),
            cur_task=cur_task,
            fallback_message="",
        )
    if continue_main:
        intent_result_cn = format_intent_result_brief_cn(
            simple=False,
            continue_main=True,
            task_id=str(task_id or intent_decision.get("task_id") or ""),
            task_title=str(
                preserved_qs
                or snap.get("query_summary")
                or snap.get("task_summary")
                or ""
            ).strip(),
        )
        if not intent_result_cn:
            intent_result_cn = intent_decision.get("reason") or "延续主任务"
    else:
        ts = str(snap.get("task_summary") or snap.get("query_summary") or "").strip()
        intent_result_cn = format_intent_result_brief_cn(
            simple=simple,
            needs_rag=bool(snap.get("needs_rag")),
            keywords=snap.get("query_keywords") if isinstance(snap.get("query_keywords"), list) else None,
        )
        if ts and not simple:
            intent_result_cn = clamp_result_brief_cn(f"{intent_result_cn}：{ts[:40]}")
    intent_out = {
        "intent_type": "simple_chat" if simple else ("task_continue" if continue_main else "task"),
        "task_kind": task_kind,
        "use_main_task": use_main,
        "task_action": mode,
        "needs_rag": bool(snap.get("needs_rag")),
        "needs_web_search": bool(snap.get("needs_web_search")),
        "needs_plan_execute": use_main and framework == "plan_execute",
        "result_brief_cn": intent_result_cn,
        "is_simple": simple,
        "continue_main_task": continue_main,
        "intent_reason": intent_decision.get("reason") or "",
        "llm_powered": intent_llm_powered,
        "task_summary": snap.get("task_summary") or snap.get("query_summary") or "",
        "query_keywords": snap.get("query_keywords") or snap.get("keywords") or [],
        "query_rewrite_decision": snap.get("query_rewrite_decision") or "",
        "query_rewrite_skip_reason": snap.get("query_rewrite_skip_reason") or "",
        "task_decompose_decision": snap.get("task_decompose_decision") or "",
        "task_decompose_skip_reason": snap.get("task_decompose_skip_reason") or "",
        "task_complexity": snap.get("task_complexity") or ("normal" if simple else "complex"),
        "mode": mode,
        "reason": intent_decision.get("reason") or "",
    }
    intent_inp = {
        "user_message": (message or "")[:500],
        "web_search": bool(runtime.web_search),
        "rag_prefetch": bool(runtime.rag_prefetch),
        "read_comments": bool(runtime.read_comments),
        "orch_pipeline_nodes": dict(getattr(runtime, "orch_pipeline_nodes", None) or {}),
    }
    runtime.emit(
        "intent_resolved",
        {
            "task_id": "" if simple else (task_id or ""),
            "task_kind": task_kind,
            "is_simple": simple,
            "persist_main_task": use_main,
            "task_action": mode,
            "continue_main_task": continue_main,
            "rewrite_snapshot": snap if use_main else merge_pipeline_into_snapshot({}, pipeline_result),
            "detected_intent": {
                "domain": pipeline_result.intent_label,
                "domain_code": pipeline_result.intent,
                "mode": mode,
                "pipeline_source": pipeline_result.pipeline_source,
            },
            "user_query": (
                preserved_uq
                if continue_main and preserved_uq
                else (message if use_main else "")
            ),
            "query_summary": (
                str(snap.get("task_summary") or snap.get("query_summary") or message or "")[:120]
                if use_main and not continue_main
                else (
                    preserved_qs
                    if continue_main and preserved_qs
                    else ((message or "")[:120] if use_main else "")
                )
            ),
            "preserve_task_identity": bool(continue_main and preserved_uq),
            "intent_reason": intent_decision.get("reason") or "",
        },
    )
    orch_emit = emit_orchestration_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id or "",
            step_name="意图识别",
            phase="intent",
            result_brief=intent_result_cn,
            input_payload=intent_inp,
            output_payload=intent_out,
            prior=None,
            think_text_override=(
                str(intent_decision.get("llm_raw") or intent_decision.get("reason") or "")
                if intent_llm_powered
                else (
                    summarize_orchestration_payload_cn("intent", intent_out)
                    or str(intent_decision.get("reason") or "")
                )
            ),
            llm_powered=intent_llm_powered,
            pre_step_id=intent_pre_step_id or None,
            think_streamed=intent_think_streamed,
        )
    group_seq = orch_emit.get("group_seq", group_seq)

    if not task_id and not simple:
        task_id = _new_id("task_")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        rewritten_q = (snap.get("rewritten_query") or message or "").strip()
        ai_chat._span_task(session_id, message, task_id=task_id)
        snap_json = {
            "fixed": {"session_id": session_id, "task_id": task_id, "trace_id": trace_id},
            "open": {
                "objective": rewritten_q[:200] or (message or "")[:200],
                "current_assessment": "意图识别完成",
                "decision": "continue",
                "metadata": snap.get("metadata", {}),
                "needs_rag": snap.get("needs_rag", False),
                "rewrite_state": rewrite_state,
                "task_kind": task_kind,
                "ephemeral": not use_main,
            },
        }
        ai_chat._span_update(
            task_id,
            status=PARENT_CREATED,
            started_at=now,
            query_summary=(message or "")[:120],
            rewritten_query=rewritten_q[:500],
            snapshot_json=snap_json,
        )
        if use_main and not continue_main:
            ai_chat._span_update(task_id, status=PARENT_SUMMARIZING)
            ai_chat._span_update(task_id, status=PARENT_PLANNING)
            runtime.emit(
                "task_created",
                {
                    "task_id": task_id,
                    "session_id": session_id,
                    "user_query": message,
                    "status": PARENT_PLANNING,
                    "task_kind": "main",
                    "persist_main_task": True,
                    "stage": "分析任务中",
                    "progress": 8,
                    "rewrite_snapshot": snap,
                    "query_summary": (message or "")[:120],
                },
            )
            try:
                from .chat_context_memory import upsert_session_main_task_history

                upsert_session_main_task_history(
                    session_id,
                    task_id=task_id,
                    user_query=message,
                    query_summary=(message or "")[:120],
                    status=PARENT_PLANNING,
                )
            except Exception:
                pass
        elif use_main and continue_main:
            from .chat_context_memory import resolve_preserved_task_queries

            cont_uq, cont_qs = resolve_preserved_task_queries(
                task_id=str(task_id or ""),
                cur_task=cur_task,
                fallback_message="",
            )
            runtime.emit(
                "task_created",
                {
                    "task_id": task_id,
                    "session_id": session_id,
                    "user_query": cont_uq,
                    "status": PARENT_EXECUTING,
                    "task_kind": "main",
                    "persist_main_task": True,
                    "stage": "延续主任务",
                    "progress": 12,
                    "rewrite_snapshot": snap,
                    "query_summary": cont_qs,
                    "task_action": "continue",
                    "preserve_task_identity": True,
                },
            )
        ai_chat._span_update(task_id, status=PARENT_EXECUTING)
        from .span_orchestration import schedule_persist_reasoning_step

        schedule_persist_reasoning_step(
            task_id,
            session_id,
            step_name="意图识别",
            phase="intent",
            trace_id=trace_id,
            input_payload=intent_inp,
            output_payload=intent_out,
            result_brief=intent_result_cn,
        )

    elif continue_main and task_id:
        from .chat_context_memory import resolve_preserved_task_queries

        cont_uq, cont_qs = resolve_preserved_task_queries(
            task_id=str(task_id or ""),
            cur_task=cur_task,
            fallback_message="",
        )
        runtime.emit(
            "task_created",
            {
                "task_id": task_id,
                "session_id": session_id,
                "user_query": cont_uq,
                "status": PARENT_EXECUTING,
                "task_kind": "main",
                "persist_main_task": True,
                "stage": "延续主任务",
                "progress": 12,
                "rewrite_snapshot": snap,
                "query_summary": cont_qs,
                "task_action": "continue",
                "preserve_task_identity": True,
            },
        )
        ai_chat._span_update(task_id, status=PARENT_EXECUTING)
        from .span_orchestration import schedule_persist_reasoning_step

        schedule_persist_reasoning_step(
            task_id,
            session_id,
            step_name="意图识别",
            phase="intent",
            trace_id=trace_id,
            input_payload=intent_inp,
            output_payload=intent_out,
            result_brief=intent_result_cn,
        )

    if not simple and not continue_main and not _hitl_early_emitted:
        runtime.emit(
            "intent_resolved",
            {
                "task_id": task_id or "",
                "task_kind": task_kind,
                "is_simple": simple,
                "persist_main_task": use_main,
                "task_action": mode,
                "continue_main_task": continue_main,
                "rewrite_snapshot": snap if use_main else None,
                "user_query": message if use_main else "",
                "query_summary": str(snap.get("task_summary") or snap.get("query_summary") or message or "")[:120]
                if use_main
                else "",
                "intent_reason": intent_decision.get("reason") or "",
                "task_complexity": snap.get("task_complexity") or "normal",
            },
        )
    elif simple:
        runtime.emit(
            "intent_resolved",
            {
                "task_id": "",
                "task_kind": "simple",
                "is_simple": True,
                "persist_main_task": False,
                "task_action": mode,
                "continue_main_task": False,
                "intent_reason": intent_decision.get("reason") or "",
            },
        )

    if continue_main:
        route = "continue_execute"
    elif simple:
        route = "simple"
    elif not _orch_enabled(runtime, "query_rewrite"):
        route = "slot_fill"
    elif _infer_domain_module(message)[0] == "社媒分析":
        route = "slot_fill"  # XHS 快径：跳过改写，社媒查询自包含无需指代消解
    else:
        route = "rewrite"

    out = {
        "orchestration_phase": PHASE_INTENT,
        "task_kind": task_kind,
        "use_main_task": use_main,
        "framework": framework,
        "intent_passed": True,
        "intent_rewrite_snapshot": snap,
        "rewrite_state": rewrite_state,
        "skip_rewrite_confirm": skip_confirm,
        "needs_rag": bool(snap.get("needs_rag")),
        "query_summary": str(snap.get("query_summary") or snap.get("task_summary") or (message or "")[:120]),
        "rewritten_query": str(snap.get("rewritten_query") or message or ""),
        "task_id": task_id,
        "step_idx": step_idx,
        "group_seq": orch_emit.get("group_seq", group_seq),
        "continue_main_task": continue_main,
        "orch_chain": orch_emit.get("orch_chain", []),
        "graph_route": route,
        "runtime_config": runtime.snapshot_config(),
    }
    out.update(_sse_events_from_runtime(runtime))
    _LOG.info(
        "[AI问答-LangGraph|chat_graph_nodes.node_intent_recognition|session:%s|硬编执行|完成] "
        "route=%s; task_kind=%s",
        session_id,
        route,
        task_kind,
    )
    return out


async def node_simple_answer(state: Dict[str, Any], config: RunnableConfig | None = None) -> Dict[str, Any]:
    """简单任务：仅 handoff 标记，直答 SSE 由 runner 流式推送（避免整节点阻塞）。"""
    runtime = _runtime_from_state_or_config(state, config)
    trace_id = state.get("trace_id") or runtime.trace_id

    runtime.emit("thinking_end", {"task_id": "", "ephemeral": True, "bundle": {"task_kind": "simple"}})

    out = {
        "orchestration_phase": PHASE_SIMPLE,
        "task_kind": "simple",
        "use_main_task": False,
        "graph_route": "handoff_simple",
        "execution_done": False,
    }
    out.update(_sse_events_from_runtime(runtime))
    _LOG.info(
        "[AI问答-LangGraph|chat_graph_nodes.node_simple_answer|trace:%s|硬编执行|handoff] "
        "route=handoff_simple",
        trace_id,
    )
    return out


async def node_rewrite_summary(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """query 改写摘要（编排段，可选 LLM 单次）。"""
    runtime = _runtime_from_state_or_config(state, config)
    snap = dict(state.get("intent_rewrite_snapshot") or {})
    message = state.get("message") or runtime.message
    trace_id = state.get("trace_id") or runtime.trace_id
    task_id = state.get("task_id") or ""

    if not _orch_enabled(runtime, "query_rewrite"):
        rewritten = str(snap.get("rewritten_query") or message or "").strip()
        return _skip_orch_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="问题改写",
            phase="rewrite",
            node_id="query_rewrite",
            graph_route="slot_fill",
            passthrough={
                "orchestration_phase": PHASE_REWRITE_PROPOSE,
                "intent_rewrite_snapshot": snap,
                "rewritten_query": rewritten,
                "query_summary": str(snap.get("query_summary") or snap.get("task_summary") or rewritten[:120]),
                "rewrite_actions": ["skipped"],
            },
        )

    node_t0 = time.perf_counter()

    rewrite_result = _rewrite_query_by_rules(message, runtime.link_ctx)
    qk = snap.get("query_keywords") or snap.get("keywords") or []
    if isinstance(qk, list) and qk:
        rewrite_result["query_keywords"] = [str(x).strip() for x in qk if str(x).strip()]
    rewritten = str(rewrite_result.get("rewritten_query") or snap.get("rewritten_query") or message or "").strip()
    llm_rewrite_text = ""
    rewrite_pre_step = ""
    if runtime.api_key and runtime.model_resolved:
        rewrite_pre_step = _new_id("step_")
        llm_rewrite_text, _ = await _orch_llm_invoke(
            runtime,
            "rewrite",
            rewritten,
            intent_snapshot=snap,
            max_tokens=480,
            step_id=rewrite_pre_step,
            stream_think=True,
            trace_id=state.get("trace_id") or runtime.trace_id,
            task_id=state.get("task_id") or "",
            step_name="问题改写",
        )
        if llm_rewrite_text.strip():
            llm_json = _parse_llm_json(llm_rewrite_text)
            if llm_json.get("rewritten_query"):
                rewritten = str(llm_json.get("rewritten_query") or rewritten).strip()[:800]
                rewrite_result["rewritten_query"] = rewritten
            elif not llm_json:
                rewritten = llm_rewrite_text[:800]
                rewrite_result["rewritten_query"] = rewritten
            if llm_json.get("query_summary"):
                rewrite_result["query_summary"] = str(llm_json.get("query_summary") or "")[:120]
            rt = llm_json.get("retrieval_terms")
            if isinstance(rt, list):
                rt = [str(x).strip() for x in rt if str(x).strip()]
                if rt:
                    rewrite_result["retrieval_terms"] = rt
                    snap["retrieval_terms"] = rt
            lqk = llm_json.get("query_keywords")
            if isinstance(lqk, list):
                lqk = [str(x).strip() for x in lqk if str(x).strip()]
                if lqk:
                    rewrite_result["query_keywords"] = lqk
                    snap["query_keywords"] = lqk
                    snap["keywords"] = lqk
            actions_from_llm = list(rewrite_result.get("rewrite_actions") or [])
            if "llm_completion" not in actions_from_llm:
                actions_from_llm.append("llm_completion")
            rewrite_result["rewrite_actions"] = actions_from_llm
    snap["rewritten_query"] = rewritten
    query_summary = str(
        rewrite_result.get("query_summary")
        or snap.get("task_summary")
        or snap.get("query_summary")
        or rewritten[:120]
        or (message or "")[:120]
    )
    orch_kb_fast_lane = False
    slot_prefill: Dict[str, Any] = {}
    decomp_prefill: Dict[str, Any] = {}
    plan_prefill: List[Any] = []
    combined_lane_text = ""
    if _eligible_kb_orch_fast_lane(runtime, message, snap):
        bundle_pre = _new_id("step_")
        combined_lane_text, combined_json = await _orch_llm_invoke(
            runtime,
            "slot_decompose_bundle",
            rewritten,
            intent_snapshot={**snap, **rewrite_result},
            max_tokens=680,
            step_id=bundle_pre,
            stream_think=True,
            trace_id=state.get("trace_id") or runtime.trace_id,
            task_id=state.get("task_id") or "",
            step_name="业务对齐+任务分解",
        )
        if combined_json:
            slot_prefill, decomp_prefill, plan_prefill = _apply_slot_decompose_bundle(
                rewritten,
                snap,
                combined_json,
                tools_meta=runtime.tools_meta,
            )
            orch_kb_fast_lane = True
    actions = [str(x) for x in (rewrite_result.get("rewrite_actions") or ["no_rewrite_needed"])]
    rewrite_out = {
        "rewritten_query": rewritten[:500],
        "query_summary": query_summary,
        "original_query": rewrite_result.get("original_query"),
        "rewrite_actions": actions,
        "query_keywords": rewrite_result.get("query_keywords") or snap.get("query_keywords") or [],
        "retrieval_terms": rewrite_result.get("retrieval_terms") or snap.get("retrieval_terms") or [],
    }
    seq = _emit_orchestration_step(
        runtime,
        state,
        trace_id=state.get("trace_id") or runtime.trace_id,
        task_id=state.get("task_id") or "",
        step_name="问题改写",
        phase="rewrite",
        result_brief="已标准化问题表述，保留原始意图",
        input_payload={
            "original_query": str(rewrite_result.get("original_query") or message or "")[:500],
            "intent_snapshot": snap,
        },
        output_payload=rewrite_out,
        think_text_override=llm_rewrite_text or None,
        llm_powered=bool(llm_rewrite_text),
        pre_step_id=rewrite_pre_step or None,
        think_streamed=bool(rewrite_pre_step and llm_rewrite_text),
    )
    out = {
        "orchestration_phase": PHASE_REWRITE_PROPOSE,
        "intent_rewrite_snapshot": snap,
        "rewritten_query": rewritten,
        "query_summary": query_summary,
        "rewrite_actions": actions,
        "graph_route": "rewrite_confirm" if _orch_enabled(runtime, "rewrite_confirm") else "slot_fill",
        "group_seq": seq.get("group_seq"),
        "orch_chain": seq.get("orch_chain", []),
        "orch_kb_fast_lane": orch_kb_fast_lane,
        "slot_snapshot_prefill": slot_prefill if orch_kb_fast_lane else {},
        "decomposition_snapshot_prefill": decomp_prefill if orch_kb_fast_lane else {},
        "plan_steps_prefill": plan_prefill if orch_kb_fast_lane else [],
        "orch_fast_lane_combined_text": combined_lane_text if orch_kb_fast_lane else "",
    }
    out.update(_sse_events_from_runtime(runtime))
    _LOG.info(
        "[AI问答-LangGraph|chat_graph_nodes.node_rewrite_summary|session:%s|硬编执行|完成] "
        "node_total_ms=%s; llm_rewrite=%s",
        runtime.session_id,
        int((time.perf_counter() - node_t0) * 1000),
        bool(llm_rewrite_text),
    )
    return out


async def node_intent_decompose(state: Dict[str, Any], config: RunnableConfig | None = None) -> Dict[str, Any]:
    """意图分解：LLM 拆解 + 规则兜底。"""
    runtime = _runtime_from_state_or_config(state, config)
    rewritten = (state.get("rewritten_query") or (state.get("intent_rewrite_snapshot") or {}).get("rewritten_query") or state.get("message") or runtime.message or "").strip()
    slot = state.get("slot_snapshot") or {}
    framework = state.get("framework") or "react"
    trace_id = state.get("trace_id") or runtime.trace_id
    task_id = state.get("task_id") or ""

    if not _orch_enabled(runtime, "task_decompose"):
        return _skip_orch_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="任务分解",
            phase="decompose",
            node_id="task_decompose",
            graph_route=_route_after_decompose(runtime, slot, framework),
            passthrough={
                "orchestration_phase": PHASE_DECOMPOSE,
                "decomposition_snapshot": state.get("decomposition_snapshot") or {},
                "plan_steps": state.get("plan_steps") or [],
            },
        )

    if state.get("orch_kb_fast_lane") and state.get("decomposition_snapshot_prefill"):
        snapshot = dict(state.get("decomposition_snapshot_prefill") or {})
        sub_tasks = list(state.get("plan_steps_prefill") or snapshot.get("sub_tasks") or [])
        llm_text = str(state.get("orch_fast_lane_combined_text") or "")
        decomp_type = str(snapshot.get("decomposition_type") or "stage")
        seq = _emit_orchestration_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="任务分解",
            phase="decompose",
            result_brief=f"已拆解为 {len(sub_tasks)} 个{'并行' if decomp_type == 'parallel' else '阶段'}任务",
            input_payload={"rewritten_query": rewritten[:500], "slot_snapshot": slot, "fast_lane": True},
            output_payload=snapshot,
            think_text_override=llm_text or None,
            llm_powered=bool(llm_text),
        )
        out = {
            "orchestration_phase": PHASE_DECOMPOSE,
            "decomposition_snapshot": snapshot,
            "plan_steps": sub_tasks,
            "graph_route": _route_after_decompose(runtime, slot, framework),
            "group_seq": seq.get("group_seq"),
            "orch_chain": seq.get("orch_chain", []),
        }
        out.update(_sse_events_from_runtime(runtime))
        return out

    snapshot = _decompose_task_by_rules(rewritten, slot)
    _is_xhs = slot.get("domain") == "社媒分析"
    decomp_pre = ""
    llm_text = ""
    if _is_xhs:
        # XHS 快径：规则分解已生成 3 步骤（定位→检索→汇总），跳过 LLM
        pass
    else:
        decomp_pre = _new_id("step_")
        llm_text, llm_json = await _orch_llm_invoke(
            runtime,
            "decompose",
            rewritten,
            intent_snapshot=slot,
            max_tokens=640,
            step_id=decomp_pre,
            stream_think=True,
            trace_id=state.get("trace_id") or runtime.trace_id,
            task_id=state.get("task_id") or "",
            step_name="任务分解",
        )
        if isinstance(llm_json.get("sub_tasks"), list) and llm_json.get("sub_tasks"):
            snapshot = {
                **snapshot,
                "decomposition_type": str(llm_json.get("decomposition_type") or snapshot.get("decomposition_type") or "stage"),
                "sub_tasks": llm_json.get("sub_tasks"),
                "dependencies": llm_json.get("dependencies") if isinstance(llm_json.get("dependencies"), list) else snapshot.get("dependencies"),
            }
    decomp_type = str(snapshot.get("decomposition_type") or "stage")
    sub_tasks = list(snapshot.get("sub_tasks") or [])
    seq = _emit_orchestration_step(
        runtime,
        state,
        trace_id=state.get("trace_id") or runtime.trace_id,
        task_id=state.get("task_id") or "",
        step_name="任务分解",
        phase="decompose",
        result_brief=f"已拆解为 {len(sub_tasks)} 个{'并行' if decomp_type == 'parallel' else '阶段'}任务",
        input_payload={
            "rewritten_query": rewritten[:500],
            "slot_snapshot": slot,
        },
        output_payload=snapshot,
        think_text_override=llm_text or None,
        llm_powered=bool(llm_text),
        pre_step_id=decomp_pre or None,
        think_streamed=bool(decomp_pre and llm_text),
    )
    out = {
        "orchestration_phase": PHASE_DECOMPOSE,
        "decomposition_snapshot": snapshot,
        "plan_steps": sub_tasks,
        "graph_route": _route_after_decompose(runtime, slot, framework),
        "group_seq": seq.get("group_seq"),
        "orch_chain": seq.get("orch_chain", []),
    }
    out.update(_sse_events_from_runtime(runtime))
    return out


async def node_intent_enhance(state: Dict[str, Any], config: RunnableConfig | None = None) -> Dict[str, Any]:
    """意图增强：LLM 生成检索提示 + 规则兜底。"""
    runtime = _runtime_from_state_or_config(state, config)
    rewritten = (state.get("rewritten_query") or state.get("message") or runtime.message or "").strip()
    slot = state.get("slot_snapshot") or {}
    decomposition = state.get("decomposition_snapshot") or {}
    framework = state.get("framework") or "react"
    trace_id = state.get("trace_id") or runtime.trace_id
    task_id = state.get("task_id") or ""

    if not _orch_enabled(runtime, "intent_enhance"):
        return _skip_orch_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="意图增强",
            phase="enhance",
            node_id="intent_enhance",
            graph_route=_route_after_enhance_chain(runtime, slot, framework),
            passthrough={
                "orchestration_phase": PHASE_ENHANCE,
                "enhancement_snapshot": state.get("enhancement_snapshot") or {},
            },
        )

    snapshot = _enhance_intent_by_rules(rewritten, slot, decomposition, web_search=bool(runtime.web_search))
    _is_xhs = slot.get("domain") == "社媒分析"
    enhance_pre = ""
    llm_text = ""
    if _is_xhs:
        # XHS 快径：规则增强已生成搜索提示，跳过 LLM
        pass
    else:
        enhance_pre = _new_id("step_")
        llm_text, llm_json = await _orch_llm_invoke(
            runtime,
            "enhance",
            rewritten,
            intent_snapshot={
                **slot,
                "decomposition": decomposition,
                "web_search": bool(runtime.web_search),
                "rag_prefetch": bool(runtime.rag_prefetch),
                "needs_rag": bool(slot.get("needs_rag")),
            },
            max_tokens=640,
            step_id=enhance_pre,
            stream_think=True,
            trace_id=trace_id,
            task_id=task_id,
            step_name="意图增强",
        )
        for key in ("retrieval_hints", "search_keyword_queries", "web_search_queries", "verification_points", "risk_flags", "hypothetical_answer", "search_objective"):
            val = llm_json.get(key)
            if val:
                snapshot[key] = val
    if not runtime.web_search:
        snapshot["web_search_queries"] = []
    seq = _emit_orchestration_step(
        runtime,
        state,
        trace_id=state.get("trace_id") or runtime.trace_id,
        task_id=state.get("task_id") or "",
        step_name="意图增强",
        phase="enhance",
        result_brief="已生成检索提示和核验要点",
        input_payload={
            "rewritten_query": rewritten[:500],
            "decomposition_snapshot": decomposition,
            "slot_snapshot": slot,
            "web_search": bool(runtime.web_search),
            "rag_prefetch": bool(runtime.rag_prefetch),
        },
        output_payload=snapshot,
        think_text_override=llm_text or None,
        llm_powered=bool(llm_text),
        pre_step_id=enhance_pre or None,
        think_streamed=bool(enhance_pre and llm_text),
    )
    route = _route_after_enhance_chain(runtime, slot, framework)
    out = {
        "orchestration_phase": PHASE_ENHANCE,
        "enhancement_snapshot": snapshot,
        "graph_route": route,
        "group_seq": seq.get("group_seq"),
        "orch_chain": seq.get("orch_chain", []),
    }
    out.update(_sse_events_from_runtime(runtime))
    return out


async def node_rewrite_confirm_ui(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """HITL：改写确认（自由对话）。interrupt 等待 resume。"""
    runtime = _runtime_from_state_or_config(state, config)
    snap = state.get("intent_rewrite_snapshot") or {}
    payload = {
        "kind": "rewrite_confirm",
        "rewrite_snapshot": snap,
        "message": "请确认改写后的任务摘要，或输入修改意见。",
        "ui": "free_dialog",
    }
    runtime.emit(
        "hitl_required",
        {
            "task_id": state.get("task_id") or "",
            "hitl_kind": "rewrite_confirm",
            "payload": payload,
            "parent_status": PARENT_EXECUTING,
        },
    )
    user = interrupt(payload)
    if not isinstance(user, dict):
        user = {"action": "confirm"}

    action = str(user.get("action") or "confirm").strip().lower()
    if action == "pause":
        return {
            "orchestration_phase": PHASE_PAUSED,
            "paused": True,
            "graph_route": "paused",
            "user_hitl": user,
            **_sse_events_from_runtime(runtime),
        }
    if action in ("reintent", "re_identify", "restart"):
        return {
            "graph_route": "intent",
            "user_hitl": user,
            **_sse_events_from_runtime(runtime),
        }
    edited = str(user.get("rewritten_query") or "").strip()
    if edited:
        snap = dict(snap)
        snap["rewritten_query"] = edited
    return {
        "orchestration_phase": PHASE_REWRITE_CONFIRM,
        "intent_rewrite_snapshot": snap,
        "user_hitl": user,
        "graph_route": "slot_fill",
        **_sse_events_from_runtime(runtime),
    }


async def node_slot_fill(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """业务对齐/槽位填充：LLM 识别领域模块 + 规则兜底。"""
    runtime = _runtime_from_state_or_config(state, config)
    snap = state.get("intent_rewrite_snapshot") or {}
    rewritten = state.get("rewritten_query") or snap.get("rewritten_query") or state.get("message")
    framework = state.get("framework") or "react"
    trace_id = state.get("trace_id") or runtime.trace_id
    task_id = state.get("task_id") or ""

    if not _orch_enabled(runtime, "slot_fill"):
        slot = _align_business_slots(str(rewritten or ""), runtime.tools_meta, snap)
        rt = snap.get("retrieval_terms")
        if isinstance(rt, list) and rt:
            slot["retrieval_terms"] = [str(x).strip() for x in rt if str(x).strip()]
        if snap.get("needs_rag") is not None:
            slot["needs_rag"] = bool(snap.get("needs_rag"))
        return _skip_orch_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="业务对齐",
            phase="slot",
            node_id="slot_fill",
            graph_route=_route_after_slot(runtime, slot, framework),
            passthrough={
                "orchestration_phase": PHASE_SLOT_FILL,
                "slot_snapshot": slot,
            },
        )

    slot = _align_business_slots(str(rewritten or ""), runtime.tools_meta, snap)
    _is_xhs = slot.get("domain") == "社媒分析"
    if state.get("orch_kb_fast_lane") and state.get("slot_snapshot_prefill"):
        slot = dict(state.get("slot_snapshot_prefill") or slot)
        llm_text = str(state.get("orch_fast_lane_combined_text") or "")
        result_brief = "已识别业务对象和查询范围"
        if slot.get("needs_rag"):
            result_brief = "已定位到资料分析任务，需按文档核验"
        seq = _emit_orchestration_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="业务对齐",
            phase="slot",
            result_brief=result_brief,
            input_payload={
                "rewritten_query": str(rewritten or "")[:500],
                "query_summary": state.get("query_summary") or snap.get("rewritten_query", "")[:120],
                "fast_lane": True,
            },
            output_payload=slot,
            think_text_override=llm_text or None,
            llm_powered=bool(llm_text),
        )
        out = {
            "orchestration_phase": PHASE_SLOT_FILL,
            "slot_snapshot": slot,
            "graph_route": _route_after_slot(runtime, slot, framework),
            "group_seq": seq.get("group_seq"),
            "orch_chain": seq.get("orch_chain", []),
        }
        out.update(_sse_events_from_runtime(runtime))
        return out

    # XHS 快径：领域识别已由规则完成，无需 LLM 槽位填充
    if _is_xhs:
        from .link_doc_routing import extract_xhs_numeric_id
        xhs_id = extract_xhs_numeric_id(str(rewritten or ""))
        if xhs_id:
            ents = [str(x).strip() for x in (slot.get("entities") or []) if str(x).strip()]
            if xhs_id not in ents:
                ents.append(xhs_id)
            slot["entities"] = ents
        seq = _emit_orchestration_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="业务对齐",
            phase="slot",
            result_brief="已识别社媒分析任务（规则快径）",
            input_payload={
                "rewritten_query": str(rewritten or "")[:500],
                "query_summary": str(state.get("query_summary") or ""),
                "fast_lane": "xhs",
            },
            output_payload=slot,
            llm_powered=False,
        )
        out = {
            "orchestration_phase": PHASE_SLOT_FILL,
            "slot_snapshot": slot,
            "graph_route": "intent_decompose",
            "group_seq": seq.get("group_seq"),
            "orch_chain": seq.get("orch_chain", []),
        }
        out.update(_sse_events_from_runtime(runtime))
        return out

    slot_pre = _new_id("step_")
    llm_text, llm_json = await _orch_llm_invoke(
        runtime,
        "slot",
        str(rewritten or ""),
        intent_snapshot=snap,
        max_tokens=520,
        step_id=slot_pre,
        stream_think=True,
        trace_id=state.get("trace_id") or runtime.trace_id,
        task_id=state.get("task_id") or "",
        step_name="业务对齐",
    )
    for key in ("domain", "module", "operation_type", "entities", "retrieval_terms", "needs_rag", "state"):
        val = llm_json.get(key)
        if val is not None and val != "":
            slot[key] = val
    qk = snap.get("query_keywords") or snap.get("keywords")
    if isinstance(qk, list) and qk:
        ents = [str(x).strip() for x in (slot.get("entities") or []) if str(x).strip()]
        for x in qk:
            s = str(x).strip()
            if s and s not in ents:
                ents.append(s)
        if ents:
            slot["entities"] = ents
    if not slot.get("retrieval_terms"):
        rt = snap.get("retrieval_terms")
        if isinstance(rt, list) and rt:
            slot["retrieval_terms"] = [str(x).strip() for x in rt if str(x).strip()]
    if snap.get("needs_rag") is not None and slot.get("needs_rag") is None:
        slot["needs_rag"] = bool(snap.get("needs_rag"))
    result_brief = "已识别业务对象和查询范围"
    if slot.get("needs_rag"):
        result_brief = "已定位到资料分析任务，需按文档核验"
    seq = _emit_orchestration_step(
        runtime,
        state,
        trace_id=state.get("trace_id") or runtime.trace_id,
        task_id=state.get("task_id") or "",
        step_name="业务对齐",
        phase="slot",
        result_brief=result_brief,
        input_payload={
            "rewritten_query": str(rewritten or "")[:500],
            "query_summary": state.get("query_summary") or snap.get("rewritten_query", "")[:120],
        },
        output_payload=slot,
        think_text_override=llm_text or None,
        llm_powered=bool(llm_text),
        pre_step_id=slot_pre,
        think_streamed=bool(llm_text),
    )
    out = {
        "orchestration_phase": PHASE_SLOT_FILL,
        "slot_snapshot": slot,
        "graph_route": _route_after_slot(runtime, slot, framework),
        "group_seq": seq.get("group_seq"),
        "orch_chain": seq.get("orch_chain", []),
    }
    out.update(_sse_events_from_runtime(runtime))
    return out


async def node_slot_confirm_ui(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """HITL：槽位表单确认。"""
    runtime = _runtime_from_state_or_config(state, config)
    slot = state.get("slot_snapshot") or {}
    payload = {
        "kind": "slot_confirm",
        "slot_snapshot": slot,
        "message": "请确认业务领域、模块和检索意图。",
        "ui": "form",
    }
    runtime.emit(
        "hitl_required",
        {
            "task_id": state.get("task_id") or "",
            "hitl_kind": "slot_confirm",
            "payload": payload,
        },
    )
    user = interrupt(payload)
    if not isinstance(user, dict):
        user = {"action": "confirm"}
    action = str(user.get("action") or "confirm").strip().lower()
    if action == "pause":
        return {
            "paused": True,
            "graph_route": "paused",
            "orchestration_phase": PHASE_PAUSED,
            **_sse_events_from_runtime(runtime),
        }
    merged = dict(slot)
    if isinstance(user.get("slot_snapshot"), dict):
        merged.update(user["slot_snapshot"])
    return {
        "slot_snapshot": merged,
        "slot_confirmed": True,
        "orchestration_phase": PHASE_SLOT_CONFIRM,
        "graph_route": "rag_filter_confirm",
        "user_hitl": user,
        **_sse_events_from_runtime(runtime),
    }


async def node_rag_filter_confirm_ui(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """HITL：RAG 元数据硬筛表单（意图/术语映射后、检索前暂停，非终止链路）。"""
    runtime = _runtime_from_state_or_config(state, config)
    slot = state.get("slot_snapshot") or {}
    needs = bool(slot.get("needs_rag"))
    if not needs:
        return {
            "rag_metadata_filter": {},
            "rag_filter_confirmed": True,
            "graph_route": "rag_decision",
            **_sse_events_from_runtime(runtime),
        }

    # 已确认过（含 resume 重入）：勿再次 interrupt / 推 hitl，避免表单弹两次
    if state.get("rag_filter_confirmed"):
        filt = state.get("rag_metadata_filter") if isinstance(state.get("rag_metadata_filter"), dict) else {}
        return {
            "rag_metadata_filter": filt,
            "rag_filter_confirmed": True,
            "orchestration_phase": PHASE_SLOT_CONFIRM,
            "graph_route": "rag_decision",
            **_sse_events_from_runtime(runtime),
        }

    from .rag_recall_filter import propose_rag_filter_form

    query = (
        slot.get("rewritten_query")
        or state.get("rewritten_query")
        or state.get("message")
        or runtime.message
        or ""
    )
    proposal = propose_rag_filter_form(
        str(query),
        slot_snapshot=slot,
        enhancement_snapshot=state.get("enhancement_snapshot") or {},
    )
    filt = dict(proposal.get("filter") or {})

    # 标准 RAG：仅由 Query 自动推导元数据筛选，不弹 HITL
    if not _orch_rag_filter_hitl(runtime):
        _emit_orchestration_step(
            runtime,
            state,
            trace_id=state.get("trace_id") or runtime.trace_id,
            task_id=state.get("task_id") or "",
            step_name="RAG 元数据筛选",
            phase="rag_filter",
            result_brief="已根据 Query 自动推导知识库筛选条件",
            input_payload={"query": str(query)[:500], "auto": True},
            output_payload={
                "rag_metadata_filter": filt,
                "extracted_terms": proposal.get("extracted_terms") or [],
                "auto_applied": True,
            },
            executed=True,
            llm_powered=False,
        )
        return {
            "rag_metadata_filter": filt,
            "rag_filter_confirmed": True,
            "orchestration_phase": PHASE_SLOT_CONFIRM,
            "graph_route": "rag_decision",
            **_sse_events_from_runtime(runtime),
        }

    payload = {
        "kind": "rag_filter_confirm",
        "query": query,
        "filter_form": proposal.get("filter") or {},
        "vocabulary": proposal.get("vocabulary") or {},
        "extracted_terms": proposal.get("extracted_terms") or [],
        "term_mapping_notes": proposal.get("term_mapping_notes") or [],
        "match_mode": proposal.get("match_mode") or {},
        "message": "请确认知识库元数据筛选条件（空=不筛）；确认后继续检索。",
        "ui": "form",
    }
    # 首次暂停由 runner 发 graph_interrupt；节点内不再 emit hitl_required，避免 resume 重入重复弹窗
    user = interrupt(payload)
    if not isinstance(user, dict):
        user = {"action": "confirm"}
    action = str(user.get("action") or "confirm").strip().lower()
    if action == "pause":
        return {
            "paused": True,
            "graph_route": "paused",
            "orchestration_phase": PHASE_PAUSED,
            **_sse_events_from_runtime(runtime),
        }
    filt = dict(proposal.get("filter") or {})
    if isinstance(user.get("rag_metadata_filter"), dict):
        filt.update({k: str(v or "").strip() for k, v in user["rag_metadata_filter"].items()})
    if isinstance(user.get("filter_form"), dict):
        filt.update({k: str(v or "").strip() for k, v in user["filter_form"].items()})

    return {
        "rag_metadata_filter": filt,
        "rag_filter_confirmed": True,
        "orchestration_phase": PHASE_SLOT_CONFIRM,
        "graph_route": "rag_decision",
        "user_hitl": user,
        **_sse_events_from_runtime(runtime),
    }


_RAG_PREFETCH_TIMEOUT_SEC = float(
    __import__("os").environ.get("CHAT_RAG_PREFETCH_TIMEOUT_SEC", "30") or "30"
)


async def _safe_kb_search(
    query: str,
    *,
    top_k: int = 5,
    span_ctx: Optional[Dict[str, Any]] = None,
    timeout_sec: float = _RAG_PREFETCH_TIMEOUT_SEC,
    metadata_filter: Optional[Dict[str, str]] = None,
) -> tuple[List[Dict[str, Any]], str]:
    """带超时与 Milvus 探测的 kb_search，避免编排 SSE 在 RAG 预取处长时间挂起。"""
    q = str(query or "").strip()
    if not q:
        return [], ""
    try:
        from .milvus_health import check_milvus

        probe = check_milvus()
        if not probe.get("milvus_ok"):
            return [], str(probe.get("error") or "Milvus 不可用")[:300]
    except Exception as ex:
        return [], str(ex)[:300]

    def _run() -> List[Dict[str, Any]]:
        from .kb_rag import kb_search

        return list(
            kb_search(q, top_k=top_k, span_ctx=span_ctx, metadata_filter=metadata_filter) or []
        )

    try:
        hits = await asyncio.wait_for(asyncio.to_thread(_run), timeout=max(1.0, timeout_sec))
        return hits, ""
    except asyncio.TimeoutError:
        return [], f"知识库预取超时（>{timeout_sec}s）"
    except Exception as ex:
        return [], str(ex)[:300]


async def node_rag_decision(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """RAG 决策：需要时 HITL 确认检索词。"""
    from .rag_recall_filter import active_filter_fields

    runtime = _runtime_from_state_or_config(state, config)
    slot = state.get("slot_snapshot") or {}
    meta_filt = active_filter_fields(state.get("rag_metadata_filter") or {})
    needs = bool(slot.get("needs_rag"))
    task_id = state.get("task_id") or ""
    session_id = state.get("session_id") or runtime.session_id
    trace_id = state.get("trace_id") or runtime.trace_id
    _LOG.info(
        "[AI问答-LangGraph|chat_graph_nodes.node_rag_decision|session:%s|硬编执行|进入] "
        "needs_rag=%s; rag_prefetch=%s; task_id=%s",
        session_id,
        needs,
        runtime.rag_prefetch,
        (task_id or "")[:16],
    )

    enhance = state.get("enhancement_snapshot") or {}
    intent_snap = state.get("intent_rewrite_snapshot") or {}
    preset_kw: List[str] = []
    for src in (
        enhance.get("search_keyword_queries"),
        slot.get("retrieval_terms"),
        intent_snap.get("keywords"),
        intent_snap.get("query_keywords"),
    ):
        if isinstance(src, list):
            for x in src:
                s = str(x or "").strip()
                if s and s not in preset_kw:
                    preset_kw.append(s)

    # 已有检索词或 RAG 筛选已确认：自动走检索，不再弹「检索词确认」HITL
    if needs and (preset_kw or state.get("rag_filter_confirmed")):
        slot = dict(slot)
        terms = list(preset_kw[:12])
        if not terms:
            for src in (intent_snap.get("query_keywords"), intent_snap.get("keywords")):
                if isinstance(src, list):
                    for x in src:
                        s = str(x or "").strip()
                        if s and s not in terms:
                            terms.append(s)
        if not terms:
            q = str(
                slot.get("rewritten_query") or state.get("rewritten_query") or state.get("message") or ""
            ).strip()
            if q:
                terms = [q[:120]]
        slot["retrieval_terms"] = terms
        _LOG.info(
            "[AI问答-LangGraph|chat_graph_nodes.node_rag_decision|session:%s|硬编执行|跳过HITL] "
            "auto_rag_confirm; keyword_count=%s; rag_filter_confirmed=%s",
            session_id,
            len(terms),
            bool(state.get("rag_filter_confirmed")),
        )
    elif needs:
        payload = {
            "kind": "rag_confirm",
            "query": slot.get("rewritten_query") or state.get("message"),
            "keywords": slot.get("retrieval_terms") or preset_kw,
            "ui": "form",
        }
        user = interrupt(payload)
        if isinstance(user, dict) and str(user.get("action")).lower() == "pause":
            return {"paused": True, "graph_route": "paused", **_sse_events_from_runtime(runtime)}
        if isinstance(user, dict) and user.get("keywords"):
            slot = dict(slot)
            slot["retrieval_terms"] = user["keywords"]

    framework = state.get("framework") or "react"
    route = "plan" if framework == "plan_execute" else "execute"
    rag_hits: List[Dict[str, Any]] = []
    rag_err = ""
    rag_query = ""
    meta_filt_applied: Dict[str, str] = dict(meta_filt) if meta_filt else {}
    filter_degraded = False
    if needs and task_id:
        from .web_search_plan import build_rag_retrieve_query

        rag_query = build_rag_retrieve_query(
            rewritten_query=(
                slot.get("rewritten_query")
                or state.get("rewritten_query")
                or state.get("message")
                or runtime.message
                or ""
            ),
            original_query=state.get("message") or runtime.message or "",
            slot_snapshot=slot,
            enhancement_snapshot=enhance,
        ).strip()
    if needs and task_id and rag_query:
        runtime.emit(
            "pipeline_progress",
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "stage": "知识库预检索中…",
                "detail": "rag_prefetch",
            },
        )
        t0 = time.perf_counter()
        rag_hits, rag_err = await _safe_kb_search(
            str(rag_query).strip(),
            top_k=5,
            span_ctx={
                "session_id": session_id,
                "task_id": task_id,
                "trace_id": trace_id,
            },
            metadata_filter=meta_filt or None,
        )
        # 降级策略 #1：元数据硬筛召回为 0 时去掉筛选重试
        if meta_filt and not rag_err and not rag_hits:
            filter_degraded = True
            _LOG.info(
                "[AI问答-LangGraph|chat_graph_nodes.node_rag_decision|session:%s|硬编执行|筛选降级] "
                "metadata_filter=%s; retry_without_filter=true",
                session_id,
                meta_filt_applied,
            )
            rag_hits, rag_err = await _safe_kb_search(
                str(rag_query).strip(),
                top_k=5,
                span_ctx={
                    "session_id": session_id,
                    "task_id": task_id,
                    "trace_id": trace_id,
                },
                metadata_filter=None,
            )
        _LOG.info(
            "[AI问答-LangGraph|chat_graph_nodes.node_rag_decision|session:%s|硬编执行|预取] "
            "ok=%s; hit_count=%s; cost_ms=%s; filter_degraded=%s; error=%s",
            session_id,
            not rag_err,
            len(rag_hits),
            int((time.perf_counter() - t0) * 1000),
            filter_degraded,
            (rag_err or "")[:120],
        )

    from .rag_slice_utils import build_rag_llm_blocks, normalize_rag_slices

    rag_slices = normalize_rag_slices(rag_hits)
    rag_ctx_block, rag_cite_block = build_rag_llm_blocks(
        rag_slices,
        prefetch_error=rag_err,
        rag_query=str(rag_query or ""),
    )
    if rag_slices:
        runtime.emit(
            "rag_prefetch_slices",
            {
                "trace_id": trace_id,
                "task_id": task_id or "",
                "rag_query": str(rag_query or "")[:300],
                "slice_count": len(rag_slices),
                "slices": rag_slices,
                "prefetch_error": rag_err[:300] if rag_err else "",
            },
        )

    rag_out = {
        "needs_rag": needs,
        "rag_query": str(rag_query)[:300],
        "prefetch_count": len(rag_hits),
        "prefetch_error": rag_err[:300] if rag_err else "",
        "metadata_filter": meta_filt_applied if needs else {},
        "metadata_filter_degraded": filter_degraded,
        "rag_slices": rag_slices,
        "citation_instruction": rag_cite_block,
    }
    seq: Dict[str, Any] = {}
    # 无需知识库检索时不向前端推送「RAG 决策」步骤组（避免空 IO / 误导性绿点）
    if needs:
        seq = _emit_orchestration_step(
            runtime,
            state,
            trace_id=trace_id,
            task_id=task_id,
            step_name="RAG 决策",
            phase="rag_decision",
            result_brief=(
                (
                    f"需要检索，已预取 {len(rag_hits)} 条"
                    + ("（元数据筛选无结果，已去掉筛选重试）" if filter_degraded else "")
                )
                if not rag_err
                else "需要检索（预取失败）"
            ),
            input_payload={
                "slot_snapshot": slot,
                "enhancement_snapshot": state.get("enhancement_snapshot") or {},
                "needs_rag": needs,
            },
            output_payload=rag_out,
            executed=True,
        )
    return {
        "orchestration_phase": PHASE_RAG_DECISION,
        "needs_rag": needs,
        "rag_confirmed": True,
        "slot_snapshot": slot,
        "rag_slices": rag_slices,
        "rag_citation_instruction": rag_cite_block,
        "rag_context_block": rag_ctx_block or state.get("rag_context_block"),
        "rag_prefetch_done": bool(needs),
        "graph_route": route,
        "group_seq": seq.get("group_seq"),
        "orch_chain": seq.get("orch_chain", []),
        **_sse_events_from_runtime(runtime),
    }


async def node_plan_detect(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """Plan-Execute：生成计划表（单次 LLM）。"""
    runtime = _runtime_from_state_or_config(state, config)
    message = state.get("message") or runtime.message
    steps: List[Dict[str, Any]] = []
    plan_pre = ""
    plan_llm_text = ""
    if runtime.api_key and runtime.model_resolved:
        plan_pre = _new_id("step_")
        plan_llm_text, _ = await _orch_llm_invoke(
            runtime,
            "plan",
            message,
            intent_snapshot=state.get("intent_rewrite_snapshot"),
            max_tokens=640,
            step_id=plan_pre,
            stream_think=True,
            trace_id=state.get("trace_id") or runtime.trace_id,
            task_id=state.get("task_id") or "",
            step_name="任务分解",
        )
        for i, line in enumerate([ln.strip() for ln in plan_llm_text.splitlines() if ln.strip()][:8], start=1):
            steps.append({"index": i, "title": line[:200], "done": False})
    if not steps:
        steps = state.get("plan_steps") or [
            {"index": 1, "title": "读取并核验相关资料", "done": False},
            {"index": 2, "title": "整理可引用依据", "done": False},
            {"index": 3, "title": "形成答复要点", "done": False},
        ]
    titles = " → ".join(str(s.get("title") or "")[:36] for s in steps[:4] if s.get("title"))
    seq = _emit_orchestration_step(
        runtime,
        state,
        trace_id=state.get("trace_id") or runtime.trace_id,
        task_id=state.get("task_id") or "",
        step_name="任务分解",
        phase="plan",
        result_brief=f"共 {len(steps)} 步：{titles}" if titles else f"共 {len(steps)} 步待办",
        extra={"plan_steps": steps},
        think_text_override=plan_llm_text or None,
        llm_powered=bool(plan_llm_text),
        pre_step_id=plan_pre or None,
        think_streamed=bool(plan_pre and plan_llm_text),
    )
    return {
        "orchestration_phase": PHASE_PLAN,
        "plan_steps": steps,
        "plan_cursor": 0,
        "graph_route": "execute",
        "group_seq": seq.get("group_seq"),
        **_sse_events_from_runtime(runtime),
    }


async def node_react_entry(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    """进入执行段：编排收尾思考 + handoff（执行段再加载 MCP 并调用工具）。"""
    runtime = _runtime_from_state_or_config(state, config)
    trace_id = state.get("trace_id") or runtime.trace_id
    task_id = str(state.get("task_id") or "").strip()
    session_id = state.get("session_id") or runtime.session_id
    # 延续主任务：跳过「执行准备」步骤组；但须补 RAG 预取（编排 rag_decision 未走）
    if str(state.get("graph_route") or "").strip() == "continue_execute":
        rag_ctx = str(state.get("rag_context_block") or "")
        rag_slices = state.get("rag_slices") if isinstance(state.get("rag_slices"), list) else []
        needs_rag = bool(state.get("needs_rag")) and bool(runtime.rag_prefetch)
        if needs_rag and task_id and not rag_slices:
            from .web_search_plan import build_rag_retrieve_query

            slot = state.get("slot_snapshot") or {}
            enhance = state.get("enhancement_snapshot") or {}
            intent_snap = state.get("intent_rewrite_snapshot") or {}
            rag_query = build_rag_retrieve_query(
                rewritten_query=str(
                    intent_snap.get("rewritten_query")
                    or state.get("rewritten_query")
                    or state.get("message")
                    or runtime.message
                    or ""
                ).strip(),
                original_query=state.get("message") or runtime.message or "",
                slot_snapshot=slot,
                enhancement_snapshot=enhance,
            ).strip()
            if rag_query:
                t0 = time.perf_counter()
                rag_hits, rag_err = await _safe_kb_search(
                    rag_query,
                    top_k=5,
                    span_ctx={"session_id": session_id, "task_id": task_id, "trace_id": trace_id},
                )
                from .rag_slice_utils import build_rag_llm_blocks, normalize_rag_slices

                rag_slices = normalize_rag_slices(rag_hits)
                rag_ctx, rag_cite = build_rag_llm_blocks(
                    rag_slices,
                    prefetch_error=rag_err,
                    rag_query=rag_query,
                )
                if rag_slices:
                    runtime.emit(
                        "rag_prefetch_slices",
                        {
                            "trace_id": trace_id,
                            "task_id": task_id,
                            "rag_query": rag_query[:300],
                            "slice_count": len(rag_slices),
                            "slices": rag_slices,
                            "prefetch_error": rag_err[:300] if rag_err else "",
                        },
                    )
                _LOG.info(
                    "[AI问答-LangGraph|chat_graph_nodes.node_react_entry|session:%s|硬编执行|续接RAG] "
                    "hit_count=%s; cost_ms=%s; error=%s",
                    session_id,
                    len(rag_slices),
                    int((time.perf_counter() - t0) * 1000),
                    (rag_err or "")[:120],
                )
                return {
                    "orchestration_phase": PHASE_REACT,
                    "graph_route": "handoff_execute",
                    "execution_done": False,
                    "react_round": 0,
                    "rag_slices": rag_slices,
                    "rag_context_block": rag_ctx,
                    "rag_citation_instruction": rag_cite,
                    "needs_rag": True,
                    **_sse_events_from_runtime(runtime),
                }
        _LOG.info(
            "[AI问答-LangGraph|chat_graph_nodes.node_react_entry|session:%s|硬编执行|handoff] "
            "continue_main_skip_prep",
            runtime.session_id,
        )
        return {
            "orchestration_phase": PHASE_REACT,
            "graph_route": "handoff_execute",
            "execution_done": False,
            "react_round": 0,
            "rag_slices": rag_slices,
            "rag_context_block": rag_ctx,
            "needs_rag": needs_rag,
            **_sse_events_from_runtime(runtime),
        }
    plan_steps = list(state.get("plan_steps") or [])
    enhance = state.get("enhancement_snapshot") or {}
    prep = emit_orchestration_step(
        runtime,
        state,
        trace_id=state.get("trace_id") or runtime.trace_id,
        task_id=state.get("task_id") or "",
        step_name="执行准备",
        phase="execute_prep",
        result_brief="固定编排完成，handoff 至检索预取与 ReAct",
        input_payload={
            "plan_steps": plan_steps,
            "enhancement_snapshot": enhance,
            "web_search": bool(runtime.web_search),
            "rag_prefetch": bool(runtime.rag_prefetch),
            "read_comments": bool(runtime.read_comments),
        },
        output_payload={
            "plan_steps": plan_steps,
            "sub_task_count": len(plan_steps),
            "verification_points": enhance.get("verification_points") or [],
        },
    )
    _LOG.info(
        "[AI问答-LangGraph|chat_graph_nodes.node_react_entry|session:%s|硬编执行|handoff] "
        "进入执行段",
        runtime.session_id,
    )
    return {
        "orchestration_phase": PHASE_REACT,
        "graph_route": "handoff_execute",
        "execution_done": False,
        "react_round": 0,
        "orch_chain": prep.get("orch_chain", []),
        "group_seq": prep.get("group_seq", state.get("group_seq")),
        **_sse_events_from_runtime(runtime),
    }


async def node_abnormal_finalize(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    runtime = _runtime_from_state_or_config(state, config)
    msg = state.get("error_message") or "任务进入异常收敛态，请检查工具权限或重试。"
    task_id = state.get("task_id") or ""
    if task_id:
        ai_chat._span_update(task_id, status=PARENT_ABNORMAL)
    runtime.emit(
        "task_completed",
        {
            "task_id": task_id,
            "status": PARENT_ABNORMAL,
            "persist_main_task": bool(state.get("use_main_task")),
            "error_message": msg,
        },
    )
    return {
        "orchestration_phase": PHASE_ABNORMAL,
        "abnormal": True,
        "graph_route": "done",
        "final_answer": msg,
        **_sse_events_from_runtime(runtime),
    }


async def node_paused(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
    runtime = _runtime_from_state_or_config(state, config)
    runtime.emit(
        "graph_paused",
        {"task_id": state.get("task_id") or "", "message": "流程已暂停，可恢复或编辑状态后继续。"},
    )
    return {
        "orchestration_phase": PHASE_PAUSED,
        "paused": True,
        "graph_route": "done",
        **_sse_events_from_runtime(runtime),
    }
