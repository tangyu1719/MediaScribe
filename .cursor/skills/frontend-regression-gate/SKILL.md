---
name: frontend-regression-gate
description: >-
  web_rebuild_v2 前端改动后的固定回测门禁：Vue export 静态校验、启动后端、浏览器控制台
  抓 Vue runtime 报错。凡修改 frontend/index.html、frontend/assets/js/app.js、
  frontend/assets/css/app.css、frontend/preview/* 后必须执行本 skill，再交付用户。
version: 1.0.0
---

# 前端改动固定回测（frontend-regression-gate）

## 何时必须执行

- 修改了 `frontend/index.html`、`frontend/assets/js/app.js`、`frontend/assets/css/**`、`frontend/preview/**`
- 在 Vue 模板里新增 `@click` / `@contextmenu` / `{{ fn() }}` 等绑定
- 在 `app.js` 的 `setup()` 里新增函数但未加入末尾 `return { ... }`

**不适用于**：仅改 `backend/`、仅改文档、仅改测试脚本。

## 根因（本仓库高频）

Vue 3 选项式/组合式挂载后，模板只能访问 **`return { ... }` 导出的标识符**。

典型报错：

```text
xxx is not defined · https://vuejs.org/error-reference/#runtime-0
```

常见失误：**写了 `function onFoo()` 且模板用了 `@click="onFoo()"`，但忘记写进 `return`；或写进 `return` 却漏写 `function` 定义**（如 `onQueueReadBadgeContext`）。

---

## 回测步骤（Agent 必须全部执行，不得跳过）

### 1. 静态门禁（必跑，30 秒内）

在仓库根目录执行（Windows 若无 `python` 用 `py`）：

```bash
py scripts/frontend/vue_export_gate.py
# 或
python scripts/frontend/vue_export_gate.py
```

- 退出码 `0`：通过
- 退出码 `1`：列出缺失 export/定义，**先修再往下**

改 `app.js` 时的自检清单：

1. 新增 `function fooBar()` → 末尾 `return { ..., fooBar, ... }` 必须有 `fooBar`
2. `index.html` 用了 `fooBar` → `app.js` 里必须有定义 + export
3. 改完再跑一遍 `vue_export_gate.py`

### 2. 启动服务（若未运行）

```bash
# Windows 仓库根
start_backend.bat
```

确认 `http://127.0.0.1:8000/` 可访问（禁止用 file:// 打开 html）。

### 3. 浏览器控制台回测（必跑）

使用 **cursor-ide-browser** MCP（或本地 Chrome DevTools）：

1. `browser_navigate` → `http://127.0.0.1:8000/`
2. 登录后进入 **本次改动涉及的页面**（至少包含改动点）：
   - 链接文档化 / 任务队列卡片 → 首页或链接页
   - AI 问答 → `page=chat`
   - Markdown 阅读 → `/preview/md.html?file=...`
3. `browser_console_messages` → **不得出现** `runtime-0`、`is not defined`、未捕获 Error
4. 对新增交互 **点一次**（如已读徽章左键/右键、备注保存/取消）

若控制台有 Vue 警告/错误，视为 **回测失败**，修复后从步骤 1 重跑。

### 4. 网络抽查（可选，改动涉及 API 时）

`browser_network_requests` 看相关 `POST/GET` 是否 4xx/5xx。

---

## 交付前输出（Agent 回复用户时附带）

```markdown
### 前端回测
- vue_export_gate: PASS / FAIL（附缺失符号列表）
- 浏览器页面: （列出实际打开的 URL）
- 控制台: 无 Vue runtime 错误 / （粘贴首条错误）
- 手点验证: （如：任务卡片已读右键弹窗 OK）
```

---

## 脚本位置

| 文件 | 作用 |
|------|------|
| `scripts/frontend/vue_export_gate.py` | 扫描 `index.html`/`md.html` 模板根符号 vs `app.js` return 导出 |

---

## 扩展（可选）

- 改动仅 `preview/md.html` 内联脚本：gate 会扫描 `md.html`；若新增全局函数仍需保证在 IIFE 内可访问
- 后续可加 Playwright 冒烟；当前以 **gate + 浏览器 console** 为最低固定回测
