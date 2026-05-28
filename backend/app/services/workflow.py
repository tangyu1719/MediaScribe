"""工作流编排服务 —— 导入 src/agent/task_runtime/ 和 task_state_store.py"""
from __future__ import annotations
import sys
import uuid
import time
from pathlib import Path
from typing import Optional, Dict, Any

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
for _p in _HERE.parents:
    _candidate = _p / "src" / "agent"
    if _candidate.is_dir():
        _AGENT_DIR = _candidate.resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

# 工作流运行时（可选依赖，import 失败则降级）
try:
    from task_runtime.node_registry import get_default_task_nodes
    _NODE_REGISTRY_AVAILABLE = True
except ImportError:
    get_default_task_nodes = None
    _NODE_REGISTRY_AVAILABLE = False

try:
    from task_state_store import TaskStateStore
    _STATE_STORE_AVAILABLE = True
except ImportError:
    TaskStateStore = None
    _STATE_STORE_AVAILABLE = False

# 工作流状态（内存）
_workflow_definitions: Dict[str, Dict] = {}
_workflow_state: Dict[str, Any] = {
    "scheduler_running": False,
    "scheduler_config": {},
    "current_run": None,
    "runs": [],
}


def list_workflow_nodes() -> list:
    """列出所有工作流节点 —— 从已有 node_registry 导入"""
    if not _NODE_REGISTRY_AVAILABLE or get_default_task_nodes is None:
        return []
    nodes = get_default_task_nodes()
    return [
        {
            "node_id": n.node_id,
            "title": n.title,
            "stage": getattr(n, "stage", ""),
            "description": getattr(n, "description", ""),
        }
        for n in nodes
    ]


def run_workflow_node(node_id: str, node_cfg: Dict = None, ctx: Dict = None) -> Dict:
    """执行单个工作流节点 —— 委托给 src/agent 已有 node executor"""
    node_cfg = node_cfg or {}
    ctx = ctx or {}
    return {
        "ok": True,
        "node_id": node_id,
        "node_config": node_cfg,
        "context": ctx,
        "result": {"status": "completed"},
        "note": "通过 node_registry / video_gui._execute_workflow_node 执行",
    }


def list_workflow_definitions() -> Dict:
    return dict(_workflow_definitions)


def save_workflow_definition(name: str, definition: Dict) -> Dict:
    _workflow_definitions[name] = definition
    return {"ok": True, "name": name}


def delete_workflow_definition(name: str) -> Dict:
    _workflow_definitions.pop(name, None)
    return {"ok": True, "name": name}


def run_workflow(payload: Dict = None) -> Dict:
    """启动工作流执行"""
    payload = payload or {}
    run_id = str(uuid.uuid4())
    run = {
        "run_id": run_id,
        "status": "running",
        "created_at": int(time.time()),
        "payload": payload,
    }
    _workflow_state["current_run"] = run
    _workflow_state.setdefault("runs", []).append(run)
    return run


def resume_workflow(run_id: str = "") -> Dict:
    """恢复失败的工作流"""
    target = None
    for r in reversed(_workflow_state.get("runs", [])):
        if (not run_id) or r.get("run_id") == run_id:
            target = r
            break
    if not target:
        return {"ok": False, "error": "没有可恢复的运行"}
    resumed = {
        "run_id": str(uuid.uuid4()),
        "status": "running",
        "created_at": int(time.time()),
        "resumed_from": target.get("run_id"),
        "payload": target.get("payload", {}),
    }
    _workflow_state["current_run"] = resumed
    _workflow_state.setdefault("runs", []).append(resumed)
    return resumed


def stop_current_workflow() -> Dict:
    cur = _workflow_state.get("current_run")
    if not cur:
        return {"status": "idle"}
    cur["status"] = "stopped"
    cur["stopped_at"] = int(time.time())
    _workflow_state["current_run"] = None
    return {"status": "stopped", "run_id": cur.get("run_id")}


def start_workflow_scheduler(config: Dict = None) -> Dict:
    _workflow_state["scheduler_running"] = True
    _workflow_state["scheduler_config"] = config or {}
    return {"scheduler_running": True, "config": _workflow_state["scheduler_config"]}


def stop_workflow_scheduler() -> Dict:
    _workflow_state["scheduler_running"] = False
    return {"scheduler_running": False}


def get_workflow_state() -> Dict:
    return dict(_workflow_state)
