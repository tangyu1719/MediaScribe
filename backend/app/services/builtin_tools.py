"""内置 Tool Call 清单（供「工具」页展示；与路由/服务实际能力对齐）。"""
from __future__ import annotations

from typing import Any, Dict, List


def list_builtin_tools() -> List[Dict[str, Any]]:
    return [
        {
            "id": "tool_link_pipeline",
            "name": "链接转写流水线",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "提交视频/图文链接，服务端执行下载、转写、摘要与 Markdown/HTML 产出。",
            "inputs": [
                {"name": "platform", "type": "string", "required": True, "hint": "抖音 / B站 / 小红书等"},
                {"name": "link", "type": "string", "required": True, "hint": "可下载或可解析的页面 URL"},
            ],
            "outputs": "任务进入队列；SSE 日志与最终 MD/HTML 路径由任务状态接口返回。",
        },
        {
            "id": "tool_xhs_user_search",
            "name": "小红书用户搜索与画像",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "通过小红书号（数字 ID）搜索用户主页，解析 profile URL 并自动启动画像分析流水线。内部通过浏览器自动化（CDP/Playwright）完成 red_id → creator_id → profile_url 的解析。",
            "inputs": [
                {"name": "red_id", "type": "string", "required": True, "hint": "小红书号（纯数字 ID，如 9545679835）"},
                {"name": "user_prompt", "type": "string", "required": False, "hint": "额外分析指令，如「做用户画像」「分析内容风格」"},
            ],
            "outputs": "解析结果（creator_id、display_name、profile_url）与流水线任务 ID。",
        },
        {
            "id": "tool_rag_index",
            "name": "RAG 索引与库管理",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "向量库文件入库、库切换、切片与 metadata 配置（kb / rag_libraries）。",
            "inputs": [
                {"name": "path", "type": "string", "required": False, "hint": "服务端白名单路径或上传后的路径"},
                {"name": "library_id", "type": "string", "required": False, "hint": "目标向量库 ID"},
            ],
            "outputs": "索引任务结果 JSON（成功条数、错误信息等，以具体 API 为准）。",
        },
        {
            "id": "tool_rag_search",
            "name": "RAG 语义检索",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "在已连接 Milvus 与当前库上执行语义检索（与 kb_search 对齐）。",
            "inputs": [
                {"name": "query", "type": "string", "required": True, "hint": "自然语言检索句"},
                {"name": "top_k", "type": "int", "required": False, "hint": "返回条数，默认由服务端配置"},
            ],
            "outputs": "命中文本片段列表（含 score、路径、切片内容摘要）。",
        },
        {
            "id": "tool_doc_analyze",
            "name": "多模态文档解析",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "服务端路径或上传文件：DocumentProcessor / MinerU 等解析链路。",
            "inputs": [
                {"name": "path", "type": "string", "required": True, "hint": "服务端可读绝对路径"},
            ],
            "outputs": "解析状态、文本长度、文档类型与错误信息（若有）。",
        },
        {
            "id": "tool_cache_rw",
            "name": "Redis 中间缓存",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "查询、更新或导出任务中间产物缓存条目。",
            "inputs": [
                {"name": "task_key", "type": "string", "required": False, "hint": "按任务键过滤"},
                {"name": "artifact", "type": "string", "required": False, "hint": "按产物类型过滤"},
            ],
            "outputs": "缓存行 JSON 或导出文件路径（取决于 API）。",
        },
        {
            "id": "tool_ops_snapshot",
            "name": "OPS 观测",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "调用统计、事件流与运维建议摘要。",
            "inputs": [],
            "outputs": "overview JSON（调用量、Top 路径、事件列表等）。",
        },
        {
            "id": "tool_comment_scraper",
            "name": "评论区抓取",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "mcp",
            "mcp_server": "comment-scraper",
            "description": "抓取社交媒体平台评论区。支持小红书/B站/抖音。返回结构化评论（作者、正文、时间、属地、点赞、回复链）。",
            "inputs": [
                {"name": "url", "type": "string", "required": True, "hint": "目标链接（小红书需带 xsec_token）"},
                {"name": "platform", "type": "string", "required": False, "hint": "xiaohongshu / bilibili / douyin，空则自动检测"},
                {"name": "max_count", "type": "int", "required": False, "hint": "最多抓取条数，默认不限制"},
            ],
            "outputs": "评论文本（含序号、作者、时间、属地、点赞数、回复链）。",
        },
        {
            "id": "tool_rss_reader",
            "name": "RSS 订阅阅读",
            "kind": "tool_call",
            "version": "1.0.0",
            "impl": "internal",
            "description": "读取当前用户在 RSS 阅读器中的订阅文章（标题、摘要、链接、已读/星标）。",
            "inputs": [
                {"name": "query", "type": "string", "required": False, "hint": "标题/摘要关键词过滤"},
                {"name": "limit", "type": "int", "required": False, "hint": "返回条数，默认 10"},
                {"name": "unread_only", "type": "bool", "required": False, "hint": "仅未读"},
                {"name": "starred_only", "type": "bool", "required": False, "hint": "仅星标"},
            ],
            "outputs": "JSON：items 列表与 stats 统计。",
        },
    ]
