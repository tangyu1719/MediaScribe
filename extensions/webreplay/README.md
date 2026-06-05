# WebReplay（开源复刻）

基于 [webXport](https://webxport.cn) **逆向思路**自研的 Chrome 扩展：**DOM 事件录制 + 多策略元素定位重放**，无 License、无远程付费校验。

## 功能（v0.1）

| 能力 | 说明 |
|------|------|
| 录制 | 真实 `click` / 表单 `change`，生成 CSS + XPath + 文本快照 |
| 重放 | `element.click()` / 填表 + 事件，随机步间延迟，iframe `frameUrl` 切换 |
| 安全守护 | 支付/删除等敏感文案点击在重放时拒绝 |
| API 线索 | `webRequest` + 页面内 `fetch/XHR` 劫持，保存 `apiHints` 供人工/Agent 参考 |
| 定时 | `chrome.alarms` + 脚本 `schedule.timeOfDay`（`HH:mm`） |
| 导入导出 | JSON 备份全部脚本 |
| 本地 MCP | `chrome.runtime.sendMessage` 外部消息：`list_scripts` / `run_script` / `get_run_status` |

### 尚未实现（相对 webXport 0.3.x）

- 完整 **apiChains** 自动推断与 MAIN world 字节系 bdms 签名重放  
- 小红书 `x-s`/`x-t` 刷新  
- 云端账号、失败上报、License  

## 构建与加载

```bash
cd extensions/webreplay
npm install
node scripts/gen-icons.mjs
npm run build
```

Chrome → `chrome://extensions` → 开发者模式 → **加载已解压的扩展程序** → 选择 `extensions/webreplay/dist`。

## 与 SuperBizAgent 前端集成

登录 Web 后，侧栏底部 **「浏览器自动化」** 父菜单（独立于「设置」）提供：

- **脚本库**：导入/导出/查看步骤（数据存服务端 `api/webreplay/*`）
- **扩展连接**：保存扩展 ID 与 MCP 示例
- **使用指南**：构建与加载说明

访问路径：`http://127.0.0.1:8000/webreplay`

## 使用

1. 打开要自动化的后台页面（保持已登录）。  
2. 点扩展图标，输入脚本名 → **开始录制**。  
3. 在页面操作；右下角黄条可 **完成** / **取消**。  
4. Popup 里对脚本点 **重放**。  

## 本地 Agent 调用（MCP 风格）

需知扩展 ID（`chrome://extensions` 里查看），在**允许的来源**（localhost）页面或另一扩展中：

```javascript
const EXT_ID = '你的扩展ID';

chrome.runtime.sendMessage(
  EXT_ID,
  { method: 'list_scripts' },
  (res) => console.log(res)
);

chrome.runtime.sendMessage(
  EXT_ID,
  { method: 'run_script', params: { name: '千帆笔记' } },
  (res) => console.log(res)
);
```

## 脚本 JSON 结构

与 webXport 类似，见 `src/shared/types.ts` 中 `Script` / `ScriptStep`。

## 合规说明

本项目为**独立实现**，仅借鉴公开可见的浏览器自动化思路，**不复制** webXport 商业代码与品牌资源。仅供学习与内部自动化，请遵守目标网站 ToS。
