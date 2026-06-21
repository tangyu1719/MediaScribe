"""链接文档化任务 — 导入来源与作者元数据。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Optional

# 来源类型（import_source）
SOURCE_MANUAL = "manual"
SOURCE_SUB_CREATOR = "subscription_creator"
SOURCE_SUB_FAVORITES = "subscription_favorites"
SOURCE_CHAT = "chat"
SOURCE_CATALOG = "catalog_seed"
SOURCE_RSS = "rss"
SOURCE_LINK_SCAN = "link_scan"
SOURCE_OTHER = "other"

_SOURCE_LABELS = {
    SOURCE_MANUAL: "导入链接",
    SOURCE_CHAT: "对话自动导入",
    SOURCE_CATALOG: "博主目录摘录",
    SOURCE_RSS: "RSS 订阅",
    SOURCE_LINK_SCAN: "链接扫描",
    SOURCE_OTHER: "其他来源",
}

_KNOWN_SOURCES = frozenset({
    SOURCE_MANUAL,
    SOURCE_SUB_CREATOR,
    SOURCE_SUB_FAVORITES,
    SOURCE_CHAT,
    SOURCE_CATALOG,
    SOURCE_RSS,
    SOURCE_LINK_SCAN,
})


def known_import_sources() -> frozenset:
    """已知 import_source 枚举（筛选/校验用）。"""
    return _KNOWN_SOURCES


def _label_implies_source(label: str) -> str:
    """从已有 source_label 反推 import_source。"""
    lbl = (label or "").strip()
    if not lbl:
        return ""
    if "收藏夹" in lbl:
        return SOURCE_SUB_FAVORITES
    if "订阅博主" in lbl or "自动订阅" in lbl:
        return SOURCE_SUB_CREATOR
    if "目录摘录" in lbl:
        return SOURCE_CATALOG
    if "RSS" in lbl.upper():
        return SOURCE_RSS
    if "对话" in lbl and "导入" in lbl:
        return SOURCE_CHAT
    if "链接扫描" in lbl or "扫描恢复" in lbl:
        return SOURCE_LINK_SCAN
    if lbl == _SOURCE_LABELS[SOURCE_MANUAL]:
        return SOURCE_MANUAL
    return ""


def _infer_from_subscription(subscription_id: str) -> tuple[str, str]:
    sid = (subscription_id or "").strip()
    if not sid:
        return "", ""
    try:
        from .creator_subscription_store import get_subscription

        sub = get_subscription(sid) or {}
        if not sub.get("subscription_id"):
            return "", ""
        plat = str(sub.get("platform") or "").strip()
        name = str(sub.get("display_name") or "").strip()
        src = SOURCE_SUB_FAVORITES if "favorite" in plat.lower() else SOURCE_SUB_CREATOR
        return src, build_source_label(src, display_name=name, platform=plat)
    except Exception:
        return "", ""


def _lookup_link_card_meta(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = str(task.get("subscription_id") or "").strip()
    uh = str(task.get("url_hash") or "").strip()
    if not sid or not uh:
        return None
    try:
        from .subscription_link_card_store import _load_card

        card = _load_card(sid, uh)
        return card if isinstance(card, dict) and card else None
    except Exception:
        return None


def build_source_label(
    import_source: str,
    *,
    display_name: str = "",
    platform: str = "",
) -> str:
    """生成可读来源文案。"""
    src = (import_source or "").strip() or SOURCE_MANUAL
    name = (display_name or "").strip()
    plat = (platform or "").strip()

    if src == SOURCE_SUB_CREATOR:
        if name:
            return f"自动订阅博主：{name}"
        return "自动订阅博主"

    if src == SOURCE_SUB_FAVORITES:
        if name:
            prefix = "小红书用户" if plat in ("xiaohongshu", "xiaohongshu_favorites", "小红书") else "用户"
            return f"自动订阅收藏夹：{prefix}——{name}"
        return "自动订阅收藏夹"

    if src == SOURCE_CATALOG:
        if name:
            return f"博主目录摘录：{name}"
        return _SOURCE_LABELS[SOURCE_CATALOG]

    return _SOURCE_LABELS.get(src, _SOURCE_LABELS[SOURCE_OTHER])


def apply_task_source_meta(
    task: Dict[str, Any],
    *,
    import_source: str = "",
    source_label: str = "",
    author_name: str = "",
    author_id: str = "",
    subscription_id: str = "",
    overwrite: bool = False,
) -> None:
    """写入任务来源/作者字段（默认不覆盖已有非空值）。"""
    if not task:
        return

    def _set(key: str, val: str) -> None:
        v = (val or "").strip()
        if not v:
            return
        if overwrite or not str(task.get(key) or "").strip():
            task[key] = v

    src = (import_source or "").strip()
    if src:
        _set("import_source", src)
    _set("source_label", source_label)
    _set("author_name", author_name)
    _set("author_id", author_id)
    _set("subscription_id", subscription_id)

    if not str(task.get("source_label") or "").strip() and str(task.get("import_source") or "").strip():
        task["source_label"] = build_source_label(str(task.get("import_source") or ""))

    opts = task.get("pipeline_options")
    if not isinstance(opts, dict):
        opts = {}
        task["pipeline_options"] = opts
    if src and (overwrite or not str(opts.get("source") or "").strip()):
        opts["source"] = src
    if str(task.get("source_label") or "").strip() and (overwrite or not str(opts.get("source_label") or "").strip()):
        opts["source_label"] = task["source_label"]


def enrich_task_source_fields(task: Dict[str, Any]) -> Dict[str, Any]:
    """队列/历史展示前补全来源与作者（兼容旧任务）。"""
    if not task:
        return task
    row = dict(task)
    opts = row.get("pipeline_options") if isinstance(row.get("pipeline_options"), dict) else {}

    card = _lookup_link_card_meta(row)
    if card:
        if not str(row.get("subscription_id") or "").strip():
            row["subscription_id"] = str(card.get("subscription_id") or "").strip()
        if not str(row.get("import_source") or "").strip() and str(card.get("import_source") or "").strip():
            row["import_source"] = str(card.get("import_source") or "").strip()
        if not str(row.get("source_label") or "").strip() and str(card.get("source_label") or "").strip():
            row["source_label"] = str(card.get("source_label") or "").strip()
        if not str(row.get("author_name") or "").strip() and str(card.get("author_name") or "").strip():
            row["author_name"] = str(card.get("author_name") or "").strip()
        if not str(row.get("author_id") or "").strip() and str(card.get("author_id") or "").strip():
            row["author_id"] = str(card.get("author_id") or "").strip()

    if not str(row.get("import_source") or "").strip():
        legacy = str(opts.get("source") or "").strip()
        if legacy:
            row["import_source"] = legacy
        else:
            lbl0 = str(row.get("source_label") or opts.get("source_label") or "").strip()
            implied = _label_implies_source(lbl0)
            if implied:
                row["import_source"] = implied
            elif str(row.get("subscription_id") or "").strip():
                src, lbl = _infer_from_subscription(str(row.get("subscription_id") or ""))
                if src:
                    row["import_source"] = src
                    if not lbl0:
                        row["source_label"] = lbl
            elif lbl0:
                row["import_source"] = SOURCE_OTHER
            else:
                row["import_source"] = SOURCE_MANUAL

    if not str(row.get("source_label") or "").strip():
        lbl = str(opts.get("source_label") or "").strip()
        if lbl:
            row["source_label"] = lbl
        else:
            sub_name = ""
            if str(row.get("subscription_id") or "").strip():
                try:
                    from .creator_subscription_store import get_subscription

                    sub_name = str((get_subscription(str(row.get("subscription_id") or "")) or {}).get("display_name") or "")
                except Exception:
                    sub_name = ""
            row["source_label"] = build_source_label(
                str(row.get("import_source") or SOURCE_MANUAL),
                display_name=sub_name or str(row.get("author_name") or ""),
                platform=str(row.get("platform") or ""),
            )

    if not str(row.get("author_name") or "").strip():
        meta = row.get("extracted_metadata") if isinstance(row.get("extracted_metadata"), dict) else {}
        for key in ("author", "author_name", "nickname", "up_name", "creator_name"):
            v = str(meta.get(key) or opts.get(key) or "").strip()
            if v:
                row["author_name"] = v
                break

    return row


def source_meta_kwargs(
    import_source: str,
    *,
    display_name: str = "",
    platform: str = "",
    author_name: str = "",
    author_id: str = "",
    subscription_id: str = "",
) -> Dict[str, str]:
    """create_task / reuse_or_enqueue_task 用的一键参数字典。"""
    label = build_source_label(import_source, display_name=display_name, platform=platform)
    return {
        "import_source": import_source,
        "source_label": label,
        "author_name": (author_name or "").strip(),
        "author_id": (author_id or "").strip(),
        "subscription_id": (subscription_id or "").strip(),
    }
