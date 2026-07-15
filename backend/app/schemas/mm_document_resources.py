"""多模态 MD 三方资源标准 — 链接导入预标记，与 RAG manifest.images 对齐。

阶段说明：
- prefetch：仅 URL/序号/ID 预标记（导入前即可写入元数据，MD 用远程链接渲染上图）
- ocr / vlm / normalized：后续 OCR+LLM、VLM 双线 enrichment（见 HaiChiAgent doc_normalizer）
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
METADATA_KEY = "external_resources"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FLOWCHART = "flowchart"
    LINK = "link"
    OTHER = "other"


class ProcessingStage(str, Enum):
    PREFETCH = "prefetch"
    OCR = "ocr"
    VLM = "vlm"
    NORMALIZED = "normalized"


class ExternalResourceItem(BaseModel):
    """单条三方资源（图片/视频/音频/流程图等）。"""

    resource_id: str = Field(..., description="全局唯一 ID，如 img_0001")
    media_type: MediaType = MediaType.IMAGE
    ordinal_in_doc: int = Field(..., ge=1, description="文档内出现序号，从 1 起")
    source_url: str = Field(default="", description="原始远程 URL 或来源地址")
    public_url: str = Field(default="", description="可 HTTP 访问 URL；预标记阶段可与 source_url 相同")
    file_name: str = Field(default="", description="落盘文件名，预标记可为空")
    picture_id: str = Field(default="", description="RAG picture 块 ID，如 图1-img_0001")
    name: str = Field(default="", description="资源名称（后期 enrichment）")
    literal_content: str = Field(default="", description="OCR/直意文本")
    description: str = Field(default="", description="VLM/描述性正文")
    processing_stage: ProcessingStage = ProcessingStage.PREFETCH
    source_format: str = Field(default="link_import", description="来源格式：link_import / docx / pdf / …")
    image_type: str = Field(default="unknown", description="ui_menu | flowchart | chart | photo | unknown")
    pipeline: str = Field(default="link_prefetch")
    degraded: bool = False

    @field_validator("resource_id")
    @classmethod
    def _normalize_resource_id(cls, v: str) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("resource_id 不能为空")
        return s

    @model_validator(mode="after")
    def _fill_derived_fields(self) -> "ExternalResourceItem":
        if not self.public_url.strip() and self.source_url.strip():
            self.public_url = self.source_url.strip()
        if not self.picture_id.strip():
            self.picture_id = f"图{self.ordinal_in_doc}-{self.resource_id}"
        if not self.file_name.strip() and self.media_type == MediaType.IMAGE:
            ext = _guess_ext_from_url(self.public_url or self.source_url)
            self.file_name = f"{self.resource_id}{ext}"
        return self

    def build_picture_block(self) -> str:
        """生成 normalized.md / 链接 MD 内 picture 块（前端 rich_content 可渲染远程 url）。"""
        url = (self.public_url or self.source_url or "").strip()
        if not url:
            return ""
        desc = (self.description or self.literal_content or "").strip()
        lines = [
            "{" + f"picture_id:{self.picture_id};",
            f"url:{url};",
        ]
        if desc:
            lines.append("description:")
            lines.append(desc)
        lines.append("}")
        return "\n".join(lines)

    def to_manifest_entry(self) -> Dict[str, Any]:
        """与 HaiChiAgent manifest.json images[] 字段对齐，便于后续 RAG 标准化复用。"""
        url = (self.public_url or self.source_url or "").strip()
        desc_body = (self.description or self.literal_content or "").strip()
        return {
            "image_id": self.resource_id,
            "source_format": self.source_format,
            "ordinal_in_doc": self.ordinal_in_doc,
            "file_name": self.file_name,
            "public_url": url,
            "picture_id": self.picture_id,
            "description": desc_body,
            "placeholder": "{" + f"picture_id:{self.picture_id}; ..." + "}",
            "image_type": self.image_type,
            "ocr_text": self.literal_content[:2000],
            "vlm_description": self.description[:4000],
            "pipeline": self.pipeline,
            "degraded": self.degraded,
            "media_type": self.media_type.value,
            "processing_stage": self.processing_stage.value,
            "source_url": self.source_url,
            "name": self.name,
        }


class DocumentExternalResources(BaseModel):
    """文档级三方资源清单（写入结构化元数据 external_resources）。"""

    schema_version: str = SCHEMA_VERSION
    source_platform: str = ""
    source_link: str = ""
    resource_count: int = 0
    resources: List[ExternalResourceItem] = Field(default_factory=list)
    processing_note: str = "link_prefetch"

    @model_validator(mode="after")
    def _sync_count(self) -> "DocumentExternalResources":
        self.resource_count = len(self.resources)
        return self

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DocumentExternalResources":
        if not data or not isinstance(data, dict):
            return cls()
        payload = dict(data)
        raw_items = payload.pop("resources", None)
        if isinstance(raw_items, list):
            payload["resources"] = [ExternalResourceItem.model_validate(x) for x in raw_items if isinstance(x, dict)]
        return cls.model_validate(payload)

    @classmethod
    def from_link_analyzer_result(
        cls,
        result: Optional[Dict[str, Any]],
        *,
        platform: str = "",
        source_link: str = "",
    ) -> "DocumentExternalResources":
        """从 link_analyzer 提取结果预标记图片/媒体（导入前即可调用）。"""
        result = result or {}
        items: List[ExternalResourceItem] = []
        seen: set[str] = set()
        ordinal = 0

        def _add_url(
            url: str,
            *,
            media_type: MediaType = MediaType.IMAGE,
            literal: str = "",
            stage: ProcessingStage = ProcessingStage.PREFETCH,
            index_hint: int = 0,
        ) -> None:
            nonlocal ordinal
            u = str(url or "").strip()
            if not u or u in seen:
                return
            seen.add(u)
            ordinal += 1
            ord_num = index_hint if index_hint > 0 else ordinal
            rid = f"img_{ord_num:04d}"
            items.append(
                ExternalResourceItem(
                    resource_id=rid,
                    media_type=media_type,
                    ordinal_in_doc=ord_num,
                    source_url=u,
                    public_url=u,
                    literal_content=literal,
                    processing_stage=stage,
                    pipeline="link_ocr" if literal else "link_prefetch",
                )
            )

        for img in result.get("image_analysis") or []:
            if not isinstance(img, dict):
                continue
            idx = int(img.get("index") or 0)
            _add_url(
                str(img.get("url") or ""),
                literal=str(img.get("text") or "").strip(),
                stage=ProcessingStage.OCR if str(img.get("text") or "").strip() else ProcessingStage.PREFETCH,
                index_hint=idx,
            )

        for url in result.get("image_links") or []:
            _add_url(str(url or ""))

        cover = str(result.get("cover_url") or result.get("cover") or "").strip()
        if cover:
            _add_url(cover, index_hint=0)

        video_url = str(result.get("video_url") or result.get("play_url") or "").strip()
        if video_url:
            ordinal += 1
            items.append(
                ExternalResourceItem(
                    resource_id=f"vid_{ordinal:04d}",
                    media_type=MediaType.VIDEO,
                    ordinal_in_doc=ordinal,
                    source_url=video_url,
                    public_url=video_url,
                    processing_stage=ProcessingStage.PREFETCH,
                    pipeline="link_prefetch",
                )
            )

        audio_url = str(result.get("audio_url") or "").strip()
        if audio_url:
            ordinal += 1
            items.append(
                ExternalResourceItem(
                    resource_id=f"aud_{ordinal:04d}",
                    media_type=MediaType.AUDIO,
                    ordinal_in_doc=ordinal,
                    source_url=audio_url,
                    public_url=audio_url,
                    processing_stage=ProcessingStage.PREFETCH,
                    pipeline="link_prefetch",
                )
            )

        items.sort(key=lambda x: x.ordinal_in_doc)
        note = "link_ocr" if any(i.processing_stage == ProcessingStage.OCR for i in items) else "link_prefetch"
        return cls(
            source_platform=str(platform or "").strip(),
            source_link=str(source_link or result.get("url") or "").strip(),
            resources=items,
            processing_note=note,
        )

    def to_meta_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    def to_manifest_dict(self, *, document_title: str = "") -> Dict[str, Any]:
        """生成与 kb_assets/manifest.json 兼容的顶层结构（供后续多模态标准化）。"""
        images = [
            r.to_manifest_entry()
            for r in self.resources
            if r.media_type in (MediaType.IMAGE, MediaType.FLOWCHART)
        ]
        return {
            "ok": True,
            "document_title": document_title,
            "schema_version": self.schema_version,
            "source_link": self.source_link,
            "source_platform": self.source_platform,
            "pipeline_note": self.processing_note,
            "image_count": len(images),
            "images": images,
            "external_resources": self.to_meta_dict(),
        }

    def render_picture_blocks_section(self) -> str:
        blocks = [
            r.build_picture_block()
            for r in self.resources
            if r.media_type in (MediaType.IMAGE, MediaType.FLOWCHART) and r.build_picture_block()
        ]
        if not blocks:
            return ""
        return "## 图片资源\n\n" + "\n\n".join(blocks)

    def append_picture_blocks_to_text(self, text: str) -> str:
        section = self.render_picture_blocks_section()
        if not section:
            return str(text or "")
        base = str(text or "").rstrip()
        if "## 图片资源" in base:
            return base
        return (base + "\n\n" + section).strip() if base else section


def _guess_ext_from_url(url: str) -> str:
    path = urlparse(str(url or "")).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        if path.endswith(ext):
            return ext
    return ".jpg"


def attach_link_external_resources(
    result: Dict[str, Any],
    *,
    platform: str = "",
    source_link: str = "",
) -> DocumentExternalResources:
    """链接提取/OCR 后立即预标记三方资源，写入 result['external_resources']。"""
    doc = DocumentExternalResources.from_link_analyzer_result(
        result, platform=platform, source_link=source_link
    )
    if result is not None:
        result["external_resources"] = doc.to_meta_dict()
    return doc


def merge_external_resources_into_metadata(
    metadata: Optional[Dict[str, Any]],
    resources: DocumentExternalResources,
) -> Dict[str, Any]:
    """将三方资源并入结构化元数据 JSON（RAG / 前端卡片共用）。"""
    meta = dict(metadata or {})
    if resources.resource_count > 0:
        meta[METADATA_KEY] = resources.to_meta_dict()
    return meta


def apply_resources_to_md_payload(
    payload: Dict[str, Any],
    link_result: Optional[Dict[str, Any]],
    *,
    platform: str = "",
    source_link: str = "",
) -> Dict[str, Any]:
    """MD 生成前：正文追加 picture 块 + 元数据写入 external_resources。"""
    out = dict(payload or {})
    ext_raw = (link_result or {}).get("external_resources")
    if ext_raw:
        doc = DocumentExternalResources.from_dict(ext_raw)
    else:
        doc = DocumentExternalResources.from_link_analyzer_result(
            link_result, platform=platform, source_link=source_link
        )
    if doc.resource_count <= 0:
        return out
    out["article"] = doc.append_picture_blocks_to_text(str(out.get("article") or ""))
    out["extracted_metadata"] = merge_external_resources_into_metadata(
        out.get("extracted_metadata") if isinstance(out.get("extracted_metadata"), dict) else {},
        doc,
    )
    out["external_resources"] = doc.to_meta_dict()
    return out


def enrich_from_manifest_images(manifest: Dict[str, Any]) -> "DocumentExternalResources":
    """从 HaiChiAgent 标准化 manifest 反建三方资源（RAG 入库后回填用）。"""
    images = manifest.get("images") or []
    items: List[ExternalResourceItem] = []
    for row in images:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("image_id") or row.get("resource_id") or "").strip()
        if not rid:
            continue
        ord_num = int(row.get("ordinal_in_doc") or 0) or 1
        ocr = str(row.get("ocr_text") or row.get("literal_content") or "").strip()
        vlm = str(row.get("vlm_description") or row.get("description") or "").strip()
        stage = ProcessingStage.NORMALIZED
        if vlm:
            stage = ProcessingStage.VLM
        elif ocr:
            stage = ProcessingStage.OCR
        try:
            media = MediaType(str(row.get("media_type") or "image"))
        except ValueError:
            media = MediaType.IMAGE
        items.append(
            ExternalResourceItem(
                resource_id=rid,
                media_type=media,
                ordinal_in_doc=ord_num,
                source_url=str(row.get("source_url") or "").strip(),
                public_url=str(row.get("public_url") or "").strip(),
                file_name=str(row.get("file_name") or "").strip(),
                picture_id=str(row.get("picture_id") or "").strip(),
                name=str(row.get("name") or "").strip(),
                literal_content=ocr,
                description=vlm,
                processing_stage=stage,
                source_format=str(row.get("source_format") or "normalized"),
                image_type=str(row.get("image_type") or "unknown"),
                pipeline=str(row.get("pipeline") or "doc_normalizer"),
                degraded=bool(row.get("degraded")),
            )
        )
    ext = manifest.get("external_resources")
    if isinstance(ext, dict) and ext.get("resources"):
        return DocumentExternalResources.from_dict(ext)
    return DocumentExternalResources(
        source_platform=str(manifest.get("source_platform") or "").strip(),
        source_link=str(manifest.get("source") or manifest.get("source_link") or "").strip(),
        resources=items,
        processing_note=str(manifest.get("pipeline_note") or "normalized"),
    )
