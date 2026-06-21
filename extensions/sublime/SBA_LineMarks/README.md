# SBA_LineMarks — ST3/ST4 行标记插件

与 SuperBizAgent Web MD 预览页共用侧车文件，标记可在浏览器与 Sublime Text 之间互通。

## 安装

将整个 `SBA_LineMarks` 文件夹复制到 Sublime 的 **Packages 根目录**（不要放在 `User` 子目录，否则 ST3 不会加载 `.py`）：

- Windows: `%APPDATA%\Sublime Text 3\Packages\SBA_LineMarks`
- macOS: `~/Library/Application Support/Sublime Text/Packages/SBA_LineMarks`

**Ctrl+Q 请继续绑定 ColorMarker**（文末 `标记内容汇总` + 全字符匹配 + `[CNT=n]`，只拷 `.md` 即可迁移）：

```json
{ "keys": ["ctrl+q"], "command": "color_marker", "args": { "action": "mark" } }
```

SBA 命令（F2 跳转 / 命令面板 Toggle）会同步写入同款文末块；侧车 JSON 仅为加速，非 portable 主路径。

重启 Sublime Text。

## 打开文件时自动恢复红框

1. **优先**读取 MD 文末 `标记内容汇总` 块（与 ColorMarker / Web 保存格式一致）
2. 支持 `[CNT=n]`、Markdown 加粗（`**...**`）等模糊匹配
3. 若无文末块，再读侧车 `*.sublime-marks.json`
4. 大文件打开后会延迟 300ms / 1200ms 二次尝试恢复（避免 ST 尚未加载完正文）

命令面板：`SBA: Reload Line Marks`（或 `sba_reload_line_marks`）可手动从文末汇总重载。

## 侧车格式

与 MD 同目录：`你的文件.md.sublime-marks.json`

```json
{
  "version": 2,
  "schema": "sublime-span-marks",
  "file": "062-08-05-示例_图文分析.md",
  "marks": [
    { "line": 12, "name": "中小公司", "start": 450, "end": 454 }
  ]
}
```

- 行号为 **1-based**（与 ST 行号一致），**每行最多一处标记**。
- 有 `start`/`end` 时为选区标记（与 Web 预览红框一致）；仅有 `line` 时为整行标记。
- **Web 预览**保存时会写入 MD 文件末尾的 **ColorMarker 兼容块**（`标记内容汇总` / `COUNT` / `ORDER_FP` / `[CNT=n]`），与 ST3 插件 `ColorMarker.py` 互通；侧车 `*.sublime-marks.json` 同步保留。

## 快捷键

| 按键 | 功能 |
|------|------|
| Ctrl+Q | **切换当前行标记**：该行已标记则取消；未标记时有选区则标记选区，无选区则标记整行 |
| F2 | 下一标记（按行号） |
| Shift+F2 | 上一标记（按行号） |

> 若与 ST 宏录制 Ctrl+Q 冲突，可在 `User/Default (*.sublime-keymap)` 中改绑。

## 原理

与 SublimeBookmarks `toggle_line` 类似：按**行**切换标记，持久化到侧车 JSON；打开文件时 `add_regions(..., PERSISTENT)` 显示书签 gutter。

Web 预览页写入同一 JSON，因此在 ST3 中打开 output 目录下的 MD 即可看到相同标记。
