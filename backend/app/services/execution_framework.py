"""
执行框架策略：ReAct / Plan-Execute（对齐 docs/ReAct 和 Plan-Execute 具体实现流程.md）。

- 简单任务：单次回答（不走本模块）
- 复杂任务：编排 handoff 后必须由策略驱动多轮 Thought → Action → Observation
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .tool_output_schema import (
    parse_inline_tool_calls_from_content,
    sanitize_user_visible_answer_text,
)


@dataclass
class ExecutionContext:
    """执行段上下文（由 ai_chat handoff 注入）。"""

    framework: str = "react"
    use_main_task: bool = True
    web_search: bool = False
    read_comments: bool = False
    react_round_idx: int = 0
    tool_round: int = 0
    web_block: Optional[Dict[str, Any]] = None
    plan_steps: List[str] = field(default_factory=list)
    enhancement_snapshot: Dict[str, Any] = field(default_factory=dict)
    react_memory: List[Dict[str, str]] = field(default_factory=list)
    min_tool_rounds: int = 1


class ExecutionFrameworkStrategy(ABC):
    """复杂任务执行策略基类。"""

    name: str = "base"

    @abstractmethod
    def min_rounds_before_finalize(self, ctx: ExecutionContext) -> int:
        ...

    @abstractmethod
    def continuation_system_hint(self, ctx: ExecutionContext, *, reason: str) -> str:
        ...

    def normalize_tool_calls(
        self,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if tool_calls:
            return list(tool_calls)
        return parse_inline_tool_calls_from_content(content or "")

    def should_finalize_without_tools(
        self,
        ctx: ExecutionContext,
        *,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]],
    ) -> bool:
        """是否允许在无 tool_calls 时结束 ReAct 环并进入最终回答。"""
        calls = self.normalize_tool_calls(content, tool_calls)
        if calls:
            return False

        if not ctx.use_main_task:
            cleaned = sanitize_user_visible_answer_text(content or "")
            return bool(cleaned)

        fw = (ctx.framework or "react").strip().lower()
        if fw not in ("react", "plan_execute", ""):
            return True

        min_r = self.min_rounds_before_finalize(ctx)
        if ctx.react_round_idx < min_r:
            return False

        if ctx.web_search:
            results = (ctx.web_block or {}).get("results") or []
            if not results and ctx.react_round_idx < max(min_r, 2):
                return False

        cleaned = sanitize_user_visible_answer_text(content or "")
        raw = (content or "").strip()
        if raw and not cleaned:
            return False
        if not cleaned and raw:
            return False

        return True


class ReActFrameworkStrategy(ExecutionFrameworkStrategy):
    """
    ReAct：Observe(推理分析) → Act(工具) → Observation → 循环。
    对齐老项目：复杂任务默认框架，禁止首轮无工具直接收尾。
    """

    name = "react"

    def min_rounds_before_finalize(self, ctx: ExecutionContext) -> int:
        return max(1, ctx.min_tool_rounds)

    def continuation_system_hint(self, ctx: ExecutionContext, *, reason: str) -> str:
        hints = ctx.enhancement_snapshot.get("search_keyword_queries") or []
        kw = "、".join(str(x) for x in hints[:5])
        base = (
            "【执行框架 · 推理分析】你处于 ReAct 执行段，尚未完成信息采集。"
            "必须先通过 function calling 调用目录中的具名工具（如 web_search、rag_search），"
            "禁止在正文中输出 <|FunctionCallBegin|> 或编造工具结果。"
            "完成至少一轮真实工具调用并拿到 Observation 后，才能给出面向用户的最终结论。"
        )
        if reason == "no_tool_calls":
            extra = "上一轮未产生有效工具调用，请说明缺什么信息并选定工具。"
        elif reason == "web_pending":
            extra = "用户已开启联网搜索，请使用 web_search（多关键词）获取外部信息。"
        elif reason == "inline_only":
            extra = "检测到正文中的工具标记但未执行，请改用 function calling。"
        elif reason == "async_pipeline_pending":
            extra = (
                "后台 link_pipeline 仍在执行；请等待 Observation 回灌或调用 cache_query，"
                "禁止在未完成时声称已产出文档。"
            )
        else:
            extra = "请继续推理并调用工具。"
        if kw:
            extra += f" 建议检索词：{kw}。"
        return f"{base}\n{extra}"


class PlanExecuteFrameworkStrategy(ReActFrameworkStrategy):
    """
    Plan-Execute：先按 plan_steps 分步，每步子任务内仍走 ReAct 单轮 Action 截断。
    """

    name = "plan_execute"

    def min_rounds_before_finalize(self, ctx: ExecutionContext) -> int:
        steps = ctx.plan_steps or []
        return max(2, len(steps), ctx.min_tool_rounds)

    def continuation_system_hint(self, ctx: ExecutionContext, *, reason: str) -> str:
        steps = ctx.plan_steps or []
        plan_txt = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps[:8]))
        hint = super().continuation_system_hint(ctx, reason=reason)
        if plan_txt:
            hint += f"\n【计划步骤】\n{plan_txt}\n请按当前进度执行下一步所需工具。"
        return hint


_STRATEGIES: Dict[str, ExecutionFrameworkStrategy] = {
    "react": ReActFrameworkStrategy(),
    "plan_execute": PlanExecuteFrameworkStrategy(),
}


def get_execution_strategy(framework: str) -> ExecutionFrameworkStrategy:
    key = (framework or "react").strip().lower()
    if key == "plan_execute":
        return _STRATEGIES["plan_execute"]
    return _STRATEGIES["react"]
