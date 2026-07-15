"""链接沉淀流水线 —— 固定阶段状态机与断点缓存（对齐 legacy video_gui stages）。"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional

from .history_manager import add_or_update_task_in_history
from . import task_manager as _tm
from .pipeline_checkpoint import (
    save_stage_payload,
    load_stage_payload,
    has_stage_payload,
    clear_pipeline_cache,
    list_cached_stage_ids,
)

# 流水线终态（非终态任务启动时需全量扫描对齐断点）
PIPELINE_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

# 各路由固定阶段顺序（与 node_registry / video_gui 对齐）
PIPELINE_ROUTES: Dict[str, List[Dict[str, Any]]] = {
    "video": [
        {"id": "comments", "label": "读取评论", "optional": True},
        {"id": "download", "label": "下载视频"},
        {"id": "transcribe", "label": "语音转文字"},
        {"id": "ai_analysis", "label": "原文整理与摘要"},
        {"id": "generate_md", "label": "生成文档"},
        {"id": "html", "label": "HTML长页", "optional": True},
    ],
    "xiaohongshu_graphic": [
        {"id": "extract", "label": "内容提取"},
        {"id": "ocr", "label": "OCR补偿"},
        {"id": "comments", "label": "评论区", "optional": True},
        {"id": "assemble", "label": "原文装配"},
        {"id": "ai_analysis", "label": "AI润色+摘要"},
        {"id": "generate_md", "label": "生成Markdown"},
        {"id": "feishu_upload", "label": "飞书上传", "optional": True},
        {"id": "html", "label": "HTML长页", "optional": True},
    ],
    "douyin_graphic": [
        {"id": "extract", "label": "内容提取"},
        {"id": "ocr", "label": "OCR补偿"},
        {"id": "assemble", "label": "原文装配"},
        {"id": "ai_analysis", "label": "AI润色+摘要"},
        {"id": "generate_md", "label": "生成文档"},
        {"id": "html", "label": "HTML长页", "optional": True},
    ],
    "rss_article": [
        {"id": "fetch_fulltext", "label": "抓取全文"},
        {"id": "ai_analysis", "label": "原文整理与摘要"},
        {"id": "generate_md", "label": "生成文档"},
        {"id": "html", "label": "HTML长页", "optional": True},
    ],
    "web_article": [
        {"id": "fetch_fulltext", "label": "抓取网页全文"},
        {"id": "ai_analysis", "label": "原文整理与摘要"},
        {"id": "generate_md", "label": "生成文档"},
        {"id": "html", "label": "HTML长页", "optional": True},
    ],
}

STATUS_TO_STAGE = {
    "video": {
        "downloading": "download",
        "transcribing": "transcribe",
        "consolidating": "ai_analysis",
        "generating": "generate_md",
    },
    "xiaohongshu_graphic": {
        "extracting": "extract",
        "ocr": "ocr",
        "comments": "comments",
        "assembling": "assemble",
        "consolidating": "ai_analysis",
        "generating": "generate_md",
        "feishu_upload": "feishu_upload",
    },
    "douyin_graphic": {
        "extracting": "extract",
        "ocr": "ocr",
        "assembling": "assemble",
        "consolidating": "ai_analysis",
        "generating": "generate_md",
    },
    "rss_article": {
        "consolidating": "ai_analysis",
        "generating": "generate_md",
    },
    "web_article": {
        "extracting": "fetch_fulltext",
        "consolidating": "ai_analysis",
        "generating": "generate_md",
    },
}


def route_stage_defs(route: str) -> List[Dict[str, Any]]:
    return PIPELINE_ROUTES.get(route) or PIPELINE_ROUTES["video"]


def init_pipeline_stages(route: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for spec in route_stage_defs(route):
        out[spec["id"]] = {
            "status": "pending",
            "label": spec["label"],
            "result": None,
            "error": "",
            "updated_at": "",
        }
    return out


def stage_label(route: str, stage_id: str) -> str:
    for spec in route_stage_defs(route):
        if spec["id"] == stage_id:
            return str(spec["label"])
    return stage_id


# 视频链路断点映射到图文链路（路由切换时复用同一卡片、同一缓存）
_CROSS_ROUTE_RESUME: Dict[tuple, Dict[str, str]] = {
    ("video", "xiaohongshu_graphic"): {
        "download": "extract",
        "transcribe": "ai_analysis",
        "comments": "comments",
        "generate_md": "generate_md",
        "html": "html",
    },
    ("video", "douyin_graphic"): {
        "download": "extract",
        "transcribe": "ai_analysis",
        "comments": "comments",
        "generate_md": "generate_md",
        "html": "html",
    },
    ("video", "web_article"): {
        "download": "fetch_fulltext",
        "transcribe": "ai_analysis",
        "comments": "comments",
        "generate_md": "generate_md",
        "html": "html",
    },
}


def remap_resume_stage(resume_from: Optional[str], from_route: str, to_route: str) -> Optional[str]:
    """跨路由断点恢复：如视频 failed_stage=download → 小红书图文 extract。"""
    rf = (resume_from or "").strip()
    if not rf or from_route == to_route:
        return rf or None
    target_ids = {s["id"] for s in route_stage_defs(to_route)}
    if rf in target_ids:
        return rf
    mapped = (_CROSS_ROUTE_RESUME.get((from_route, to_route)) or {}).get(rf)
    if mapped:
        return mapped
    if "extract" in target_ids:
        return "extract"
    return rf


def _sanitize_checkpoint(stage_id: str, result: Any) -> Any:
    """只保留可 JSON 序列化、可用于断点恢复的轻量字段。"""
    if result is None:
        return None
    if isinstance(result, str):
        return {"path": result}
    if not isinstance(result, dict):
        return {"value": str(result)[:500]}
    allow = {
        "comments": ("comments_file_path", "fetched_count"),
        "download": ("video_path", "path"),
        "transcribe": ("full_text", "transcript", "segments", "transcribe_source", "title", "video_path"),
        "fetch_fulltext": ("source_text", "fetch_source", "char_len"),
        "extract": ("raw_text", "link_title", "cover_url", "content_type"),
        "ocr": ("ocr_text", "merged_text"),
        "assemble": ("article", "raw_text"),
        "ai_analysis": ("ai_summary", "article", "title", "link_title"),
        "generate_md": ("doc_path", "doc_filename"),
        "feishu_upload": ("feishu_doc_url", "feishu_doc_token"),
        "html": ("html_path", "html_status"),
    }
    keys = allow.get(stage_id)
    if not keys:
        return {k: v for k, v in result.items() if k in ("path", "doc_path", "video_path", "title")}
    slim = {}
    for k in keys:
        if k in result and result[k] is not None:
            v = result[k]
            if isinstance(v, (str, int, float, bool)):
                slim[k] = v
            elif isinstance(v, list) and len(v) < 50:
                slim[k] = v
    return slim or None


def _merge_resume_context(ctx: Dict[str, Any], stage_id: str, result: Any) -> Dict[str, Any]:
    merged = dict(ctx or {})
    ckpt = _sanitize_checkpoint(stage_id, result)
    if ckpt:
        merged[stage_id] = ckpt
    return merged


def _persist_task_stages(task_id: str, *, sync_history: bool = False) -> None:
    task = _tm.get_task(task_id)
    if not task:
        return
    if sync_history or task.get("status") in ("failed", "cancelled", "completed"):
        add_or_update_task_in_history(task)


class PipelineStageTracker:
    """单任务阶段追踪：写 pipeline_stages / resume_context / failed_stage。"""

    def __init__(
        self,
        task_id: str,
        *,
        route: str,
        existing_stages: Optional[Dict] = None,
        resume_from: Optional[str] = None,
        resume_context: Optional[Dict] = None,
    ):
        self.task_id = task_id
        self.route = route if route in PIPELINE_ROUTES else "video"
        self.order = [s["id"] for s in route_stage_defs(self.route)]
        task = _tm.get_task(task_id) or {}
        self.stages: Dict[str, Dict[str, Any]] = copy.deepcopy(existing_stages) if existing_stages else init_pipeline_stages(self.route)
        for sid, row in init_pipeline_stages(self.route).items():
            self.stages.setdefault(sid, row)
            self.stages[sid].setdefault("label", row["label"])
        self.ctx: Dict[str, Any] = dict(resume_context or task.get("resume_context") or {})
        self.resume_from = (resume_from or task.get("resume_from") or "").strip() or None
        if self.resume_from:
            row = self.stages.setdefault(self.resume_from, {"label": stage_label(self.route, self.resume_from)})
            if row.get("status") == "failed":
                row["status"] = "pending"
                row["error"] = ""
        _tm.update_task(
            task_id,
            pipeline_route=self.route,
            pipeline_stages=self.stages,
            resume_context=self.ctx,
            resume_from=self.resume_from,
        )

    def _touch(self, sync_history: bool = False) -> None:
        _tm.update_task(
            self.task_id,
            pipeline_route=self.route,
            pipeline_stages=self.stages,
            resume_context=self.ctx,
            resume_from=self.resume_from,
        )
        if sync_history:
            _persist_task_stages(self.task_id, sync_history=True)

    def should_run(self, stage_id: str) -> bool:
        if not self.resume_from:
            return True
        try:
            fail_idx = self.order.index(self.resume_from)
            cur_idx = self.order.index(stage_id)
        except ValueError:
            return True
        if cur_idx < fail_idx:
            return False
        return True

    def log_skip(self, stage_id: str) -> None:
        from .task_manager import add_log
        label = stage_label(self.route, stage_id)
        add_log(self.task_id, f"[断点恢复] 跳过已完成步骤「{label}」，复用缓存", "INFO")

    def start(self, stage_id: str) -> None:
        row = self.stages.setdefault(stage_id, {"label": stage_label(self.route, stage_id)})
        row["status"] = "in_progress"
        row["error"] = ""
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            from .pipeline_span_bridge import on_stage_start

            on_stage_start(self.task_id, self.route, stage_id)
        except Exception:
            pass
        self._touch()

    def complete(
        self,
        stage_id: str,
        result: Any = None,
        *,
        sync_history: bool = False,
        persist_payload: Any = None,
    ) -> None:
        row = self.stages.setdefault(stage_id, {"label": stage_label(self.route, stage_id)})
        row["status"] = "completed"
        ckpt = _sanitize_checkpoint(stage_id, result if isinstance(result, dict) else result)
        if persist_payload is not None:
            ck_path = save_stage_payload(self.task_id, stage_id, persist_payload)
            ckpt = dict(ckpt or {})
            ckpt["checkpoint_path"] = ck_path
        row["result"] = ckpt
        row["error"] = ""
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        merge_src = ckpt if isinstance(ckpt, dict) else result
        self.ctx = _merge_resume_context(self.ctx, stage_id, merge_src)
        _tm.update_task(self.task_id, resume_context=self.ctx)
        try:
            from .pipeline_span_bridge import on_stage_finish

            on_stage_finish(
                self.task_id,
                self.route,
                stage_id,
                status="completed",
                output_payload=ckpt if isinstance(ckpt, dict) else {"stage_id": stage_id},
            )
        except Exception:
            pass
        self._touch(sync_history=sync_history)

    def finish_success(self) -> None:
        """全流程正式完成：清除断点标记；磁盘缓存由调用方按 url_hash 清除。"""
        _tm.update_task(
            self.task_id,
            failed_stage="",
            failed_stage_label="",
            resume_from="",
            status="completed",
        )
        self._touch(sync_history=True)

    def ctx_get(self, stage_id: str) -> Dict[str, Any]:
        loaded = load_stage_payload(self.task_id, stage_id)
        if loaded is not None:
            return loaded if isinstance(loaded, dict) else {"value": loaded}
        meta = dict(self.ctx.get(stage_id) or {})
        return meta

    def fail(self, stage_id: str, error: str, *, error_code: str = "") -> None:
        err_text = str(error)[:500]
        resolved_code = (error_code or "").strip()
        canonical_msg = err_text
        try:
            from .ops_error_classifier import classify_task_failure

            cls = classify_task_failure(
                error_message=err_text,
                error_code=resolved_code,
                stage=stage_id,
            )
            if cls.get("error_code"):
                resolved_code = str(cls["error_code"])
            if cls.get("error_message"):
                canonical_msg = str(cls["error_message"])[:500]
        except Exception:
            pass
        row = self.stages.setdefault(stage_id, {"label": stage_label(self.route, stage_id)})
        row["status"] = "failed"
        row["error"] = canonical_msg or err_text
        if resolved_code:
            row["error_code"] = resolved_code
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        label = stage_label(self.route, stage_id)
        try:
            from .pipeline_finalize import apply_task_card_metrics

            metrics = apply_task_card_metrics(self.task_id, persist=True)
        except Exception:
            metrics = {}
        _tm.update_task(
            self.task_id,
            status="failed",
            failed_stage=stage_id,
            failed_stage_label=label,
            resume_from=stage_id,
            resume_context=self.ctx,
            error=f"{resolved_code}: {canonical_msg}" if resolved_code else (canonical_msg or err_text),
            stage=f"失败于：{label}",
            **metrics,
        )
        try:
            from .pipeline_span_bridge import on_stage_finish

            on_stage_finish(
                self.task_id,
                self.route,
                stage_id,
                status="failed",
                error_code=resolved_code,
                error_message=canonical_msg or err_text,
            )
        except Exception:
            pass
        self._touch(sync_history=True)


def is_pipeline_terminal_status(status: str) -> bool:
    return (status or "").strip() in PIPELINE_TERMINAL_STATUSES


def hydrate_resume_context_from_cache(
    task_id: str,
    url_hash: str,
    route: str,
    ctx: Optional[Dict[str, Any]] = None,
    stages: Optional[Dict[str, Dict[str, Any]]] = None,
) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """从磁盘步骤缓存回填 resume_context，并同步 pipeline_stages 已完成标记。"""
    merged_ctx = dict(ctx or {})
    merged_stages = copy.deepcopy(stages or {})
    if not url_hash:
        return merged_ctx, merged_stages
    for sid in list_cached_stage_ids(url_hash):
        if sid not in merged_ctx:
            loaded = load_stage_payload(task_id, sid, url_hash=url_hash)
            if loaded is not None:
                merged_ctx[sid] = loaded if isinstance(loaded, dict) else {"value": loaded}
        row = merged_stages.setdefault(sid, {"label": stage_label(route, sid)})
        if row.get("status") not in ("completed", "failed"):
            row["status"] = "completed"
            ck = merged_ctx.get(sid)
            if ck:
                row["result"] = ck if isinstance(ck, dict) else {"value": ck}
    return merged_ctx, merged_stages


def reconcile_resume_from_stages(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据阶段状态机 + 磁盘缓存推断断点恢复位点。
    返回应写入 task 的 patch（pipeline_stages / resume_context / resume_from / failed_stage）。
    """
    route = (task.get("pipeline_route") or task.get("route_type") or "video").strip()
    if route not in PIPELINE_ROUTES:
        route = "video"
    task_id = str(task.get("task_id") or task.get("id") or "")
    url_hash = str(task.get("url_hash") or "").strip()
    stages = copy.deepcopy(task.get("pipeline_stages") or {})
    if not stages:
        stages = init_pipeline_stages(route)
    for sid, row in init_pipeline_stages(route).items():
        stages.setdefault(sid, row)
        stages[sid].setdefault("label", row["label"])

    ctx, stages = hydrate_resume_context_from_cache(
        task_id, url_hash, route, task.get("resume_context"), stages
    )
    order = [s["id"] for s in route_stage_defs(route)]

    resume_from = (task.get("resume_from") or task.get("failed_stage") or "").strip()
    failed_in_order = [sid for sid in order if stages.get(sid, {}).get("status") == "failed"]
    in_progress = [sid for sid in order if stages.get(sid, {}).get("status") == "in_progress"]

    if failed_in_order:
        resume_from = failed_in_order[0]
    elif in_progress:
        resume_from = in_progress[0]
    elif not resume_from:
        inferred = infer_stage_from_status(
            route, str(task.get("status") or ""), str(task.get("stage") or "")
        )
        if inferred:
            resume_from = inferred
        else:
            last_done = -1
            for i, sid in enumerate(order):
                if stages.get(sid, {}).get("status") == "completed":
                    last_done = i
            if last_done + 1 < len(order):
                resume_from = order[last_done + 1]
            elif last_done >= 0:
                resume_from = order[last_done]

    if resume_from:
        row = stages.setdefault(resume_from, {"label": stage_label(route, resume_from)})
        if row.get("status") in ("failed", "in_progress"):
            row["status"] = "pending"
            row["error"] = ""

    label = stage_label(route, resume_from) if resume_from else ""
    return {
        "pipeline_route": route,
        "pipeline_stages": stages,
        "resume_context": ctx,
        "resume_from": resume_from,
        "failed_stage": resume_from,
        "failed_stage_label": label,
    }


