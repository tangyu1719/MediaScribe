"""大/中/小三级粒度元数据推断：domain(大) / module(中) / doc_type(小)，父文档与子切片共用同一套标记。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

# 默认面试必背目录（服务端批量导入）
DEFAULT_INTERVIEW_IMPORT_PATH = (
    r"F:\java\AIOPS\SuperBizAgent-release-2026-01-02\demo_wendanghua\output\AI\面试必背"
)


def _norm_seg(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def infer_granularity_metadata(
    file_path: str,
    content: str = "",
    *,
    import_root: Optional[str] = None,
) -> Dict[str, str]:
    """
    按路径与文件名推断三级元数据（映射到 domain / module / doc_type）。
    一级(大)=domain，二级(中)=module，三级(小)=doc_type。
    """
    p = Path(file_path)
    name = p.name
    name_lower = name.lower()
    content_lower = (content or "")[:8000].lower()
    root = Path(import_root).resolve() if import_root else None
    rel_parts: list[str] = []
    try:
        if root and root.is_dir():
            rel = p.resolve().relative_to(root)
            rel_parts = [x for x in rel.parts[:-1] if x]
    except ValueError:
        rel_parts = []

    # 一级：领域（大）
    domain = "技术"
    path_join = "/".join(rel_parts + [name]).lower()
    if any(x in path_join for x in ("面试", "interview", "必背", "必看")):
        domain = "技术"
    elif any(x in path_join for x in ("产品", "prd", "需求")):
        domain = "产品"
    elif any(x in path_join for x in ("运营", "市场", "营销")):
        domain = "运营"
    elif any(x in path_join for x in ("财务", "人事", "法务")):
        for d, keys in (("财务", ("财务",)), ("人事", ("人事", "hr")), ("法务", ("法务", "合规"))):
            if any(k in path_join for k in keys):
                domain = d
                break

    # 二级：模块（中）— 优先子目录名
    module = "其他"
    if rel_parts:
        sub = _norm_seg(rel_parts[0])
        sub_map = {
            "aiops-docs": "运维",
            "aiops": "运维",
            "rag": "后端",
            "agent": "后端",
            "frontend": "前端",
            "algorithm": "算法",
            "algo": "算法",
            "test": "测试",
            "面试": "文档",
        }
        module = sub_map.get(sub.lower(), sub[:32] if sub else "文档")
    if module == "其他":
        if any(k in name_lower or k in content_lower for k in ("rag", "检索", "向量", "embedding", "milvus")):
            module = "后端"
        elif any(k in name_lower or k in content_lower for k in ("agent", "react", "tool", "mcp", "编排")):
            module = "后端"
        elif any(k in name_lower for k in ("cpu", "memory", "disk", "运维", "k8s", "docker", "监控")):
            module = "运维"
        elif any(k in name_lower for k in ("prompt", "微调", "llm", "大模型")):
            module = "算法"

    # 三级：文档类型（小）
    doc_type = "文档"
    if name_lower.endswith(".py") or any(
        name_lower.endswith(ext) for ext in (".js", ".java", ".go", ".ts", ".tsx")
    ):
        doc_type = "代码"
    elif "skill" in name_lower or "规范" in content_lower or "标准" in content_lower:
        doc_type = "规范"
    elif any(k in name_lower for k in ("报告", "总结", "周报", "复盘")):
        doc_type = "报告"
    elif any(k in name_lower for k in ("纪要", "会议")):
        doc_type = "会议"
    elif any(k in name_lower for k in ("cpu", "memory", "disk", "usage", "故障", "排查")):
        doc_type = "报告"
    elif any(k in name_lower for k in ("面试", "问答", "q&a")):
        doc_type = "聊天记录"

    # 关键词：标题 token
    stem = re.sub(r"\.(md|markdown|txt)$", "", name, flags=re.I)
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_]{2,}", stem)
    kw1 = tokens[0][:64] if tokens else ""
    kw2 = tokens[1][:64] if len(tokens) > 1 else ""

    return {
        "domain": domain,
        "module": module,
        "doc_type": doc_type,
        "keyword1": kw1,
        "keyword2": kw2,
        "granularity": {"level1": domain, "level2": module, "level3": doc_type},
    }
