"""MCP 评论区抓取工具服务。

以 stdio 模式运行，向 AI 对话暴露 scrape_comments 工具。
注册到 mcp_servers.json 后，AI 可在对话中直接调用抓取评论。
"""
from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any

# 确保项目路径可导入
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from comment_scraper import scrape_comments, format_comments_as_text, format_comments_as_json

# ─── MCP 协议简单实现（stdio JSON-RPC） ───

def _rpc_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _write(msg: dict):
    """写 JSON-RPC 消息到 stdout。"""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _log(msg: str):
    """日志写到 stderr，不干扰 stdio 协议。"""
    print(f"[mcp_comment] {msg}", file=sys.stderr, flush=True)


TOOL_DEFINITIONS = [
    {
        "name": "scrape_comments",
        "description": (
            "抓取社交媒体平台的评论区内容。"
            "支持小红书(xiaohongshu)、B站(bilibili)、抖音(douyin)三个平台。"
            "返回结构化的评论数据，包含作者、正文、时间、属地、点赞数和回复链。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "目标链接。小红书需要携带 xsec_token 参数。",
                },
                "platform": {
                    "type": "string",
                    "description": "平台标识：xiaohongshu / bilibili / douyin。空则自动检测。",
                    "default": "",
                },
                "max_count": {
                    "type": "integer",
                    "description": "最多抓取条数，不填则不限制。",
                    "default": 20,
                },
                "sort_by": {
                    "type": "string",
                    "description": "排序方式：default(原始顺序) / likes(按点赞数降序) / time(最新在前)",
                    "default": "default",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "scrape_comments_json",
        "description": (
            "同 scrape_comments，但返回 JSON 格式便于程序处理。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标链接"},
                "platform": {"type": "string", "description": "平台标识", "default": ""},
                "max_count": {"type": "integer", "description": "最多抓取条数", "default": 20},
                "sort_by": {"type": "string", "description": "排序方式", "default": "default"},
            },
            "required": ["url"],
        },
    },
]


def _handle_tool_call(tool_name: str, arguments: dict) -> str:
    """执行工具调用并返回文本结果。"""
    url = (arguments.get("url") or "").strip()
    if not url:
        return "[error] 缺少 url 参数"

    platform = (arguments.get("platform") or "").strip()
    max_count = arguments.get("max_count", None)
    if max_count is not None:
        try:
            max_count = int(max_count)
        except (TypeError, ValueError):
            max_count = None
    sort_by = (arguments.get("sort_by") or "default").strip()

    _log(f"抓取评论: platform={platform or 'auto'} sort={sort_by} max={max_count} url={url[:100]}")

    try:
        result = scrape_comments(url, platform=platform, max_count=max_count, sort_by=sort_by)

        if tool_name == "scrape_comments_json":
            return format_comments_as_json(result)

        return format_comments_as_text(result)
    except Exception as e:
        return f"[error] 评论抓取异常: {e}\n{traceback.format_exc()[-500:]}"


def run_stdio():
    """主循环：读取 JSON-RPC 请求，处理后写回。"""
    _log("MCP comment server started")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _log(f"无效 JSON: {line[:100]}")
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        if method == "initialize":
            _write(_rpc_response(req_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "mcp-comment-scraper", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            }))

        elif method == "notifications/initialized":
            pass  # 无需响应

        elif method == "tools/list":
            _write(_rpc_response(req_id, {"tools": TOOL_DEFINITIONS}))

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            _log(f"tool_call: {tool_name}")

            try:
                result_text = _handle_tool_call(tool_name, arguments)
                _write(_rpc_response(req_id, {
                    "content": [{"type": "text", "text": result_text}],
                }))
            except Exception as e:
                _write(_rpc_error(req_id, -32000, f"工具执行失败: {e}"))

        elif method == "ping":
            _write(_rpc_response(req_id, {}))

        else:
            _write(_rpc_error(req_id, -32601, f"未知方法: {method}"))


if __name__ == "__main__":
    run_stdio()
