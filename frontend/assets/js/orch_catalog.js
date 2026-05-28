/**
 * 工具页能力分类与同义词搜索（SKILL + MCP）
 */
(function (global) {
  const ORCH_CATEGORIES = [
    {
      id: "doc",
      label: "文档处理",
      icon: "📄",
      keywords: [
        "doc",
        "docx",
        "pdf",
        "ppt",
        "pptx",
        "xlsx",
        "word",
        "文档",
        "办公",
        "deskhub",
        "deskclaw",
        "generator",
        "填表",
        "form",
      ],
    },
    {
      id: "writing",
      label: "写作与润色",
      icon: "✍️",
      keywords: [
        "writing",
        "write",
        "paper",
        "humanize",
        "aigc",
        "写作",
        "论文",
        "coauthor",
        "润色",
        "arxiv",
        "scholar",
        "latex",
        "thesis",
        "ml-paper",
      ],
    },
    {
      id: "dev",
      label: "开发与流程",
      icon: "⚙️",
      keywords: [
        "vibe",
        "plan",
        "debug",
        "coding",
        "engineering",
        "harness",
        "triage",
        "review",
        "pipeline",
        "regression",
        "sop",
        "测试",
        "开发",
      ],
    },
    {
      id: "browser",
      label: "浏览器与自动化",
      icon: "🌐",
      keywords: ["browser", "playwright", "scrap", "爬虫", "自动化", "agent-browser"],
    },
    {
      id: "media",
      label: "图像与视频",
      icon: "🎬",
      keywords: [
        "image",
        "video",
        "gpt-image",
        "generation",
        "图",
        "视频",
        "canvas-design",
        "design",
        "ui-ux",
        "frontend",
        "impeccable",
      ],
    },
    {
      id: "diagram",
      label: "图表与长页",
      icon: "📊",
      keywords: [
        "diagram",
        "mermaid",
        "flow",
        "chart",
        "longpage",
        "html",
        "可视化",
        "流程图",
        "图例",
      ],
    },
    {
      id: "ad",
      label: "广告与投放",
      icon: "📢",
      keywords: ["ad-", "广告", "投放", "strategy", "analysis", "creative", "budget"],
    },
    {
      id: "rag",
      label: "检索与知识",
      icon: "🔍",
      keywords: ["rag", "kb", "retriever", "search", "检索", "知识", "向量", "milvus"],
    },
    {
      id: "collab",
      label: "协作与飞书",
      icon: "💬",
      keywords: ["lark", "feishu", "飞书", "slack", "im", "comment", "评论"],
    },
    {
      id: "other",
      label: "其他",
      icon: "📦",
      keywords: [],
    },
  ];

  /** 同义词簇：命中任一词则扩展整簇 */
  const ORCH_SYNONYM_GROUPS = [
    ["文档", "doc", "docx", "pdf", "word", "ppt", "xlsx", "办公", "office"],
    ["写作", "write", "writing", "论文", "paper", "润色", "humanize", "降重", "aigc"],
    ["开发", "dev", "code", "coding", "vibe", "debug", "plan", "工程"],
    ["浏览器", "browser", "playwright", "自动化", "爬取", "scrape"],
    ["图像", "image", "picture", "画图", "生成图", "gpt-image"],
    ["视频", "video", "成片"],
    ["图表", "diagram", "流程图", "mermaid", "可视化", "chart", "图例"],
    ["广告", "ad", "投放", "营销", "巨量"],
    ["检索", "search", "rag", "知识库", "向量"],
    ["飞书", "lark", "feishu", "多维表格"],
    ["skill", "技能", "slash", "斜杠"],
    ["mcp", "工具", "tool", "server"],
  ];

  function normalizeSearchQuery(q) {
    return String(q || "")
      .trim()
      .toLowerCase();
  }

  function expandSearchTerms(query) {
    const q = normalizeSearchQuery(query);
    if (!q) return [];
    const terms = new Set([q]);
    const parts = q.split(/[\s,，、/|]+/).filter(Boolean);
    parts.forEach((p) => terms.add(p));
    ORCH_SYNONYM_GROUPS.forEach((group) => {
      const low = group.map((x) => x.toLowerCase());
      const hit = low.some((w) => q.includes(w) || parts.some((p) => w.includes(p) || p.includes(w)));
      if (hit) low.forEach((w) => terms.add(w));
    });
    return Array.from(terms);
  }

  function haystackForItem(item) {
    return [
      item.name,
      item.aliasCn,
      item.description,
      item.command,
      item.server,
      item.categoryHint,
      (item.tags || []).join(" "),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
  }

  function matchOrchSearch(item, query) {
    const q = normalizeSearchQuery(query);
    if (!q) return true;
    const hay = haystackForItem(item);
    const terms = expandSearchTerms(q);
    return terms.some((t) => hay.includes(t));
  }

  function classifyOrchText(text) {
    const t = String(text || "").toLowerCase();
    const ids = [];
    ORCH_CATEGORIES.forEach((cat) => {
      if (cat.id === "other") return;
      if (cat.keywords.some((k) => t.includes(k.toLowerCase()))) ids.push(cat.id);
    });
    return ids.length ? ids : ["other"];
  }

  function classifyOrchItem(item) {
    const boardCat = item.boardCategory || (item.board && item.board.category);
    if (boardCat && ORCH_CATEGORIES.some((c) => c.id === boardCat)) {
      const extra = (item.board && item.board.categories) || [];
      const ids = [boardCat];
      if (Array.isArray(extra)) {
        extra.forEach((c) => {
          if (c && c !== boardCat && ORCH_CATEGORIES.some((x) => x.id === c) && !ids.includes(c)) ids.push(c);
        });
      }
      return ids;
    }
    const text = [
      item.name,
      item.description,
      item.command,
      item.server,
      item.categoryHint,
      (item.tags || []).join(" "),
      item.board && (item.board.summary || ""),
      item.board && (item.board.tags || []).join(" "),
    ]
      .filter(Boolean)
      .join(" ");
    return classifyOrchText(text);
  }

  global.SBA_ORCH_CATALOG = {
    ORCH_CATEGORIES,
    expandSearchTerms,
    matchOrchSearch,
    classifyOrchItem,
    classifyOrchText,
  };
})(typeof window !== "undefined" ? window : globalThis);
