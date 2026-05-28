# 编排 JSON · 图例联动字段（与 `diagram_requests_summary`）

供 **编排 / Layout Agent** 在产出 `diagram_requests_summary` 时使用；Python 侧 `normalize_diagram_requests_from_layout` 会原样透传到图例绘制与兜底 HTML。

## 每项可选扩展字段

| 字段 | 说明 |
|------|------|
| `companion_excerpt_for_legend` | 配合该图的一句 **原文或分析摘录**，用于图例 Agent / 兜底图例中 `data-summary-llm` 与正文承接。 |
| `orchestration_note_zh` | **为何需要该图**、与上下文的承接说明（总结 LLM 读图例区时可对齐叙事）。 |

## 网关 Agent 名称

- **总编排（multiphase）**：`longpage_ir_agent`（IR）、`overview_digest_agent`、`diagram_drawer_agent`、`longpage_html_assembler_agent` 等见编排器与 delivery 模块。
- **IR 单一文档体系**：`AGENT_LONGPAGE_IR_V1.md` — 含顶层 JSON 契约、**图式范式库**、`overview_lead_ui`、`diagram_mining_hints`（二阶段预埋）及**附录完整范例 JSON**。
- **图例侧翼**：`diagram_legend_agent`（task_type: `diagram_legend_html`）。
- **强制双 LLM 脚本**：`src/agent/run_dual_llm_longpage_delivery.py` 会打开 `longpage_multiphase_enabled`、`longpage_legend_llm_required`（图例失败则页面报错块，**不**静默规则兜底）。
- 绘图并行：`diagram_drawer_agent`（既有）。

## 配置

| 键 | 含义 |
|----|------|
| `longpage_legend_agent_enabled` | 是否调用图例侧翼网关（默认 true）。 |
| `longpage_legend_llm_required` | true：图例必须由 LLM 产出；失败插入显式提示，禁止规则兜底顶替图例区。 |

## 样式契约

长页壳 CSS 已对齐 **light-diagram-html-suite / diagram-design**：暖纸底、`Instrument Serif` 标题、`Inter` 正文；图例块 `.legend-suite`；Mermaid 主题变量（浅色线 `#2a2a2a`、纸白节点）。

## 文档回归

范式 ID、`diagram_mining_hints`、`overview_lead_ui` 与附录范例 JSON 的**单一真相源**为同目录 **`AGENT_LONGPAGE_IR_V1.md`**（文末含「文档回归」联动表）；本文件仅维护 **编排图项扩展键** 与网关路由说明。
