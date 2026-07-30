"""流程图 PDF/图片转 Mermaid 的 Web 服务包装层。"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .file_naming import is_under_output_dir
from .task_manager import get_output_dir

_LOG = logging.getLogger(__name__)

_FLOWCHART_INPUT_SUFFIXES = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
)


def _log_event(
    action: str,
    *,
    stage: str,
    obj: str,
    ok: Optional[bool] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    parts = [f"[多模态Web-流程图转Mermaid|flowchart_scoring_service|{obj}|{stage}] {action}"]
    if ok is not None:
        parts.append(f"ok={ok}")
    if extra:
        for k, v in extra.items():
            parts.append(f"{k}={v}")
    _LOG.info("; ".join(parts))


def abs_path_to_output_url(abs_path: str | Path) -> str:
    """将 output 根下的绝对路径转为 /output/ 相对 URL（支持子目录）。"""
    p = Path(abs_path).resolve()
    root = get_output_dir().resolve()
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:
        return ""
    from urllib.parse import quote

    return "/output/" + quote(rel, safe="/")


def run_flowchart_score(
    file_path: str,
    *,
    page: int = 1,
    zoom: float = 3.0,
    mineru_json: str = "",
    column_band_split: bool = True,
    column_bands: int = 0,
    min_band_h: int = 48,
    skip_arrows: bool = True,
    ocr_engine: str = "auto",
    direction: str = "auto",
    vlm_refine: bool = True,
    artifact_subdir: str = "",
) -> Dict[str, Any]:
    """
    对 PDF/图片执行坐标 OCR、节点拆分、拓扑恢复和 Mermaid 生成。
    产物落在 output/flowchart_scoring_web/ 下以便 /output 静态访问。
    """
    src = Path(file_path).resolve()
    obj = src.name
    if not src.is_file():
        _log_event("路径无效", stage="校验", obj=obj, ok=False)
        return {"ok": False, "error": "路径无效或不是可读文件", "file_path": str(src)}
    if src.suffix.lower() not in _FLOWCHART_INPUT_SUFFIXES:
        _log_event("扩展名不支持", stage="校验", obj=obj, ok=False, extra={"suffix": src.suffix})
        return {
            "ok": False,
            "error": f"流程图转换仅支持: {', '.join(sorted(_FLOWCHART_INPUT_SUFFIXES))}",
            "file_path": str(src),
        }

    job_id = (artifact_subdir or "").strip() or uuid.uuid4().hex[:12]
    art_root = (get_output_dir() / "flowchart_scoring_web" / job_id).resolve()
    art_root.mkdir(parents=True, exist_ok=True)

    _log_event(
        "开始",
        stage="执行",
        obj=obj,
        extra={
            "page": page,
            "column_bands": column_bands,
            "column_band_split": column_band_split,
            "job_id": job_id,
        },
    )

    try:
        from .flowchart_mermaid_service import convert_flowchart_to_mermaid

        report = convert_flowchart_to_mermaid(
            str(src),
            page=int(page),
            zoom=float(zoom),
            ocr_engine=ocr_engine,
            direction=direction,
            vlm_refine=bool(vlm_refine),
            artifact_root=art_root,
        )
    except Exception as exc:
        _log_event(
            "链路异常",
            stage="失败",
            obj=obj,
            ok=False,
            extra={"error_type": type(exc).__name__, "error_message": str(exc)[:500]},
        )
        return {"ok": False, "error": str(exc), "file_path": str(src), "job_id": job_id}

    if not report.get("ok"):
        return {
            "ok": False,
            "error": report.get("error") or "流程图转换失败",
            "file_path": str(src),
            "job_id": job_id,
        }

    pages = report.get("pages") or []
    for item in pages:
        overlay_abs = item.get("overlay_path") or ""
        origin_abs = item.get("origin_path") or ""
        item["overlay_url"] = abs_path_to_output_url(overlay_abs) if overlay_abs else ""
        item["origin_url"] = abs_path_to_output_url(origin_abs) if origin_abs else ""
    overlay_abs = pages[0].get("overlay_path") if pages else ""
    overlay_url = pages[0].get("overlay_url") if pages else ""
    work_dir = report.get("work_dir") or str(art_root)
    node_count = int(report.get("node_count") or 0)
    edge_count = int(report.get("edge_count") or 0)
    geom = {
        "block_count": node_count,
        "edge_count": edge_count,
        "overlap_pair_count": 0,
        "overlap_pairs": [],
        "overlap_ok": True,
    }

    under = is_under_output_dir(Path(work_dir)) if work_dir else False
    _log_event(
        "完成",
        stage="收尾",
        obj=obj,
        ok=True,
        extra={
            "block_count": node_count,
            "edge_count": edge_count,
            "overlay_under_output": under,
        },
    )

    return {
        **report,
        "ok": True,
        "file_path": str(src),
        "job_id": job_id,
        "work_dir": work_dir,
        "overlay_path": overlay_abs,
        "overlay_url": overlay_url,
        "geometry_score": geom,
        "final_block_count": node_count,
        "overlap_ok": True,
        "column_band_cuts": [],
        "report": {
            "method": report.get("method"),
            "node_count": node_count,
            "edge_count": edge_count,
            "page_count": report.get("page_count"),
            "direction": report.get("direction"),
            "ocr_diagnostics": report.get("ocr_diagnostics"),
        },
        "error": "",
    }
