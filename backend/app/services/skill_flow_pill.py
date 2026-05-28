"""药丸流程图（DeskHub 风格）结构校验与兜底。"""
from __future__ import annotations

from typing import Any, Dict, List


def normalize_pill_flow(raw: Dict[str, Any], name: str, description: str) -> Dict[str, Any]:
    nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    edges = raw.get("edges") if isinstance(raw.get("edges"), list) else []
    out_nodes: List[Dict[str, Any]] = []
    for i, n in enumerate(nodes[:24]):
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or f"n{i}")[:40]
        label = str(n.get("label") or "步骤")[:80]
        typ = str(n.get("type") or "auto").lower()
        if typ not in ("start", "auto", "decision", "user", "done"):
            typ = "auto"
        item: Dict[str, Any] = {"id": nid, "label": label, "type": typ}
        if n.get("hint"):
            item["hint"] = str(n.get("hint"))[:60]
        out_nodes.append(item)
    if not out_nodes:
        out_nodes = [
            {"id": "start", "label": "开始", "type": "start"},
            {"id": "s1", "label": (description or name or "SKILL")[:40], "type": "auto"},
            {"id": "done", "label": "完成", "type": "done"},
        ]
        edges = [{"from": "start", "to": "s1"}, {"from": "s1", "to": "done"}]
    out_edges: List[Dict[str, str]] = []
    for e in edges[:40]:
        if not isinstance(e, dict):
            continue
        fr = str(e.get("from") or "")
        to = str(e.get("to") or "")
        if fr and to:
            row = {"from": fr, "to": to}
            if e.get("label"):
                row["label"] = str(e.get("label"))[:40]
            out_edges.append(row)
    if not out_edges and len(out_nodes) >= 2:
        for i in range(len(out_nodes) - 1):
            out_edges.append({"from": out_nodes[i]["id"], "to": out_nodes[i + 1]["id"]})
    return {"nodes": out_nodes, "edges": out_edges}
