"""MCP：使用 LangChain `langchain-mcp-adapters` 的 MultiServerMCPClient（与 TRAE 同类「连接后拉工具」模型）。

配置文件：`mcp_servers.json`，顶层字段 `servers` 为 dict，
键为服务别名，值为官方文档中的连接块（transport + command/args 或 url）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

# backend/app/services/mcp_langchain.py → parents[3] = web_rebuild_v2 根目录
_BASE = Path(__file__).resolve().parents[3]
_MCP_FILE = _BASE / "mcp_servers.json"


def mcp_config_path() -> Path:
    return _MCP_FILE


def _normalize_server_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """将 cwd / PYTHONPATH 解析为绝对路径，避免 WinError 267。"""
    out = dict(block)
    cmd = str(out.get("command") or "").strip().lower()
    if cmd in ("python", "python3", "py"):
        out["command"] = sys.executable
    cwd = str(out.get("cwd") or "").strip()
    if cwd:
        p = Path(cwd)
        if not p.is_absolute():
            p = (_BASE / cwd).resolve()
        out["cwd"] = str(p)
    env = out.get("env")
    if isinstance(env, dict):
        new_env = dict(env)
        pp = str(new_env.get("PYTHONPATH") or "").strip()
        if pp:
            parts = []
            for seg in pp.replace(";", ":").split(":"):
                seg = seg.strip()
                if not seg:
                    continue
                sp = Path(seg)
                if not sp.is_absolute():
                    sp = (_BASE / seg).resolve()
                parts.append(str(sp))
            new_env["PYTHONPATH"] = os.pathsep.join(parts)
        out["env"] = new_env
    return out


def load_mcp_server_dict() -> Dict[str, Any]:
    if not _MCP_FILE.exists():
        return {}
    try:
        data = json.loads(_MCP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    servers = data.get("servers")
    if isinstance(servers, dict):
        return {k: _normalize_server_block(v) for k, v in servers.items() if isinstance(v, dict)}
    return {}


def save_mcp_server_dict(servers: Dict[str, Any]) -> None:
    _MCP_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MCP_FILE.write_text(
        json.dumps({"servers": servers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def upsert_mcp_server(alias: str, block: Dict[str, Any]) -> Dict[str, Any]:
    """新增或更新单个 MCP 服务别名配置。"""
    alias = (alias or "").strip()
    if not alias:
        raise ValueError("服务别名不能为空")
    if not isinstance(block, dict):
        raise ValueError("配置须为 JSON 对象")
    servers = load_mcp_server_dict()
    servers[alias] = block
    save_mcp_server_dict(servers)
    return servers


def delete_mcp_server(alias: str) -> Dict[str, Any]:
    alias = (alias or "").strip()
    servers = load_mcp_server_dict()
    if alias not in servers:
        raise ValueError("服务不存在")
    del servers[alias]
    save_mcp_server_dict(servers)
    return servers


def get_mcp_server_block(alias: str) -> Optional[Dict[str, Any]]:
    alias = (alias or "").strip()
    servers = load_mcp_server_dict()
    blk = servers.get(alias)
    return blk if isinstance(blk, dict) else None


def _tool_openai_parameters(t: Any) -> Dict[str, Any]:
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        ot = convert_to_openai_tool(t)
        fn = ot.get("function") or {}
        return fn.get("parameters") or {}
    except Exception:
        return {}


async def mcp_sync_list_tools() -> Dict[str, Any]:
    """按服务别名分别连接 MCP，枚举工具并附带 JSON Schema 参数（供详情页「输入」展示）。"""
    servers = load_mcp_server_dict()
    if not servers:
        return {"ok": False, "error": "尚未配置 MCP：请在下方 JSON 填写 servers 后保存，再点「连接并拉取工具」。", "tools": [], "by_server": {}}

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore
    except ImportError:
        return {
            "ok": False,
            "error": "未安装 langchain-mcp-adapters。请在 backend 目录执行：py -3 -m pip install langchain-mcp-adapters",
            "tools": [],
            "by_server": {},
        }

    out: List[Dict[str, Any]] = []
    by_server: Dict[str, Any] = {}
    for alias, block in servers.items():
        if not isinstance(alias, str) or not isinstance(block, dict):
            continue
        try:
            client = MultiServerMCPClient({alias: block})
            tools = await client.get_tools()
        except Exception as e:
            by_server[alias] = {"ok": False, "error": str(e), "tools": []}
            continue
        st_tools: List[Dict[str, Any]] = []
        for t in tools:
            name = getattr(t, "name", None) or getattr(t, "name_", None) or str(t)
            desc = getattr(t, "description", None) or ""
            params = _tool_openai_parameters(t)
            row = {
                "server": alias,
                "name": str(name),
                "description": str(desc or ""),
                "input_schema": params,
            }
            out.append(row)
            st_tools.append(row)
        by_server[alias] = {"ok": True, "error": "", "tools": st_tools}

    if not out and by_server:
        first_err = next((v.get("error") for v in by_server.values() if v.get("error")), "")
        return {"ok": False, "error": first_err or "所有 MCP 服务连接失败", "tools": [], "by_server": by_server}

    return {"ok": True, "tools": out, "count": len(out), "by_server": by_server}


async def probe_mcp_server_health(alias: str) -> Dict[str, Any]:
    """单 MCP 服务连通性探测（健康检查用）。"""
    alias = (alias or "").strip()
    servers = load_mcp_server_dict()
    block = servers.get(alias)
    if not block:
        raise ValueError(f"MCP 服务不存在: {alias}")
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore
    except ImportError as ex:
        raise RuntimeError(
            "未安装 langchain-mcp-adapters，请在 backend 目录 pip install"
        ) from ex
    client = MultiServerMCPClient({alias: block})
    tools = await client.get_tools()
    return {"alias": alias, "tool_count": len(tools or [])}


async def mcp_get_langchain_tools() -> tuple[List[Any], str]:
    """返回 (LangChain BaseTool 列表, 错误信息)。无配置或失败时 tools=[] 且 error 非空。"""
    servers = load_mcp_server_dict()
    if not servers:
        return [], ""
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore
    except ImportError:
        return [], "未安装 langchain-mcp-adapters"
    import logging
    _log = logging.getLogger("sba.mcp")

    def _exc_text(e: BaseException) -> str:
        if hasattr(e, "exceptions"):
            parts = [_exc_text(x) for x in e.exceptions]  # type: ignore[attr-defined]
            return "; ".join(p for p in parts if p)
        return f"{type(e).__name__}: {e}"

    try:
        client = MultiServerMCPClient(servers)
        tools: Sequence[Any] = await client.get_tools()
        out = list(tools)
        _log.info(
            "[AI问答-MCP|mcp_langchain.mcp_get_langchain_tools|tools|硬编执行|完成] "
            "MCP 工具已加载; count=%s; servers=%s",
            len(out),
            ",".join(servers.keys()),
        )
        return out, ""
    except Exception as e:
        err = _exc_text(e)[:500]
        _log.warning(
            "[AI问答-MCP|mcp_langchain.mcp_get_langchain_tools|tools|硬编执行|失败] "
            "MCP 加载失败; error_message=%s",
            err,
        )
        return [], err
