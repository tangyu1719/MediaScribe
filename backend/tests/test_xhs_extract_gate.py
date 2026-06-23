"""小红书抓取/装配/沉淀前校验门禁。"""
from __future__ import annotations

from app.services.pipeline_output_quality import (
    XHS_EXTRACT_EMPTY,
    XHS_CONTENT_JUNK,
    assess_assembled_source_text,
    assess_consolidation_input,
    assess_xhs_extractor_result,
    is_junk_body_text,
)


def test_junk_body_detects_platform_only():
    assert is_junk_body_text("小红书")
    assert is_junk_body_text("# 小红书")
    assert not is_junk_body_text("agent 算法简历 面试通不过的原因说明文字足够长")


def test_extract_gate_rejects_empty_payload():
    gate = assess_xhs_extractor_result({"type": "xiaohongshu", "text_content": "", "image_links": []})
    assert not gate.ok
    assert gate.error_code == XHS_EXTRACT_EMPTY


def test_extract_gate_accepts_images_for_ocr():
    gate = assess_xhs_extractor_result(
        {"type": "xiaohongshu", "text_content": "", "image_links": ["http://img/1.jpg"]},
    )
    assert gate.ok
    assert gate.image_links == 1


def test_extract_gate_rejects_junk_text_without_images():
    gate = assess_xhs_extractor_result(
        {"type": "xiaohongshu", "text_content": "小红书", "image_links": []},
    )
    assert not gate.ok
    assert gate.error_code == XHS_CONTENT_JUNK


def test_assemble_gate_rejects_title_shell():
    gate = assess_assembled_source_text("# 小红书")
    assert not gate.ok


def test_consolidation_gate_blocks_llm_on_empty():
    gate = assess_consolidation_input("小红书", stage_label="test")
    assert not gate.ok
