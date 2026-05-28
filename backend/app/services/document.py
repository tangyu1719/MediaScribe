"""文档处理服务 —— 导入 src/agent/document_processor.py 和 mineru_processor.py"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional, Dict, Any

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
for _p in _HERE.parents:
    _candidate = _p / "src" / "agent"
    if _candidate.is_dir():
        _AGENT_DIR = _candidate.resolve()
        break
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from document_processor import DocumentProcessor, DocumentType, ProcessingResult
from mineru_processor import MinerUProcessor, process_with_mineru, MinerUResult

_doc_processor: Optional[DocumentProcessor] = None
_mineru_processor: Optional[MinerUProcessor] = None


def _get_doc_processor() -> DocumentProcessor:
    global _doc_processor
    if _doc_processor is None:
        _doc_processor = DocumentProcessor()
    return _doc_processor


def _get_mineru_processor(vlm_api_key: str = None) -> MinerUProcessor:
    global _mineru_processor
    if _mineru_processor is None:
        _mineru_processor = MinerUProcessor(vlm_api_key=vlm_api_key)
    return _mineru_processor


def detect_document_type(file_path: str) -> str:
    """检测文档类型"""
    dp = _get_doc_processor()
    doc_type = dp.detect_type(file_path)
    return doc_type.value if hasattr(doc_type, 'value') else str(doc_type)


def analyze_document(file_path: str, **kwargs) -> Dict[str, Any]:
    """分析文档内容"""
    dp = _get_doc_processor()
    result: ProcessingResult = dp.process(file_path, **kwargs)
    text = result.content.text if result.content else ""
    chunk_mode = str(kwargs.get("slice_method") or kwargs.get("chunk_mode") or "auto")
    chunk_preview = []
    chunk_stats = {"mode": chunk_mode, "count": 0}
    if text:
        try:
            from .chonkie_chunker import chunk_text_with_meta

            chunk_stats = chunk_text_with_meta(
                text,
                mode=chunk_mode if chunk_mode in ("token", "sentence", "recursive", "semantic", "auto") else "auto",
                max_tokens=int(kwargs.get("max_tokens") or 350),
                overlap=int(kwargs.get("overlap") or 40),
                source=str(file_path),
            )
            chunk_preview = chunk_stats.get("chunks") or []
        except Exception as e:
            chunk_stats = {"mode": chunk_mode, "count": 0, "error": str(e)}
    return {
        "ok": result.success,
        "doc_type": str(result.doc_type),
        "text": text,
        "chunks": chunk_preview,
        "chunk_stats": chunk_stats,
        "error": result.error,
        "file_path": result.file_path,
        "file_size": result.file_size,
        "processing_time": result.processing_time,
    }


def process_with_mineru(file_path: str, output_dir: str = None, vlm_api_key: str = None) -> Dict[str, Any]:
    """使用 MinerU 处理文档"""
    result: MinerUResult = process_with_mineru(file_path, output_dir=output_dir, vlm_api_key=vlm_api_key)
    return {
        "ok": result.success,
        "content": result.content,
        "markdown": result.markdown,
        "metadata": result.metadata,
        "images": result.images,
        "tables": result.tables,
        "image_descriptions": result.image_descriptions,
        "error": result.error,
    }


def get_supported_formats() -> list:
    """获取支持的文档格式"""
    mp = _get_mineru_processor()
    return mp.get_supported_formats()
