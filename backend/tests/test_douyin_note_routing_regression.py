"""抖音 /note/ 图文短链路由回归 —— BUG-2d7c6c01 对应用例。

用法（在项目根 web_rebuild_v2 下）：
  python -m pytest backend/tests/test_douyin_note_routing_regression.py -v -s
  python backend/tests/test_douyin_note_routing_regression.py --live-md
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

# 保证 backend 与 src/agent 可 import
_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
_AGENT = _ROOT.parent / "src" / "agent"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if _AGENT.is_dir() and str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))

TEST_LINK = "https://v.douyin.com/u8IA0RQ4TgI/"
NOTE_ID = "7646450898256393381"


def test_link_analyzer_detects_note_from_short_url():
    from link_analyzer import LinkAnalyzer

    analyzer = LinkAnalyzer()
    dtype = analyzer._detect_douyin_type(TEST_LINK)
    assert dtype == "douyin_image", f"短链应识别为 douyin_image，实际={dtype}"


def test_video_pipeline_probe_douyin_graphic():
    from app.services.video_pipeline import _probe_douyin_graphic_sync, _is_douyin_graphic_url

    assert not _is_douyin_graphic_url(TEST_LINK), "短链本身不含 /note/，快判应为 False"
    is_graphic, hint = _probe_douyin_graphic_sync(TEST_LINK)
    assert is_graphic, f"探测应识别为图文，hint={hint}"


def test_douyin_article_type_detection():
    from app.services.douyin_article import _detect_douyin_type

    t = _detect_douyin_type(TEST_LINK)
    assert t in ("note", "article"), f"应路由到 note/article 提取，实际={t}"


def test_note_content_extract_has_text():
    from app.services.douyin_article import _extract_content

    result = _extract_content(TEST_LINK, task_id="regression_probe")
    assert result, "图文提取结果不应为空"
    text = (result.get("text_content") or result.get("summary") or "").strip()
    assert len(text) > 80, f"正文过短: len={len(text)}"
    assert "RAG" in text or "embed" in text.lower(), "正文应含 RAG/embed 关键词"
    imgs = list(result.get("image_links") or [])
    ia = list(result.get("image_analysis") or [])
    if imgs:
        print(f"[info] 提取到 {len(imgs)} 张内容图")
    if ia:
        ocr_chars = sum(len((x.get("text") or "")) for x in ia)
        print(f"[info] OCR {len(ia)} 张幻灯片, 共 {ocr_chars} 字")
        assert len(ia) >= 8, f"note 应有 8 张幻灯片 OCR，实际 {len(ia)}"
        joined = "\n".join((x.get("text") or "") for x in ia)
        assert any(k in joined for k in ("bge", "BGE", "2560", "pgvector", "embed")), (
            "OCR 应含 RAG/embedding 技术关键词"
        )


async def _run_full_md_pipeline() -> str:
    from app.services.task_manager import create_task, get_task
    from app.services.douyin_article import process_douyin_article_pipeline

    tid = f"reg_{uuid.uuid4().hex[:8]}"
    create_task("抖音", TEST_LINK, pipeline_route="douyin_graphic")
    # create_task 可能复用同链卡片，取实际 task_id
    from app.services.task_manager import find_task_by_url_hash
    from app.services.link_hash import url_hash

    existing = find_task_by_url_hash(url_hash(TEST_LINK))
    tid = (existing or {}).get("task_id") or tid
    await process_douyin_article_pipeline(tid)
    task = get_task(tid) or {}
    assert task.get("status") == "completed", f"流水线未完成: {task.get('error')}"
    doc_path = (task.get("doc_path") or "").strip()
    assert doc_path and Path(doc_path).is_file(), "未生成 MD 文件"
    body = Path(doc_path).read_text(encoding="utf-8")
    assert len(body) > 200, "MD 内容过短"
    assert NOTE_ID in body or "embed" in body.lower() or "RAG" in body, "MD 应含原文关键词"
    return doc_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-md", action="store_true", help="跑完整图文沉淀并生成 MD（需 LLM 网关）")
    args = parser.parse_args()

    test_link_analyzer_detects_note_from_short_url()
    print("[OK] link_analyzer 短链 → douyin_image")

    test_video_pipeline_probe_douyin_graphic()
    print("[OK] video_pipeline 路由探测 → 图文")

    test_douyin_article_type_detection()
    print("[OK] douyin_article 类型 → note/article")

    test_note_content_extract_has_text()
    print("[OK] 图文内容提取有正文")

    if args.live_md:
        doc = asyncio.run(_run_full_md_pipeline())
        print(f"[OK] 完整 MD 生成: {doc}")
    else:
        print("[SKIP] 未传 --live-md，跳过 LLM 沉淀与 MD 落盘（单元探测已通过）")


if __name__ == "__main__":
    main()
