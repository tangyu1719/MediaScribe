/**
 * 能力看板筛选/排序逻辑回归（与 app.js orchBoardFilteredItems 对齐）
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CATALOG = join(ROOT, "assets/js/orch_catalog.js");

function loadCatalog() {
  const src = readFileSync(CATALOG, "utf8");
  const ctx = { window: {}, globalThis: {} };
  ctx.window = ctx.globalThis;
  vm.runInNewContext(src, ctx);
  return ctx.window.SBA_ORCH_CATALOG;
}

function parseIsoMs(v) {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  const s = String(v || "").trim();
  if (!s) return 0;
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : 0;
}

function joinCutoffMs(range) {
  const r = String(range || "all");
  if (r === "all") return 0;
  const days = r === "7d" ? 7 : r === "30d" ? 30 : r === "90d" ? 90 : 0;
  if (!days) return 0;
  return Date.now() - days * 86400000;
}

function matchItem(cat, item, q) {
  if (!cat || !String(q || "").trim()) return true;
  return cat.matchOrchSearch(item, q);
}

function filterBoardItems(cat, items, { tab, q, joinRange }) {
  const joinCut = joinCutoffMs(joinRange);
  return items.filter((it) => {
    if (tab === "skill" && it.type !== "skill") return false;
    if (tab === "mcp" && it.type === "skill") return false;
    if (joinCut > 0) {
      if (!it.joinMs || it.joinMs < joinCut) return false;
    }
    return matchItem(cat, it, q);
  });
}

function sortBoardItems(items, sort) {
  const arr = [...items];
  if (sort === "join_desc") return arr.sort((a, b) => parseIsoMs(b.joinMs) - parseIsoMs(a.joinMs));
  if (sort === "join_asc") return arr.sort((a, b) => parseIsoMs(a.joinMs) - parseIsoMs(b.joinMs));
  if (sort === "heat_desc") return arr.sort((a, b) => (b.usageCount || 0) - (a.usageCount || 0));
  return arr;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const cat = loadCatalog();
const now = Date.now();
const day = 86400000;
const sampleItems = [
  {
    type: "skill",
    name: "lark-doc",
    aliasCn: "飞书文档",
    description: "创建编辑飞书云文档",
    command: "/lark-doc",
    joinMs: now - 2 * day,
    usageCount: 10,
    tags: [],
  },
  {
    type: "skill",
    name: "arxiv-search",
    aliasCn: "论文检索",
    description: "arxiv scholar search",
    command: "/arxiv",
    joinMs: now - 40 * day,
    usageCount: 2,
    tags: [],
  },
  {
    type: "mcp",
    name: "browser_navigate",
    description: "open url",
    server: "cursor-ide-browser",
    joinMs: 0,
    usageCount: 5,
    tags: [],
  },
  {
    type: "mcp-server",
    name: "my-mcp",
    aliasCn: "自定义MCP",
    description: "local server",
    joinMs: 0,
    usageCount: 0,
    tags: [],
  },
];

// tab 筛选
const skillOnly = filterBoardItems(cat, sampleItems, { tab: "skill", q: "", joinRange: "all" });
assert(skillOnly.length === 2, `SKILL tab 应剩 2 条，实际 ${skillOnly.length}`);

const mcpOnly = filterBoardItems(cat, sampleItems, { tab: "mcp", q: "", joinRange: "all" });
assert(mcpOnly.length === 2, `MCP tab 应剩 2 条，实际 ${mcpOnly.length}`);

// 加入时间筛选
const recent = filterBoardItems(cat, sampleItems, { tab: "all", q: "", joinRange: "7d" });
assert(recent.length === 1 && recent[0].name === "lark-doc", `近7天应只剩 lark-doc，实际 ${recent.map((x) => x.name).join(",")}`);

const month = filterBoardItems(cat, sampleItems, { tab: "all", q: "", joinRange: "30d" });
assert(month.length === 1, `近30天 SKILL 仅 1 条在范围内，实际 ${month.length}`);

// 关键词搜索
const byCn = filterBoardItems(cat, sampleItems, { tab: "all", q: "飞书", joinRange: "all" });
assert(byCn.length === 1 && byCn[0].name === "lark-doc", "中文别名搜索应命中 lark-doc");

const byEn = filterBoardItems(cat, sampleItems, { tab: "all", q: "arxiv", joinRange: "all" });
assert(byEn.length === 1 && byEn[0].name === "arxiv-search", "英文名搜索应命中 arxiv-search");

// 排序
const heatSorted = sortBoardItems(sampleItems.filter((x) => x.type === "skill"), "heat_desc");
assert(heatSorted[0].name === "lark-doc", "热度降序首项应为 lark-doc");

const joinSorted = sortBoardItems(sampleItems.filter((x) => x.type === "skill"), "join_asc");
assert(joinSorted[0].name === "arxiv-search", "加入时间升序首项应为 arxiv-search");

// v-model 导出门禁（修复 orchBoardJoinRange / orchBoardSort 漏 export）
const appSrc = readFileSync(join(ROOT, "assets/js/app.js"), "utf8");
const html = readFileSync(join(ROOT, "index.html"), "utf8");
const retM = appSrc.match(/return\{([\s\S]*?)\};\s*\}\}\);/);
assert(retM, "app.js return 块未找到");
const exports = new Set(
  retM[1]
    .split(",")
    .map((p) => p.trim().split(":")[0].trim())
    .filter(Boolean)
);
for (const name of ["orchBoardJoinRange", "orchBoardSort", "orchToolSearch", "orchBoardTab"]) {
  assert(exports.has(name), `${name} 须在 Vue return 中导出`);
}
const vModelRe = /v-model(?:\.[\w-]+)*="([^"]+)"/g;
let vmM;
while ((vmM = vModelRe.exec(html)) !== null) {
  const expr = vmM[1].trim();
  if (/^orchBoard/.test(expr)) {
    assert(exports.has(expr), `模板 v-model="${expr}" 须在 return 导出`);
  }
}

console.log("[test_orch_board_filter] ok; tab/join/search/sort/export 回归通过");
