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
                "parent_document": title,
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
    rag_context_block 供模型阅读；citation_instruction_block 约束回答格式（论文式上标+脚注）。
    """
    slices = [s for s in (rag_slices or []) if isinstance(s, dict) and s.get("content")]
    cite_lines = [
        "【回答格式 · 文献上标引用（必须遵守，类似学术论文）】",
        "1. 正文：凡依据下方「预检索文献」切片写出的每一句事实/论断，句末必须标注上标编号，"
        "格式为 ¹ 或 ¹² 或 ¹《父文档名》²《父文档名》。"
        "上标数字 n 唯一对应预检索文献中的 [n]；禁止无出处的知识库论断。",
        "2. 正文结束后，必须单独增加一节标题「## 文献注释」（脚注区），按上标编号逐条列出：",
        "   ¹ 《父文档名》（父文档路径，若有）",
        "     · 切片原文：引用该编号切片中的关键原文（用引号标出，可摘录）",
        "     · 推理链路：说明从该原文如何归纳/推断出正文中对应句子的结论（逻辑须具体、可核对）",
        "3. 禁止编造未出现在切片中的事实；无切片依据时写「知识库未检索到相关依据」，不得虚构上标。",
        "4. 禁止在正文输出 FunctionCall、```json 工具参数；引用仅用上标+文献注释。",
    ]
    if not slices:
        ctx = "编排段知识库预检索：未命中切片。"
        if prefetch_error:
            ctx += f" 原因：{prefetch_error[:300]}"
        if rag_query:
            ctx += f" 检索词：{rag_query[:200]}"
        return ctx, "\n".join(cite_lines)

    doc_lines = ["【预检索文献 · 原文切片（编号供上标引用）】"]
    if rag_query:
        doc_lines.append(f"检索词：{rag_query[:200]}")
    for sl in slices:
        rid = sl.get("ref_id")
        parent = sl.get("parent_document") or sl.get("title") or "片段"
        src = sl.get("source_file") or ""
        score = sl.get("score")
        score_s = f" score={score:.4f}" if isinstance(score, (int, float)) else ""
        doc_lines.append(f"\n[{rid}] 父文档：《{parent}》{score_s}")
        if src:
            doc_lines.append(f"父文档路径：{src}")
        doc_lines.append("切片全文：")
        doc_lines.append(str(sl.get("content") or ""))

    return "\n".join(doc_lines), "\n".join(cite_lines)


def answer_has_rag_citations(answer: str) -> bool:
    """探测回答是否含论文式上标或文献注释节。"""
    text = str(answer or "")
    if not text.strip():
        return False
    has_sup = any(ch in text for ch in "¹²³⁴⁵⁶⁷⁸⁹")
    has_bracket_ref = "[1]" in text or "【1】" in text
    has_footnote_sec = "文献注释" in text or "脚注" in text
    has_paren_doc = "《" in text and "》" in text
    return has_sup or has_bracket_ref or (has_footnote_sec and has_paren_doc)
