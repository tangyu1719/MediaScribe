# 长页 IR Agent · JSON 契约（v1）

## 职责

你是 **语义 IR 抽取 Agent**。输入为流水线 Markdown 已切分后的 **「分析侧 Markdown」**（对应 AI 分析语义，可能含管线噪声）与 **「原文 Markdown」**（口播/文稿）。

你必须完成 **语义归类**，输出 **唯一一个 JSON**（可包在 ```json 围栏内），供下游并行子 Agent 与 HTML 总装使用。

## 绝对禁止进入 `reader_analysis_markdown` 的内容

以下内容 **只能** 写入 `dropped_internal_spans`，**禁止**出现在 `reader_analysis_markdown` 任意位置：

- `COUNT:`、`ORDER_FP:`、`[CNT=n]`、整块「标记内容汇总」及 `===` 围栏统计块  
- 标题或正文含 **错别字 / 勘误 / 格式优化说明** 等管线工序段落  
- 「由视频转文字处理工具自动生成」等脚注  

若输入中出现上述片段，须在 `dropped_internal_spans` 逐条说明 **kind + brief_reason**，读者正文 **零字节复现**。

## `reader_analysis_markdown` 是什么

- **唯一**允许进入 HTML `#analysis`（分析正文）区的 Markdown **正文**来源（由下游渲染为 HTML）。
- 必须是 **面向读者的技术分析**：结构清晰，可用 `##` / 列表；可从输入提炼、归纳，**不得编造与输入矛盾的事实**。
- **不是**口播原文（口播只在「原文」区）；不是内部调试清单。

## JSON 顶层字段（契约骨架；可选键见文末「字段必填/可选」表）

```json
{
  "schema_version": "longpage_ir_v1",
  "doc_title_hint": "可选短标题建议",
  "reader_analysis_markdown": "字符串，读者向分析正文 Markdown",
  "overview_one_liner_hint": "一句话，供导读参考（≤120字）",
  "diagram_requests_summary": [
    {
      "id": "d1",
      "kind": "flowchart",
      "intent_zh": "图示意图",
      "context_excerpt": "≤400字摘录",
      "omit_reason": ""
    }
  ],
  "dropped_internal_spans": [
    { "kind": "order_fp_block", "brief_reason": "流水线统计块，非读者内容" }
  ],
  "analysis_lead_card_markdown": "可选。高密度对照表/架构要点（Markdown）；**由编排器插在导读区主卡片之后**，不进入分析正文区。",
  "inline_diagram_placements": [
    { "diagram_id": "d1", "after_text": "分析正文中某段落的**原文子串**（建议 8～40 字），在该段落后插入对应结构图" }
  ],
  "overview_lead_ui": {
    "compare": {
      "left_title": "RAG",
      "right_title": "微调",
      "vs_text": "VS",
      "left_blurb": "可选 Markdown：左侧一段话，突出检索增强与可更新知识。",
      "right_blurb": "可选 Markdown：右侧一段话，突出权重内化的格式与风格。",
      "rows": [
        { "dim": "知识更新", "left": "快（索引）", "right": "慢（训练）" }
      ]
    },
    "layers": [
      { "name": "接入层", "detail": "鉴权、租户、限流" },
      { "name": "知识层", "detail": "连接器与血缘" }
    ]
  },
  "diagram_mining_hints": []
}
```

以上 JSON 为**骨架示例**；各键语义与必填性以 **「字段必填 / 可选（回归清单）」** 表为准，避免与下文段落描述冲突。

## 图式范式库（范式句 · 与图需求 / 导读内嵌 UI 对齐）

以下 **范式句** 用于 IR 自检与（未来）二阶段图例发掘：当 `reader_analysis_markdown` 中出现同类叙述结构时，应在 `diagram_requests_summary` / `overview_lead_ui` / `diagram_mining_hints` 中给出 **可验收** 的对应产出，避免「一图交差」。

| 范式 ID | 典型触发（正文信号） | `diagram_requests_summary.kind` 建议 | `overview_lead_ui` 关联 |
|---------|----------------------|----------------------------------------|-------------------------|
| `hierarchy_total_part` | 「总—分」「先总后分」「框架如下再展开」 | `flowchart` 或层级框图 | 可用 `layers` 展示总骨架 |
| `layer_stack` | 「六层」「分层架构」「自下而上/自上而下」 | `flowchart` / 分层泳道 | **优先** `layers` 六条（或 3～8 条） |
| `linear_chain` | 「链路」「流程」「步骤：A→B→C」「从…到…」 | `flowchart` | 一般不单占 `overview_lead_ui` |
| `compare_dual` | 「A vs B」「对比」「二者差异」「互补/替代」 | 第二张图：`flowchart`（双中心发散）或专用说明 | **优先** `compare`（双卡+VS+矩阵） |
| `state_decision` | 「若…则…」「分支」「状态机」 | `stateDiagram` / `flowchart` | 通常无 |
| `sequence_interaction` | 「调用顺序」「时序」「先鉴权后检索」 | `sequenceDiagram` | 通常无 |

