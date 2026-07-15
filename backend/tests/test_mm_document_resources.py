"""三方资源 schema 与链接预标记。"""
from __future__ import annotations

from app.schemas.mm_document_resources import (
    DocumentExternalResources,
    ExternalResourceItem,
    MediaType,
    ProcessingStage,
    apply_resources_to_md_payload,
    attach_link_external_resources,
    enrich_from_manifest_images,
    merge_external_resources_into_metadata,
)


def test_from_link_analyzer_prefetch():
    result = {
        "url": "https://www.xiaohongshu.com/explore/abc",
        "image_links": [
            "https://sns-img.cdn.com/a.jpg",
            "https://sns-img.cdn.com/b.jpg",
        ],
    }
    doc = DocumentExternalResources.from_link_analyzer_result(
        result, platform="小红书", source_link=result["url"]
    )
    assert doc.resource_count == 2
    assert doc.resources[0].resource_id == "img_0001"
    assert doc.resources[0].picture_id == "图1-img_0001"
    assert doc.resources[0].public_url == "https://sns-img.cdn.com/a.jpg"
    assert doc.resources[0].processing_stage == ProcessingStage.PREFETCH


def test_from_link_analyzer_with_ocr():
    result = {
        "image_links": ["https://cdn/x.png"],
        "image_analysis": [{"url": "https://cdn/x.png", "text": "标题文字", "index": 1}],
    }
    doc = DocumentExternalResources.from_link_analyzer_result(result, platform="小红书")
    assert doc.resource_count == 1
    item = doc.resources[0]
    assert item.literal_content == "标题文字"
    assert item.processing_stage == ProcessingStage.OCR
    assert "标题文字" in item.build_picture_block()


def test_picture_block_remote_url():
    item = ExternalResourceItem(
        resource_id="img_0003",
        media_type=MediaType.IMAGE,
        ordinal_in_doc=3,
        source_url="https://example.com/pic.webp",
    )
    block = item.build_picture_block()
    assert "picture_id:图3-img_0003" in block
    assert "url:https://example.com/pic.webp" in block


def test_to_manifest_entry_aligned():
    item = ExternalResourceItem(
        resource_id="img_0001",
        media_type=MediaType.IMAGE,
        ordinal_in_doc=1,
        source_url="https://cdn/a.png",
        literal_content="OCR文本",
        description="VLM描述",
    )
    entry = item.to_manifest_entry()
    assert entry["image_id"] == "img_0001"
    assert entry["picture_id"] == "图1-img_0001"
    assert entry["ocr_text"] == "OCR文本"
    assert entry["vlm_description"] == "VLM描述"


def test_attach_and_apply_md_payload():
    result = {
        "image_links": ["https://cdn/z.jpg"],
    }
    attach_link_external_resources(result, platform="抖音", source_link="https://douyin.com/x")
    payload = apply_resources_to_md_payload(
        {"article": "正文段落", "extracted_metadata": {"domain": "测试"}},
        result,
        platform="抖音",
        source_link="https://douyin.com/x",
    )
    assert "## 图片资源" in payload["article"]
    assert "picture_id:" in payload["article"]
    assert payload["extracted_metadata"]["domain"] == "测试"
    assert payload["extracted_metadata"]["external_resources"]["resource_count"] == 1


def test_enrich_from_manifest_images():
    manifest = {
        "source": "/data/doc.docx",
        "pipeline_note": "docx_p0_ordered",
        "images": [
            {
                "image_id": "img_0001",
                "ordinal_in_doc": 1,
                "public_url": "/output/kb_assets/1/images/img_0001.png",
                "picture_id": "图1-img_0001",
                "ocr_text": "按钮",
                "vlm_description": "登录界面",
                "image_type": "ui_menu",
            }
        ],
    }
    doc = enrich_from_manifest_images(manifest)
    assert doc.resource_count == 1
    assert doc.resources[0].processing_stage == ProcessingStage.VLM
    assert doc.resources[0].literal_content == "按钮"
    assert doc.resources[0].description == "登录界面"
