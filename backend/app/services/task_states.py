"""主/子任务状态枚举（产品口径优先，与 HITL 双层快照兼容）。"""
from __future__ import annotations

# 主任务（父任务）状态
PARENT_CREATED = "created"           # 已创建
PARENT_SUMMARIZING = "summarizing"   # 摘要中
PARENT_PLANNING = "planning"        # 计划中
PARENT_EXECUTING = "executing"       # 执行中
PARENT_PAUSED = "paused"             # 暂停中（用户或校验触发）
PARENT_ABNORMAL = "abnormal"         # 异常中
PARENT_RESOLVED = "resolved"         # 已解决（AI 认为合理完成）
PARENT_FAILED = "failed"             # 已失败
PARENT_CLOSED = "closed"             # 已结案（用户手动或 AI 确认成功后的终态）

PARENT_LABELS = {
    PARENT_CREATED: "已创建",
    PARENT_SUMMARIZING: "摘要中",
    PARENT_PLANNING: "计划中",
    PARENT_EXECUTING: "执行中",
    PARENT_PAUSED: "暂停中",
    PARENT_ABNORMAL: "异常中",
    PARENT_RESOLVED: "已解决",
    PARENT_FAILED: "已失败",
    PARENT_CLOSED: "已结案",
}

PARENT_TERMINAL = {PARENT_RESOLVED, PARENT_FAILED, PARENT_CLOSED}

# 主任务状态机：仅允许列出的目标态（禁止异常→已解决等越权跳转）
PARENT_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    PARENT_ABNORMAL: (PARENT_PAUSED, PARENT_CLOSED),
    PARENT_RESOLVED: (PARENT_PAUSED, PARENT_CLOSED),
    PARENT_EXECUTING: (PARENT_PAUSED, PARENT_ABNORMAL, PARENT_RESOLVED),
    PARENT_PAUSED: (PARENT_EXECUTING, PARENT_CLOSED, PARENT_ABNORMAL),
    PARENT_PLANNING: (PARENT_PAUSED, PARENT_EXECUTING, PARENT_CLOSED),
    PARENT_SUMMARIZING: (PARENT_PLANNING, PARENT_PAUSED, PARENT_CLOSED),
    PARENT_CREATED: (PARENT_PLANNING, PARENT_PAUSED, PARENT_CLOSED),
    PARENT_FAILED: (PARENT_CLOSED, PARENT_PAUSED),
}

# 子任务（ReAct 子 plan / 子步骤）状态
SUB_THINKING = "thinking"      # 思考中
SUB_ACTING = "acting"          # 执行中（工具/动作）
SUB_DONE = "done"            # 完成

SUB_LABELS = {
    SUB_THINKING: "思考中",
    SUB_ACTING: "执行中",
    SUB_DONE: "完成",
}

# 工具调用（挂在子 plan 下）状态
TOOL_RUNNING = "running"
TOOL_DONE = "done"

TOOL_LABELS = {
    TOOL_RUNNING: "执行中",
    TOOL_DONE: "已完成",
}