**硬规则（自检）**

1. 同时存在 **线性主链路**（如 RAG 全链路）与 **对称双轨对比**（RAG vs 微调）时：`diagram_requests_summary` **至少 2 条**（除非在对应项 `omit_reason` 写明理由）。  
2. 存在 **分层枚举**（如六层）且正文有对比表意时：应同时给出 `overview_lead_ui.layers`（或 `compare`）与至少一条 **链路/分层** 类 `diagram_requests_summary`。  
3. `companion_excerpt_for_legend` / `orchestration_note_zh`（见 `AGENT_DIAGRAM_ORCHESTRATION.md`）应尽量填，便于图例 Agent **分块** 解释多图。

### 范式句样例（正文出现类似表述时 → 必须能映射到上表范式 ID）

以下句子**不要求逐字出现**，语义相近即触发对应范式；IR 输出中应能在 `trigger_excerpt` 或 `context_excerpt` 中找到等价摘录。

- **`hierarchy_total_part`**：「先给结论再展开」「总览如下，后文分三节说明」「整体框架分三层」  
- **`layer_stack`**：「自下而上分为六层」「接入层、知识层、索引层…」「分层架构中，编排层负责…」  
- **`linear_chain`**：「请求进入后依次经过」「端到端链路为」「步骤一…步骤二…最后落盘审计」  
- **`compare_dual`**：「RAG 与微调并非二选一」「二者互补」「对比维度包括知识更新与可溯源」「A 侧重事实更新，B 侧重格式固化」  
- **`state_decision`**：「若检索为空则走兜底」「分支一…分支二…」「状态从 pending 到 committed」  
- **`sequence_interaction`**：「先鉴权再检索」「网关返回后再调用模型」「时序上必须先写审计日志」  

## 字段必填 / 可选（回归清单）

| 字段 | 必填 | 说明 |
|------|------|------|
| `schema_version` | 是 | 须为 `longpage_ir_v1` |
| `reader_analysis_markdown` | 是 | 分析正文唯一 Markdown 源 |
| `overview_one_liner_hint` | 是 | 一句导航提示 |
| `diagram_requests_summary` | 是 | 可为 `[]` |
| `dropped_internal_spans` | 是 | 可为 `[]` |
| `analysis_lead_card_markdown` | 否 | 与 `overview_lead_ui` 二选一或同用；有 UI 时正文勿重复表格 |
| `inline_diagram_placements` | 否 | 可为 `[]` 或省略 |
| `overview_lead_ui` | 否 | 有则走双卡+VS+层条版式 |
| `diagram_mining_hints` | 否 | 可为 `[]` 或省略；二阶段预埋 |
| （扩展）`diagram_requests_summary[]` | 否 | 每项可含 `companion_excerpt_for_legend`、`orchestration_note_zh`，见 `AGENT_DIAGRAM_ORCHESTRATION.md` |

## 可选字段 `diagram_mining_hints`（二阶段图例发掘预埋 · v1）

- **用途**：IR 在通读正文后，列出「已定稿展示文本」上仍值得补图或补图例说明的结构化机会；下游可选择 **merge** 进下一轮 `diagram_requests_summary` 或仅作审计。  
- **类型**：`array`；可为 `[]` 或省略。  
- **每项字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `paradigm` | string | 上表范式 ID，如 `compare_dual` |
| `trigger_excerpt` | string | 从 `reader_analysis_markdown` 摘录 20～120 字，证明触发 |
| `suggested_diagram_id` | string | 建议新图 id，勿与已有 id 冲突 |
| `suggested_kind` | string | 如 `flowchart` / `stateDiagram` |
| `rationale_zh` | string | 人类可读：为何需要第二张图、与第一张如何分工 |
| `merge_policy` | string | 建议 `append`（追加绘图）或 `legend_only`（仅补图例文案） |

## 附录：完整范例 JSON（结构对齐 · 可复制改字段名）

下列示例演示：**双图**（全链路 + 双中心对比）、**导读内嵌** `overview_lead_ui`、**穿插锚点**、**二阶段预埋**。`reader_analysis_markdown` 在真实输出中应远长于示例。

