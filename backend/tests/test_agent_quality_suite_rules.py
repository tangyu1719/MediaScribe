"""复杂 Agent 评测的业务达成规则。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_agent_quality_suite import _complex_goal_checks


def test_haiyun_evidenced_failure_is_not_business_success():
    answer = (
        "海云日记用例已结束，但因 CDP 浏览器自动化不可用，"
        "目前无法完成这个人的分析。"
    )
    events = [
        {
            "event": "thought_step_end",
            "step_name": "Tool Call · xhs_user_search",
            "status": "completed",
            "output_text": '{"ok": false, "error": "CDP unavailable"}',
        }
    ]

    checks = _complex_goal_checks("haiyun", answer, events)

    assert checks["domain_goal_reached"] is False
    assert checks["xhs_profile_result_present"] is False


def test_haiyun_requires_real_profile_tool_result():
    answer = "海云日记近期内容画像已完成，下面按 5 篇近期笔记整理主题与证据。"
    events = [
        {
            "event": "thought_step_end",
            "step_name": "Tool Call · xhs_user_search",
            "status": "completed",
            "output_text": (
                '{"ok": true, "profile_run_id": "profile_1", '
                '"selected_notes": [{"note_id": "n1"}, {"note_id": "n2"}, '
                '{"note_id": "n3"}]}'
            ),
        }
    ]

    checks = _complex_goal_checks("haiyun", answer, events)

    assert all(checks.values())


def test_haiyun_accepts_nested_sse_tool_result_envelope():
    answer = "haiyun862 的近期内容画像已完成，以下为 5 篇真实笔记的轻量证据报告。"
    events = [
        {
            "event": "thought_step_end",
            "step_name": "Tool Call · xhs_user_search",
            "status": "completed",
            "output_text": (
                '{"tool_name":"xhs_user_search","tool_result":'
                '"{\\"ok\\":true,\\"profile_run_id\\":\\"profile_1\\",'
                '\\"selected_notes\\":[{\\"note_id\\":\\"n1\\"},'
                '{\\"note_id\\":\\"n2\\"},{\\"note_id\\":\\"n3\\"}]}"}'
            ),
        }
    ]

    checks = _complex_goal_checks("haiyun", answer, events)

    assert all(checks.values())


def test_haiyun_allows_evidenced_lightweight_limitations():
    answer = (
        "haiyun862 近期 5 篇笔记的轻量报告已完成。"
        "受资源模式限制，无法获取视频中的具体讲解内容，以下结论仅基于公开网页证据。"
    )
    events = [
        {
            "event": "thought_step_end",
            "step_name": "Tool Call · xhs_user_search",
            "status": "completed",
            "output_text": (
                '{"ok":true,"profile_run_id":"profile_1","selected_notes":['
                '{"note_id":"n1"},{"note_id":"n2"},{"note_id":"n3"}]}'
            ),
        }
    ]

    checks = _complex_goal_checks("haiyun", answer, events)

    assert all(checks.values())


def test_sft_requires_xhs_links_and_direction_coverage():
    answer = """
    SFT 入门介绍、原理解读、训练框架和实践经验如下：
    https://www.xiaohongshu.com/explore/a
    https://www.xiaohongshu.com/explore/b
    https://www.xiaohongshu.com/explore/c
    """
    events = [
        {
            "event": "thought_step_end",
            "status": "completed",
            "step_name": "小红书搜索",
            "output_text": "小红书 SFT 搜索结果",
        }
    ]

    checks = _complex_goal_checks("sft", answer, events)

    assert all(checks.values())
