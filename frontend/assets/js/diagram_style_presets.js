/**
 * 图表样式预设（按类型拆分），与 src/agent/diagram_style_presets.py 对齐。
 * 图例 Agent 配置页可编辑 JSON 覆盖；工具页 Mermaid / 药丸流程图从此读取。
 */
(function (global) {
  const MERMAID_THEME_VARIABLES = {
    fontFamily: 'Inter, "Microsoft YaHei UI", "PingFang SC", sans-serif',
    primaryColor: "#ffffff",
    primaryBorderColor: "#2a2a2a",
    primaryTextColor: "#141414",
    secondaryColor: "#fafaf7",
    secondaryBorderColor: "#cbd5e1",
    secondaryTextColor: "#141414",
    tertiaryColor: "#f5f4ed",
    lineColor: "#94a3b8",
    noteBkgColor: "#fafaf7",
    noteTextColor: "#141414",
  };

  const MERMAID_INIT = {
    startOnLoad: false,
    securityLevel: "loose",
    theme: "base",
    look: "classic",
    flowchart: { curve: "basis", padding: 18, htmlLabels: true, nodeSpacing: 50, rankSpacing: 48 },
    themeVariables: { ...MERMAID_THEME_VARIABLES },
  };

  const LEGEND_SUITE_CSS = {
    marginBottom: "18px",
    padding: "14px 16px",
    background: "#fafaf7",
    border: "2px solid #2a2a2a",
    borderRadius: "8px",
  };

  const DIAG_SLOT_CSS = {
    margin: "14px 0",
    padding: "12px",
    background: "#fafaf7",
    borderRadius: "8px",
    border: "1px solid rgba(0,0,0,.14)",
  };

  const ER_DIAGRAM_CSS = {
    tableLayout: "fixed",
    width: "100%",
    borderCollapse: "collapse",
    headerBackground: "#f3f2ec",
    cellBorder: "1px solid rgba(0,0,0,.08)",
    entityAccent: "#2a2a2a",
    relationAccent: "#f7591f",
  };

  const TOOL_FLOW_PILL_CSS = {
    boardBackground: "linear-gradient(180deg,#fafbfc 0%,#f4f6f8 100%)",
    dotGrid: "radial-gradient(circle,rgba(15,23,42,.06) 1px,transparent 1px)",
    pillRadius: "999px",
    lineColor: "#cbd5e1",
  };

  const DIAGRAM_TYPE_META = [
    { key: "diagram_style_mermaid_json", label: "Mermaid", hint: "流程图/时序图渲染（theme、themeVariables、flowchart）" },
    { key: "diagram_style_legend_suite_json", label: "图例块", hint: "legend-suite 长页图例说明区" },
    { key: "diagram_style_diag_slot_json", label: "图表槽", hint: "diag-slot 单图容器" },
    { key: "diagram_style_er_json", label: "E-R / 对照表", hint: "实体关系或双栏对照矩阵" },
    { key: "diagram_style_tool_flow_pill_json", label: "工具页药丸流", hint: "SKILL 详情左侧药丸流程图" },
  ];

  function parseJsonField(raw) {
    if (raw == null || raw === "") return null;
    if (typeof raw === "object") return raw;
    try {
      const o = JSON.parse(String(raw));
      return o && typeof o === "object" ? o : null;
    } catch (_) {
      return null;
    }
  }

  function mergeMermaidInit(override) {
    const base = JSON.parse(JSON.stringify(MERMAID_INIT));
    const o = parseJsonField(override);
    if (!o) return base;
    const tv = { ...(base.themeVariables || {}) };
    if (o.themeVariables && typeof o.themeVariables === "object") {
      Object.assign(tv, o.themeVariables);
      delete o.themeVariables;
    }
    Object.assign(base, o);
    base.themeVariables = tv;
    return base;
  }

  function getMermaidInitFromFields(fields) {
    return mergeMermaidInit(fields && fields.diagram_style_mermaid_json);
  }

  function applyMermaidInitialize(mermaid, fields) {
    if (!mermaid || typeof mermaid.initialize !== "function") return;
    mermaid.initialize(getMermaidInitFromFields(fields));
  }

  function defaults() {
    return {
      diagram_style_mermaid_json: MERMAID_INIT,
      diagram_style_legend_suite_json: LEGEND_SUITE_CSS,
      diagram_style_diag_slot_json: DIAG_SLOT_CSS,
      diagram_style_er_json: ER_DIAGRAM_CSS,
      diagram_style_tool_flow_pill_json: TOOL_FLOW_PILL_CSS,
    };
  }

  global.SBA_DIAGRAM_STYLES = {
    MERMAID_INIT,
    MERMAID_THEME_VARIABLES,
    LEGEND_SUITE_CSS,
    DIAG_SLOT_CSS,
    ER_DIAGRAM_CSS,
    TOOL_FLOW_PILL_CSS,
    DIAGRAM_TYPE_META,
    defaults,
    mergeMermaidInit,
    getMermaidInitFromFields,
    applyMermaidInitialize,
    parseJsonField,
  };
})(typeof window !== "undefined" ? window : globalThis);
