---
name: web-to-android-responsive
description: >-
  Web 端转 Android/手机浏览器响应式适配：竖屏与横屏分轨、子菜单最小粒度逐页验收。
  在用户提及手机版、竖屏、横屏、移动端适配、WEB转安卓、响应式、375px、safe-area 时使用。
---

# WEB 端 → Android/手机浏览器 页面适配

本 Skill 专用于 **SuperBizAgent Web Rebuild V2**（`frontend/index.html` + `assets/css/app.css` + `assets/js/app.js`），将桌面多栏布局改造为可在 Android Chrome / iOS Safari 上可用的 **竖屏优先、横屏增强** 体验。

**参考规范（执行时必读）：**

- `web_migration/skills_downloaded/ui-ux-pro-max/SKILL.md` — **主规范**：`bottom-nav-limit`、`top-app-bar-android`、`mobile-first`、`horizontal-scroll` 禁止、`touch-friendly-input` 48px
- 本文件 — 本项目落地细则

## 核心原则（禁止再犯）

1. **竖屏 ≠ 横屏**：不得只用 `@media (max-width:768px)` 一套规则覆盖两种方向。
2. **子菜单最小粒度**：以 `page` + `*.sec` / `chatPanelTab` / `orchRail.tab` 为验收单元，一页一 PR、一验一勾。
3. **内容优先**：竖屏默认 **单列 + 底部/抽屉导航**；横屏可恢复 **双栏**（列表|详情），但主内容区宽度须 ≥ 60vw。
4. **禁止横向溢出**：表格改 **卡片列表** 或 **横向滚动容器 + sticky 首列**；不得裁切列头（如 `Sourc`）。
5. **触控 ≥ 44px**：按钮、Tab、侧栏图标最小点击区域 44×44dp（Material）。
6. **Safe area**：继续使用 `--ios-safe-*`；固定顶栏/底栏须 `padding-bottom: env(safe-area-inset-bottom)`。
7. **不改业务逻辑**：仅 CSS + 必要时少量 `app.js` 布局状态（如 `mobilePanel`）；禁止假 Agent / 假数据。

## 断点与方向矩阵（强制）

| 代号 | 条件 | 布局策略 |
|------|------|----------|
| `m-portrait` | `max-width:768px` **且** `orientation: portrait` | 单列；侧栏 → 底部 Tab 或汉堡抽屉；多栏 grid → stack |
| `m-landscape` | `max-width:932px` **且** `orientation: landscape` | 可双栏（min 240px + flex:1）；侧栏可缩为 48px 图标轨 |
| `t-portrait` | `769px–1024px` **且** portrait | Tablet：可 2 栏，侧栏可折叠 |
| `desktop` | `min-width:1025px` | 保持现有桌面布局 |

**CSS 文件组织：**

```
frontend/assets/css/
  app.css              # 桌面默认 + 共享 token
  mobile-shell.css     # Phase 0：壳层、nav-rail、topbar（新建，由 index 引入）
    cache.css
    agpz.css
    ...
```

在 `app.css` 末尾 **不得** 继续堆无方向的 `@media(max-width:768px)`；新规则必须带 `(orientation: portrait|landscape)` 或迁入 `mobile-shell.css`。

## 壳层改造标准（Phase 0）

### 竖屏 `m-portrait`

- `#app`：`flex-direction: column` 或 grid `auto 1fr`（顶/底 nav + 主区）
- `.nav-rail .sidebar`：**隐藏固定左侧 60px 轨**，改为：
  - **方案 A（推荐）**：`.mobile-bottom-nav` 固定底栏，5+ 项时「更多」进 Sheet
  - **方案 B**：左上角汉堡 → 全屏抽屉（含订阅/定时/设置子树）
- `.app-topbar--main`：Tab 横向滚动保留，但 **不得与侧栏同时占宽**
- `.p-60`：`padding-top` 仅留 topbar；`padding-bottom` 留 bottom-nav + safe-area

### 横屏 `m-landscape`

- 恢复 **窄侧栏 48px** 或 **左列表 + 右详情** 双栏
- `.agpz-grid`、`.kb-layout`、`.wr-layout` 等：允许 `grid-template-columns: minmax(200px,28vw) 1fr`
- 顶栏高度压缩：`--ios-nav-h: 44px`

## 页面模式 catalog

| 模式 | 典型页面 | 竖屏 | 横屏 |
|------|----------|------|------|
| **P1 表单+列表** | cache, sched | 过滤表单项 stack；表格→卡片 | 表+详情上下或左右 |
| **P2 三栏编辑** | agpz, orch-rail | 分步：库→编辑→历史（Tab 切换） | 库|编辑 双栏，历史折叠 |
| **P3 聊天** | chat/room | 全宽聊天气泡；会话列表进抽屉 | 列表|聊天 双栏 |
| **P4 双栏浏览** | rag, rss, reader, wr | 列表全屏，点进详情 overlay | 列表|详情 |
| **P5 仪表盘** | ops, tasks, video | 统计卡 2 列 grid；图表全宽 | 3–4 列 grid |
| **P6 配置表单** | settings/* | 单列表单，分组 `<details>` | 同竖屏或左导航右表单 |

## 单页验收清单（每个子菜单必跑）

1. **375×667 竖屏**（iPhone SE 逻辑）— 无横向滚动条、无列头截断
2. **844×390 横屏**（常见 Android）— 主操作区可见，不需缩放
3. **触控**：主要按钮可点，间距不误触
4. **登录态**：`http://<LAN-IP>:8000/` 真机 WiFi 访问（非 127.0.0.1）
5. **静态检查**：`cd frontend && npm run check`
6. **浏览器**：cursor-ide-browser 或 Playwright 截图归档 `docs/mobile-screenshots/<page>-<sec>/`

## 实施顺序

**必须按** `docs/plans/PLAN-mobile-responsive-submenus.md` 阶段执行；未完成 Phase 0 不得改单页。

## 反模式（本仓库已出现，禁止延续）

- 侧栏 60px + 顶栏 Tab + 三栏 grid **同时**出现在 360px 宽屏
- 仅缩小字号/圆角，不改变信息架构
- 表格 6 列硬塞 viewport
- 竖屏与横屏共用 `.agpz-grid{grid-template-columns:1fr}` 而无分步导航

## 与 app.js 的协作

允许新增 **纯 UI 状态**（须写入 `return`）：

- `mobileNavOpen` — 抽屉开关
- `mobileAgpzStep` — `'list'|'edit'|'hist'`
- `mobileKbPane` — `'files'|'detail'`

禁止为此引入新 npm 依赖；优先 CSS `display` + 现有 Vue `v-show`。

## 输出要求

每完成一个子菜单，在 PLAN 文档对应行更新：

- `status`: todo / doing / done
- `verified`: 375P / 844L / 真机
- `notes`: 关键选择器或 PR 链接
