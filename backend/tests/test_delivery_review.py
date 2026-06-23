"""交付审核 Agent 规则预审。"""
from __future__ import annotations

from app.services.pipeline_delivery_review import run_delivery_review
from app.services.pipeline_output_quality import DELIVERY_REVIEW_FAILED


def test_delivery_review_rule_blocks_junk_article():
    result = run_delivery_review(
        task_id="test-task",
        doc_path="/tmp/fake.md",
        doc_title="小红书平台概述",
        article="小红书",
        summary="小红书是一个 UGC 社区平台概述……" * 3,
        link_title="agent 算法简历",
        platform="小红书",
        source_text_len=3,
    )
    assert not result.ok
    assert result.error_code == DELIVERY_REVIEW_FAILED
    assert any("占位" in i or "过短" in i for i in result.issues)


def test_delivery_review_rule_blocks_platform_overview_mismatch():
    result = run_delivery_review(
        task_id="test-task-2",
        doc_path="/tmp/fake2.md",
        doc_title="小红书平台概述",
        article="# 小红书",
        summary="小红书平台概述：UGC 内容社区，用户分享生活方式" + "x" * 60,
        link_title="面试技巧",
        platform="小红书",
        source_text_len=5,
    )
    assert not result.ok
    assert result.error_code == DELIVERY_REVIEW_FAILED
