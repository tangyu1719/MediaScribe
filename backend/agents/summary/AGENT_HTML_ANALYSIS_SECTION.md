# 交付编排 · 「分析正文」区（sections，对应 MD「AI分析摘要」）

## 与网页极简摘要的关系

- **`sections[]`** 只服务 HTML **`#analysis`（分析正文）`**：对 MD「AI分析摘要」做 **版块化、组件化**（对比表、时间线、叙事块等），**body_md_hint** 写该版块要点（可压缩排版），**不要**写导航向的一句话导读（导读由 overview_digest 负责）；**不要**与折叠区「原始内容 / 原文」混淆。
- **禁止**新增 `title` 为「导读架构」的 section：对照表/架构速览须由 IR 的 `analysis_lead_card_markdown` / `overview_lead_ui` 经编排器插入 **`#overview`**，不得出现在 `#analysis`。
- **禁止**在 body_md_hint 中整段复制 MD 摘要全文；应拆分为主题块、对照块、步骤块。
- 若内容含 **RAG / 微调 / 检索 / 混合架构** 等对照关系，**必须**使用 `comparison_table` 或 `diagram_placeholder`，并在 `diagram_requests_summary` 中给出对应图需求。
