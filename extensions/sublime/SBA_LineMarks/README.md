# SBA_LineMarks — ST3/ST4 行标记插件

与 SuperBizAgent Web MD 预览页共用侧车文件，标记可在浏览器与 Sublime Text 之间互通。

## 安装

将整个 `SBA_LineMarks` 文件夹复制到 Sublime 的 Packages 目录：

- Windows: `%APPDATA%\Sublime Text\Packages\User\SBA_LineMarks`
- macOS: `~/Library/Application Support/Sublime Text/Packages/User/SBA_LineMarks`

重启 Sublime Text。

## 侧车格式

与 MD 同目录：`你的文件.md.sublime-marks.json`

```json
{
  "version": 1,
  "schema": "sublime-line-marks",
  "file": "062-08-05-示例_图文分析.md",
  "marks": [
    { "line": 12, "name": "小红书图文分析" }
  ]
}
```

行号为 **1-based**（与 ST 行号一致）。

## 快捷键

| 按键 | 功能 |
|------|------|
| Ctrl+Q | 切换当前行标记 |
| F2 | 下一标记 |
| Shift+F2 | 上一标记 |

> 若与 ST 宏录制 Ctrl+Q 冲突，可在 `User/Default (*.sublime-keymap)` 中改绑。

## 原理

与 SublimeBookmarks `toggle_line` 类似：按**行**切换标记，持久化到侧车 JSON；打开文件时 `add_regions(..., PERSISTENT)` 显示书签 gutter。

Web 预览页写入同一 JSON，因此在 ST3 中打开 output 目录下的 MD 即可看到相同标记。
