"""内置 Tool Call 完整 Schema：function calling 参数、步骤输出壳、源码定位。"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .builtin_tools import list_builtin_tools
from .chat_tool_registry import _BUILTIN_ID_TO_FN
from .tool_output_schema import SCHEMA_VERSION, build_tool_step_output

_SERVICES_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _SERVICES_DIR.parents[1]
_REGISTRY_FILE = _SERVICES_DIR / "chat_tool_registry.py"

# 工具页 ID → 主要实现模块（相对 backend/app/services/）
_TOOL_IMPL_MODULES: Dict[str, List[str]] = {
    "tool_link_pipeline": ["task_manager.py", "video_pipeline.py", "link_doc_routing.py"],
    "tool_xhs_user_search": ["creator_profile_runner.py", "xhs_local_browser.py"],
    "tool_rag_index": ["kb_rag.py"],
    "tool_rag_search": ["kb_rag.py", "milvus_rag_query.py"],
    "tool_doc_analyze": ["document.py"],
    "tool_cache_rw": ["cache.py"],
    "tool_ops_snapshot": ["ops_overview.py"],
    "tool_comment_scraper": ["comment_scraper.py"],
    "tool_rss_reader": ["rss_reader.py"],
    "tool_xhs_cookie_sync": ["cookie_manager.py", "xhs_local_browser.py"],
    "tool_local_file_list": ["local_file_tools.py"],
    "tool_local_file_read": ["local_file_tools.py"],
    "tool_local_file_write": ["local_file_tools.py"],
    "tool_local_file_mkdir": ["local_file_tools.py"],
    "tool_local_file_move": ["local_file_tools.py"],
    "tool_local_file_copy": ["local_file_tools.py"],
    "tool_local_file_find": ["local_file_tools.py"],
    "tool_local_file_grep": ["local_file_tools.py"],
    "tool_local_file_info": ["local_file_tools.py"],
    "tool_local_file_delete": ["local_file_tools.py"],
}


def _find_builtin_meta(tool_id: str) -> Optional[Dict[str, Any]]:
    tid = (tool_id or "").strip()
    for row in list_builtin_tools():
        if row.get("id") == tid:
            return dict(row)
    return None


def resolve_invoke_name(tool_id: str) -> str:
    tid = (tool_id or "").strip()
    if tid in _BUILTIN_ID_TO_FN:
        return _BUILTIN_ID_TO_FN[tid]
    return tid.replace("tool_", "")


def _get_structured_tool(invoke_name: str) -> Any:
    from .chat_tool_registry import build_internal_chat_tools

    name = (invoke_name or "").strip()
    for tool in build_internal_chat_tools(read_comments=True):
        if getattr(tool, "name", None) == name:
            return tool
    return None


def _tool_parameters_schema(tool: Any) -> Dict[str, Any]:
    if tool is None:
        return {"type": "object", "properties": {}, "required": []}
    for attr in ("tool_call_schema", "args_schema"):
        val = getattr(tool, attr, None)
        if val is None:
            continue
        try:
            if hasattr(val, "model_json_schema"):
                sch = val.model_json_schema()
            elif hasattr(val, "schema"):
                sch = val.schema()
            elif isinstance(val, dict):
                sch = val
            else:
                continue
            if isinstance(sch, dict):
                params = sch.get("parameters") if isinstance(sch.get("parameters"), dict) else sch
                if isinstance(params, dict) and params.get("type") == "object":
                    return params
        except Exception:
            pass
    try:
        inp = tool.get_input_schema()
        if hasattr(inp, "model_json_schema"):
            sch = inp.model_json_schema()
            if isinstance(sch, dict):
                props = sch.get("properties") or {}
                req = sch.get("required") or []
                return {"type": "object", "properties": props, "required": req}
    except Exception:
        pass
    return {"type": "object", "properties": {}, "required": []}


def _ui_inputs_to_properties(inputs: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    props: Dict[str, Any] = {}
    required: List[str] = []
    type_map = {
        "string": "string",
        "int": "integer",
        "integer": "integer",
        "bool": "boolean",
        "boolean": "boolean",
        "float": "number",
        "number": "number",
        "array": "array",
        "object": "object",
    }
    for row in inputs or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        raw_t = str(row.get("type") or "string").lower()
        prop: Dict[str, Any] = {"type": type_map.get(raw_t, "string")}
        hint = str(row.get("hint") or "").strip()
        if hint:
            prop["description"] = hint
        props[name] = prop
        if row.get("required"):
            required.append(name)
    return props, required


def _merge_parameters(
    fc_params: Dict[str, Any],
    ui_props: Dict[str, Any],
    ui_required: List[str],
) -> Dict[str, Any]:
    fc = dict(fc_params or {})
    props = dict(fc.get("properties") or {})
    required = list(fc.get("required") or [])
    for k, v in ui_props.items():
        if k not in props:
            props[k] = v
        elif isinstance(v, dict) and v.get("description") and isinstance(props.get(k), dict):
            if not props[k].get("description"):
                props[k]["description"] = v["description"]
    for k in ui_required:
        if k not in required:
            required.append(k)
    return {"type": "object", "properties": props, "required": required}


def _parse_registry_function(invoke_name: str) -> Tuple[int, int, List[str]]:
    """从 chat_tool_registry 定位工具函数行号与函数内 import 模块。"""
    if not _REGISTRY_FILE.is_file():
        return 0, 0, []
    text = _REGISTRY_FILE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0, 0, []
    target = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == invoke_name:
            target = node
            break
        if isinstance(node, ast.Assign):
            # async def xhs_user_search 前可能有赋值，跳过
            continue
    if target is None:
        # 嵌套定义：StructuredTool.from_function 内的 def
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == invoke_name:
                target = node
                break
    if target is None:
        return 0, 0, []
    deps: List[str] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.ImportFrom) and sub.module:
            mod = sub.module
            if mod.startswith("."):
                mod = mod.lstrip(".")
            deps.append(mod.replace(".", "/") + ".py")
        elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if isinstance(sub.func.value, ast.Name):
                deps.append(sub.func.value.id)
    start = getattr(target, "lineno", 0) or 0
    end = getattr(target, "end_lineno", start) or start
    return start, end, sorted(set(deps))


def resolve_source_files(tool_id: str, invoke_name: str) -> List[str]:
    rels: List[str] = ["backend/app/services/chat_tool_registry.py"]
    for mod in _TOOL_IMPL_MODULES.get(tool_id or "", []):
        p = f"backend/app/services/{mod}"
        if p not in rels:
            rels.append(p)
    _, _, inner_deps = _parse_registry_function(invoke_name)
    for dep in inner_deps:
        if dep.endswith(".py"):
            p = f"backend/app/services/{dep}"
            if p not in rels:
                rels.append(p)
    return rels


def read_tool_source_bundle(tool_id: str, *, max_chars: int = 14000) -> str:
    """读取工具注册函数 + 实现模块片段，供流程图 Agent 分析。"""
    meta = _find_builtin_meta(tool_id) or {}
    invoke = resolve_invoke_name(tool_id)
    parts: List[str] = [
        f"工具页 ID: {tool_id}",
        f"调用名: {invoke}",
        f"名称: {meta.get('name') or invoke}",
        f"说明: {meta.get('description') or ''}",
    ]
    start, end, _ = _parse_registry_function(invoke)
    if _REGISTRY_FILE.is_file() and start > 0:
        lines = _REGISTRY_FILE.read_text(encoding="utf-8").splitlines()
        snippet = "\n".join(lines[max(0, start - 1) : min(len(lines), end + 40)])
        parts.append("\n--- chat_tool_registry 工具定义 ---\n")
        parts.append(snippet)
    cap_left = max_chars - sum(len(p) for p in parts)
    for rel in resolve_source_files(tool_id, invoke)[1:]:
        if cap_left <= 800:
            break
        sub = rel.split("backend/", 1)[-1] if "backend/" in rel else rel
        abs_p = (_BACKEND_ROOT / sub).resolve()
        if not abs_p.is_file():
            continue
        try:
            body = abs_p.read_text(encoding="utf-8")
        except OSError:
            continue
        if len(body) > cap_left:
            body = body[: cap_left // 2] + "\n\n…[源码中段截断]…\n\n" + body[-cap_left // 2 :]
        parts.append(f"\n--- {rel} ---\n")
        parts.append(body)
        cap_left -= len(body)
    out = "\n".join(parts)
    return out[:max_chars]


def build_full_tool_schema(tool_id: str) -> Dict[str, Any]:
    tid = (tool_id or "").strip()
    meta = _find_builtin_meta(tid)
    if not meta:
        return {"ok": False, "error": f"未知内置工具: {tid}"}
    invoke = resolve_invoke_name(tid)
    tool = _get_structured_tool(invoke)
    fc_params = _tool_parameters_schema(tool)
    ui_props, ui_required = _ui_inputs_to_properties(meta.get("inputs") or [])
    parameters = _merge_parameters(fc_params, ui_props, ui_required)
    description = str(getattr(tool, "description", None) or meta.get("description") or "")
    source_files = resolve_source_files(tid, invoke)
    start, end, inner_deps = _parse_registry_function(invoke)
    step_shell = build_tool_step_output(
        tool_name=invoke,
        tool_args={k: f"<{k}>" for k in (parameters.get("required") or [])[:3]},
        tool_result={"ok": True, "result_msg": "示例返回值"},
        phase="tool",
    )
    schema: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool_id": tid,
        "invoke_name": invoke,
        "display_name": meta.get("name") or tid,
        "version": meta.get("version") or "1.0.0",
        "kind": meta.get("kind") or "tool_call",
        "impl": meta.get("impl") or "internal",
        "description": description,
        "function_calling": {
            "name": invoke,
            "description": description,
            "parameters": parameters,
        },
        "ui_inputs": meta.get("inputs") or [],
        "output": {
            "summary": str(meta.get("outputs") or ""),
            "format": "json_string",
            "envelope_hint": "工具返回经 json.dumps 的 JSON 字符串；常见字段 ok / error / hint / result_msg。",
            "sse_step_output_template": step_shell,
        },
        "implementation": {
            "registry_file": "backend/app/services/chat_tool_registry.py",
            "registry_function": invoke,
            "registry_lines": [start, end] if start else [],
            "source_files": source_files,
            "inner_imports": inner_deps,
        },
    }
    if meta.get("mcp_server"):
        schema["mcp_server"] = meta["mcp_server"]
    return {"ok": True, "schema": schema}


def dumps_full_schema(tool_id: str) -> str:
    data = build_full_tool_schema(tool_id)
    if not data.get("ok"):
        return json.dumps(data, ensure_ascii=False, indent=2)
    return json.dumps(data.get("schema"), ensure_ascii=False, indent=2)
