"""UP 画像五阶段编排 — 目录拉取 → 轻量画像 → 选篇 → 原文 MD → 深度画像 → 持久化。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .creator_feed_adapter import get_feed_adapter
from .creator_profile_article import article_text_usable, run_article_only_for_note
from .creator_profile_llm import (
    build_deep_profile,
    build_light_profile,
    build_note_selection,
    render_profile_markdown,
)
from .creator_profile_store import (
    create_profile_run,
    get_latest_profile_doc,
    get_profile_run,
    save_profile_doc,
    update_profile_run,
)
from .creator_subscription_store import get_subscription, init_db
from .task_manager import get_output_dir

_log = logging.getLogger("sba.creator_profile_runner")
_CHAIN = "社媒订阅-UP画像-五阶段编排"

_PROFILE_LOCKS: Dict[str, asyncio.Lock] = {}


def _public_note_url(value: Any) -> str:
    return str(value or "").split("?", 1)[0].split("#", 1)[0]


def _selected_access_notes_for_media(selected_notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build process-local media inputs; callers must remove this field before tool serialization."""
    return [
        {
            "note_id": note.get("note_id"),
            "title": note.get("title"),
            "content_type": note.get("content_type"),
            "access_url": note.get("pipeline_url") or note.get("canonical_url"),
        }
        for note in selected_notes
    ]


def _lock_for(subscription_id: str) -> asyncio.Lock:
    if subscription_id not in _PROFILE_LOCKS:
        _PROFILE_LOCKS[subscription_id] = asyncio.Lock()
    return _PROFILE_LOCKS[subscription_id]


def _red_id_from_sub(sub: Dict[str, Any]) -> str:
    for tag in sub.get("tags") or []:
        if str(tag).startswith("red_id:"):
            return str(tag).split(":", 1)[1]
    return ""


def _catalog_to_dicts(items) -> List[Dict[str, Any]]:
    return [it.to_dict() if hasattr(it, "to_dict") else dict(it) for it in items]


def _display_name_from_catalog(items, fallback: str, red_id: str) -> str:
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("author_name") or "").strip()
        else:
            name = str(getattr(item, "author_name", "") or "").strip()
        if name and name.casefold() != str(red_id or "").strip().casefold():
            return name
    return fallback


