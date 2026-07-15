"""结构化 schema（多模态 MD、RAG 元数据等）。"""

from .mm_document_resources import (
    DocumentExternalResources,
    ExternalResourceItem,
    MediaType,
    ProcessingStage,
    attach_link_external_resources,
    apply_resources_to_md_payload,
    merge_external_resources_into_metadata,
)

__all__ = [
    "DocumentExternalResources",
    "ExternalResourceItem",
    "MediaType",
    "ProcessingStage",
    "attach_link_external_resources",
    "apply_resources_to_md_payload",
    "merge_external_resources_into_metadata",
]
