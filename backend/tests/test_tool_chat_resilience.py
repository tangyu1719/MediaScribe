"""工具失败韧性：错误码分类与重试规划。"""
from app.services.tool_chat_resilience import (
    build_tool_failure_summary_block,
    classify_tool_failure,
    extract_error_code,
    plan_tool_retry,
    should_mark_task_abnormal,
)


def test_extract_error_code():
    assert extract_error_code("SUB_XHS_COOKIE_UNAVAILABLE: foo") == "S1001"
    assert extract_error_code("plain error") == ""


def test_classify_known_xhs():
    info = classify_tool_failure(
        tool_name="xhs_user_search",
        error_message="SUB_XHS_CDP_REQUIRED: chrome 未开启 CDP",
    )
    assert info["known"] is True
    assert info["error_code"] == "S1002"


def test_plan_tool_retry_xhs():
    args, hook = plan_tool_retry(
        tool_name="xhs_user_search",
        tool_args={"red_id": "981032418"},
        error_message="SUB_XHS_COOKIE_UNAVAILABLE",
        attempt=0,
    )
    assert args["red_id"] == "981032418"
    assert hook == "refresh_xhs_cookies"


def test_mark_abnormal_at_three():
    assert should_mark_task_abnormal(tool_name="x", fail_count=3, max_fail=3)
    assert not should_mark_task_abnormal(tool_name="x", fail_count=2, max_fail=3)


def test_failure_summary_block():
    block = build_tool_failure_summary_block(
        failures=[
            {
                "tool_name": "xhs_user_search",
                "error_code": "SUB_XHS_COOKIE_UNAVAILABLE",
                "error_message": "cookie fail",
            }
        ],
        task_id="task_abc",
    )
    assert "xhs_user_search" in block
    assert "task_abc" in block


def test_timeout_failure_keeps_user_visible_evidence():
    info = classify_tool_failure(
        tool_name="third_party_search",
        error_message="工具超时（>60s）",
    )
    assert info["error_message"] == "工具超时（>60s）"
    block = build_tool_failure_summary_block(
        failures=[info],
        task_id="task_timeout",
    )
    assert "third_party_search" in block
    assert "工具超时" in block
