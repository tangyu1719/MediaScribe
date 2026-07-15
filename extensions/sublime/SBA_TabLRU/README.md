# SBA_TabLRU — ST3 最近 10 个原文件恢复

启动 Sublime Text 3 时，按 **最近打开/激活/保存** 的时间顺序，恢复磁盘上仍存在的 **最近 10 个原文件**（第 11 个起 FIFO 淘汰，最多缓存 30 条路径）。

## 安装

将整个 `SBA_TabLRU` 文件夹复制到 ST3 **Packages 根目录**（不要放在 `User` 子目录）：

- Windows: `%APPDATA%\Sublime Text 3\Packages\SBA_TabLRU`
- macOS: `~/Library/Application Support/Sublime Text/Packages/SBA_TabLRU`

重启 Sublime Text。

## 行为说明

1. **只存路径，不存内容**：`sba_tab_lru.json` 仅记录原文件绝对路径与时间戳；恢复时用 `open_file` 直接从磁盘打开，不会生成副本缓冲。
2. **触发记录**：文件 `on_load` / 切换标签 `on_activated` / 保存 `on_post_save` 时更新 LRU。
3. **恢复范围**：跳过已打开的文件；只打开仍存在于磁盘的路径。
4. **与 ST 会话互斥**：若已关闭 `hot_exit` / `save_session` / `remember_open_files`，本插件负责恢复标签。

## 命令

- `SBA: Restore LRU Tabs` — 手动恢复最近 10 个原文件

## 配置项（改 `sba_tab_lru.py` 顶部常量）

| 常量 | 默认 | 含义 |
|------|------|------|
| `RESTORE_MAX` | 10 | 启动时恢复的文件数 |
| `STORE_MAX` | 30 | JSON 中保留的路径条数 |
| `STARTUP_DELAY_MS` | 400 | 启动延迟（毫秒） |

## 若打开后仍显示「未保存」圆点

多为 **换行符** 与 `Preferences` 里 `default_line_ending: unix` 不一致（Windows 下 CRLF 文件会被标脏），与 TabLRU 无关。可将该设置改为 `"Windows"`，或保存一次统一换行。
