"""常见 MCP 服务预设（TRAE / Cursor 类「厂商目录 + 市场片段 + 手动 JSON」思路）。

仅返回可合并进 mcp_servers.json 的 `servers` 片段与文档链接；不发起外网请求。
国内条目侧重协同办公、云文档与表格；可执行片段需本机 Node/Python 与对应依赖。"""
from __future__ import annotations

from typing import Any, Dict, List


def list_mcp_vendor_presets() -> List[Dict[str, Any]]:
    return [
        # ── 国内市场 / 文档办公（文档入口为主；部分含可合并 stdio 片段）──
        {
            "id": "cn-feishu-lark-mcp",
            "category": "国内 · 协同办公",
            "title": "飞书 Lark MCP（官方 @larksuiteoapi/lark-mcp）",
            "vendor": "飞书",
            "doc_url": "https://www.npmjs.com/package/@larksuiteoapi/lark-mcp",
            "note": "从市场添加后请在「配置」中填写开放平台 App ID / App Secret，保存并连接拉取。需 Node.js 与 npx。",
            "preset_alias": "feishu",
            "config_kind": "lark-mcp",
            "merge": {
                "feishu": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": [
                        "-y",
                        "@larksuiteoapi/lark-mcp",
                        "mcp",
                        "-a",
                        "YOUR_FEISHU_APP_ID",
                        "-s",
                        "YOUR_FEISHU_APP_SECRET",
                    ],
                }
            },
        },
        {
            "id": "cn-feishu-open",
            "category": "国内 · 协同办公",
            "title": "飞书开放平台文档（仅入口）",
            "vendor": "飞书",
            "doc_url": "https://open.feishu.cn/document/home/index",
            "note": "创建应用、权限与事件订阅说明；可执行 MCP 请用上方的「飞书 Lark MCP」条目。",
            "merge": {},
        },
        {
            "id": "cn-lark-intl",
            "category": "国内 · 协同办公",
            "title": "Lark International（海外飞书同一套 API）",
            "vendor": "Lark",
            "doc_url": "https://open.larksuite.com/document/",
            "note": "与飞书能力同源；跨境团队常用。",
            "merge": {},
        },
        {
            "id": "cn-dingtalk-open",
            "category": "国内 · 协同办公",
            "title": "钉钉开放平台（钉盘 / 审批 / 机器人）",
            "vendor": "钉钉",
            "doc_url": "https://open.dingtalk.com/",
            "note": "官方以 HTTP API 为主；MCP 多为企业内部封装，可参考开放平台鉴权自建。",
            "merge": {},
        },
        {
            "id": "cn-wecom-open",
            "category": "国内 · 协同办公",
            "title": "企业微信（文档 / 会话 / 通讯录）",
            "vendor": "腾讯",
            "doc_url": "https://developer.work.weixin.qq.com/document/",
            "note": "常见模式：自建企微应用 + 中间层暴露 MCP；敏感数据勿写入公共示例。",
            "merge": {},
        },
        {
            "id": "cn-yuque",
            "category": "国内 · 知识库",
            "title": "语雀 Open API（知识库与团队文档）",
            "vendor": "语雀",
            "doc_url": "https://www.yuque.com/yuque/developer/api",
            "note": "社区有语雀 MCP 参考实现；合并前请核对 Token 与可见范围。",
            "merge": {},
        },
        {
            "id": "cn-wps-open",
            "category": "国内 · 办公文档",
            "title": "WPS 开放平台（在线文档 / 表格）",
            "vendor": "金山办公",
            "doc_url": "https://open.wps.cn/",
            "note": "适合表格与排版类自动化；MCP 多为第三方封装，以官方 OAuth 为准。",
            "merge": {},
        },
        {
            "id": "cn-tencent-docs",
            "category": "国内 · 办公文档",
            "title": "腾讯文档开放平台",
            "vendor": "腾讯",
            "doc_url": "https://docs.qq.com/open/document/app/",
            "note": "在线表格/文档场景；需申请应用与回调域名。",
            "merge": {},
        },
        {
            "id": "cn-aliyun-bailian-mcp",
            "category": "国内 · 模型与工具",
            "title": "阿里云百炼 / Model Studio（MCP 场景）",
            "vendor": "阿里云",
            "doc_url": "https://help.aliyun.com/zh/model-studio/",
            "note": "通义与插件生态；关注官方「MCP / 工具调用」说明与地域 endpoint。",
            "merge": {},
        },
        {
            "id": "markitdown-npx",
            "category": "文档转换",
            "title": "MarkItDown（Office/PDF → Markdown，Microsoft）",
            "vendor": "Microsoft",
            "doc_url": "https://github.com/microsoft/markitdown/blob/main/packages/markitdown-mcp/README.md",
            "note": "需本机 Node + Python 3.10+；适合把 Word/Excel/PPT/PDF 等转为 Markdown 再喂给模型。",
            "merge": {
                "markitdown": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "markitdown-mcp-npx"],
                }
            },
        },
        {
            "id": "excel-mcp-negokaz",
            "category": "表格",
            "title": "Excel MCP（读写 .xlsx）",
            "vendor": "社区（negokaz）",
            "doc_url": "https://www.npmjs.com/package/@negokaz/excel-mcp-server",
            "note": "需本机 Node；将 PATH 换为允许读写的目录根。",
            "merge": {
                "excel": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@negokaz/excel-mcp-server", "PATH_TO_WORKDIR"],
                }
            },
        },
        {
            "id": "mcp-official-hub",
            "category": "目录与文档",
            "title": "MCP 官方规范与示例",
            "vendor": "modelcontextprotocol",
            "doc_url": "https://modelcontextprotocol.io/",
            "note": "协议说明、stdio/SSE/HTTP 传输与鉴权约定。",
            "merge": {},
        },
        {
            "id": "servers-official",
            "category": "目录与文档",
            "title": "官方参考实现（GitHub）",
            "vendor": "modelcontextprotocol",
            "doc_url": "https://github.com/modelcontextprotocol/servers",
            "note": "filesystem / fetch / memory 等官方维护服务器集合。",
            "merge": {},
        },
        {
            "id": "awesome-mcp",
            "category": "目录与文档",
            "title": "Awesome MCP Servers",
            "vendor": "社区",
            "doc_url": "https://github.com/punkpeye/awesome-mcp-servers",
            "note": "社区维护的 MCP 服务索引（按领域检索）。",
            "merge": {},
        },
        {
            "id": "smithery",
            "category": "目录与托管",
            "title": "Smithery（MCP 注册表 / 托管）",
            "vendor": "Smithery",
            "doc_url": "https://smithery.ai/",
            "note": "浏览、搜索与一键安装式 MCP（若 CLI 支持可对接）。",
            "merge": {},
        },
        {
            "id": "npm-mcp-scope",
            "category": "目录与文档",
            "title": "npm @modelcontextprotocol 作用域",
            "vendor": "npm",
            "doc_url": "https://www.npmjs.com/search?q=%40modelcontextprotocol",
            "note": "搜索官方与社区发布的 MCP server 包名。",
            "merge": {},
        },
        {
            "id": "filesystem-stdio",
            "category": "常用 stdio",
            "title": "本地文件系统（stdio）",
            "vendor": "Anthropic official",
            "doc_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
            "note": "将下方 PATH 替换为允许访问的根目录。",
            "merge": {
                "filesystem": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "PATH_TO_ROOT"],
                }
            },
        },
        {
            "id": "fetch-stdio",
            "category": "常用 stdio",
            "title": "HTTP 抓取（fetch）",
            "vendor": "Anthropic official",
            "doc_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
            "note": "适合拉取公开文档/API 说明页（注意合规与频率）。",
            "merge": {
                "fetch": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-fetch"],
                }
            },
        },
        {
            "id": "memory-stdio",
            "category": "常用 stdio",
            "title": "进程内键值记忆（memory）",
            "vendor": "Anthropic official",
            "doc_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
            "note": "会话级记忆；重启进程后清空。",
            "merge": {
                "memory": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-memory"],
                }
            },
        },
        {
            "id": "sequential-thinking",
            "category": "常用 stdio",
            "title": "顺序思考（Sequential Thinking）",
            "vendor": "Anthropic official",
            "doc_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
            "note": "适合复杂推理链路的辅助工具。",
            "merge": {
                "sequential_thinking": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
                }
            },
        },
        {
            "id": "playwright-mcp",
            "category": "浏览器自动化",
            "title": "Playwright MCP（浏览器）",
            "vendor": "Microsoft",
            "doc_url": "https://github.com/microsoft/playwright-mcp",
            "note": "需本机 Node；与内置 IDE Browser 不同，走 MCP 协议供 Agent 调用。",
            "merge": {
                "playwright": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@playwright/mcp@latest"],
                }
            },
        },
        {
            "id": "brave-search",
            "category": "搜索（需 Key）",
            "title": "Brave Search MCP",
            "vendor": "Brave",
            "doc_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
            "note": "将 BRAVE_API_KEY 写入环境变量或按官方示例配置 env。",
            "merge": {
                "brave_search": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-brave-search"],
                    "env": {"BRAVE_API_KEY": "YOUR_BRAVE_API_KEY"},
                }
            },
        },
        {
            "id": "slack-remote",
            "category": "SaaS（远程）",
            "title": "Slack MCP（官方远程示例）",
            "vendor": "Slack",
            "doc_url": "https://github.com/modelcontextprotocol/servers",
            "note": "远程 URL + OAuth 以官方最新文档为准；此处仅占位结构。",
            "merge": {
                "slack": {
                    "transport": "sse",
                    "url": "https://example.invalid/mcp/slack",
                }
            },
        },
    ]
