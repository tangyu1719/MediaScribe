# 小红书对话人物画像（xhs_user_search）

## 适用场景

用户在 AI 对话中提供**小红书号（5–24 位字母、数字、下划线或短横线）**，要求分析博主内容、输出人物画像。与「社媒订阅 → UP 画像」同一业务链路，但**不写入订阅库**。

## 禁止

- **禁止**将 `https://www.xiaohongshu.com/user/profile/{creator_id}` 当作单条链接调用 `link_pipeline_start`。
- **禁止**把主页 HTML 当一篇「笔记」做标题/作者提取。
- **禁止**默认下载音视频或启动 FFmpeg、Whisper、画面 OCR；只有用户明确要求视频内证据时才进入深媒体链路。
- **禁止**把 `xsec_token` 写入日志、Markdown、前端响应或模型上下文。

## 五阶段路线

| 阶段 | 动作 | 产物 |
|------|------|------|
| 0 解析 | `resolve_xhs_red_id`（CDP → HTTP → 本机 Chrome） | `creator_id`, `profile_url`, `display_name` |
| 1 目录 | `fetch_catalog`（HTTP + CDP 浏览器兜底） | 笔记列表（note_id, title, content_type, published_at） |
| 2 轻量画像 | `build_light_profile`（仅标题 LLM） | 行业/领域/人设初判 JSON |
| 3 选篇 | `build_note_selection`（结合 `user_prompt`） | 3–5 篇 note_id |
| 3.5 访问链接 | `resolve_note_links_for_selection`（CDP 获取本轮访问上下文） | 进程内访问 URL；公开结果仅保留无 token URL |
| 4 轻量正文 | `run_article_only_for_note` × N（仅解析网页正文/元数据） | 本地 MD、`article` 正文、证据级别 |
| 5 深度画像 | `build_deep_profile` + `render_profile_markdown` | `chat_profiles/{red_id}/*.md` |
| 6 可选深媒体 | 用户明确要求视频内容/转写/字幕/画面 OCR 时提交 hybrid 任务 | `pipeline_task_ids`、等待快照和续接结果 |

## 工具入口

- 函数：`run_xhs_chat_profile`（`creator_profile_runner.py`）
- Tool Call：`xhs_user_search`（`chat_tool_registry.py`）

## 前置条件

1. 小红书 Cookie：可先 `sync_xhs_cookies`（CDP 9223 已登录 Tab）。
2. CDP 未就绪时：目录/链接采集可能降级；失败码 `PROFILE_CATALOG_EMPTY` / `SUB_XHS_CDP_REQUIRED`。

## 返回给对话模型

- `profile_summary`：深度画像正文摘要（可直接引用回答用户）
- `profile_md_path`：完整 Markdown 路径（可用 `local_file_read` 二次读取）
- `selected_notes`：采样笔记及 `doc_path`
- `resource_mode`：`lightweight_no_media` 或 `deep_media_async`
- `pipeline_task_ids`：仅深媒体模式存在；Agent 必须等待任务结束后从 checkpoint 续接