```json
{
  "schema_version": "longpage_ir_v1",
  "doc_title_hint": "企业大模型落地：RAG 与微调",
  "overview_one_liner_hint": "先看双轨对比与六层骨架，再读分节正文与全链路图。",
  "reader_analysis_markdown": "## 背景\\n…\\n## 核心概念\\n…\\n## 分层架构\\n…\\n## 数据流\\n…\\n## 对比小结\\nRAG 与微调互补替代。",
  "diagram_requests_summary": [
    {
      "id": "d1",
      "kind": "flowchart",
      "intent_zh": "企业 RAG 问答全链路（鉴权→检索→生成→审计）",
      "context_excerpt": "典型问答路径：用户问题→权限校验→检索…",
      "omit_reason": "",
      "companion_excerpt_for_legend": "鉴权节点与审计落盘在流程中为珊瑚强调位。",
      "orchestration_note_zh": "主叙事为时间顺序链路，与对比图 d2 分工。"
    },
    {
      "id": "d2",
      "kind": "flowchart",
      "intent_zh": "RAG vs 微调 双中心对比（子模块齐平对照）",
      "context_excerpt": "RAG 将可更新知识外挂；微调将格式与风格写入权重…",
      "omit_reason": "",
      "companion_excerpt_for_legend": "左右两簇节点一一对应对比维度。",
      "orchestration_note_zh": "与 d1 链路图互补，禁止合并为一张。"
    }
  ],
  "dropped_internal_spans": [],
  "analysis_lead_card_markdown": "",
  "inline_diagram_placements": [
    { "diagram_id": "d1", "after_text": "典型问答路径：" },
    { "diagram_id": "d2", "after_text": "互补替代" }
  ],
  "overview_lead_ui": {
    "compare": {
      "left_title": "RAG",
      "right_title": "微调",
      "vs_text": "VS",
      "left_blurb": "检索片段注入上下文，**不改权重**；知识随索引更新。",
      "right_blurb": "领域训练写入参数；**格式与语气**稳定，事实更新需另配检索或工具。",
      "rows": [
        { "dim": "知识更新", "left": "快（刷新索引）", "right": "慢（重训+回归）" },
        { "dim": "可溯源", "left": "强（可贴证据）", "right": "弱（需外挂说明）" },
        { "dim": "运维重心", "left": "检索质量与评测", "right": "数据与训练流水线" }
      ]
    },
    "layers": [
      { "name": "接入层", "detail": "鉴权、租户隔离、限流" },
      { "name": "知识层", "detail": "多源连接器、版本血缘" },
      { "name": "索引层", "detail": "向量 + 关键词混合" },
      { "name": "编排层", "detail": "改写、检索、重排、工具路由" },
      { "name": "模型层", "detail": "网关主备、缓存降级" },
      { "name": "观测层", "detail": "评测、漂移、审计回放" }
    ]
  },
  "diagram_mining_hints": [
    {
      "paradigm": "compare_dual",
      "trigger_excerpt": "RAG 与微调并非互斥…组合模式为…",
      "suggested_diagram_id": "d2",
      "suggested_kind": "flowchart",
      "rationale_zh": "已有 d1 全链路；正文含对称对比，应单开 d2 以免一张图混写两套语义。",
      "merge_policy": "append"
    }
  ]
}
```

## `reader_analysis_markdown` 与导读的关系

- `overview_one_liner_hint`：供导读 Agent 的一句话提示（≤120 字），**不写长文**。
- `reader_analysis_markdown`：**完整技术分析正文**，须含多个 `##` 小节（背景、机制、架构、流程、落地、风险与演进等），不得用「总览 + 重复导读」代替实质段落。

## 文档回归（与本契约联动清单）

实现或改 prompt 时，下列文档须与本文件 **同步验收**，避免「IR 已扩展字段、子角色仍按旧假设输出」：

| 文档 | 联动点 |
|------|--------|
| `AGENT_DIAGRAM_ORCHESTRATION.md` | `diagram_requests_summary[]` 扩展键；图例/绘图 Agent 名 |
| `AGENT_HTML_OVERVIEW.md` | 导读 digest 与 `overview_lead_ui` / `overview_one_liner_hint` 分工 |
| `AGENT_HTML_LONGPAGE.md` | `#overview` 骨架、`overview-arch-stack`、修订记录 |
| `AGENT_LONGPAGE_HTML_ASSEMBLER_V1.md` | 总装对 `overview_inner_html` 内嵌结构的保留约束 |
| `AGENT.md`（summary） | 指向本 IR 文档的索引行 |
| `longpage_ir_agent.py` | `load_ir_spec` 路径、`validate_ir_payload` 可选字段类型 |
