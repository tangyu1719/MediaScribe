from __future__ import annotations


def test_sft_long_instruction_becomes_short_diverse_web_queries():
    from app.services.web_search_plan import build_web_search_plan

    prompt = (
        "小红书上搜索一下 SFT微调方面相关的比较权威的博客，"
        "大概给我5篇左右，尽量方向上做到每个方面都有。"
    )
    plan = build_web_search_plan(rewritten_query=prompt, original_query=prompt)

    assert plan["search_queries"] == [
        "小红书 SFT 微调",
        "SFT 微调 原理",
        "SFT 微调 学习框架",
        "SFT 微调 实践 评测",
    ]
    assert all("大概" not in query and "尽量" not in query for query in plan["search_queries"])


def test_xhs_topic_search_intent_is_distinct_from_account_profile():
    from app.services.web_search_plan import extract_xhs_content_search_query

    assert extract_xhs_content_search_query("小红书上搜索一下 SFT 微调相关博客") == "SFT 微调"
    assert extract_xhs_content_search_query("小红书号 haiyun862，分析人物画像") == ""


def test_web_query_removes_polite_count_delivery_and_source_instructions():
    from app.services.web_search_plan import build_web_search_plan

    prompt = (
        "请帮我联网搜索一下 DeepSeek V4 API function calling 429 限流的官方说明，"
        "给出 3 个可靠来源并整理成报告"
    )
    plan = build_web_search_plan(rewritten_query=prompt, original_query=prompt)

    assert plan["search_queries"][0] == "DeepSeek V4 API function calling 429 限流的官方说明"
    assert all("帮我" not in query and "3 个" not in query and "报告" not in query for query in plan["search_queries"])


def test_web_query_preserves_core_topic_and_builds_focused_facets():
    from app.services.web_search_plan import build_web_search_plan

    prompt = (
        "查一下 2026 年 LangGraph checkpoint 最佳实践，"
        "重点关注异步工具断点恢复和人工审核，最后做成表格"
    )
    plan = build_web_search_plan(rewritten_query=prompt, original_query=prompt)

    assert plan["search_queries"] == [
        "2026 年 LangGraph checkpoint 最佳实践",
        "2026 年 LangGraph checkpoint 最佳实践 异步工具断点恢复",
        "2026 年 LangGraph checkpoint 最佳实践 人工审核",
    ]


def test_continue_status_query_uses_parent_goal_not_progress_words():
    from app.services.web_search_plan import build_web_search_plan

    plan = build_web_search_plan(
        rewritten_query="现在执行到哪了？",
        original_query="现在执行到哪了？",
        task_user_query="搜索 DeepSeek V4 function calling 429 限流官方说明",
        continue_main=True,
    )

    assert plan["search_queries"][0] == "DeepSeek V4 function calling 429 限流官方说明"
    assert all("执行到哪" not in query for query in plan["search_queries"])


def test_platform_job_search_keeps_business_entities_but_drops_sorting_request():
    from app.services.web_search_plan import build_web_search_plan

    prompt = "在 BOSS直聘 上搜索北京 AI Agent 工程师岗位薪资，筛选 10 条并按公司排序"
    plan = build_web_search_plan(rewritten_query=prompt, original_query=prompt)

    assert plan["search_queries"][0] == "BOSS直聘 北京 AI Agent 工程师岗位薪资"
    assert "10 条" not in plan["search_queries"][0]
    assert "排序" not in plan["search_queries"][0]
