"""飞书上传管线 — 标题与导入。"""
from __future__ import annotations

from pathlib import Path


def test_run_feishu_upload_doc_title_uses_md_stem(monkeypatch, tmp_path):
    md = tmp_path / "010-05-27-测试标题_图文分析.md"
    md.write_text("# test\n\nbody", encoding="utf-8")

    captured = {}

    class FakeKb:
        last_error = None
        last_doc_url = None

        def __init__(self, app_id, app_secret):
            pass

        def parse_feishu_folder_from_prompt(self, prompt):
            return None

        def upload_document(self, title, content, **kwargs):
            captured["title"] = title
            return "tok_fake"

    monkeypatch.setitem(
        __import__("sys").modules,
        "feishu_integration",
        type(__import__("sys"))("feishu_integration", FeishuKnowledgeBase=FakeKb),
    )

    from app.services import feishu_pipeline

    res = feishu_pipeline.run_feishu_upload(
        str(md),
        "t1",
        "https://example.com",
        {
            "feishu_sync_enabled": True,
            "feishu_app_id": "id",
            "feishu_app_secret": "sec",
            "feishu_upload_postcheck_enabled": False,
        },
    )
    assert res.get("ok") is True
    assert captured["title"] == md.stem
