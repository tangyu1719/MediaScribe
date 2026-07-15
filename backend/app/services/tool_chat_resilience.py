"""聊天链路工具失败：错误码分类、可配置重试、运维 Agent 唤醒、异常结案。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger(__name__)


def extract_error_code(message: str) -> str:
    from .ops_error_classifier import extract_error_code as _extract

    return _extract(message)


# 已知错误码 → 重试策略（标准码 S1001 等；运维/聊天链路）
_KNOWN_RETRY: Dict[str, Dict[str, Any]] = {
    "S1001": {
        "hint": "小红书 Cookie 未同步；重试前刷新 CDP Cookie",
        "pre_retry": "refresh_xhs_cookies",
    },
    "S1002": {
        "hint": "Chrome 未开 CDP；重试前探测 CDP 端口",
        "pre_retry": "probe_cdp",
    },
    "SUB_XHS_CDP_SEARCH_FAILED": {
        "hint": "CDP 已连接但搜索未命中；请打开含 red_id 的搜索结果 Tab",
        "pre_retry": None,
    },
    "S1003": {
        "hint": "HTTP 通道无登录 Cookie；CDP 可用时无需 JSON",
        "pre_retry": "refresh_xhs_cookies",
    },
    "SUB_XHS_BROWSER_UNAVAILABLE": {
        "hint": "本机浏览器不可用",
        "pre_retry": None,
    },
    "SUB_RED_ID_NOT_FOUND": {
        "hint": "未找到小红书号；可换 type=51 搜索或请用户提供 profile 链接",
        "pre_retry": "xhs_alt_search",
    },
    "SUB_PROFILE_UNREACHABLE": {
        "hint": "主页不可达",
        "pre_retry": None,
    },
}


def classify_tool_failure(
    *,
    tool_name: str,
    error_message: str = "",
    raw_out: Any = None,
) -> Dict[str, Any]:
    """解析工具失败：错误码、是否已知、用户可见摘要。"""
    err = str(error_message or "").strip()
    if not err and isinstance(raw_out, dict):
        err = str(raw_out.get("error") or raw_out.get("reason") or "")
    code = extract_error_code(err)
    if not code and isinstance(raw_out, dict):
        code = extract_error_code(str(raw_out.get("error_code") or ""))
    known = _KNOWN_RETRY.get(code) if code else None
    return {
        "tool_name": tool_name,
        "error_code": code,
        "error_message": err[:500],
        "known": bool(known),
        "retry_hint": (known or {}).get("hint") or "",
        "pre_retry": (known or {}).get("pre_retry") or "",
    }


async def run_pre_retry_hook(hook: str, *, tool_name: str, tool_args: Dict[str, Any]) -> None:
    """重试前钩子：命中已知错误码时执行确定性修复（非假步骤）。"""
    if hook == "refresh_xhs_cookies":
        try:
            from .xhs_local_browser import ensure_xhs_cookies_synced

            ensure_xhs_cookies_synced(force=True)
        except Exception as ex:
            _LOG.warning(
                "[AI问答-工具韧性|tool_chat_resilience.run_pre_retry_hook|xhs|硬编执行|失败] "
                "refresh_cookies; error_type=%s; error_message=%s",
                type(ex).__name__,
                str(ex)[:200],
            )
    elif hook == "probe_cdp":
        try:
            from .cookie_manager import find_cdp_port
            from .chrome_profile_prep import ensure_sba_cdp_chrome_running, is_cdp_ready

            port = find_cdp_port()
            if not port or not is_cdp_ready(port):
                port = ensure_sba_cdp_chrome_running(wait_sec=30.0)
            _LOG.info(
                "[AI问答-工具韧性|tool_chat_resilience.run_pre_retry_hook|cdp|硬编执行|探测] port=%s",
                port or "",
            )
        except Exception as ex:
            _LOG.warning(
                "[AI问答-工具韧性|tool_chat_resilience.run_pre_retry_hook|cdp|硬编执行|失败] "
                "error_type=%s; error_message=%s",
                type(ex).__name__,
                str(ex)[:200],
            )


def plan_tool_retry(
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    error_message: str,
    attempt: int,
    raw_out: Any = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    根据错误码调整下次重试参数；返回 (新 args, pre_retry hook)。
    attempt 为 0-based（第 1 次失败后 attempt=0 → 准备第 2 次调用）。
    """
    info = classify_tool_failure(
        tool_name=tool_name, error_message=error_message, raw_out=raw_out
    )
    args = dict(tool_args or {})
    hook = info.get("pre_retry") or None
    code = info.get("error_code") or ""

    if tool_name == "xhs_user_search" and code in (
        "S1001",
        "S1002",
        "S1003",
        "SUB_RED_ID_NOT_FOUND",
    ):
        hook = hook or ("probe_cdp" if code == "S1002" else "refresh_xhs_cookies")
    if tool_name == "web_search" and attempt >= 1:
        # 第二次起缩小 query，避免泛化搜索
        q = str(args.get("query") or args.get("q") or "").strip()
        if len(q) > 40:
            args["query"] = q[:40]

    return args, hook


