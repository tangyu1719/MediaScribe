---
name: skill-usage-archive
description: 在 SKILL 使用结束后（非调用过程中）延迟生成使用分析报告并文件归档，含场景、过程、产出与满意度。
command: /skill-archive
version: 1.0.0
---

# SKILL 使用归档（skill-usage-archive）

## 适用场景

当系统中任意 SKILL 被 **Tool Call 调用** 或用户通过 **`/command` 挂载** 后，需要在**使用结束后**（不在调用过程中）自动生成使用分析报告并文件归档。

## 核心原则

1. **使用中不总结**：SKILL 正文加载与 Agent 执行期间，禁止插入归档 LLM 步骤。
2. **延迟触发**：调用完成后登记 pending；默认 **90 秒**无新操作后，后台 sweep 读取会话上下文并生成归档。
3. **真实 LLM**：归档正文须经 `diagram_legend_agent` 网关；失败时写入带原因的 fallback Markdown，并标记 `archive_error`。
4. **可观测**：每条归档含 `meta.json` + `report.md`，索引见 `output/skill_usage_archives/index.json`。

## 系统硬编码链路

```
skill_{id} Tool Call / /command 挂载
  → record_skill_usage_start（pending JSON）
  → [90s 静默] sweep_pending_archives
  → _gather_session_context（data/chat_sessions）
  → LLM 生成归档 JSON
  → output/skill_usage_archives/{archive_id}/report.md
```

## 归档报告结构

| 章节 | 内容 |
|------|------|
| 使用任务/场景 | 用户意图、业务背景 |
| 使用过程 | 步骤数组（调用→执行→产出） |
| 产出文件示例 | 实际或推断的文件路径/类型 |
| 结果及效果 | 是否达成目标 |
| 满意度 / 采纳度 | 1–5 分 + 说明 |

## API

- `GET /api/skills/{skill_id}/usage-archives` — 列表
- `GET /api/skills/usage-archives/{archive_id}` — 详情（含 report_md）
- `POST /api/skills/usage-archives/sweep?force=1` — 手动 sweep

## 示例

用户：`/ui-ux 帮我审查首页按钮可访问性`

1. Agent 调用 `skill_527b70a431`，登记 pending
2. 对话结束约 90s 后，sweep 读取会话尾部消息
3. 生成 `output/skill_usage_archives/a1b2c3/report.md`，含场景、过程、建议与满意度占位

## 禁止

- 禁止在 SKILL invoke 同步路径中调用归档 LLM
- 禁止编造未出现在会话/产出中的文件路径（信息不足须标注「待补充」）
