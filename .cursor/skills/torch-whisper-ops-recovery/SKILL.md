---
name: torch-whisper-ops-recovery
description: >-
  修复 PyTorch/Whisper 转写环境损坏（T1012 DLL/_C、~orch 残留）、执行善后运维并重跑失败视频任务。
  在用户报告转写失败 T1003/T1012、DLL load failed importing _C、语音转文字批量失败、
  PyTorch 环境修复、断点恢复 transcribe 时使用。
version: 1.0.0
command: /torch-whisper-recover
---

# PyTorch/Whisper 转写环境恢复（torch-whisper-ops-recovery）

## 适用场景

- 视频链接沉淀在 **语音转文字** 阶段批量失败
- 日志出现 `ImportError: DLL load failed while importing _C`
- `pip show torch` 报 `Ignoring invalid distribution ~orch`
- `torch/__init__.py` 缺失，Whisper 池预热或转写借槽失败

## 标准错误码

| 码 | 标题 | 典型原文 |
|----|------|----------|
| **T1012** | PyTorch 环境损坏或 DLL 缺失 | `DLL load failed while importing _C` |
| T1001 | ffmpeg/ffprobe 缺失 | `找不到 ffmpeg/ffprobe` |
| T1003 | 语音转文字失败（泛化） | 无更具体 pattern 时的兜底 |

**MSG → 码映射**（`error_code_registry.classify_by_message`）：

- `DLL load failed.*_C` / `importing _C` / `~orch` / `torch/__init__.py` → **T1012**
- `找不到 ffmpeg|ffprobe` → **T1001**

## 执行流程（Agent 必须按序）

### 1. 诊断（只读）

```powershell
& 'D:\python解释器\python.exe' -c "import os; sp=__import__('site').getsitepackages()[0]; print('torch init', os.path.exists(os.path.join(sp,'torch','__init__.py'))); import importlib; print([x for x in os.listdir(sp) if 'orch' in x.lower()])"
& 'D:\python解释器\python.exe' -m pip show torch
```

判定：**T1012** 若 `~orch` 残留或 `torch/__init__.py` 不存在。

### 2. 修复环境

1. **停止**后端 uvicorn（释放 torch 文件锁）
2. 执行修复脚本：

```powershell
& 'D:\python解释器\python.exe' backend\scripts\fix_torch_env.py
```

3. 验收：

```powershell
& 'D:\python解释器\python.exe' -c "import torch; import whisper; print(torch.__version__); print('whisper ok')"
```

### 3. 重启后端并确认 Whisper 预热

启动日志须含：

```text
Whisper池 warmup 完成; core=4
```

### 4. 善后：断点恢复失败任务

```powershell
& 'D:\python解释器\python.exe' backend\scripts\retry_transcribe_failures_20260711.py
```

或单条 API：

```http
POST /api/history/restart
{"link":"<url>","platform":"小红书","action":"resume","task_id":"<id>"}
```

### 5. 验收

- `pipeline.log` 转写 SPAN `status=completed`
- 任务 `transcribe_error_code` 不再为 T1012/T1003
- 至少 1 条失败链接生成 `doc_path`

### 6. 记录

- 更新 `docs/dev-handbook/modules/link-pipeline-transcribe.md`
- 必要时触发 `skill-usage-archive` 归档

## 禁止

- 禁止用 MOCK 转写冒充修复成功
- 禁止在未修 torch 前反复点「重跑」同一批链接

---

## 第一版案例（2026-07-11）

### 使用任务/场景

用户批量沉淀 7+ 条小红书视频；7/10 因 **T1001 ffmpeg 缺失**失败，7/11 16:52 起因 **T1012 PyTorch 损坏**连续失败。链接识别与下载正常，断点复用 mp4 正常。

### 使用过程

| 步骤 | 动作 | 产出 |
|------|------|------|
| 1 | 查 `pipeline.log` + `history.json` | 定位 transcribe 统一失败，非链接 API |
| 2 | 本机 Python 诊断 | 发现 `~orch` 残留、`torch/__init__.py` 缺失 |
| 3 | 新增 `fix_torch_env.py` 清理并重装 torch 2.5.1+cpu | 环境恢复 |
| 4 | 补齐 T1012 explanation/remediation + MSG 映射 | `error_code_registry` / `ops_error_classifier` |
| 5 | 重启后端 + `retry_transcribe_failures_20260711.py` | 失败任务从 transcribe 恢复 |

### 产出文件

- `backend/scripts/fix_torch_env.py`
- `backend/scripts/retry_transcribe_failures_20260711.py`
- `src/agent/torch_runtime.py`（DLL 注入自检）
- `src/agent/error_code_registry.py`（T1012 解决办法）
- `.cursor/skills/torch-whisper-ops-recovery/SKILL.md`（本文件）

### 结果及效果

- 根因从泛化 T1003 收敛为可运维 **T1012**
- 用户可见 **错误解释 + 分步解决办法**（`get_error_remediation`）
- 修复后视频转写链路恢复，失败卡片可断点续跑

### 满意度 / 采纳度

- 目标达成：环境修复 + 错误码规范化 + SKILL 固化 — **5/5**（待用户确认转写任务最终 completed 数）

---

## 参考

- 运维提案：`backend/app/services/ops_exception_proposals.py` → `T1012`
- 转写入口：`src/agent/video_downloader.py` → `_prepare_whisper_runtime`
- 回归测试：`backend/tests/test_error_code_registry.py`
