"""RAG 预取切片规范化与 LLM 引用块（编排段 → 执行段）。"""
from __future__ import annotations

import os
import re
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


def _citation_format_block() -> str:
    """按句编号引用 + 逐处逻辑注释的硬性格式说明。"""
    return "\n".join(
        [
            "【回答格式 · 按句引用 + 逻辑注释（必须严格遵守）】",
            "一、正文（按句为单位）",
            "  · 每一句依据知识库写出的论断，句末必须标注引用编号，格式为阿拉伯数字 1、2、3…（不用上标）。",
            "  · 同一句话可引用多个切片则写 1,2；编号对应下方预检索文献 [n]，禁止无编号的知识库论断。",
            "二、正文结束后依次输出两节（标题固定，不可省略）：",
            "  ## 文献切片明细",
            "  逐条列出本回答用到的切片（按 [n] 编号），每条须含：",
            "    - 切片[n]：所属父文档《父文档名》（父文档路径）",
            "    - 切片全文：（完整粘贴该切片正文，不可截断）",
            "    - 父文档全文：（写「见路径 xxx，前端可点击查看」；路径用预检索文献中的父文档路径）",
            "  ## 注释",
            "  按正文引用编号逐条写「处逻辑链路」（与正文句末编号一一对应，不可合并）：",
            "    1 处逻辑链路：摘录切片【1】原文「…关键句…」；因原文…，故正文第1句写成…。置信度：100",
            "    2 处逻辑链路：通过切片【1】【4】中…，结合…，判断正文第2句…。置信度：80",
            "  · 置信度为 0–100 整数；直接摘录原文可 100，跨切片归纳酌情 60–90。",
            "  · 每条必须写清：用了哪几个切片【n】、摘录了哪句原文、如何推到正文对应句。",
            "三、禁止编造未出现在切片中的事实；无依据时写「知识库未检索到相关依据」，不得虚构编号。",
            "四、禁止在正文输出 FunctionCall、```json 工具参数。",
        ]
    )


def build_rag_llm_blocks(
    rag_slices: List[Dict[str, Any]],
    *,
    prefetch_error: str = "",
    rag_query: str = "",
) -> Tuple[str, str]:
    """
    返回 (rag_context_block, citation_instruction_block)。
    rag_context_block 供模型阅读；citation_instruction_block 约束按句引用+逻辑注释格式。
    """
    cite_lines = [_citation_format_block()]
    slices = [s for s in (rag_slices or []) if isinstance(s, dict) and s.get("content")]
    if not slices:
        ctx = "编排段知识库预检索：未命中切片。"
        if prefetch_error:
            ctx += f" 原因：{prefetch_error[:300]}"
        if rag_query:
            ctx += f" 检索词：{rag_query[:200]}"
        return ctx, cite_lines[0]

    doc_lines = ["【预检索文献 · 原文切片（编号供正文句末引用）】"]
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

    return "\n".join(doc_lines), cite_lines[0]


def answer_has_rag_citations(answer: str) -> bool:
    """探测回答是否含按句编号引用与逻辑注释。"""
    text = str(answer or "")
    if not text.strip():
        return False
    body = text.split("## 文献切片明细", 1)[0] if "## 文献切片明细" in text else text
    # 句末引用：…协议1。 / …模块2 / …入口3。
    has_sentence_ref = bool(
        re.search(r"[\u4e00-\u9fff\w\)）]\d{1,2}(?:[,，]\d{1,2})*[。；！？]?", body)
    )
    has_slice_sec = "文献切片明细" in text or "切片全文" in text
    has_note_sec = "## 注释" in text or "\n注释" in text
    note_part = ""
    if "## 注释" in text:
        note_part = text.split("## 注释", 1)[-1]
    elif "\n注释" in text:
        note_part = text.split("\n注释", 1)[-1]
    has_logic = "处逻辑链路" in note_part
    has_conf = "置信度" in note_part
    has_parent = "《" in text and "》" in text
    return (
        has_sentence_ref
        and has_slice_sec
        and has_note_sec
        and has_logic
        and has_conf
        and has_parent
    )
