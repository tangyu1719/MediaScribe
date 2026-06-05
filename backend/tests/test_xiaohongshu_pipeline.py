"""小红书图文流水线回归：禁止 get_task NameError。"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("import_path", [
    "app.services.xiaohongshu_article",
])
def test_module_uses_task_manager_namespace(import_path: str):
    mod = __import__(import_path, fromlist=["_tm"])
    assert hasattr(mod, "_tm")
    assert callable(getattr(mod._tm, "get_task"))
    assert callable(getattr(mod._tm, "update_task"))


def test_process_pipeline_entry_no_get_task_name_error():
    async def _run() -> None:
        from app.services import xiaohongshu_article as xhs
        from app.services.task_manager import create_task

        link = f"https://www.xiaohongshu.com/explore/{uuid.uuid4().hex[:24]}"
        tid = create_task(
            "小红书",
            link,
            user_prompt="admin",
        )
        with patch.object(xhs, "_extract_xiaohongshu_content", return_value=None):
            await xhs.process_xiaohongshu_article_pipeline(tid, user_prompt="admin")
        task = xhs._tm.get_task(tid)
        assert task is not None
        blob = " ".join(str(x.get("message") or "") for x in (task.get("logs") or []))
        assert "name 'get_task' is not defined" not in blob

    asyncio.run(_run())


def test_video_pipeline_routes_xhs_without_get_task_name_error():
    async def _run() -> None:
        from app.services.task_manager import create_task, get_task
        from app.services.video_pipeline import process_video_pipeline

        link = (
            f"https://www.xiaohongshu.com/explore/{uuid.uuid4().hex[:24]}"
            "?xsec_token=ABBfS-iml4Q9w1C8unyXSg6XUnnBAIbvsyuWfQGZFKUfA=&xsec_source=pc_collect"
        )
        tid = create_task("小红书", link, user_prompt="admin")
        stub = {
            "type": "xiaohongshu",
            "title": "回归测试标题",
            "text_content": "正文内容",
            "image_links": [],
            "image_analysis": [],
        }
        with patch(
            "app.services.video_pipeline._probe_xiaohongshu_graphic_sync",
            return_value=(True, stub),
        ):
            with patch("app.services.xiaohongshu_article._extract_xiaohongshu_content", return_value=stub):
                with patch("app.services.xiaohongshu_article._ocr_compensation", side_effect=lambda r, _tid: r):
                    with patch("app.services.xiaohongshu_article._build_xiaohongshu_raw_text", return_value="装配正文"):
                        with patch(
                            "app.services.xiaohongshu_article.run_document_consolidation",
                            return_value={"ai_summary": "摘要", "article": "正文"},
                        ):
                            with patch("app.services.xiaohongshu_article.resolve_doc_title", return_value="文档标题"):
                                with patch(
                                    "app.services.xiaohongshu_article._generate_md",
                                    return_value="/tmp/xhs_test.md",
                                ):
                                    with patch("app.services.xiaohongshu_article.start_feishu_upload_async"):
                                        with patch("app.services.video_pipeline.start_html_generation"):
                                            with patch("app.services.video_pipeline.complete_task_after_md"):
                                                await process_video_pipeline(tid)
        task = get_task(tid)
        assert task is not None
        blob = " ".join(str(x.get("message") or "") for x in (task.get("logs") or []))
        assert "name 'get_task' is not defined" not in blob
        assert task.get("pipeline_route") == "xiaohongshu_graphic"

    asyncio.run(_run())
