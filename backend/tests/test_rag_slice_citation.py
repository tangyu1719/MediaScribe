"""RAG 切片规范化与按句引用 prompt 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.rag_slice_utils import (  # noqa: E402
    answer_has_rag_citations,
    build_rag_llm_blocks,
    normalize_rag_slices,
)


def test_normalize_rag_slices_parent_and_full_content():
    hits = [
        {
            "content": "Model Context Protocol 是开放协议。",
            "source_file": "docs/mcp-guide.md",
            "metadata": {"title": "MCP 完整技术指南"},
            "score": 0.91,
        }
    ]
    slices = normalize_rag_slices(hits)
    assert len(slices) == 1
    sl = slices[0]
    assert sl["ref_id"] == 1
    assert sl["parent_document"] == "MCP 完整技术指南"
    assert "Model Context Protocol" in sl["content"]
    assert sl["source_file"] == "docs/mcp-guide.md"


def test_build_rag_llm_blocks_citation_instruction():
    slices = normalize_rag_slices(
        [{"content": "切片正文", "metadata": {"title": "企业级 MCP 构建完全指南"}}]
    )
    ctx, cite = build_rag_llm_blocks(slices, rag_query="MCP")
    assert "父文档：《企业级 MCP 构建完全指南》" in ctx
    assert "切片全文：" in ctx
    assert "切片正文" in ctx
    assert "按句引用" in cite
    assert "文献切片明细" in cite
    assert "处逻辑链路" in cite
    assert "置信度" in cite


def test_answer_has_rag_citations():
    ans = (
        "MCP 是协议1。\n"
        "企业架构含多系统2。\n\n"
        "## 文献切片明细\n"
        "切片[1]：父文档《指南》\n切片全文：...\n\n"
        "## 注释\n"
        "1 处逻辑链路：摘录切片【1】原文「…」。置信度：100\n"
        "2 处逻辑链路：通过切片【1】【2】…。置信度：80"
    )
    assert answer_has_rag_citations(ans)
    assert not answer_has_rag_citations("仅普通回答，无引用。")
