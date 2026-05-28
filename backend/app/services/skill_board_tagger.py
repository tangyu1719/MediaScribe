"""SKILL 能力看板：导入时用 LLM 分析并写入固定槽位标签（与前端 orch_catalog 对齐）。"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parents[2]
_AGENT_DIR = (_BASE_DIR.parent / "src" / "agent").resolve()
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

# 与 frontend/assets/js/orch_catalog.js 中 ORCH_CATEGORIES.id 保持一致
BOARD_CATEGORY_IDS = (
    "doc",
    "writing",
    "dev",
    "browser",
    "media",
    "diagram",
    "ad",
    "rag",
    "collab",
    "other",
)

BOARD_CATEGORY_LABELS: Dict[str, str] = {
    "doc": "文档处理",
    "writing": "写作与润色",
    "dev": "开发与流程",
    "browser": "浏览器与自动化",
    "media": "图像与视频",
    "diagram": "图表与长页",
    "ad": "广告与投放",
    "rag": "检索与知识",
    "collab": "协作与飞书",
    "other": "其他",
}

_KEYWORD_FALLBACK: Dict[str, List[str]] = {
    "doc": ["doc", "docx", "pdf", "ppt", "word", "文档", "办公", "deskclaw", "form"],
    "writing": ["writing", "write", "paper", "aigc", "写作", "论文", "latex", "thesis", "arxiv"],
    "dev": ["vibe", "plan", "debug", "coding", "harness", "review", "pipeline", "开发", "测试"],
    "browser": ["browser", "playwright", "scrap", "爬虫", "自动化"],
    "media": ["image", "video", "generation", "图", "视频", "design", "ui-ux", "frontend"],
    "diagram": ["diagram", "mermaid", "flow", "chart", "longpage", "html", "流程图", "图例"],
    "ad": ["ad-", "广告", "投放", "strategy", "creative"],
    "rag": ["rag", "kb", "retriever", "search", "检索", "知识", "向量"],
    "collab": ["lark", "feishu", "飞书", "slack", "im", "comment"],
}


def _env_on(name: str, default: str = "1") -> bool:
    return (os.environ.get(name) or default).strip().lower() not in ("0", "false", "no", "off")


def _load_llm_cfg() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    for cp in [_BASE_DIR / "config.json", _AGENT_DIR / "config.json"]:
        if cp.exists():
            try:
                cfg = json.loads(cp.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    return cfg


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _mostly_latin(text: str) -> bool:
    t = re.sub(r"\s", "", text or "")
    if not t:
        return False
    letters = len(re.findall(r"[A-Za-z]", t))
    return letters / len(t) > 0.55


def _split_description_fallback(desc: str) -> Tuple[str, str]:
    """从 frontmatter description 粗分中英文（无 LLM）。"""
    raw = (desc or "").strip()
    if not raw:
        return "", ""
    invoke = re.search(r"\bInvoke when\b", raw, re.I)
    if invoke and invoke.start() > 12:
        return raw[: invoke.start()].strip().rstrip("。. "), raw[invoke.start() :].strip()
    parts = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if len(parts) >= 2:
        zh_parts: List[str] = []
        en_parts: List[str] = []
        for p in parts:
            if _has_cjk(p) and not _mostly_latin(p):
                zh_parts.append(p)
            elif _mostly_latin(p) and not _has_cjk(p):
                en_parts.append(p)
            elif _has_cjk(p):
                zh_parts.append(p)
            else:
                en_parts.append(p)
        if zh_parts and en_parts:
            return "\n".join(zh_parts), "\n".join(en_parts)
    if _has_cjk(raw) and not _mostly_latin(raw):
        return raw, ""
    if _mostly_latin(raw):
        return "", raw
    return raw, ""


def _label_src(val: str) -> str:
    v = (val or "").strip().lower()
    return v if v in ("file", "ai") else "ai"


def _normalize_display(raw: Dict[str, Any], description: str) -> Dict[str, Any]:
    zh = str(raw.get("desc_zh") or raw.get("description_zh") or "").strip()
    en = str(raw.get("desc_en") or raw.get("description_en") or "").strip()
    zh_src = _label_src(str(raw.get("desc_zh_source") or raw.get("desc_zh_label") or ""))
    en_src = _label_src(str(raw.get("desc_en_source") or raw.get("desc_en_label") or ""))
    card = str(raw.get("card_summary") or raw.get("card_summary_zh") or "").strip()
    if not zh and not en:
        zh, en = _split_description_fallback(description)
        if zh:
            zh_src = "file"
        if en:
            en_src = "file"
    if zh and not zh_src:
        zh_src = "file" if _has_cjk(description) and description.strip() in zh else "ai"
    if en and not en_src:
        en_src = "file" if _mostly_latin(description) and description.strip() in en else "ai"
    if not card:
        base = zh or en or description
        card = base[:56] + ("…" if len(base) > 56 else "")
    if len(card) > 72:
        card = card[:71] + "…"
    return {
        "desc_zh": zh,
        "desc_en": en,
        "desc_zh_source": zh_src if zh else "",
        "desc_en_source": en_src if en else "",
        "card_summary": card,
        "display_source": str(raw.get("display_source") or raw.get("source") or "keyword"),
        "tagged_at": datetime.now().isoformat(),
    }


def _display_fallback(description: str) -> Dict[str, Any]:
    zh, en = _split_description_fallback(description)
    card_base = zh or en or description
    card = card_base[:56] + ("…" if len(card_base) > 56 else "")
    return _normalize_display(
        {
            "desc_zh": zh,
            "desc_en": en,
            "desc_zh_source": "file" if zh else "",
            "desc_en_source": "file" if en else "",
            "card_summary": card,
            "display_source": "keyword",
        },
        description,
    )


def _body_preview(body_md: str, limit: int = 1200) -> str:
    t = " ".join((body_md or "").split())
    if len(t) <= limit:
        return t
    return t[:limit] + "…"


def keyword_classify(name: str, description: str, extra: str = "") -> str:
    """无 LLM 时的关键词兜底，返回主分类 id。"""
    text = f"{name} {description} {extra}".lower()
    hits: List[str] = []
    for cid, keys in _KEYWORD_FALLBACK.items():
        if any(k.lower() in text for k in keys):
            hits.append(cid)
    return hits[0] if hits else "other"


def _normalize_board(raw: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    cat = str(raw.get("category") or raw.get("board_category") or "other").strip().lower()
    if cat not in BOARD_CATEGORY_IDS:
        cat = "other"
    tags_in = raw.get("tags") or raw.get("board_tags") or []
    tags: List[str] = []
    if isinstance(tags_in, list):
        for t in tags_in:
            s = str(t).strip()
            if s and s not in tags:
                tags.append(s[:40])
    tags = tags[:12]
    alias_cn = str(raw.get("alias_cn") or raw.get("aliasCn") or "").strip()[:32]
    summary = str(raw.get("summary") or raw.get("board_summary") or "").strip()[:120]
    cats_in = raw.get("categories") or []
    categories: List[str] = [cat]
    if isinstance(cats_in, list):
        for c in cats_in:
            c2 = str(c).strip().lower()
            if c2 in BOARD_CATEGORY_IDS and c2 not in categories:
                categories.append(c2)
    return {
        "category": cat,
        "categories": categories[:3],
        "tags": tags,
        "alias_cn": alias_cn,
        "summary": summary,
        "source": source,
        "tagged_at": datetime.now().isoformat(),
    }


def _parse_llm_json(text: str) -> Any:
    s = (text or "").strip()
    if not s:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.I)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", s)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


def _llm_tag_batch(items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """items: [{name, description, body_preview}]"""
    cfg = _load_llm_cfg()
    api_key = (cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
    base_url = (
        cfg.get("volcengine_base_url")
        or cfg.get("openai_base_url")
        or "https://ark.cn-beijing.volces.com/api/v3"
    ).strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    model = (cfg.get("ai_chat_model") or "").strip()
    if not api_key or not model:
        return []

    cats_desc = "\n".join(f"- {cid}: {BOARD_CATEGORY_LABELS[cid]}" for cid in BOARD_CATEGORY_IDS if cid != "other")
    system = (
        "你是企业 SKILL 目录助手。根据名称、description 与正文摘要，完成：\n"
        "1) 能力看板分类 2) 侧栏双语说明 3) 卡片中文简介。\n"
        "只输出 JSON，不要 markdown。\n"
        f"看板 category（必选其一）:\n{cats_desc}\n- other: 无法归类\n\n"
        "每个 item 字段：\n"
        "- name: 与输入一致\n"
        "- category, categories[], tags[], alias_cn(≤16字), summary(≤60字)\n"
        "- desc_zh: 中文说明（完整、通顺；若原文为英文则翻译，若原文含中文则保留/整理）\n"
        "- desc_en: 英文说明（完整；若原文为中文则翻译，若原文为英文则保留/整理）\n"
        "- desc_zh_source: \"file\"=来自原文中文未改义改写, \"ai\"=AI翻译或生成\n"
        "- desc_en_source: 同上（英文侧）\n"
        "- card_summary: 仅中文，48~64字，用于卡片简介，概括能力不含废话\n\n"
        "格式：{\"items\":[{\"name\":\"...\",\"category\":\"doc\",...}]}\n"
        "不得编造 SKILL 不具备的能力。"
    )
    payload = json.dumps(items, ensure_ascii=False)
    user = f"请分类以下 {len(items)} 个 SKILL：\n{payload}"

    try:
        from provider_adapters import invoke_unified
    except ImportError as e:
        _LOG.warning(
            "[SKILL看板-AI打标|skill_board_tagger._llm_tag_batch|batch|Agent执行|调用] "
            "provider 不可用; error=%s",
            e,
        )
        return []

    try:
        out = invoke_unified(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.15,
            max_tokens=min(4096, 320 * len(items) + 280),
            timeout=90.0,
            thinking_enabled=False,
        )
    except Exception as e:
        _LOG.warning(
            "[SKILL看板-AI打标|skill_board_tagger._llm_tag_batch|batch|Agent执行|失败] "
            "LLM 调用失败; error_type=%s; error_message=%s",
            type(e).__name__,
            str(e)[:300],
        )
        return []

    data = _parse_llm_json(out or "")
    if not isinstance(data, dict):
        return []
    rows = data.get("items")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _apply_llm_row(row: Dict[str, Any], description: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    board = _normalize_board(row, source="llm")
    disp = _normalize_display({**row, "display_source": "llm"}, description)
    return board, disp


def analyze_skill_profile(
    *,
    name: str,
    description: str,
    body_md: str = "",
    use_llm: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """分析单个 SKILL，返回 (board, display)。"""
    preview = _body_preview(body_md)
    if use_llm and _env_on("SKILL_BOARD_TAG_ON_IMPORT", "1"):
        llm_rows = _llm_tag_batch(
            [{"name": name, "description": description, "body_preview": preview}]
        )
        for row in llm_rows:
            if (row.get("name") or "").strip().lower() == name.strip().lower():
                return _apply_llm_row(row, description)
    cat = keyword_classify(name, description, preview)
    board = _normalize_board(
        {
            "category": cat,
            "categories": [cat],
            "tags": [BOARD_CATEGORY_LABELS.get(cat, "其他")],
            "alias_cn": "",
            "summary": (description or "")[:120],
        },
        source="keyword",
    )
    return board, _display_fallback(description)


def analyze_skill_board(
    *,
    name: str,
    description: str,
    body_md: str = "",
    use_llm: bool = True,
) -> Dict[str, Any]:
    board, _ = analyze_skill_profile(
        name=name, description=description, body_md=body_md, use_llm=use_llm
    )
    return board


def tag_skills_by_ids(skill_ids: List[str], *, force: bool = False) -> Dict[str, Any]:
    """批量为已注册 SKILL 打看板标签（导入后或手动重跑）。"""
    from .skill_registry import _load_raw, _save_raw, get_skill

    ids = [str(i).strip() for i in skill_ids if str(i).strip()]
    if not ids:
        return {"ok": True, "tagged": 0, "skipped": 0, "errors": []}

    use_llm = _env_on("SKILL_BOARD_TAG_ON_IMPORT", "1")
    batch_size = max(1, min(12, int(os.environ.get("SKILL_BOARD_TAG_BATCH_SIZE") or "6")))
    data = _load_raw()
    skills: List[Dict[str, Any]] = list(data.get("skills") or [])
    by_id = {s.get("id"): i for i, s in enumerate(skills) if s.get("id")}

    pending: List[Tuple[str, int]] = []
    skipped = 0
    for sid in ids:
        idx = by_id.get(sid)
        if idx is None:
            continue
        row = skills[idx]
        disp = row.get("display") if isinstance(row.get("display"), dict) else {}
        if (
            not force
            and isinstance(row.get("board"), dict)
            and row["board"].get("source") == "llm"
            and disp.get("card_summary")
            and disp.get("desc_zh")
        ):
            skipped += 1
            continue
        pending.append((sid, idx))

    tagged = 0
    errors: List[Dict[str, str]] = []

    if use_llm and pending:
        for start in range(0, len(pending), batch_size):
            chunk = pending[start : start + batch_size]
            llm_inputs: List[Dict[str, str]] = []
            for sid, idx in chunk:
                row = skills[idx]
                llm_inputs.append(
                    {
                        "name": row.get("name") or "",
                        "description": row.get("description") or "",
                        "body_preview": _body_preview(row.get("body_md") or ""),
                    }
                )
            llm_rows = _llm_tag_batch(llm_inputs) if use_llm else []
            by_name = {
                (r.get("name") or "").strip().lower(): r
                for r in llm_rows
                if isinstance(r, dict)
            }
            for (sid, idx), inp in zip(chunk, llm_inputs):
                row = skills[idx]
                name = row.get("name") or ""
                try:
                    desc = row.get("description") or ""
                    raw = by_name.get(name.strip().lower())
                    if raw:
                        board, display = _apply_llm_row(raw, desc)
                    else:
                        board, display = analyze_skill_profile(
                            name=name,
                            description=desc,
                            body_md=row.get("body_md") or "",
                            use_llm=False,
                        )
                    row["board"] = board
                    row["display"] = display
                    skills[idx] = row
                    tagged += 1
                except Exception as e:
                    errors.append({"id": sid, "name": name, "error": str(e)[:200]})
    else:
        for sid, idx in pending:
            row = skills[idx]
            try:
                desc = row.get("description") or ""
                board, display = analyze_skill_profile(
                    name=row.get("name") or "",
                    description=desc,
                    body_md=row.get("body_md") or "",
                    use_llm=False,
                )
                row["board"] = board
                row["display"] = display
                skills[idx] = row
                tagged += 1
            except Exception as e:
                errors.append({"id": sid, "name": row.get("name", ""), "error": str(e)[:200]})

    if tagged:
        data["skills"] = skills
        _save_raw(data)
        _LOG.info(
            "[SKILL看板-AI打标|skill_board_tagger.tag_skills_by_ids|registry|Agent执行|完成] "
            "看板标签已写入; tagged=%s; skipped=%s; errors=%s",
            tagged,
            skipped,
            len(errors),
        )

    return {
        "ok": not errors,
        "tagged": tagged,
        "skipped": skipped,
        "errors": errors,
        "llm_enabled": use_llm,
    }


def tag_and_persist_skill(row: Dict[str, Any], *, force: bool = False) -> Dict[str, Any]:
    """导入单条后立即打标并写回注册表。"""
    sid = (row.get("id") or "").strip()
    if not sid:
        return row
    tag_skills_by_ids([sid], force=force)
    from .skill_registry import get_skill

    return get_skill(sid) or row
