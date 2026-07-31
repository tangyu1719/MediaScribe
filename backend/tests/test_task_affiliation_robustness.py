"""主任务归属状态机：验证延续、新建、历史召回和简单问答的决策边界。"""
from __future__ import annotations

from app.services.chat_context_memory import (
    peek_fast_continue_eligible,
    resolve_intent_mode,
    resolve_task_affiliation,
)


def _task(task_id: str, query: str, status: str = "executing"):
    return {
        "task_id": task_id,
        "user_query": query,
        "query_summary": query[:120],
        "status": status,
        "task_kind": "main",
    }


def test_self_contained_new_goal_does_not_inherit_active_task_without_marker():
    cur = _task("task_mcp_old", "检索 MCP 技术知识库并总结文档")
    msg = "小红书上搜索 SFT 微调方面比较权威的博客，整理五篇"
    assert resolve_task_affiliation(msg, cur_task=cur, main_task_history=[cur]) is None
    dec = resolve_intent_mode(
        msg, cur_task=cur, is_simple_heuristic=False, main_task_history=[cur]
    )
    assert dec["mode"] == "new_main"
    assert not peek_fast_continue_eligible(msg, cur_task=cur, main_task_history=[cur])


def test_same_topic_followup_keeps_task_identity():
    cur = _task("task_sft", "调研 SFT 微调的原理、学习框架和实践资料")
    msg = "把 SFT 微调的评测框架再补充成一张表"
    dec = resolve_intent_mode(
        msg, cur_task=cur, is_simple_heuristic=False, main_task_history=[cur]
    )
    assert dec["mode"] == "continue_main"
    assert dec["task_id"] == "task_sft"


def test_semantic_recall_can_select_non_recent_main_task():
    older = _task("task_mcp", "检索 MCP 技术知识库并总结文档", "resolved")
    recent = _task("task_xhs", "分析小红书账号 haiyun862 的人物画像", "resolved")
    msg = "继续之前的 MCP 技术知识库总结，再补一下协议边界"
    aff = resolve_task_affiliation(
        msg, cur_task=recent, main_task_history=[older, recent]
    )
    assert aff is not None
    assert aff["task_id"] == "task_mcp"


def test_generic_progress_question_targets_current_task():
    older = _task("task_old", "整理 Redis 部署说明", "resolved")
    cur = _task("task_running", "抓取小红书 SFT 微调资料")
    aff = resolve_task_affiliation(
        "现在执行到哪了？", cur_task=cur, main_task_history=[older, cur]
    )
    assert aff is not None
    assert aff["task_id"] == "task_running"


def test_explicit_task_id_beats_recent_pointer():
    older = _task("task_123456abcdef", "文件 DIFF 和回退测试", "resolved")
    cur = _task("task_recent999999", "小红书搜索", "executing")
    aff = resolve_task_affiliation(
        "恢复 task_123456abcdef，继续验证冲突回退",
        cur_task=cur,
        main_task_history=[older, cur],
    )
    assert aff is not None
    assert aff["task_id"] == "task_123456abcdef"
    assert aff["affiliation"] == "explicit_task_id"


def test_short_ack_continues_but_meta_question_stays_simple():
    cur = _task("task_running", "抓取小红书 SFT 微调资料")
    ack = resolve_intent_mode(
        "好的", cur_task=cur, is_simple_heuristic=True, main_task_history=[cur]
    )
    assert ack["mode"] == "continue_main"
    meta = resolve_intent_mode(
        "你是谁，你能做什么？",
        cur_task=cur,
        is_simple_heuristic=True,
        main_task_history=[cur],
    )
    assert meta["mode"] == "simple"


def test_semantic_recall_scans_full_history_not_fixed_recent_window():
    history = [
        _task(f"task_topic_{idx:02d}", f"调研第 {idx} 个互不相关的主题", "resolved")
        for idx in range(20)
    ]
    target = _task("task_kubernetes_old", "调研 Kubernetes Operator 调谐循环与故障恢复", "resolved")
    history.insert(1, target)
    cur = _task("task_current", "分析小红书 SFT 微调博客", "executing")

    aff = resolve_task_affiliation(
        "继续之前的 Kubernetes Operator 调研，补充调谐循环边界",
        cur_task=cur,
        main_task_history=history + [cur],
    )

    assert aff is not None
    assert aff["task_id"] == "task_kubernetes_old"
    assert aff["affiliation"] == "semantic_history_main"


def test_active_status_alone_never_swallows_unrelated_self_contained_goal():
    cur = _task("task_running_xhs", "分析小红书账号 haiyun862 的最近视频", "executing")
    msg = "设计 Redis 高可用部署方案，并给出 Sentinel 故障切换检查表"

    assert resolve_task_affiliation(msg, cur_task=cur, main_task_history=[cur]) is None
    decision = resolve_intent_mode(
        msg,
        cur_task=cur,
        is_simple_heuristic=False,
        main_task_history=[cur],
    )
    assert decision["mode"] == "new_main"


def test_completed_chinese_deliverable_can_be_recalled_after_many_tasks():
    target = _task(
        "task_link_delivery",
        "把本项目链接分析能力接入 Agent，并形成测试方案与量化验收标准",
        "resolved",
    )
    history = [target] + [
        _task(f"task_filler_{idx}", f"第 {idx} 个互不相关的历史主题与运行说明", "resolved")
        for idx in range(24)
    ]
    cur = _task("task_current_sft", "搜索小红书 SFT 微调资料并整理学习框架", "executing")

    aff = resolve_task_affiliation(
        "继续之前的链接分析接入测试方案，把量化验收标准改成表格",
        cur_task=cur,
        main_task_history=history + [cur],
    )

    assert aff is not None
    assert aff["task_id"] == "task_link_delivery"
    assert aff["affiliation"] == "semantic_history_main"
