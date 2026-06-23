"""流式/SSE 产品元问答：禁止 LLM 编造「非流式 POST」结论。"""

from app.services.chat_tool_registry import (
    format_streaming_architecture_markdown,
    is_streaming_meta_query,
)


def test_streaming_meta_query_positive():
    assert is_streaming_meta_query("？？？为啥你不是流式输出啊")
    assert is_streaming_meta_query("为什么不是逐字显示")
    assert is_streaming_meta_query("后端是不是 SSE streaming")


def test_streaming_meta_query_negative():
    assert not is_streaming_meta_query("搜索知识库中 WMS 报错文档")
    assert not is_streaming_meta_query("")


def test_streaming_architecture_markdown_facts():
    md = format_streaming_architecture_markdown()
    assert "SSE" in md
    assert "/api/chat/stream" in md
    assert "ReadableStream" in md
    assert "一次性 POST" in md
    assert "禁止" in md or "请勿" in md