def infer_stage_from_status(route: str, status: str, stage_text: str = "") -> Optional[str]:
    m = STATUS_TO_STAGE.get(route) or {}
    if status in m:
        return m[status]
    text = (stage_text or "").lower()
    for spec in route_stage_defs(route):
        if spec["label"] in (stage_text or "") or spec["id"] in text:
            return spec["id"]
    return None


def mark_failure_from_task(task_id: str, error: str, *, route: Optional[str] = None, stage_id: Optional[str] = None) -> None:
    """失败兜底：根据 task.status/stage 推断 failed_stage 并持久化。"""
    task = _tm.get_task(task_id)
    if not task:
        return
    r = route or task.get("pipeline_route") or "video"
    sid = stage_id or task.get("failed_stage") or infer_stage_from_status(r, task.get("status", ""), task.get("stage", ""))
    if not sid:
        sid = "download" if r == "video" else "extract"
    tracker = PipelineStageTracker(
        task_id,
        route=r,
        existing_stages=task.get("pipeline_stages"),
        resume_from=sid,
        resume_context=task.get("resume_context"),
    )
    if tracker.stages.get(sid, {}).get("status") != "failed":
        tracker.fail(sid, error or task.get("error") or "未知错误")


def pipeline_summary(stages: Optional[Dict], route: str) -> List[Dict[str, Any]]:
    """供 API / 前端展示的阶段列表。"""
    stages = stages or {}
    out = []
    for spec in route_stage_defs(route):
        sid = spec["id"]
        row = stages.get(sid) or {}
        out.append({
            "id": sid,
            "label": row.get("label") or spec["label"],
            "optional": bool(spec.get("optional")),
            "status": row.get("status") or "pending",
            "error": row.get("error") or "",
        })
    return out
