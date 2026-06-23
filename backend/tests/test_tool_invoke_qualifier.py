"""工具调用定语：Tool Call / MCP / SKILL 不得混标。"""

from app.services.tool_invoke_qualifier import (
    INVOKE_REACT,
    build_invoke_labels,
    format_tool_action_label,
    normalize_legacy_tool_step_name,
    resolve_tool_source,
)


def test_resolve_tool_source_from_catalog():
    meta = {
        "tools": [
            {"name": "web_search", "source": "builtin"},
            {"name": "xhs_user_search", "source": "mcp"},
        ]
    }
    assert resolve_tool_source("web_search", meta) == "builtin"
    assert resolve_tool_source("xhs_user_search", meta) == "mcp"


def test_build_invoke_labels_separates_tool_call_and_mcp():
    meta = {"tools": [{"name": "web_search", "source": "builtin"}]}
    builtin = build_invoke_labels(
        mode=INVOKE_REACT,
        tool_name="web_search",
        tools_meta=meta,
    )
    assert "联网搜索" in builtin["what"]
    assert "MCP" not in builtin["what"]
    assert "模型工具调用" not in builtin["invoke_purpose"]
    assert builtin["tool_source"] == "builtin"

    mcp = build_invoke_labels(
        mode=INVOKE_REACT,
        tool_name="xhs_user_search",
        tools_meta={"tools": [{"name": "xhs_user_search", "source": "mcp"}]},
    )
    assert "MCP · xhs_user_search" in mcp["what"]
    assert mcp["tool_source"] == "mcp"
    assert mcp["invoke_purpose"] == "MCP 调用"


def test_normalize_legacy_tool_step_name():
    assert normalize_legacy_tool_step_name("MCP 工具: rag_search", "rag_search") == "MCP · rag_search"
    assert format_tool_action_label("link_pipeline_start", "builtin") == "Tool Call · link_pipeline_start"
