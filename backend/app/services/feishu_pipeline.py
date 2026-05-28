"""飞书上传 —— 对齐 video_gui._run_feishu_upload_if_enabled 日志与校验。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .pipeline_logging import pipeline_log


def run_feishu_upload(
    md_path: str,
    task_id: str,
    link: str,
    cfg: Dict[str, Any],
    *,
    user_prompt: str = "",
    feishu_folder_override: str = "",
    log_cb: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """
    上传 Markdown 到飞书知识库。
    返回 dict：ok, doc_token, doc_url, folder, verify, skipped, reason
    """
    obj = Path(md_path).name or "unknown.md"
    chain = "链接沉淀文档-飞书上传-传后校验"
    module = "feishu_pipeline.run_feishu_upload"

    def _flog(phase: str, action: str, level: str = "INFO", **kw: Any) -> None:
        pipeline_log(
            task_id, chain, module, obj, phase, "硬编执行", action, level,
            log_cb=log_cb, **kw,
        )

    effective = bool(cfg.get("feishu_sync_enabled"))
    _flog("上传判定", "飞书上传判定", effective=effective, feishu_sync_enabled=effective)
    if not effective:
        _flog("上传判定", "未开启飞书同步，跳过上传", "INFO")
        return {"ok": False, "skipped": True, "reason": "feishu_sync_disabled"}

    app_id = (cfg.get("feishu_app_id") or "").strip()
    app_secret = (cfg.get("feishu_app_secret") or "").strip()
    if not app_id or not app_secret:
        _flog(
            "上传判定",
            "未配置飞书凭证，跳过上传",
            "WARNING",
            feishu_app_id_set=bool(app_id),
            config_hint="请在 AI 配置填写 feishu_app_id / feishu_app_secret",
        )
        return {"ok": False, "skipped": True, "reason": "no_credentials"}

    try:
        content = Path(md_path).read_text(encoding="utf-8")
    except Exception as e:
        _flog("读取文档", "读取 Markdown 失败", "ERROR", error_type=type(e).__name__, error_message=str(e))
        return {"ok": False, "reason": "read_failed"}

    _flog("读取文档", "读取 Markdown 成功", chars=len(content))
    if len((content or "").strip()) < 80:
        _flog("读取文档", "Markdown 内容过短，存在空壳风险", "WARNING", chars=len((content or "").strip()))

    try:
        from feishu_integration import FeishuKnowledgeBase
    except Exception as e:
        _flog("初始化", "feishu_integration 不可用", "ERROR", error_message=str(e))
        return {"ok": False, "reason": "import_failed"}

    kb = FeishuKnowledgeBase(app_id=app_id, app_secret=app_secret)
    default_folder = (cfg.get("feishu_default_folder_path") or "").strip()
    folder_token_cfg = (cfg.get("feishu_folder_token") or "").strip() or None
    final_folder = (feishu_folder_override or "").strip() or default_folder or None

    prompt_folder = ""
    try:
        prompt_folder = kb.parse_feishu_folder_from_prompt(user_prompt or "") or ""
    except Exception:
        pass
    if prompt_folder:
        final_folder = prompt_folder

    # 对齐 video_gui：用落盘文件名（无 .md）作为飞书文档标题
    doc_title = Path(md_path).stem or "文档"
    max_retry = max(0, int(cfg.get("feishu_upload_retry_on_check_fail", 1) or 1))
    recreate_on_fail = bool(cfg.get("feishu_upload_recreate_on_check_fail", True))
    verify_enabled = bool(cfg.get("feishu_upload_postcheck_enabled", True))

    _flog(
        "上传参数",
        "飞书上传参数就绪",
        folder_path=final_folder or "(默认根)",
        folder_token_cfg=bool(folder_token_cfg),
        doc_title=doc_title,
        max_retry=max_retry,
        verify_enabled=verify_enabled,
        recreate_on_fail=recreate_on_fail,
    )

    success = False
    final_token = ""
    final_url = ""
    final_verify: Dict[str, Any] = {"ok": True, "reason": "skipped"}

    for attempt in range(max_retry + 1):
        _flog("上传", "开始上传飞书文档", attempt=f"{attempt + 1}/{max_retry + 1}", folder=final_folder or "")
        try:
            doc_token = kb.upload_document(
                doc_title,
                content,
                feishu_folder_path=final_folder,
                folder_token=folder_token_cfg,
            )
        except Exception as e:
            _flog("上传", "upload_document 异常", "WARNING", attempt=f"{attempt + 1}/{max_retry + 1}", error_message=str(e))
            doc_token = None
        if not doc_token:
            detail = getattr(kb, "last_error", None)
            _flog("上传", "上传失败", "WARNING", attempt=f"{attempt + 1}/{max_retry + 1}", detail=detail or "无详细错误")
            continue

        doc_url = getattr(kb, "last_doc_url", None) or ""
        if not doc_url:
            if str(doc_token).startswith("wiki:"):
                doc_url = f"https://www.feishu.cn/wiki/{str(doc_token)[5:]}"
            else:
                doc_url = f"https://www.feishu.cn/docx/{doc_token}"

        _flog("上传", "飞书 API 返回成功", doc_token=doc_token, doc_url=doc_url)

        if verify_enabled:
            wait_sec = round(1.5 + attempt * 0.5, 2)
            _flog("上传后等待", "等待飞书内容同步", wait_sec=wait_sec, attempt=f"{attempt + 1}/{max_retry + 1}")
            time.sleep(wait_sec)

        verify_res: Dict[str, Any] = {"ok": True, "reason": "skipped", "remote_length": 0, "expected_length": len(content)}
        if verify_enabled and hasattr(kb, "verify_document_content"):
            verify_res = kb.verify_document_content(doc_url or str(doc_token), content) or verify_res
            _flog(
                "首次校验",
                "校验完成",
                "INFO" if verify_res.get("ok") else "WARNING",
                ok=verify_res.get("ok"),
                remote_length=verify_res.get("remote_length"),
                expected_length=verify_res.get("expected_length"),
                reason=verify_res.get("reason"),
            )
            if (
                not bool(verify_res.get("ok"))
                and int(verify_res.get("remote_length") or 0) <= 20
            ):
                _flog("二次校验前等待", "远端内容过短，准备二次校验", "WARNING")
                time.sleep(2.5 + attempt * 0.8)
                verify_res = kb.verify_document_content(doc_url or str(doc_token), content) or verify_res
                _flog(
                    "二次校验",
                    "校验完成",
                    "INFO" if verify_res.get("ok") else "WARNING",
                    ok=verify_res.get("ok"),
                    remote_length=verify_res.get("remote_length"),
                    expected_length=verify_res.get("expected_length"),
                    reason=verify_res.get("reason"),
                )

        if bool(verify_res.get("ok")):
            success = True
            final_token = str(doc_token)
            final_url = str(doc_url)
            final_verify = verify_res
            break

        final_verify = verify_res
        _flog("重试", "上传校验失败，准备回退重试", "WARNING", attempt=f"{attempt + 1}/{max_retry + 1}")
        if attempt < max_retry and recreate_on_fail and int(verify_res.get("remote_length") or 0) <= 20:
            if hasattr(kb, "delete_document"):
                try:
                    deleted = bool(kb.delete_document(doc_url or str(doc_token)))
                    _flog("重建", "空壳文档删除", "INFO" if deleted else "WARNING", deleted=deleted)
                except Exception as de:
                    _flog("重建", "删除异常", "WARNING", error_message=str(de))
            time.sleep(1.0)

    if success:
        _flog(
            "完成",
            "文档上传成功",
            "INFO",
            ok=True,
            doc_token=final_token,
            doc_url=final_url,
            folder=final_folder or "",
            verify_ok=final_verify.get("ok"),
        )
        result = {
            "ok": True,
            "doc_token": final_token,
            "doc_url": final_url,
            "folder": final_folder or "",
            "verify": final_verify,
        }
        try:
            from .ops_hooks import ops_async_hook_allowed, ops_dispatch_feishu_check

            if ops_async_hook_allowed("feishu_upload_postcheck") or ops_async_hook_allowed("ops_async_review"):
                ops_dispatch_feishu_check(
                    link=link or "",
                    task_id=task_id,
                    doc_url=final_url,
                    verify_result=final_verify,
                )
        except Exception:
            pass
        return result

    detail = getattr(kb, "last_error", None)
    _flog("失败", "上传到飞书最终失败", "WARNING", detail=detail, verify=final_verify)
    fail_result = {"ok": False, "reason": "upload_failed", "verify": final_verify, "detail": detail}
    try:
        from .ops_hooks import ops_async_hook_allowed, ops_dispatch_feishu_check

        if ops_async_hook_allowed("feishu_upload_postcheck") or ops_async_hook_allowed("ops_async_review"):
            ops_dispatch_feishu_check(
                link=link or "",
                task_id=task_id,
                doc_url="",
                verify_result=final_verify if isinstance(final_verify, dict) else {"ok": False, "message": detail},
            )
    except Exception:
        pass
    return fail_result


def start_feishu_upload_async(
    md_path: str,
    task_id: str,
    *,
    link: str = "",
    user_prompt: str = "",
    cfg: Optional[Dict[str, Any]] = None,
    log_cb: Optional[Callable[[str, str], None]] = None,
    pipeline_route: str = "xiaohongshu_graphic",
) -> None:
    """
    飞书上传放入后台线程池（与 HTML 长页同级），失败不将主流水线 status 置为 failed。
    Python GIL 不阻止 I/O 型上传并发；阻塞 HTTP 走 ThreadPoolExecutor 即可。
    """
    from .config import load_config
    from .history_manager import add_or_update_task_in_history
    from .pipeline_executor import get_background_executor
    from .pipeline_stages import PipelineStageTracker
    from .task_manager import add_log, get_task, update_task

    merged_cfg = dict(cfg or load_config())
    update_task(task_id, feishu_status="async_pending", feishu_message="飞书后台上传中…")
    add_log(task_id, "[飞书] 已提交后台上传（MD 已完成，不阻塞任务完成态）", "INFO")

    def _run() -> None:
        try:
            res = run_feishu_upload(
                md_path,
                task_id,
                link,
                merged_cfg,
                user_prompt=user_prompt,
                log_cb=log_cb,
            )
            task = get_task(task_id) or {}
            tracker = PipelineStageTracker(
                task_id,
                route=pipeline_route,
                existing_stages=task.get("pipeline_stages"),
            )
            upload_ok = bool(res.get("ok"))
            skipped = bool(res.get("skipped"))
            if upload_ok:
                update_task(
                    task_id,
                    feishu_status="completed",
                    feishu_message="飞书上传完成",
                    feishu_doc_url=res.get("doc_url"),
                    feishu_doc_token=res.get("doc_token"),
                )
                tracker.complete(
                    "feishu_upload",
                    {"uploaded": True, "feishu_doc_url": res.get("doc_url")},
                )
                add_log(task_id, "[飞书] 后台上传完成", "INFO")
            elif skipped:
                reason = str(res.get("reason") or "skipped")
                update_task(
                    task_id,
                    feishu_status="skipped",
                    feishu_message=reason,
                )
                tracker.complete("feishu_upload", {"uploaded": False, "skipped": True, "reason": reason})
                add_log(task_id, f"[飞书] 已跳过：{reason}", "INFO")
            else:
                err = str(res.get("reason") or res.get("error") or "飞书上传失败")
                update_task(task_id, feishu_status="failed", feishu_message=err[:240])
                tracker.complete("feishu_upload", {"uploaded": False, "error": err[:500]})
                add_log(task_id, f"[飞书] 后台上传失败（任务仍已完成）: {err}", "WARNING")
        except Exception as ex:
            update_task(task_id, feishu_status="failed", feishu_message=str(ex)[:240])
            add_log(task_id, f"[飞书] 后台上传异常: {ex}", "ERROR")
        finally:
            done = get_task(task_id)
            if done:
                add_or_update_task_in_history(done)

    get_background_executor().submit(_run)
