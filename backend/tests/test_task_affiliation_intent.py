"""任务归属优先于 simple/复杂 分流的规则测试。"""
from __future__ import annotations

from app.services.chat_context_memory import (
    extract_task_id_from_message,
    peek_fast_continue_eligible,
    resolve_intent_mode,
    resolve_task_affiliation,
)


def test_extract_task_id_from_recovery_message():
    msg = (
        "任务恢复说明\n之前执行task_554272e197cc（检索MCP技术知识库并总结）"
        "时出现代码报错，当前将重新启动检索流程"
    )
    assert extract_task_id_from_message(msg) == "task_554272e197cc"


def test_resume_message_continues_closed_task():
    hist = [
        {
            "task_id": "task_554272e197cc",
            "user_query": "检索MCP技术知识库并总结",
            "query_summary": "MCP技术知识库总结",
            "status": "resolved",
            "task_kind": "main",
        }
    ]
    msg = (
        "任务恢复说明\n之前执行task_554272e197cc（检索MCP技术知识库并总结）"
        "时出现代码报错，当前将重新启动检索流程：调用 rag_search"
    )
    aff = resolve_task_affiliation(msg, cur_task=None, main_task_history=hist)
    assert aff is not None
    assert aff["mode"] == "continue_main"
    assert aff["task_id"] == "task_554272e197cc"

    decision = resolve_intent_mode(
        msg,
        cur_task=None,
        is_simple_heuristic=False,
        main_task_history=hist,
    )
    assert decision["mode"] == "continue_main"
    assert decision["task_id"] == "task_554272e197cc"


def test_continue_without_client_context_uses_session_history():
    """简单直答清空 cur_task 后，服务端仍应从会话重建归属（trace_42 类场景）。"""
    from app.services.ai_chat import _is_simple_intent
    from app.services.chat_context_memory import hydrate_client_task_context, resolve_intent_mode

    sid = "trace_42da22b8"
    msg = "继续"
    assert _is_simple_intent(msg) is False
    cur, hist = hydrate_client_task_context(sid, client_cur_task=None, client_main_task_history=[])
    assert hist, "会话应能重建 main_task_history"
    assert any(str(h.get("task_id") or "") == "task_554272e197cc" for h in hist)
    dec = resolve_intent_mode(msg, cur_task=cur, is_simple_heuristic=False, main_task_history=hist)
    assert dec["mode"] == "continue_main"
    assert dec["task_id"] == "task_554272e197cc"


def test_short_reply_with_history_not_simple():
    hist = [
        {
            "task_id": "task_554272e197cc",
            "user_query": "检索MCP技术知识库并总结",
            "status": "resolved",
            "task_kind": "main",
        }
    ]
    dec = resolve_intent_mode(
        "嗯",
        cur_task=None,
        is_simple_heuristic=True,
        main_task_history=hist,
    )
    assert dec["mode"] == "continue_main"
    assert dec["task_id"] == "task_554272e197cc"


def test_self_intro_not_affiliated_to_active_main_task():
    """元问答不得因未结案主任务被强行续接（trace_42 二次问「你是谁」类）。"""
    cur = {
        "task_id": "task_554272e197cc",
        "user_query": "搜素知识库中关于MCP技术相关的文档进行总结反馈。",
        "status": "executing",
        "task_kind": "main",
    }
    msg = "你是谁，你有什么能力"
    aff = resolve_task_affiliation(msg, cur_task=cur, main_task_history=[cur])
    assert aff is None
    dec = resolve_intent_mode(
        msg,
        cur_task=cur,
        is_simple_heuristic=True,
        main_task_history=[cur],
    )
    assert dec["mode"] == "simple"
    assert not peek_fast_continue_eligible(msg, cur_task=cur, main_task_history=[cur])


def test_explicit_new_task_skips_affiliation():
    hist = [
        {
            "task_id": "task_abc123456789",
            "user_query": "旧任务",
            "status": "resolved",
            "task_kind": "main",
        }
    ]
    msg = "换个问题，新问题：今天天气怎么样"
    aff = resolve_task_affiliation(msg, cur_task=None, main_task_history=hist)
    assert aff is None