def _enrich_selected_notes(
    selected_notes: List[Dict[str, Any]],
    fetch_results: List[Any],
) -> List[Dict[str, Any]]:
    """合并原文拉取结果：小红书链接 + 本地 MD 路径 + 字数 + 是否可用。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in fetch_results:
        if isinstance(r, Exception):
            continue
        nid = str(r.get("note_id") or "")
        if not nid:
            continue
        art = str(r.get("article") or "")
        usable = article_text_usable(art)
        by_id[nid] = {
            "doc_path": r.get("doc_path") or "",
            "task_id": r.get("task_id") or "",
            "char_len": len(art),
            "fetch_ok": usable,
            "evidence_level": r.get("evidence_level") or "",
            "media_processing": bool(r.get("media_processing")),
            "fetch_error": ""
            if usable
            else (
                "轻量网页正文不足（未做音视频下载/转写）"
                if art.strip()
                else str(r.get("error") or r.get("warning") or "拉取失败")
            ),
        }
    out: List[Dict[str, Any]] = []
    for n in selected_notes:
        nid = str(n.get("note_id") or "")
        extra = by_id.get(nid) or {}
        out.append(
            {
                "note_id": n.get("note_id"),
                "title": n.get("title"),
                "canonical_url": _public_note_url(n.get("canonical_url") or n.get("pipeline_url")),
                "pipeline_url": _public_note_url(n.get("pipeline_url") or n.get("canonical_url")),
                "link_source": n.get("link_source") or "",
                "content_type": n.get("content_type"),
                "published_at": n.get("published_at"),
                **extra,
            }
        )
    return out


def _recover_articles_from_pipeline_results(
    results: List[Any],
    selected_notes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """流水线已完成但 MD 正文提取失败时，尝试回收 ai_analysis 摘要。"""
    from .task_manager import get_task

    note_by_id = {str(n.get("note_id") or ""): n for n in selected_notes}
    recovered: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        nid = str(r.get("note_id") or "")
        note = note_by_id.get(nid) or {}
        tid = str(r.get("task_id") or "")
        task = get_task(tid) if tid else {}
        task = task or {}
        ai = (task.get("resume_context") or {}).get("ai_analysis") or {}
        body = str(r.get("article") or "").strip()
        if not body:
            body = str(ai.get("article") or ai.get("summary") or "").strip()
        if not article_text_usable(body, min_len=80):
            continue
        recovered.append(
            {
                "ok": True,
                "note_id": nid,
                "task_id": tid,
                "title": note.get("title") or r.get("title") or task.get("link_title"),
                "published_at": note.get("published_at"),
                "content_type": note.get("content_type"),
                "canonical_url": _public_note_url(note.get("canonical_url") or r.get("canonical_url")),
                "pipeline_url": _public_note_url(note.get("pipeline_url") or r.get("pipeline_url")),
                "link_source": note.get("link_source") or r.get("link_source") or "recovered_ai_analysis",
                "doc_path": r.get("doc_path") or task.get("doc_path") or "",
                "article": body,
            }
        )
    return recovered


def _render_light_only_profile_md(
    *,
    display_name: str,
    red_id: str,
    creator_id: str,
    profile_run_id: str,
    light_profile: Dict[str, Any],
    selection: Dict[str, Any],
    selected_notes: List[Dict[str, Any]],
    fetch_fail_count: int,
) -> str:
    lines = [
        f"# UP 人物画像（轻量降级）：{display_name}",
        "",
        f"- 小红书号：{red_id}",
        f"- Creator ID：{creator_id}",
        f"- 画像运行 ID：{profile_run_id}",
        f"- 说明：{fetch_fail_count} 篇未得到足够的网页正文，以下为可追溯的轻量画像。",
        "- 资源约束：本次未下载音视频，未调用 FFmpeg/Whisper，不将标题推断冒充为视频原文。",
        "",
        "## 轻量画像（标题推断）",
        "",
        light_profile.get("markdown_excerpt")
        or str(light_profile.get("persona_summary") or ""),
        "",
        "## 计划采样篇目",
        "",
    ]
    for n in selected_notes:
        meta = [str(n.get("content_type") or "unknown")]
        if n.get("published_at"):
            meta.append(str(n.get("published_at")))
        if n.get("evidence_level"):
            meta.append(f"证据={n.get('evidence_level')}")
        lines.append(
            f"- [{n.get('title', n.get('note_id'))}]({n.get('canonical_url', '')}) "
            f"· {' · '.join(meta)}"
        )
    if selection.get("rationale"):
        lines.extend(["", "## 选篇说明", "", str(selection.get("rationale"))])
    return "\n".join(lines)


async def run_creator_profile(
    subscription_id: str,
    *,
    trigger: str = "manual",
    min_pick: int = 5,
    max_pick: int = 10,
) -> Dict[str, Any]:
    init_db()
    sub = get_subscription(subscription_id)
    if not sub:
        return {"ok": False, "error_code": "SUB_NOT_FOUND", "error": "订阅不存在"}

    lock = _lock_for(subscription_id)
    if lock.locked():
        return {"ok": False, "error_code": "PROFILE_BUSY", "error": "该 UP 画像任务正在进行"}

    async with lock:
        run = create_profile_run(subscription_id, trigger=trigger)
        run_id = run["profile_run_id"]
        display_name = sub.get("display_name") or sub.get("creator_id") or ""
        red_id = _red_id_from_sub(sub)
        creator_id = sub.get("creator_id") or ""
        profile_url = sub.get("profile_url") or ""

        _log.info(
            "[%s|creator_profile_runner.run_creator_profile|%s|Agent执行|开始] subscription_id=%s; display_name=%s",
            _CHAIN,
            run_id,
            subscription_id,
            display_name,
        )

        try:
            # ── 阶段0：拉取笔记目录 ──
            update_profile_run(run_id, status="running", stage="catalog")
            adapter = get_feed_adapter(sub["platform"])
            loop = asyncio.get_event_loop()
            catalog_items = await loop.run_in_executor(
                None,
                lambda: adapter.fetch_catalog(creator_id, profile_url=profile_url),
            )
            catalog = _catalog_to_dicts(catalog_items)
            if not catalog:
                raise RuntimeError("PROFILE_CATALOG_EMPTY: 主页未解析到笔记列表")
            update_profile_run(run_id, catalog_count=len(catalog), stage="light_profile")

            # ── 阶段1：轻量画像（仅标题） ──
            light = await loop.run_in_executor(
                None,
                lambda: build_light_profile(
                    display_name=display_name,
                    red_id=red_id,
                    catalog=catalog,
                ),
            )
            if not light.get("ok"):
                raise RuntimeError(f"PROFILE_LIGHT_FAILED: {light.get('error')}")
            update_profile_run(run_id, light_profile_json=light, stage="selecting")

            # ── 阶段2：选篇 5-10 ──
            selection = await loop.run_in_executor(
                None,
                lambda: build_note_selection(
                    display_name=display_name,
                    light_profile=light,
                    catalog=catalog,
                    min_pick=min_pick,
                    max_pick=max_pick,
                ),
            )
            if not selection.get("ok"):
                raise RuntimeError(f"PROFILE_SELECT_FAILED: {selection.get('error')}")
            selected_ids = [str(x) for x in selection.get("selected_note_ids") or []]
            id_set = set(selected_ids)
            selected_notes = [it for it in catalog if str(it.get("note_id")) in id_set]
            if not selected_notes:
                raise RuntimeError("PROFILE_SELECT_EMPTY: 选篇结果为空")
            update_profile_run(
                run_id,
                selection_json=selection,
                selected_count=len(selected_notes),
                stage="resolve_links",
            )

            # ── 阶段2.5：从博主主页采集真实笔记链接（含 xsec_token）再喂流水线 ──
            from .creator_feed_adapter import resolve_note_links_for_selection

            selected_notes = await loop.run_in_executor(
                None,
                lambda: resolve_note_links_for_selection(
                    selected_notes,
                    creator_id=creator_id,
                    profile_url=profile_url,
                    catalog=catalog,
                ),
            )
            # 访问参数只保留在本轮进程内，供后续深度媒体工具提交；公开工具结果仍只返回无 token 链接。
            selected_access_notes = _selected_access_notes_for_media(selected_notes)
            unresolved_notes = [n for n in selected_notes if not n.get("link_resolved")]
            if unresolved_notes:
                _log.warning(
                    "[%s|creator_profile_runner.run_creator_profile|%s|Agent执行|链接未补全] unresolved=%s; total=%s",
                    _CHAIN,
                    run_id,
                    len(unresolved_notes),
                    len(selected_notes),
                )
            selected_notes = [n for n in selected_notes if n.get("link_resolved")]
            if not selected_notes:
                raise RuntimeError("PROFILE_LINKS_UNRESOLVED: 选篇链接均未补全到可访问 token 链接")
            update_profile_run(run_id, stage="deep_fetch")
            # ── 阶段3：并行原文 MD（用主页采集到的真实链接输入流水线） ──
            per_timeout = int(os.environ.get("PROFILE_ARTICLE_TIMEOUT_SEC", "1800"))
            results = await asyncio.gather(
                *[run_article_only_for_note(note=n, timeout_sec=per_timeout) for n in selected_notes],
                return_exceptions=True,
            )
            articles: List[Dict[str, Any]] = []
            ok_n = 0
            fail_n = 0
            for r in results:
                if isinstance(r, Exception):
                    fail_n += 1
                    continue
                art = str(r.get("article") or "")
                if r.get("ok") and article_text_usable(art):
                    articles.append(r)
                    ok_n += 1
                else:
                    fail_n += 1
                    if r.get("ok") and art.strip():
                        _log.warning(
                            "[%s|creator_profile_runner.run_creator_profile|%s|Agent执行|原文无效] "
                            "note_id=%s; doc_path=%s; char_len=%s",
                            _CHAIN,
                            run_id,
                            r.get("note_id"),
                            r.get("doc_path"),
                            len(art),
                        )
            update_profile_run(
                run_id,
                deep_ok_count=ok_n,
                deep_fail_count=fail_n,
                stage="deep_profile",
            )
            if not articles:
                recovered = _recover_articles_from_pipeline_results(results, selected_notes)
                if recovered:
                    articles = recovered
                    ok_n = len(recovered)
                    _log.warning(
                        "[%s|creator_profile_runner.run_creator_profile|%s|Agent执行|回收摘要] recovered=%s",
                        _CHAIN,
                        run_id,
                        ok_n,
                    )

            enriched_notes = _enrich_selected_notes(selected_notes, results)

            if not articles:
                final_status = "light_only"
                profile_md = _render_light_only_profile_md(
                    display_name=display_name,
                    red_id=red_id,
                    creator_id=creator_id,
                    profile_run_id=run_id,
                    light_profile=light,
                    selection=selection,
                    selected_notes=enriched_notes,
                    fetch_fail_count=fail_n,
                )
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", display_name)[:40] or creator_id[:12]
                out_dir = get_output_dir() / "creator_profiles" / subscription_id
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                md_path = out_dir / f"{safe_name}_profile_{ts}.md"
                md_path.write_text(profile_md, encoding="utf-8")

                payload = {
                    "display_name": display_name,
                    "red_id": red_id,
                    "creator_id": creator_id,
                    "subscription_id": subscription_id,
                    "profile_run_id": run_id,
                    "light_profile": light,
                    "selection": selection,
                    "selected_notes": enriched_notes,
                    "sampled_articles": [],
                    "persona_summary": str(light.get("persona_summary") or ""),
                    "content_style": str(light.get("content_style") or ""),
                    "recent_topics": light.get("recent_topics") or [],
                }
                doc = save_profile_doc(
                    subscription_id=subscription_id,
                    profile_run_id=run_id,
                    payload=payload,
                    profile_md=profile_md,
                    profile_md_path=str(md_path),
                    llm_model="",
                )
                update_profile_run(
                    run_id,
                    status=final_status,
                    stage="done",
                    profile_md=profile_md,
                )
                _log.info(
                    "[%s|creator_profile_runner.run_creator_profile|%s|Agent执行|完成] status=%s; deep_ok=%s; deep_fail=%s",
                    _CHAIN,
                    run_id,
                    final_status,
                    ok_n,
                    fail_n,
                )
                return {
                    "ok": True,
                    "profile_run_id": run_id,
                    "status": final_status,
                    "profile_doc_id": doc.get("profile_doc_id"),
                    "profile_md_path": str(md_path),
                    "catalog_count": len(catalog),
                    "selected_count": len(selected_notes),
                    "deep_ok_count": ok_n,
                    "deep_fail_count": fail_n,
                    "profile_doc": doc,
                }

            # ── 阶段4：深度画像 ──
            deep = await loop.run_in_executor(
                None,
                lambda: build_deep_profile(
                    display_name=display_name,
                    red_id=red_id,
                    light_profile=light,
                    articles=articles,
                ),
            )
            if not deep.get("ok"):
                raise RuntimeError(f"PROFILE_DEEP_FAILED: {deep.get('error')}")

            # ── 阶段5：固化文档 + DB ──
            profile_md = render_profile_markdown(
                display_name=display_name,
                red_id=red_id,
                creator_id=creator_id,
                profile_run_id=run_id,
                light_profile=light,
                selection=selection,
                deep_profile=deep,
                selected_notes=enriched_notes,
                sampled_articles=articles,
            )
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", display_name)[:40] or creator_id[:12]
            out_dir = get_output_dir() / "creator_profiles" / subscription_id
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            md_path = out_dir / f"{safe_name}_profile_{ts}.md"
            md_path.write_text(profile_md, encoding="utf-8")

            payload = {
                **deep,
                "display_name": display_name,
                "red_id": red_id,
                "creator_id": creator_id,
                "subscription_id": subscription_id,
                "profile_run_id": run_id,
                "light_profile": light,
                "selection": selection,
                "selected_notes": enriched_notes,
                "sampled_articles": [
                    {
                        "note_id": a.get("note_id"),
                        "title": a.get("title"),
                        "canonical_url": a.get("canonical_url"),
                        "content_type": a.get("content_type"),
                        "doc_path": a.get("doc_path"),
                        "task_id": a.get("task_id"),
                        "char_len": len(a.get("article") or ""),
                        "fetch_ok": article_text_usable(str(a.get("article") or "")),
                    }
                    for a in articles
                ],
            }
            doc = save_profile_doc(
                subscription_id=subscription_id,
                profile_run_id=run_id,
                payload=payload,
                profile_md=profile_md,
                profile_md_path=str(md_path),
                llm_model=deep.get("llm_model") or "",
            )

            final_status = "completed" if fail_n == 0 else "partial"
            update_profile_run(
                run_id,
                status=final_status,
                stage="done",
                deep_profile_json=deep,
                profile_md=profile_md,
                llm_model=deep.get("llm_model") or "",
            )

            _log.info(
                "[%s|creator_profile_runner.run_creator_profile|%s|Agent执行|完成] status=%s; deep_ok=%s; deep_fail=%s",
                _CHAIN,
                run_id,
                final_status,
                ok_n,
                fail_n,
            )
            return {
                "ok": True,
                "profile_run_id": run_id,
                "status": final_status,
                "profile_doc_id": doc.get("profile_doc_id"),
                "profile_md_path": str(md_path),
                "catalog_count": len(catalog),
                "selected_count": len(selected_notes),
                "deep_ok_count": ok_n,
                "deep_fail_count": fail_n,
                "profile_doc": doc,
            }

        except Exception as ex:
            msg = str(ex)
            code = "PROFILE_FAILED"
            if "PROFILE_" in msg:
                code = msg.split(":", 1)[0]
            update_profile_run(
                run_id,
                status="failed",
                stage="failed",
                error_code=code,
                error_message=msg,
            )
            _log.error(
                "[%s|creator_profile_runner.run_creator_profile|%s|Agent执行|失败] error_code=%s; error=%s",
                _CHAIN,
                run_id,
                code,
                msg,
            )
            return {"ok": False, "profile_run_id": run_id, "error_code": code, "error": msg}


async def run_creator_profile_by_red_id(
    red_id: str,
    *,
    display_name: str = "",
    trigger: str = "regression",
) -> Dict[str, Any]:
    """回归/脚本入口：按小红书号创建或复用订阅并跑画像。"""
    from .creator_subscription_api import api_create_subscription
    from .creator_subscription_store import list_subscriptions

    init_db()
    existing = None
    for row in (list_subscriptions(page_size=50).get("items") or []):
        tags = row.get("tags") or []
        if f"red_id:{red_id}" in tags or red_id in (row.get("display_name") or ""):
            existing = row
            break

    if existing:
        subscription_id = existing["subscription_id"]
    else:
        sub = api_create_subscription(
            {
                "platform": "xiaohongshu",
                "red_id": red_id,
                "display_name": display_name or red_id,
            }
        )
        subscription_id = sub["subscription_id"]

    return await run_creator_profile(subscription_id, trigger=trigger)


_CHAT_PROFILE_LOCKS: Dict[str, asyncio.Lock] = {}
_CHAT_CHAIN = "AI对话-小红书人物画像"


def _chat_lock_for(red_id: str) -> asyncio.Lock:
    key = f"xhs:{red_id}"
    if key not in _CHAT_PROFILE_LOCKS:
        _CHAT_PROFILE_LOCKS[key] = asyncio.Lock()
    return _CHAT_PROFILE_LOCKS[key]


async def run_xhs_chat_profile(
    *,
    red_id: str,
    display_name: str = "",
    user_prompt: str = "",
    creator_id: str = "",
    profile_url: str = "",
    min_pick: int = 3,
    max_pick: int = 5,
) -> Dict[str, Any]:
    """AI 对话工具入口：五阶段人物画像（不写订阅库，与订阅画像同链路）。"""
    import uuid

    rid = (red_id or "").strip()
    if not rid:
        return {"ok": False, "error_code": "SUB_RED_ID_INVALID", "error": "请提供小红书号"}

    lock = _chat_lock_for(rid)
    if lock.locked():
        return {"ok": False, "error_code": "PROFILE_BUSY", "error": f"小红书号 {rid} 画像任务正在进行"}

    async with lock:
        run_id = f"chat_profile_{uuid.uuid4().hex[:12]}"
        up = (user_prompt or "").strip()
        loop = asyncio.get_event_loop()
        access_profile_url = (profile_url or "").strip()

        if not creator_id:
            from .creator_feed_adapter import resolve_xhs_red_id

            try:
                resolved = await loop.run_in_executor(None, lambda: resolve_xhs_red_id(rid))
            except Exception as ex:
                msg = str(ex)
                code = msg.split(":", 1)[0] if msg.startswith("SUB_") else "SUB_RED_ID_NOT_FOUND"
                return {"ok": False, "error_code": code, "error": msg}

            creator_id = str(resolved.get("creator_id") or "")
            resolved_profile_url = str(
                resolved.get("profile_url") or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
            )
            access_profile_url = str(
                resolved.get("_access_profile_url") or resolved_profile_url
            ).strip()
            if not display_name:
                display_name = str(resolved.get("display_name") or rid)
        else:
            access_profile_url = access_profile_url or (
                f"https://www.xiaohongshu.com/user/profile/{creator_id}"
            )
            display_name = display_name or rid

        if not creator_id:
            return {"ok": False, "error_code": "SUB_RED_ID_NOT_FOUND", "error": "未能解析 creator_id"}

        profile_url = f"https://www.xiaohongshu.com/user/profile/{creator_id}"
        catalog_profile_url = access_profile_url or profile_url

        _log.info(
            "[%s|creator_profile_runner.run_xhs_chat_profile|%s|Agent执行|开始] red_id=%s; creator_id=%s",
            _CHAT_CHAIN,
            run_id,
            rid,
            creator_id,
        )

        try:
            from .xhs_local_browser import ensure_xhs_cookies_synced

            await loop.run_in_executor(None, lambda: ensure_xhs_cookies_synced(force=False))

            adapter = get_feed_adapter("xiaohongshu")
            catalog_items = await loop.run_in_executor(
                None,
                lambda: adapter.fetch_catalog(creator_id, profile_url=catalog_profile_url),
            )
            if not display_name or display_name == rid:
                display_name = _display_name_from_catalog(catalog_items, display_name or rid, rid)
            catalog = _catalog_to_dicts(catalog_items)
            if not catalog:
                raise RuntimeError("PROFILE_CATALOG_EMPTY: 主页未解析到笔记列表，请确认 CDP/Cookie 就绪")

            light = await loop.run_in_executor(
                None,
                lambda: build_light_profile(display_name=display_name, red_id=rid, catalog=catalog),
            )
            if not light.get("ok"):
                raise RuntimeError(f"PROFILE_LIGHT_FAILED: {light.get('error')}")

            pick_min = max(1, min(int(min_pick or 3), 10))
            pick_max = max(pick_min, min(int(max_pick or 5), 10))
            selection = await loop.run_in_executor(
                None,
                lambda: build_note_selection(
                    display_name=display_name,
                    light_profile=light,
                    catalog=catalog,
                    min_pick=pick_min,
                    max_pick=pick_max,
                    user_prompt=up,
                ),
            )
            if not selection.get("ok"):
                raise RuntimeError(f"PROFILE_SELECT_FAILED: {selection.get('error')}")
            selected_ids = [str(x) for x in selection.get("selected_note_ids") or []]
            id_set = set(selected_ids)
            selected_notes = [it for it in catalog if str(it.get("note_id")) in id_set]
            if not selected_notes:
                raise RuntimeError("PROFILE_SELECT_EMPTY: 选篇结果为空")

            from .creator_feed_adapter import resolve_note_links_for_selection

            selected_notes = await loop.run_in_executor(
                None,
                lambda: resolve_note_links_for_selection(
                    selected_notes,
                    creator_id=creator_id,
                    profile_url=catalog_profile_url,
                    catalog=catalog,
                ),
            )
            # Keep signed access URLs process-local for async media submission. Public selected_notes
            # are rebuilt through _enrich_selected_notes and therefore contain tokenless URLs only.
            selected_access_notes = _selected_access_notes_for_media(selected_notes)

            per_timeout = int(os.environ.get("PROFILE_ARTICLE_TIMEOUT_SEC", "1800"))
            results = await asyncio.gather(
                *[run_article_only_for_note(note=n, timeout_sec=per_timeout) for n in selected_notes],
                return_exceptions=True,
            )
            articles: List[Dict[str, Any]] = []
            ok_n = 0
            fail_n = 0
            for r in results:
                if isinstance(r, Exception):
                    fail_n += 1
                    continue
                art = str(r.get("article") or "")
                if r.get("ok") and article_text_usable(art):
                    articles.append(r)
                    ok_n += 1
                else:
                    fail_n += 1

            if not articles:
                recovered = _recover_articles_from_pipeline_results(results, selected_notes)
                if recovered:
                    articles = recovered
                    ok_n = len(recovered)
                    _log.warning(
                        "[%s|creator_profile_runner.run_xhs_chat_profile|%s|Agent执行|回收摘要] recovered=%s",
                        _CHAT_CHAIN,
                        run_id,
                        ok_n,
                    )

            enriched_notes = _enrich_selected_notes(selected_notes, results)
            deep: Dict[str, Any] = {}
            final_status = "completed"

            if not articles:
                final_status = "light_only"
                profile_md = _render_light_only_profile_md(
                    display_name=display_name,
                    red_id=rid,
                    creator_id=creator_id,
                    profile_run_id=run_id,
                    light_profile=light,
                    selection=selection,
                    selected_notes=enriched_notes,
                    fetch_fail_count=fail_n,
                )
                summary = str(
                    light.get("markdown_excerpt") or light.get("persona_summary") or ""
                ).strip()
            else:
                deep = await loop.run_in_executor(
                    None,
                    lambda: build_deep_profile(
                        display_name=display_name,
                        red_id=rid,
                        light_profile=light,
                        articles=articles,
                        user_prompt=up,
                    ),
                )
                if not deep.get("ok"):
                    raise RuntimeError(f"PROFILE_DEEP_FAILED: {deep.get('error')}")
                profile_md = render_profile_markdown(
                    display_name=display_name,
                    red_id=rid,
                    creator_id=creator_id,
                    profile_run_id=run_id,
                    light_profile=light,
                    selection=selection,
                    deep_profile=deep,
                    selected_notes=enriched_notes,
                    sampled_articles=articles,
                )
                summary = str(deep.get("markdown_body") or deep.get("persona_summary") or "").strip()
                if not summary:
                    summary = str(light.get("markdown_excerpt") or light.get("persona_summary") or "")[:2000]
                final_status = "completed" if fail_n == 0 else "partial"

            safe_name = re.sub(r'[\\/:*?"<>|]', "_", display_name)[:40] or creator_id[:12]
            out_dir = get_output_dir() / "chat_profiles" / rid
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            md_path = out_dir / f"{safe_name}_profile_{ts}.md"
            md_path.write_text(profile_md, encoding="utf-8")

            _log.info(
                "[%s|creator_profile_runner.run_xhs_chat_profile|%s|Agent执行|完成] status=%s; deep_ok=%s; deep_fail=%s",
                _CHAT_CHAIN,
                run_id,
                final_status,
                ok_n,
                fail_n,
            )
            return {
                "ok": True,
                "profile_run_id": run_id,
                "status": final_status,
                "red_id": rid,
                "creator_id": creator_id,
                "display_name": display_name,
                "profile_url": profile_url,
                "profile_md_path": str(md_path),
                "profile_summary": summary[:6000],
                "catalog_count": len(catalog),
                "selected_count": len(selected_notes),
                "deep_ok_count": ok_n,
                "deep_fail_count": fail_n,
                "resource_mode": "lightweight_no_media",
                "media_processing": False,
                "selected_notes": [
                    {
                        "note_id": n.get("note_id"),
                        "title": n.get("title"),
                        "canonical_url": n.get("canonical_url"),
                        "pipeline_url": n.get("pipeline_url"),
                        "doc_path": n.get("doc_path"),
                        "fetch_ok": n.get("fetch_ok"),
                        "fetch_error": n.get("fetch_error"),
                        "content_type": n.get("content_type"),
                        "published_at": n.get("published_at"),
                        "char_len": n.get("char_len"),
                        "evidence_level": n.get("evidence_level"),
                        "media_processing": n.get("media_processing"),
                    }
                    for n in enriched_notes
                ],
                "light_profile": {
                    k: light.get(k)
                    for k in ("industry", "domain", "niche", "persona_summary", "content_style")
                },
                "deep_profile": {
                    k: deep.get(k)
                    for k in (
                        "persona_summary",
                        "target_audience",
                        "content_style",
                        "recent_topics",
                        "confidence",
                    )
                }
                if deep
                else {},
                "_selected_access_notes": selected_access_notes,
            }

        except Exception as ex:
            msg = str(ex)
            code = "PROFILE_FAILED"
            if "PROFILE_" in msg:
                code = msg.split(":", 1)[0]
            _log.error(
                "[%s|creator_profile_runner.run_xhs_chat_profile|%s|Agent执行|失败] error_code=%s; error=%s",
                _CHAT_CHAIN,
                run_id,
                code,
                msg,
            )
            return {"ok": False, "profile_run_id": run_id, "error_code": code, "error": msg}
