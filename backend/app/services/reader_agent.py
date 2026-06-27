"""辅助阅读 Agent — 基于当前文档的简单问答（深度思考 + 回答，无 LangGraph/ReAct）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from .ai_chat import (
    _async_iter_llm_token_stream,
    _sse,
    load_chat_llm_config,
    resolve_chat_api_credentials,
)
from .config import load_config
from .reader_session_store import get_session, upsert_session, flush_session

_LOG = logging.getLogger("sba.reader_agent")

_DOC_MAX_CHARS = 5_000  # 回答注入上限；更大文档走关键词摘录
_THINK_DOC_MAX_CHARS = 2_400  # 深度思考摘录（先于回答流式输出，给用户即时反馈）
_OVERVIEW_Q_RE = re.compile(
    r"讲什么|讲啥的|说什么|概述|总结|大意|主旨|讲了啥|内容是什么|这篇文章|全文|主要讲",
    re.I,
)
_CASUAL_Q_RE = re.compile(
    r"为啥|为什么|为啥不|烦不|能不能|是不是|咋|难道|有必要吗|行不行|直接拿|干嘛要|图啥",
    re.I,
)
_FORMAL_REPORT_Q_RE = re.compile(
    r"总结|概述|梳理全文|分析报告|要点提炼|归纳全文|读后感|结构化",
    re.I,
)
_THINK_STEP = "reader_deep_think"
_ANSWER_STEP = "reader_answer"
_COLOR_MARKER_FOOTER_RE = re.compile(
    r"\n={10,}\s*\n\s*COUNT\s*:\s*\d+.*$",
    re.DOTALL | re.IGNORECASE,
)


def _doc_content_fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def resolve_doc_text_for_chat(
    *,
    doc_name: str,
    doc_text: str,
    doc_version: Optional[int] = None,
    local_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """
    文档预读：按磁盘 mtime 版本决定是否重读 output 文件。

    - doc_version：客户端已知的磁盘 mtime（毫秒）
    - local_revision：编辑器未保存修订计数；>0 时优先使用客户端 doc_text
    """
    client_text = _strip_color_marker_footer(doc_text or "")
    name = (doc_name or "").strip()
    out: Dict[str, Any] = {
        "text": client_text,
        "version": int(doc_version or 0),
        "refreshed": False,
        "source": "client",
    }
    if not name:
        return out
    try:
        from .output_file_io import output_file_mtime_ms, read_output_file, safe_output_basename

        base = safe_output_basename(name)
    except ValueError:
        return out

    disk_mtime = output_file_mtime_ms(base)
    if disk_mtime is None:
        return out

    has_unsaved = int(local_revision or 0) > 0

    def _load_disk_body() -> str:
        payload = read_output_file(base)
        return _strip_color_marker_footer(str(payload.get("content") or ""))

    try:
        if disk_mtime > int(doc_version or 0):
            if has_unsaved:
                # 磁盘已更新但编辑器有未保存内容：仍用客户端正文，仅同步版本号供下次比对
                out["version"] = disk_mtime
                out["source"] = "client_unsaved_over_disk"
                _LOG.info(
                    "[辅助阅读-文档预读|reader_agent.resolve_doc_text_for_chat|%s|硬编执行|跳过磁盘] "
                    "local_revision=%s; disk_mtime=%s; client_version=%s",
                    base,
                    local_revision,
                    disk_mtime,
                    doc_version,
                )
                return out
            disk_body = _load_disk_body()
            out.update(
                text=disk_body,
                version=disk_mtime,
                refreshed=True,
                source="disk_mtime",
            )
            _LOG.info(
                "[辅助阅读-文档预读|reader_agent.resolve_doc_text_for_chat|%s|硬编执行|重读磁盘] "
                "disk_mtime=%s; client_version=%s; chars=%s",
                base,
                disk_mtime,
                doc_version,
                len(disk_body),
            )
            return out

        if not has_unsaved:
            disk_body = _load_disk_body()
            if _doc_content_fingerprint(disk_body) != _doc_content_fingerprint(client_text):
                out.update(
                    text=disk_body,
                    version=disk_mtime,
                    refreshed=True,
                    source="disk_hash",
                )
                _LOG.info(
                    "[辅助阅读-文档预读|reader_agent.resolve_doc_text_for_chat|%s|硬编执行|哈希不一致重读] "
                    "chars=%s",
                    base,
                    len(disk_body),
                )
                return out

        out["version"] = disk_mtime
    except Exception as ex:
        _LOG.warning(
            "[辅助阅读-文档预读|reader_agent.resolve_doc_text_for_chat|%s|硬编执行|重读失败] "
            "error_type=%s; error_message=%s",
            base,
            type(ex).__name__,
            ex,
        )
        out["version"] = disk_mtime
    return out


def _default_agent_layers() -> Dict[str, str]:
    return {
        "reader_system_prompt": (
            "你是 SuperBizAgent 辅助阅读 Agent（reader_agent）。"
            "用户正在阅读一篇文档，你的职责是帮助理解、梳理、对比与延伸，"
            "回答必须优先依据【当前文档】与对话历史；不确定处明确标注。"
        ),
        "reader_role_task": (
            "角色：文档阅读助教。\n"
            "任务：解答与当前文档相关的问题；必要时结合 RAG/联网补充，但不得覆盖文档原文结论。"
        ),
        "reader_action_framework": (
            "执行框架：COT（链式思考）单轮。\n"
            "1) 定位文档中与问题相关的段落；2) 归纳要点；3) 组织回答。"
        ),
        "reader_standards_must": (
            "必须：简体中文；先直接回应用户原话里的疑问，再展开；"
            "用户粘贴段落+追问时，只答该段与追问，勿复述无关章节；"
            "能从段落逻辑推出的就直说，勿用「文档未直接解答」敷衍；"
            "若文档未涉及则一句点明；开启 RAG/联网时区分「文档」与「外部资料」。"
            "【当前文档】在每次提问前会按磁盘版本自动同步，以最新正文为准，勿声称只能看到旧快照。"
        ),
        "reader_output_template": (
            "默认口语直答：2~6 句或短条目，像同事讲解。"
            "仅当用户明确要求「总结/概述/分析报告」时，才用【结论】+条目要点；"
            "禁止对随口追问套【结论】【依据】【补充】【待确认】四段公文模板。"
        ),
        "reader_no_doing": (
            "禁止：编造文档未出现的人名/数据/结论；"
            "禁止假装已调用未开启的工具；"
            "禁止输出 ### Thought/Action 等 ReAct 标记；"
            "禁止代替用户做未请求的改写/翻译全文；"
            "禁止堆砌【补充】【待确认】凑字数；禁止回避用户「为啥/烦不」类直问。"
        ),
    }


def _agent_fields_from_config() -> Dict[str, str]:
    cfg = load_config() or {}
    defaults = _default_agent_layers()
    out = dict(defaults)
    for k in defaults:
        v = str(cfg.get(k) or "").strip()
        if v:
            out[k] = v
    return out


def _strip_color_marker_footer(text: str) -> str:
    return _COLOR_MARKER_FOOTER_RE.sub("", text or "").strip()


def _question_terms(question: str) -> List[str]:
    q = (question or "").strip().lower()
    if not q:
        return []
    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]{3,}", q)
    stop = {"什么", "怎么", "为什么", "是否", "可以", "这个", "那个", "一下", "请问", "文档", "文章"}
    return [p for p in parts if p not in stop][:24]


def _is_overview_question(question: str) -> bool:
    return bool(_OVERVIEW_Q_RE.search((question or "").strip()))


def _wants_formal_report(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _CASUAL_Q_RE.search(q):
        return False
    return bool(_FORMAL_REPORT_Q_RE.search(q))


def _answer_style_hint(question: str) -> str:
    if _wants_formal_report(question):
        return (
            "\n## 本轮回答模式：结构化摘要\n"
            "先一句话概括，再用条目列要点；不必凑【补充】【待确认】。\n"
        )
    return (
        "\n## 本轮回答模式：口语直答（默认）\n"
        "1. 第一句直接回应用户原话（如「为啥不…」「烦不烦」），口语、干脆。\n"
        "2. 总共 2~6 句或 3~5 条短 bullet，说清原因即可。\n"
        "3. 禁止套【结论】【依据】【补充】【待确认】四段模板。\n"
        "4. 用户贴了段落时，只答段落+追问，不要扩写成整篇导读。\n"
    )


def _think_user_instruction(question: str) -> str:
    q = (question or "").strip()
    if _wants_formal_report(q):
        return f"请用 3~6 条短条目梳理阅读思路（暂不给最终结论）：\n{q}"
    return (
        f"用户原话：\n{q}\n\n"
        "用 3~5 条口语短句写下你会怎么直接回答他的疑问。"
        "不要报告体、不要【结论】标签、不要「待核对」「预判后续」等套话。"
    )


def _extract_doc_excerpt(doc_text: str, question: str, *, max_chars: int) -> tuple[str, bool]:
    """按问题关键词截取相关段落，降低 Ark 首 token 延迟（输入 token 越少 TTFT 越低）。"""
    raw = _strip_color_marker_footer(doc_text or "")
    if len(raw) <= max_chars:
        return raw, False
    # 概览类问题：优先文首结构化段落，避免只命中元数据/页脚
    if _is_overview_question(question):
        head = raw[: max_chars - 80]
        if len(raw) > max_chars:
            head += "\n\n…（后文已省略，回答阶段会结合更多摘录）…"
        return head, True
    terms = _question_terms(question)
    blocks = [b.strip() for b in re.split(r"\n{2,}", raw) if b.strip()]
    if not blocks:
        return raw[:max_chars], True
    if not terms:
        head = raw[: max_chars // 2]
        tail = raw[-(max_chars // 2) :]
        return head + "\n\n…（中段省略）…\n\n" + tail, True

    def score(block: str) -> int:
        low = block.lower()
        return sum(1 for t in terms if t in low or t.lower() in low)

    ranked = sorted(range(len(blocks)), key=lambda i: score(blocks[i]), reverse=True)
    picked: List[str] = []
    used = 0
    for idx in ranked:
        if score(blocks[idx]) <= 0 and picked:
            continue
        block = blocks[idx]
        if used + len(block) + 2 > max_chars:
            remain = max_chars - used - 16
            if remain > 200:
                picked.append(block[:remain] + "…")
                used = max_chars
            break
        picked.append(block)
        used += len(block) + 2
        if used >= max_chars:
            break
    if not picked:
        return raw[:max_chars], True
    body = "\n\n".join(picked)
    if len(body) > max_chars:
        body = body[:max_chars]
    return body, True


def build_reader_system_prompt(
    *,
    doc_name: str,
    doc_text: str,
    extra_context: str = "",
    question: str = "",
    doc_max_chars: int = _DOC_MAX_CHARS,
) -> str:
    f = _agent_fields_from_config()
    body, truncated = _extract_doc_excerpt(doc_text or "", question, max_chars=doc_max_chars)
    parts = [
        f["reader_system_prompt"],
        "\n## 角色与任务\n",
        f["reader_role_task"],
        "\n## 动作框架\n",
        f["reader_action_framework"],
        "\n## 规范（必须）\n",
        f["reader_standards_must"],
        "\n## 输出格式\n",
        f["reader_output_template"],
        "\n## 禁止（NOT DO）\n",
        f["reader_no_doing"],
        f"\n## 当前文档\n文件名：{doc_name or '未命名'}\n",
    ]
    if truncated:
        parts.append(f"（正文已按问题摘录，上限 {doc_max_chars} 字符）\n")
    parts.append("---\n")
    parts.append(body)
    if extra_context.strip():
        parts.append("\n\n## 检索补充\n")
        parts.append(extra_context.strip()[:20000])
    if question.strip():
        parts.append(_answer_style_hint(question))
    return "".join(parts)


_PREFETCH_WAIT_SEC = 0.35  # 无 RAG/联网时最多等待
_PREFETCH_WEB_WAIT_SEC = 18.0
_PREFETCH_RAG_WAIT_SEC = 12.0


async def _reader_run_prefetch(
    msg: str,
    *,
    rag_prefetch: bool,
    web_search: bool,
    sse_side: Optional[asyncio.Queue] = None,
    trace_id: str = "",
) -> tuple[str, Dict[str, Any], List[Dict[str, Any]], str, List[str]]:
    """与 AI 对话同源：关键词抽取 → RAG/联网预取 → LLM 补充块。"""
    extra_parts: List[str] = []
    search_results: Dict[str, Any] = {}
    rag_slices: List[Dict[str, Any]] = []
    rag_query = ""
    keyword_queries: List[str] = []

    async def _emit(ev: str, payload: Dict[str, Any]) -> None:
        if sse_side is None:
            return
        body = dict(payload)
        if trace_id:
            body.setdefault("trace_id", trace_id)
        await sse_side.put(_sse(ev, body))

    if rag_prefetch:
        from .chat_graph_nodes import _safe_kb_search
        from .rag_slice_utils import build_rag_llm_blocks, normalize_rag_slices
        from .web_search_plan import build_rag_retrieve_query, build_rag_search_keyword_queries

        keyword_queries = build_rag_search_keyword_queries(msg, original_query=msg)
        rag_query = build_rag_retrieve_query(
            rewritten_query=msg,
            original_query=msg,
        ).strip()
        if rag_query:
            hits, rag_err = await _safe_kb_search(rag_query, top_k=5, timeout_sec=12.0)
            rag_slices = normalize_rag_slices(hits)
            rag_ctx, rag_cite = build_rag_llm_blocks(
                rag_slices,
                prefetch_error=rag_err,
                rag_query=rag_query,
            )
            if rag_ctx:
                extra_parts.append(rag_ctx)
            if rag_cite:
                extra_parts.append(rag_cite)
            search_results["rag"] = {
                "slices": rag_slices,
                "query": rag_query,
                "search_keyword_queries": keyword_queries,
                "error": rag_err[:300] if rag_err else "",
            }

    if web_search:
        from .web_search import web_search_multi_for_chat
        from .web_search_plan import build_web_search_plan

        plan = build_web_search_plan(rewritten_query=msg, original_query=msg)
        queries = [str(q or "").strip() for q in (plan.get("search_queries") or []) if str(q or "").strip()]
        await _emit(
            "web_search_start",
            {
                "label": "联网搜索",
                "search_queries": queries,
                "llm_powered": False,
            },
        )
        web_payload: Dict[str, Any] = {
            "results": [],
            "search_queries": queries,
            "error": "",
            "per_query": [],
            "provider": "",
        }
        try:
            for q in queries[:3]:
                await _emit(
                    "web_search_progress",
                    {"query": q, "label": f"搜索：{q[:48]}", "llm_powered": False},
                )
            merged = await asyncio.to_thread(
                web_search_multi_for_chat,
                queries,
                max_results_per_query=4,
                objective=msg[:120],
            )
            if isinstance(merged, dict):
                web_payload = {
                    "results": merged.get("results") or [],
                    "search_queries": merged.get("search_queries") or queries,
                    "error": str(merged.get("error") or "")[:300],
                    "per_query": merged.get("per_query") or [],
                    "provider": str(merged.get("provider") or ""),
                }
        except Exception as ex:
            web_payload["error"] = str(ex)[:300]
        search_results["web"] = web_payload
        await _emit(
            "web_search_results",
            {
                "web": web_payload,
                "result_count": len(web_payload.get("results") or []),
                "llm_powered": False,
            },
        )
        results = web_payload.get("results") or []
        if results:
            lines = ["### 联网搜索结果（外部资料，与文档区分引用）"]
            for i, r in enumerate(results[:6], 1):
                if not isinstance(r, dict):
                    continue
                lines.append(
                    f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {str(r.get('snippet', ''))[:300]}"
                )
            extra_parts.append("\n".join(lines))
        elif queries:
            err = str(web_payload.get("error") or "").strip()
            hint = "检索词：" + "；".join(queries[:3])
            if err:
                hint += f"\n（检索异常：{err}）"
            extra_parts.append("### 联网搜索\n" + hint)

    return "\n\n".join(extra_parts), search_results, rag_slices, rag_query, keyword_queries


def _drain_sse_queue(q: asyncio.Queue) -> List[str]:
    out: List[str] = []
    while True:
        try:
            item = q.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(item, str):
            out.append(item)
    return out


async def stream_reader_chat(
    *,
    doc_id: str,
    doc_name: str,
    doc_text: str,
    message: str,
    rag_prefetch: bool = False,
    web_search: bool = False,
    deep_think: bool = False,
    model: Optional[str] = None,
    doc_version: Optional[int] = None,
    local_revision: Optional[int] = None,
) -> AsyncIterator[str]:
    """SSE：即时反馈 + 预取/思考并行，首字尽快输出（与 AI 对话 RAG/联网同源）。"""
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    msg = (message or "").strip()
    if not msg:
        yield _sse("error", {"trace_id": trace_id, "error": "message 不能为空"})
        return

    # 立刻推送，避免客户端空等（加载动效由 typing_start 驱动）
    yield _sse("typing_start", {"trace_id": trace_id, "doc_id": doc_id, "llm_powered": True})
    yield _sse("answer_start", {"trace_id": trace_id, "stream_mode": "token", "llm_powered": True})
    yield _sse("answer_generating", {"trace_id": trace_id, "label": "正在准备…", "llm_powered": True})

    cfg = load_chat_llm_config()
    creds = resolve_chat_api_credentials(cfg)
    api_key = creds.get("api_key") or ""
    base_url = creds.get("base_url") or ""
    provider = creds.get("provider") or "ark"
    use_model = (model or "").strip() or creds.get("model") or ""
    if not api_key or not use_model:
        yield _sse("error", {
            "trace_id": trace_id,
            "error": "未配置 LLM：请在 config.json 填写 volcengine_api_key 与 ai_chat_model",
        })
        return

    doc_res = resolve_doc_text_for_chat(
        doc_name=doc_name,
        doc_text=doc_text,
        doc_version=doc_version,
        local_revision=local_revision,
    )
    doc_text = str(doc_res.get("text") or "")
    if not doc_text.strip():
        yield _sse("error", {"trace_id": trace_id, "error": "文档正文为空，请先打开或保存文档"})
        return
    if doc_res.get("refreshed"):
        yield _sse(
            "doc_snapshot_refreshed",
            {
                "trace_id": trace_id,
                "doc_name": doc_name,
                "doc_version": doc_res.get("version"),
                "source": doc_res.get("source"),
                "label": "已从磁盘同步最新文档",
                "llm_powered": False,
            },
        )

    session = get_session(doc_id, doc_name=doc_name)
    history: List[Dict[str, str]] = []
    for m in session.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})

    # 预取（限时）与深度思考：开启深度思考时先流式输出思考，再生成回答（用户可见思考过程）
    sse_side: asyncio.Queue = asyncio.Queue()
    think_text = ""
    think_step_id = f"{_THINK_STEP}_{trace_id[:6]}"

    async def run_deep_think_stream() -> AsyncIterator[str]:
        """先思考、后回答：短摘录 + 流式 step_think_delta，降低首屏空等感。"""
        nonlocal think_text
        yield _sse(
            "answer_generating",
            {"trace_id": trace_id, "label": "深度思考中…", "llm_powered": True},
        )
        yield _sse(
            "thought_step_start",
            {
                "trace_id": trace_id,
                "step_id": think_step_id,
                "step_name": "深度思考",
                "phase": "deep",
                "llm_powered": True,
            },
        )
        think_system = build_reader_system_prompt(
            doc_name=doc_name,
            doc_text=doc_text,
            extra_context="",
            question=msg,
            doc_max_chars=_THINK_DOC_MAX_CHARS,
        )
        think_msgs = [
            {"role": "system", "content": think_system},
            *history[-6:],
            {"role": "user", "content": _think_user_instruction(msg)},
        ]
        try:
            async for tok in _async_iter_llm_token_stream(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=use_model,
                messages=think_msgs,
                temperature=0.4,
                max_tokens=420,
                thinking_enabled=False,
            ):
                think_text += tok
                yield _sse(
                    "step_think_delta",
                    {
                        "trace_id": trace_id,
                        "step_id": think_step_id,
                        "content": tok,
                        "llm_powered": True,
                    },
                )
        except Exception as ex:
            think_text = f"（思考阶段异常：{ex}）"
            yield _sse(
                "step_think_delta",
                {
                    "trace_id": trace_id,
                    "step_id": think_step_id,
                    "content": think_text,
                    "llm_powered": False,
                    "error": str(ex),
                },
            )
        yield _sse(
            "thought_step_end",
            {
                "trace_id": trace_id,
                "step_id": think_step_id,
                "step_name": "深度思考",
                "output_text": think_text,
                "llm_powered": True,
            },
        )

    async def prefetch_worker() -> tuple[str, Dict[str, Any], List[Dict[str, Any]], str, List[str]]:
        if not (rag_prefetch or web_search):
            return "", {}, [], "", []
        await sse_side.put(
            _sse(
                "prefetch_segment_start",
                {"trace_id": trace_id, "label": "检索预取", "llm_powered": False},
            )
        )
        extra, search_results, rag_slices, rag_query, kw = await _reader_run_prefetch(
            msg,
            rag_prefetch=rag_prefetch,
            web_search=web_search,
            sse_side=sse_side,
            trace_id=trace_id,
        )
        if rag_slices:
            rag_err = str((search_results.get("rag") or {}).get("error") or "")
            await sse_side.put(
                _sse(
                    "rag_prefetch_slices",
                    {
                        "trace_id": trace_id,
                        "rag_query": rag_query[:300],
                        "search_keyword_queries": kw,
                        "slice_count": len(rag_slices),
                        "slices": rag_slices,
                        "prefetch_error": rag_err[:300],
                    },
                )
            )
        await sse_side.put(
            _sse("prefetch_segment_end", {"trace_id": trace_id, "llm_powered": False})
        )
        return extra, search_results, rag_slices, rag_query, kw

    prefetch_task = asyncio.create_task(prefetch_worker()) if (rag_prefetch or web_search) else None

    extra = ""
    search_results: Dict[str, Any] = {}
    prefetch_timeout = _PREFETCH_WAIT_SEC
    if web_search:
        prefetch_timeout = max(prefetch_timeout, _PREFETCH_WEB_WAIT_SEC)
    elif rag_prefetch:
        prefetch_timeout = max(prefetch_timeout, _PREFETCH_RAG_WAIT_SEC)

    async def _yield_side_queue() -> AsyncIterator[str]:
        for chunk in _drain_sse_queue(sse_side):
            yield chunk

    async def _wait_prefetch_result() -> None:
        nonlocal extra, search_results
        if not prefetch_task:
            return
        if prefetch_task.done():
            try:
                extra, search_results, _, _, _ = prefetch_task.result()
            except Exception:
                pass
            return
        try:
            extra, search_results, _, _, _ = await asyncio.wait_for(
                asyncio.shield(prefetch_task),
                timeout=prefetch_timeout,
            )
        except asyncio.TimeoutError:
            pass
        if prefetch_task.done() and not search_results:
            try:
                extra, search_results, _, _, _ = prefetch_task.result()
            except Exception:
                pass

    if deep_think:
        think_first_ms: Optional[int] = None
        think_iter = run_deep_think_stream().__aiter__()
        think_done = False
        prefetch_deadline = (
            time.perf_counter() + prefetch_timeout if prefetch_task else time.perf_counter()
        )
        # 深度思考与 RAG/联网预取并行：思考 token 立即 SSE，不再等预取 18s
        while not think_done or (prefetch_task and not prefetch_task.done()):
            async for side_chunk in _yield_side_queue():
                yield side_chunk
            if not think_done:
                try:
                    think_chunk = await asyncio.wait_for(think_iter.__anext__(), timeout=0.025)
                    if think_first_ms is None and think_text:
                        think_first_ms = int((time.perf_counter() - t0) * 1000)
                        _LOG.info(
                            "[辅助阅读-问答|reader_agent.stream_reader_chat|%s|Agent执行|思考首字] "
                            "think_first_token_ms=%s",
                            doc_id,
                            think_first_ms,
                        )
                    yield think_chunk
                except asyncio.TimeoutError:
                    pass
                except StopAsyncIteration:
                    think_done = True
            if prefetch_task and prefetch_task.done():
                break
            if prefetch_task and time.perf_counter() >= prefetch_deadline and think_done:
                break
            if not prefetch_task and think_done:
                break
            await asyncio.sleep(0.02)
        if not think_done:
            async for think_chunk in think_iter:
                if think_first_ms is None and think_text:
                    think_first_ms = int((time.perf_counter() - t0) * 1000)
                yield think_chunk
        await _wait_prefetch_result()
        async for side_chunk in _yield_side_queue():
            yield side_chunk
        if think_text.strip():
            extra = (
                "## 阅读思路（深度思考，供组织回答参考）\n"
                + think_text.strip()[:3000]
                + ("\n\n" + extra if extra else "")
            )
    elif prefetch_task:
        poll_deadline = time.perf_counter() + prefetch_timeout
        while time.perf_counter() < poll_deadline:
            async for side_chunk in _yield_side_queue():
                yield side_chunk
            if prefetch_task.done():
                break
            await asyncio.sleep(0.05)
        await _wait_prefetch_result()
        async for side_chunk in _yield_side_queue():
            yield side_chunk
    else:
        async for side_chunk in _yield_side_queue():
            yield side_chunk

    system_prompt = build_reader_system_prompt(
        doc_name=doc_name,
        doc_text=doc_text,
        extra_context=extra,
        question=msg,
    )
    _LOG.info(
        "[辅助阅读-问答|reader_agent.stream_reader_chat|%s|Agent执行|开答前] "
        "prompt就绪; prompt_chars=%s; deep_think=%s; rag=%s; web=%s",
        doc_id,
        len(system_prompt),
        deep_think,
        rag_prefetch,
        web_search,
    )

    yield _sse(
        "answer_generating",
        {"trace_id": trace_id, "label": "正在生成回答…", "llm_powered": True},
    )

    answer_msgs: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        *history[-20:],
        {"role": "user", "content": msg},
    ]

    full_answer = ""
    streamed = False
    first_token_ms: Optional[int] = None
    try:
        async for tok in _async_iter_llm_token_stream(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=use_model,
            messages=answer_msgs,
            temperature=0.35,
            max_tokens=800,
            thinking_enabled=False,
        ):
            for chunk in _drain_sse_queue(sse_side):
                yield chunk
            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - t0) * 1000)
                _LOG.info(
                    "[辅助阅读-问答|reader_agent.stream_reader_chat|%s|Agent执行|回答首字] "
                    "answer_first_token_ms=%s; deep_think=%s; prompt_chars=%s",
                    doc_id,
                    first_token_ms,
                    deep_think,
                    len(system_prompt),
                )
            streamed = True
            full_answer += tok
            yield _sse(
                "answer_delta",
                {
                    "trace_id": trace_id,
                    "content": tok,
                    "kind": "body",
                    "stream_mode": "token",
                    "llm_powered": True,
                },
            )
    except Exception as ex:
        err = f"回答生成失败：{ex}"
        full_answer = err
        yield _sse(
            "answer_delta",
            {
                "trace_id": trace_id,
                "content": err,
                "kind": "body",
                "stream_mode": "replay",
                "llm_powered": False,
            },
        )

    for chunk in _drain_sse_queue(sse_side):
        yield chunk

    if not streamed and full_answer:
        yield _sse(
            "answer_delta",
            {
                "trace_id": trace_id,
                "content": full_answer,
                "kind": "body",
                "stream_mode": "replay",
                "llm_powered": True,
            },
        )

    total_ms = int((time.perf_counter() - t0) * 1000)
    yield _sse(
        "answer_end",
        {
            "trace_id": trace_id,
            "full_text": full_answer,
            "stream_mode": "token" if streamed else "replay",
            "search_results": search_results,
            "llm_powered": True,
            "total_duration_ms": total_ms,
            "first_token_ms": first_token_ms,
        },
    )

    new_msgs = list(session.get("messages") or [])
    new_msgs.append({"role": "user", "content": msg})
    asst: Dict[str, Any] = {"role": "assistant", "content": full_answer}
    if think_text.strip():
        asst["thinking"] = think_text.strip()
    if search_results:
        asst["search_results"] = search_results
    new_msgs.append(asst)
    upsert_session(
        doc_id,
        doc_name=doc_name,
        messages=new_msgs,
        prefs={
            "rag_prefetch": rag_prefetch,
            "web_search": web_search,
            "deep_think": deep_think,
        },
    )
    flush_session(doc_id)

    yield _sse(
        "reader_session_saved",
        {
            "trace_id": trace_id,
            "doc_id": doc_id,
            "message_count": len(new_msgs),
        },
    )


def get_agent_config_payload() -> Dict[str, Any]:
    fields = _agent_fields_from_config()
    return {"agent_key": "reader_agent", "fields": fields}
