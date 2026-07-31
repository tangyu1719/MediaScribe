"""UP 画像 — 真实 LLM 调用（轻量标题画像 / 选篇 / 深度画像）。"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("sba.creator_profile_llm")
_CHAIN = "社媒订阅-UP画像-LLM分析"


def _load_llm_cfg() -> Dict[str, Any]:
    import os

    base = Path(__file__).resolve().parents[2]
    candidates = [
        Path(os.environ.get("SBA_AGENT_CONFIG", "").strip()) if os.environ.get("SBA_AGENT_CONFIG") else None,
        base / "config.json",
        base.parent / "src" / "agent" / "config.json",
        base.parent.parent / "src" / "agent" / "config.json",
    ]
    for cp in candidates:
        if not cp or not cp.is_file():
            continue
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def invoke_profile_llm(system: str, user: str, *, max_tokens: int = 4000) -> Dict[str, Any]:
    cfg = _load_llm_cfg()
    api_key = (cfg.get("volcengine_api_key") or cfg.get("openai_api_key") or "").strip()
    base_url = (
        cfg.get("volcengine_base_url") or cfg.get("openai_base_url") or "https://ark.cn-beijing.volces.com/api/v3"
    ).strip()
    provider = (cfg.get("gateway_provider") or "ark").strip().lower()
    model = (cfg.get("ai_chat_model") or "").strip()
    if not api_key or not model:
        return {"ok": False, "error": "未配置 LLM 网关（volcengine_api_key + ai_chat_model）"}

    agent_dir = Path(__file__).resolve().parents[2].parent / "src" / "agent"
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    try:
        from provider_adapters import invoke_chat_completion_raw, _extract_openai_message_dict
    except ImportError as ex:
        return {"ok": False, "error": f"provider_adapters 不可用: {ex}"}

    try:
        data = invoke_chat_completion_raw(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.25,
            max_tokens=max_tokens,
            timeout=180.0,
            thinking_enabled=False,
            tools=None,
        )
        msg = _extract_openai_message_dict(data)
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        return {"ok": True, "content": content.strip(), "model": model}
    except Exception as ex:
        _log.error(
            "[%s|creator_profile_llm.invoke_profile_llm|LLM|Agent执行|失败] error=%s",
            _CHAIN,
            ex,
        )
        return {"ok": False, "error": str(ex)}


def _parse_json_block(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    m = re.search(r"```json\s*([\s\S]*?)```", text, re.I)
    if m:
        text = m.group(1).strip()
    elif text.startswith("{"):
        pass
    else:
        idx = text.find("{")
        if idx >= 0:
            text = text[idx:]
    try:
        return json.loads(text)
    except Exception:
        return {}


def build_light_profile(
    *,
    display_name: str,
    red_id: str,
    catalog: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """阶段1：仅标题/元数据的轻量画像。"""
    items = [
        {
            "note_id": it.get("note_id"),
            "title": it.get("title"),
            "content_type": it.get("content_type"),
            "published_at": it.get("published_at"),
        }
        for it in catalog
    ]
    system = (
        "你是社媒博主分析师。用户会给你某小红书 UP 的笔记标题列表（不含正文）。"
        "请基于标题推断该博主的大致行业、领域、内容形态、受众、深入方向。"
        "禁止编造具体正文细节；只能基于标题合理推断，不确定处标注「待深度采样验证」。"
        "最后一行输出 ```json ... ```，结构："
        "{industry,domain,niche,persona_summary,target_audience,content_style,"
        "deep_directions:[],content_type_distribution:{video:N,graphic:N,other:N},"
        "title_topic_buckets:[{topic,count,example_titles:[]}]}。"
    )
    user = json.dumps(
        {"display_name": display_name, "red_id": red_id, "note_count": len(items), "notes": items[:80]},
        ensure_ascii=False,
    )[:28000]
    llm = invoke_profile_llm(system, user, max_tokens=2500)
    if not llm.get("ok"):
        return {"ok": False, "error": llm.get("error")}
    data = _parse_json_block(llm.get("content") or "")
    if not data:
        return {"ok": False, "error": "轻量画像 JSON 解析失败", "raw": llm.get("content")}
    data["ok"] = True
    data["llm_model"] = llm.get("model") or ""
    data["markdown_excerpt"] = (llm.get("content") or "").split("```json")[0].strip()
    return data


def build_note_selection(
    *,
    display_name: str,
    light_profile: Dict[str, Any],
    catalog: List[Dict[str, Any]],
    min_pick: int = 5,
    max_pick: int = 10,
    user_prompt: str = "",
) -> Dict[str, Any]:
    """阶段2：选取 5-10 篇深度采样笔记（时间+类型分散）。"""
    videos = [it for it in catalog if (it.get("content_type") or "") == "video"]
    pool = videos if len(videos) >= min_pick else catalog
    items = [
        {
            "note_id": it.get("note_id"),
            "title": it.get("title"),
            "content_type": it.get("content_type"),
            "published_at": it.get("published_at"),
            "canonical_url": it.get("canonical_url"),
        }
        for it in pool
    ]
    system = (
        f"你是内容采样策划。从候选笔记中选出 {min_pick}-{max_pick} 篇用于深度阅读（将走链接分析拉原文）。"
        "硬性要求：\n"
        "1) 优先选 video 类型；不足时可含 graphic。\n"
        "2) 发布时间尽量分散（早/中/近），避免扎堆同一周。\n"
        "3) 内容类型分散：若多篇都是面经/教程/日常等同质标题，也要选不同时间段或不同子话题。\n"
        "4) 禁止选重复 note_id。\n"
        "输出 ```json```：{selected_note_ids:[], selected:[{note_id,title,published_at,content_type,reason}], rationale}。"
    )
    payload: Dict[str, Any] = {
        "display_name": display_name,
        "light_profile": {
            k: light_profile.get(k)
            for k in (
                "industry",
                "domain",
                "niche",
                "content_type_distribution",
                "title_topic_buckets",
            )
        },
        "candidates": items,
        "min_pick": min_pick,
        "max_pick": max_pick,
    }
    up = (user_prompt or "").strip()
    if up:
        payload["user_analysis_goal"] = up
        system += (
            "\n5) 若提供 user_analysis_goal，优先选取最能支撑该分析目标的笔记（仍须满足时间/类型分散）。"
        )
    user = json.dumps(payload, ensure_ascii=False)[:28000]
    llm = invoke_profile_llm(system, user, max_tokens=2000)
    if not llm.get("ok"):
        return {"ok": False, "error": llm.get("error")}
    data = _parse_json_block(llm.get("content") or "")
    valid_ids = {str(it.get("note_id") or "") for it in pool if it.get("note_id")}
    raw_ids = [str(x) for x in (data.get("selected_note_ids") or []) if x]
    for selected in data.get("selected") or []:
        if isinstance(selected, dict) and selected.get("note_id"):
            raw_ids.append(str(selected.get("note_id")))
    ids: List[str] = []
    for note_id in raw_ids:
        if note_id in valid_ids and note_id not in ids:
            ids.append(note_id)
    if len(ids) < min_pick:
        # LLM 可能返回序号/截断 ID/编造 ID；必须先与真实目录取交集。
        # 交集不足时按时间分桶确定性兜底，禁止把空选篇交给后续链路。
        ids = _fallback_select(pool, min_pick, max_pick)
        data = {
            "selected_note_ids": ids,
            "rationale": "LLM 有效选篇不足，规则分桶兜底",
            "fallback": True,
            "invalid_selected_count": len([nid for nid in raw_ids if nid not in valid_ids]),
        }
    else:
        data["selected_note_ids"] = ids[:max_pick]
    data["ok"] = True
    data["llm_model"] = llm.get("model") or ""
    return data


def _fallback_select(pool: List[Dict[str, Any]], min_pick: int, max_pick: int) -> List[str]:
    from datetime import datetime

    def _ts(it: Dict[str, Any]) -> float:
        raw = it.get("published_at") or ""
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00").split("+")[0]).timestamp()
        except Exception:
            return 0.0

    sorted_items = sorted(pool, key=_ts)
    if not sorted_items:
        return []
    n = min(max_pick, max(min_pick, len(sorted_items)))
    if n <= 1:
        return [str(sorted_items[0].get("note_id"))]
    step = max(1, len(sorted_items) // n)
    picked: List[str] = []
    for i in range(0, len(sorted_items), step):
        nid = str(sorted_items[i].get("note_id") or "")
        if nid and nid not in picked:
            picked.append(nid)
        if len(picked) >= n:
            break
    return picked[:n]


def build_deep_profile(
    *,
    display_name: str,
    red_id: str,
    light_profile: Dict[str, Any],
    articles: List[Dict[str, Any]],
    user_prompt: str = "",
) -> Dict[str, Any]:
    """阶段4：基于原文全文的深度人物画像。"""
    system = (
        "你是深度用户/博主画像分析师。用户已提供轻量标题画像 + 若干篇笔记原文（真实转写/整理）。"
        "请输出完整 UP 人物画像：所属行业/领域/细分赛道、人设摘要、目标受众、内容风格、"
        "典型产出类型、近期输出主题与方向变化、价值主张、可合作场景、风险/边界。"
        "必须基于原文证据，引用具体篇目标题；禁止编造未出现的信息。"
        "最后一行 ```json```："
        "{industry,domain,niche,persona_summary,target_audience,content_style,"
        "deep_directions:[],recent_topics:[],recent_direction_shift,"
        "output_analysis:{themes:[],formats:[],cadence_hint,freshness},"
        "evidence_notes:[{note_id,title,key_points:[]}],collaboration_scenarios:[],"
        "confidence,open_questions:[]}。"
    )
    payload: Dict[str, Any] = {
        "display_name": display_name,
        "red_id": red_id,
        "light_profile": light_profile,
        "articles": [
            {
                "note_id": a.get("note_id"),
                "title": a.get("title"),
                "published_at": a.get("published_at"),
                "content_type": a.get("content_type"),
                "article_excerpt": (a.get("article") or "")[:12000],
            }
            for a in articles
        ],
    }
    up = (user_prompt or "").strip()
    if up:
        payload["user_analysis_goal"] = up
        system += " 若提供 user_analysis_goal，请在画像中显式回应该分析诉求。"
    user = json.dumps(payload, ensure_ascii=False)[:48000]
    llm = invoke_profile_llm(system, user, max_tokens=4500)
    if not llm.get("ok"):
        return {"ok": False, "error": llm.get("error")}
    data = _parse_json_block(llm.get("content") or "")
    if not data:
        return {"ok": False, "error": "深度画像 JSON 解析失败", "raw": llm.get("content")}
    data["ok"] = True
    data["llm_model"] = llm.get("model") or ""
    data["markdown_body"] = (llm.get("content") or "").split("```json")[0].strip()
    return data


def render_profile_markdown(
    *,
    display_name: str,
    red_id: str,
    creator_id: str,
    profile_run_id: str,
    light_profile: Dict[str, Any],
    selection: Dict[str, Any],
    deep_profile: Dict[str, Any],
    selected_notes: List[Dict[str, Any]],
    sampled_articles: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """阶段5：固化人物资料文档。"""
    lines = [
        f"# UP 人物画像：{display_name}",
        "",
        f"- 小红书号：{red_id or '—'}",
        f"- Creator ID：{creator_id or '—'}",
        f"- 画像运行 ID：{profile_run_id}",
        f"- LLM：{deep_profile.get('llm_model') or light_profile.get('llm_model') or '—'}",
        "",
        "## 一、轻量画像（标题推断）",
        "",
        light_profile.get("markdown_excerpt") or json.dumps(
            {k: light_profile.get(k) for k in ("industry", "domain", "niche", "persona_summary")},
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "## 二、深度采样篇目",
        "",
    ]
    art_by_id = {
        str(a.get("note_id") or ""): a for a in (sampled_articles or []) if a.get("note_id")
    }
    for n in selected_notes:
        nid = str(n.get("note_id") or "")
        art = art_by_id.get(nid) or n
        doc_path = str(art.get("doc_path") or n.get("doc_path") or "")
        char_len = art.get("char_len") or n.get("char_len") or ""
        fetch_ok = art.get("fetch_ok", n.get("fetch_ok"))
        status = "正文有效" if fetch_ok else "正文无效/页面不可访问"
        local_ref = Path(doc_path).name if doc_path else "—"
        lines.append(
            f"- [{n.get('title', n.get('note_id'))}]({n.get('canonical_url', '')}) "
            f"· {n.get('content_type', '')} · {n.get('published_at') or '—'}"
        )
        pipe = str(art.get("pipeline_url") or n.get("pipeline_url") or doc_path or "")
        if pipe and pipe != n.get("canonical_url"):
            lines.append(f"  - 流水线链接：`{pipe}`")
        if n.get("link_source") or art.get("link_source"):
            lines.append(f"  - 链接来源：`{n.get('link_source') or art.get('link_source')}`")
        lines.append(f"  - 本地 MD：`{local_ref}` · 字数 {char_len or '—'} · {status}")
        if doc_path:
            lines.append(f"  - 路径：`{doc_path}`")
        if n.get("task_id") or art.get("task_id"):
            lines.append(f"  - 任务 ID：`{n.get('task_id') or art.get('task_id')}`")
    lines.extend(["", "## 三、深度人物画像", "", deep_profile.get("markdown_body") or ""])
    lines.extend(["", "## 四、结构化字段（JSON）", "", "```json", json.dumps(deep_profile, ensure_ascii=False, indent=2), "```"])
    if selection.get("rationale"):
        lines.extend(["", "## 选篇说明", "", str(selection.get("rationale"))])
    return "\n".join(lines)
