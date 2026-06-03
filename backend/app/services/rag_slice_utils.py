"""RAG 预取切片规范化与 LLM 引用块（编排段 → 执行段）。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


def normalize_rag_slices(hits: List[Any], *, max_slices: int = 8) -> List[Dict[str, Any]]:
    """将 kb_search 命中转为前端/LLM 统一的文献切片结构（保留全文）。"""
    out: List[Dict[str, Any]] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        content = str(
            hit.get("content") or hit.get("text") or hit.get("snippet") or ""
        ).strip()
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        src = str(hit.get("source_file") or hit.get("file") or hit.get("source") or "")
        title = (
            str(meta.get("title") or hit.get("title") or "").strip()
            or (os.path.basename(src) if src else "")
            or "知识库片段"
        )
        ref_id = len(out) + 1
        out.append(
            {
                "ref_id": ref_id,
                "title": title,
                "source_file": src,
                "chunk_id": hit.get("chunk_id"),
                "score": hit.get("score"),
                "content": content,
                "metadata": meta,
            }
        )
        if len(out) >= max_slices:
            break
    return out


def build_rag_llm_blocks(
    rag_slices: List[Dict[str, Any]],
    *,
    prefetch_error: str = "",
    rag_query: str = "",
) -> Tuple[str, str]:
    """
    返回 (rag_context_block, citation_instruction_block)。
    rag_context_block 供模型阅读；citation_instruction_block 约束回答格式。
    """
    slices = [s for s in (rag_slices or []) if isinstance(s, dict) and s.get("content")]
    cite_lines = [
        "【回答格式 · 文献引用（必须遵守）】",
        "1. 最终回答中每一句结论句末须标注 [n]，n 对应下方「预检索文献」编号。",
        "2. 引用后紧跟简短推理，格式：（推理：…）说明该结论如何由对应切片推出。",
        "3. 禁止编造未出现在下方切片中的事实；无依据时明确写「知识库未检索到相关依据」。",
    ]
    if not slices:
        ctx = "编排段知识库预检索：未命中切片。"
        if prefetch_error:
            ctx += f" 原因：{prefetch_error[:300]}"
        if rag_query:
            ctx += f" 检索词：{rag_query[:200]}"
        return ctx, "\n".join(cite_lines)

    doc_lines = ["【预检索文献 · 原文切片（编号供引用）】"]
    if rag_query:
        doc_lines.append(f"检索词：{rag_query[:200]}")
    for sl in slices:
        rid = sl.get("ref_id")
        title = sl.get("title") or "片段"
        src = sl.get("source_file") or ""
        score = sl.get("score")
        score_s = f" score={score:.4f}" if isinstance(score, (int, float)) else ""
        doc_lines.append(f"\n[{rid}] {title}{score_s}")
        if src:
            doc_lines.append(f"来源：{src}")
        doc_lines.append(str(sl.get("content") or ""))

    return "\n".join(doc_lines), "\n".join(cite_lines)