def should_invoke_ops_agent(*, error_code: str, attempt: int, max_retry: int) -> bool:
    """未知错误码且已达第 2 次重试：唤醒运维 Agent 分析 MSG（后台，不阻塞）。"""
    if error_code and error_code in _KNOWN_RETRY:
        return False
    return attempt >= max(1, max_retry - 1)


def maybe_dispatch_ops_for_tool(
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    error_message: str,
    task_id: str = "",
    session_id: str = "",
) -> None:
    try:
        from .ops_hooks import ops_dispatch_log_incident

        msg = (
            f"工具 {tool_name} 失败; task_id={task_id}; session={session_id}; "
            f"args={json.dumps(tool_args, ensure_ascii=False)[:300]}; "
            f"err={str(error_message)[:400]}"
        )
        ops_dispatch_log_incident(msg, "ERROR", task_id=task_id or None)
    except Exception as ex:
        _LOG.warning(
            "[AI问答-工具韧性|tool_chat_resilience.maybe_dispatch_ops_for_tool|ops|Agent执行|跳过] "
            "error_type=%s",
            type(ex).__name__,
        )


def should_mark_task_abnormal(*, tool_name: str, fail_count: int, max_fail: int = 3) -> bool:
    """同一工具连续失败达上限 → 主任务标异常。"""
    return fail_count >= max(1, int(max_fail or 3))


def build_tool_failure_summary_block(
    *,
    failures: List[Dict[str, Any]],
    task_id: str = "",
) -> str:
    """生成面向用户的工具失败汇总（Markdown）。"""
    if not failures:
        return ""
    lines = ["## 工具执行失败汇总", ""]
    if task_id:
        lines.append(f"主任务 ID：`{task_id}`")
        lines.append("")
    for i, f in enumerate(failures[:8], 1):
        fn = f.get("tool_name") or "unknown"
        code = f.get("error_code") or "未知"
        em = str(f.get("error_message") or "")[:200]
        lines.append(f"{i}. **{fn}** — `{code}`")
        if em:
            lines.append(f"   - {em}")
    lines.append("")
    lines.append(
        "系统已将该主任务标记为**异常**。请根据上表修复环境（如 Chrome CDP / 小红书登录）"
        "后新建会话重试，或换一种实现方式（如直接提供主页链接）。"
    )
    return "\n".join(lines)


def build_react_rethink_hint(
    *,
    tool_name: str,
    error_code: str,
    error_message: str,
    fail_count: int,
) -> str:
    """注入 ReAct working：要求模型换工具或向用户说明，禁止同工具盲重试。"""
    return (
        f"【工具失败 · 须重新思考】工具 `{tool_name}` 第 {fail_count} 次失败。"
        f"错误码={error_code or '未知'}；详情={str(error_message)[:180]}。"
        "禁止无差别重复同一工具；应换用其他已注册工具（如 web_search / link_pipeline_start / cache_query）"
        "或基于已有上下文直接回答用户。若无法替代，须如实说明失败原因与建议操作。"
    )


def resolve_chat_tool_timeout_sec(tool_name: str, default_sec: float) -> float:
    """部分工具（如五阶段人物画像）需远超默认 60s。"""
    import os

    if tool_name == "xhs_user_search":
        return float(os.environ.get("CHAT_XHS_PROFILE_TIMEOUT_SEC", "3600") or 3600)
    return default_sec
