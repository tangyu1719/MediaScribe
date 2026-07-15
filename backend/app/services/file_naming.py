"""导出文件命名与双层标题 —— 与 video_gui / 摘要 Agent 预设对齐。

双层标题（任务字段 link_title / doc_title，供 UI 与模板占位）：
  1. 首层 link_title：先 extract_title_from_link；平台/链接分析器返回有效标题后覆盖。
  2. 二层 doc_title：摘要 Agent 输出首行（# 标题或首段中文标题，见 extract_title_from_summary）。

file_naming_rule 仅用于落盘文件名（build_output_md_path），不得套在 link_title/doc_title 展示字段上。
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .task_manager import get_output_dir
from .document_consolidation import extract_title_from_summary, clean_title
from .pipeline_output_quality import validate_extracted_title


def apply_naming_template(template: str, **kwargs) -> str:
    """将 file_naming_rule / 标题模板中的占位符替换为实际值。"""
    tpl = (template or "").strip()
    if not tpl:
        return ""
    try:
        return tpl.format(**kwargs)
    except Exception:
        out = tpl
        for key, val in kwargs.items():
            out = out.replace("{" + key + "}", str(val or ""))
        return out


_GENERIC_DOC_TITLE_MARKERS = frozenset({
    "小红书图文分析", "小红书视频分析", "抖音图文分析", "抖音视频分析",
    "B站视频分析", "B站图文分析", "内容分析", "未知标题", "文档标题",
})

_GENERIC_AUTHOR_NAMES = frozenset({
    "小编", "编辑", "作者", "博主", "官方", "管理员", "运营", "未知", "匿名",
})


def is_generic_author_name(name: str) -> bool:
    """Return whether an author label is empty or too generic to persist."""
    text = re.sub(r"\s+", "", str(name or "").strip())
    if not text:
        return True
    if text.lower() in {"unknown", "author", "editor", "admin"}:
        return True
    if text in _GENERIC_AUTHOR_NAMES:
        return True
    return len(text) <= 1


def resolve_output_author_name(
    *,
    author_name: str = "",
    extracted_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """MD 产物元数据中的作者展示名（任务字段优先，其次 extracted_metadata）。"""
    meta = extracted_metadata if isinstance(extracted_metadata, dict) else {}
    name = str(author_name or "").strip()
    if not name or is_generic_author_name(name):
        for key in ("author_name", "author", "nickname", "up_name", "creator_name"):
            v = str(meta.get(key) or "").strip()
            if v and not is_generic_author_name(v):
                name = v
                break
    return name or "未知"


def resolve_effective_doc_title(
    *,
    doc_title: str = "",
    link_title: str = "",
    platform: str = "",
    content_type: str = "",
    summary: str = "",
) -> str:
    """成品 Markdown 一级标题：优先 doc_title / link_title，禁止回退为「平台+类型+分析」泛称。"""
    candidates: List[str] = []
    for raw in (doc_title, link_title):
        t = (raw or "").strip()
        if t.endswith(" - 小红书"):
            t = t[:-5].strip()
        if t and not _is_junk_link_title(t) and t not in _GENERIC_DOC_TITLE_MARKERS:
            candidates.append(t)
    if candidates:
        return sanitize_filename_part(candidates[0].replace(" ", "_"))[:50].replace("_", " ")
    if summary:
        from .document_consolidation import extract_title_from_summary

        guessed = (extract_title_from_summary(summary, "") or "").strip()
        if guessed and guessed not in _GENERIC_DOC_TITLE_MARKERS:
            return guessed[:50]
    fb = (link_title or doc_title or "").strip()
    if fb.endswith(" - 小红书"):
        fb = fb[:-5].strip()
    if fb and not _is_junk_link_title(fb):
        return fb[:50]
    return "未命名文档"


def render_output_template(
    template: str,
    *,
    platform: str,
    link: str,
    article: str,
    summary: str,
    content_type: str = "视频",
    transcribe_source: str = "",
    link_title: str = "",
    doc_title: str = "",
    author_name: str = "",
    extracted_metadata: Optional[Dict[str, Any]] = None,
    comments_section: str = "",
    comments_analysis: str = "",
    comments_file_link: str = "",
    meta_json: str = "",
    task_note: str = "",
    task_keywords: str = "",
) -> str:
    """按 config output_template 渲染成品 Markdown。"""
    tpl = (template or "").strip()
    if not tpl:
        return ""
    # 兼容旧模板：将泛称 H1 占位自动替换为具体 doc_title
    if "{platform}{content_type}分析" in tpl and "{doc_title}" not in tpl.split("\n", 1)[0]:
        tpl = tpl.replace("# {platform}{content_type}分析", "# {doc_title}", 1)
    effective_title = resolve_effective_doc_title(
        doc_title=doc_title,
        link_title=link_title,
        platform=platform,
        content_type=content_type,
        summary=summary,
    )
    eff_author = resolve_output_author_name(
        author_name=author_name,
        extracted_metadata=extracted_metadata if isinstance(extracted_metadata, dict) else {},
    )
    ctx = {
        "platform": platform,
        "link": link,
        "article": article,
        "summary": summary,
        "transcript": article,
        "text": article,
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "content_type": content_type,
        "transcribe_source": (transcribe_source or "").strip() or "unknown",
        "link_title": link_title,
        "doc_title": effective_title,
        "author_name": eff_author,
        "author": eff_author,
        "comments_section": (comments_section or "").strip(),
        "comments_analysis": (comments_analysis or "").strip(),
        "comments_file_link": (comments_file_link or "").strip(),
        "meta_json": (meta_json or "").strip(),
        "task_note": (task_note or "").strip(),
        "task_keywords": (task_keywords or "").strip(),
    }
    rendered = apply_naming_template(tpl, **ctx)
    # 未在模板中放置 {meta_json} 时，默认在文首展示结构化元数据
    if meta_json and "{meta_json}" not in tpl and meta_json not in rendered:
        rendered = meta_json.rstrip() + "\n\n" + rendered
    return rendered


def _detect_platform_from_link(link: str, platform: str = "") -> str:
    if platform:
        return platform
    low = (link or "").lower()
    if "xiaohongshu.com" in low or "xhslink.com" in low:
        return "小红书"
    if "douyin.com" in low or "iesdouyin" in low:
        return "抖音"
    if "bilibili.com" in low or "b23.tv" in low:
        return "B站"
    if "mp.weixin.qq.com" in low:
        return "微信"
    return ""


def _is_junk_link_title(title: str) -> bool:
    """过滤 URL 参数、token 等不可作为展示标题的片段。"""
    t = (title or "").strip()
    if not t or len(t) < 2:
        return True
    if len(t) > 120:
        return True
    low = t.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return True
    if "xsec_token" in low or "xsec_source" in low:
        return True
    if re.fullmatch(r"[A-Za-z0-9_-]{24,}", t):
        return True
    if t in ("小红书", "未知标题", "未命名文档", "抖音", "抖音图文"):
        return True
    return False


def _link_fetch_headers(link: str) -> dict:
    low = (link or "").lower()
    referer = "https://www.xiaohongshu.com/"
    if "douyin.com" in low or "iesdouyin" in low:
        referer = "https://www.douyin.com/"
    elif "bilibili.com" in low or "b23.tv" in low:
        referer = "https://www.bilibili.com/"
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
    }


def _fetch_link_html(link: str, timeout: float = 4.0) -> str:
    link = (link or "").strip()
    if not link:
        return ""
    try:
        import requests

        resp = requests.get(link, headers=_link_fetch_headers(link), timeout=timeout)
        if resp.status_code == 200:
            return resp.text or ""
    except Exception:
        pass
    return ""


def _meta_content(soup, *keys: str) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag:
            val = (tag.get("content") or "").strip()
            if val:
                return val
    return ""


def _title_from_soup(soup, platform: str) -> str:
    if not soup:
        return ""
    candidates = []
    for key in ("og:title", "twitter:title"):
        og = _meta_content(soup, key)
        if og:
            candidates.append(og)
    title_tag = soup.find("title")
    if title_tag and title_tag.text:
        candidates.append(title_tag.text.strip())
    for raw in candidates:
        cleaned = clean_title(raw.replace("\n", " ").replace("\r", " "), platform=platform)
        if cleaned and not _is_junk_link_title(cleaned):
            return cleaned
    return ""


def _cover_from_soup(soup, html: str = "") -> str:
    if soup:
        for key in ("og:image", "twitter:image"):
            cover = _meta_content(soup, key)
            if cover and cover.startswith("http"):
                return cover
    if html:
        m = re.search(r'"urlDefault"\s*:\s*"(https?://[^"]+)"', html)
        if m:
            return m.group(1).replace("\\u002F", "/")
        m = re.search(r'"coverUrl"\s*:\s*"(https?://[^"]+)"', html)
        if m:
            return m.group(1).replace("\\u002F", "/")
    return ""


def _detect_content_kind(link: str, platform: str, html: str = "", route_type: str = "") -> str:
    plat = _detect_platform_from_link(link, platform)
    rt = (route_type or "").lower()
    low = (html or "").lower()
    link_low = (link or "").lower()
    if rt in ("xiaohongshu", "douyin_image", "douyin_article", "graphic", "note"):
        return "图文"
    if rt in ("video", "xiaohongshu_video"):
        return "视频"
    if plat == "小红书":
        if re.search(r'"type"\s*:\s*"video"', html or ""):
            return "视频"
        if "note-video" in low or "videoid" in low:
            return "视频"
        return "图文"
    if plat == "抖音":
        if "/note/" in link_low or "douyin.com/note" in link_low:
            return "图文"
        if re.search(r'"awemeType"\s*:\s*(?:2|68)', html or ""):
            return "图文"
        return "视频"
    if plat == "B站":
        return "视频"
    return "视频"


def bootstrap_link_meta(link: str, platform: str = "", log_cb: Optional[Callable[[str], None]] = None) -> Dict[str, str]:
    """入队时同步抓取链接首层标题、封面与路由类型（单次 HTTP）。"""
    plat = _detect_platform_from_link(link, platform)
    meta: Dict[str, str] = {
        "link_title": "",
        "cover_url": "",
        "content_type": _detect_content_kind(link, plat),
        "route_type": "",
    }
    html = ""
    soup = None
    if plat in ("小红书", "B站", "抖音"):
        html = _fetch_link_html(link)
        if html:
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(html, "html.parser")
            except ImportError:
                if log_cb:
                    log_cb("缺少 bs4，跳过网页解析")
            if soup:
                title = _title_from_soup(soup, plat)
                if title:
                    meta["link_title"] = title
                    if log_cb:
                        log_cb(f"从链接页提取标题：{title}")
                cover = _cover_from_soup(soup, html)
                if cover:
                    meta["cover_url"] = cover
                meta["content_type"] = _detect_content_kind(link, plat, html=html)
    if not meta["link_title"]:
        fallback = extract_title_from_link(link, log_cb=log_cb)
        if fallback and not _is_junk_link_title(fallback):
            meta["link_title"] = fallback
    return meta


def preview_from_analyzer_result(result: Dict, link: str, platform: str = "") -> Dict[str, str]:
    """平台分析器返回后，补齐封面与路由标签字段。"""
    result = result or {}
    route_type = (result.get("type") or "").strip()
    content_type = _detect_content_kind(link, platform, route_type=route_type)
    cover = ""
    for key in ("cover_url", "cover", "poster", "thumbnail"):
        val = (result.get(key) or "").strip()
        if val.startswith("http"):
            cover = val
            break
    imgs = list(result.get("image_links") or [])
    if not cover and imgs:
        first = (imgs[0] or "").strip()
        if first.startswith("http"):
            cover = first
    return {
        "content_type": content_type,
        "cover_url": cover,
        "route_type": route_type,
    }


_link_author_cache: dict = {}


def _extract_author_from_html(html: str, platform: str) -> Tuple[str, str]:
    """从页面 HTML 中提取作者昵称和 ID。小红书优先从 __INITIAL_STATE__ 取。"""
    if not html:
        return "", ""
    author_name = ""
    author_id = ""
    try:
        if platform == "小红书":
            import re as _re
            m = _re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*</script>", html, _re.DOTALL)
            if not m:
                m = _re.search(r"__INITIAL_STATE__\s*=\s*(\{.+?\});", html, _re.DOTALL)
            if m:
                import json as _json
                try:
                    state = _json.loads(m.group(1).replace("undefined", "null"))
                except Exception:
                    state = {}
                note = state.get("note") or {}
                note_detail = note.get("noteDetailMap") or {}
                for _k, nd in note_detail.items():
                    note_data = nd.get("note") or nd
                    user = note_data.get("user") or {}
                    if user:
                        author_name = str(user.get("nickname") or user.get("name") or "")
                        author_id = str(user.get("userId") or user.get("id") or user.get("user_id") or "")
                        if author_name:
                            break
        elif platform == "抖音":
            import re as _re
            import json as _json
            for pat in (
                r"window\._SSR_HYDRATED_DATA\s*=\s*(\{.+?\})\s*</script>",
                r"_SSR_HYDRATED_DATA\s*=\s*(\{.+?\});",
            ):
                m = _re.search(pat, html, _re.DOTALL)
                if not m:
                    continue
                try:
                    data = _json.loads(m.group(1))
                except Exception:
                    continue
                video_info = (data.get("app") or {}).get("videoInfo") or {}
                author_info = video_info.get("authorInfo") or {}
                author_name = str(author_info.get("nickname") or author_info.get("name") or "")
                author_id = str(
                    author_info.get("secUid")
                    or author_info.get("sec_uid")
                    or author_info.get("uid")
                    or author_info.get("userId")
                    or ""
                )
                if author_name and not is_generic_author_name(author_name):
                    break
        elif platform == "B站":
            import re as _re
            import json as _json
            m = _re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});", html, _re.DOTALL)
            if m:
                try:
                    state = _json.loads(m.group(1).replace("undefined", "null"))
                except Exception:
                    state = {}
                video_data = (state.get("videoData") or {}).get("data") or {}
                owner = video_data.get("owner") or {}
                author_name = str(owner.get("name") or "")
                author_id = str(owner.get("mid") or "")
            if not author_id:
                m2 = _re.search(r'"mid"\s*:\s*(\d+)', html)
                if m2:
                    author_id = m2.group(1)
    except Exception:
        pass
    if not author_name:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            author_name = _meta_content(soup, "author", "article:author")
            if author_name:
                author_name = str(author_name).strip()
        except Exception:
            pass
    return author_name, author_id


def extract_author_from_link(link: str) -> Tuple[str, str]:
    """下载页面并提取作者昵称和 ID（复用 _link_author_cache 避免重复请求）。"""
    import json as _json
    cache_key = _json.dumps(link, sort_keys=True)
    if cache_key in _link_author_cache:
        return _link_author_cache[cache_key]
    plat = _detect_platform_from_link(link, "")
    html = _fetch_link_html(link)
    result = _extract_author_from_html(html, plat)
    _link_author_cache[cache_key] = result
    return result


def extract_title_from_link(link: str, log_cb: Optional[Callable[[str], None]] = None) -> str:
    """首层标题：从链接页面 title 或链接片段提取（video_gui.extract_title_from_link）。
    同时也提取作者信息缓存到 _link_author_cache 供 pipeline 后续使用。"""
    link = (link or "").strip()
    if not link:
        return ""
    try:
        plat = _detect_platform_from_link(link, "")
        if plat in ("小红书", "B站", "抖音"):
            html = _fetch_link_html(link)
            if html:
                try:
                    from bs4 import BeautifulSoup

                    soup = BeautifulSoup(html, "html.parser")
                    title = _title_from_soup(soup, plat)
                    # 顺便提取作者信息并缓存
                    an, ai = _extract_author_from_html(html, plat)
                    if an:
                        import json as _json
                        _link_author_cache[_json.dumps(link, sort_keys=True)] = (an, ai)
                    if title:
                        if log_cb:
                            log_cb(f"从链接中提取标题：{title}")
                        return title
                except ImportError:
                    if log_cb:
                        log_cb("缺少 bs4，跳过网页解析")
                except Exception as e:
                    if log_cb:
                        log_cb(f"{plat}链接解析异常：{e}")

        bv = re.search(r"BV[0-9A-Za-z]{10}", link)
        if bv:
            return bv.group(0)
    except Exception:
        pass
    return ""


def resolve_link_title(
    link: str,
    platform: str = "",
    analyzer_title: str = "",
    log_cb: Optional[Callable[[str], None]] = None,
    naming_rule: str = "",  # 兼容旧调用；展示标题不再套用 file_naming_rule
) -> str:
    """首层展示名：先链接抓取，平台/链接 AI 分析返回有效标题后覆盖。"""
    _ = naming_rule  # 保留参数避免破坏调用方；命名规则仅用于 build_output_md_path
    plat = _detect_platform_from_link(link, platform)
    core = extract_title_from_link(link, log_cb=log_cb) or ""
    raw = (analyzer_title or "").strip()
    if raw:
        t = clean_title(raw, platform=plat) if plat else clean_title(raw)
        if t and t not in ("未命名文档", "未知标题"):
            if log_cb and t != core:
                log_cb(f"平台分析标题覆盖链接标题：{t}")
            core = t
    return core if not _is_junk_link_title(core) else ""


def bootstrap_link_title(link: str, platform: str = "") -> str:
    """任务入队时同步提取首层标题（对齐 video_gui 创建任务时 extract_title_from_link）。"""
    return resolve_link_title(link, platform=platform)


def resolve_doc_title(
    ai_summary: str,
    link: str,
    link_title: str = "",
    fallback: str = "",
    log_cb: Optional[Callable[[str], None]] = None,
    naming_rule: str = "",  # 兼容旧调用；展示/任务字段不套用 file_naming_rule
    platform: str = "",
    source_text_len: int = 0,
    *,
    quality_gate: bool = True,
) -> str:
    """二层标题：从摘要 Agent 输出提取（extract_title_from_summary），用于任务 doc_title 与文件名 stem。"""
    _ = naming_rule
    plat = _detect_platform_from_link(link, platform)
    name = extract_title_from_summary(ai_summary, link, log_cb=log_cb)
    if name and name not in ("内容分析", "未知标题"):
        safe = sanitize_filename_part(name.replace(" ", "_"))[:50]
        if quality_gate:
            validate_extracted_title(
                safe,
                link=link,
                link_title=link_title,
                source_text_len=source_text_len or len((ai_summary or "").strip()),
            )
        return safe
    if link_title:
        lt = clean_title(link_title, platform=plat) if plat else clean_title(link_title)
        return sanitize_filename_part(lt.replace(" ", "_"))[:50]
    fb = (fallback or "").strip()
    if fb:
        fb_clean = clean_title(fb, platform=plat) if plat else clean_title(fb)
        return sanitize_filename_part(fb_clean.replace(" ", "_"))[:50]
    return "内容分析"


def sanitize_filename_part(name: str) -> str:
    """文件名安全片段（与 video_gui extract_title_from_summary 下划线习惯一致）。"""
    t = (name or "").strip()
    t = re.sub(r'[\\/:*?"<>|&]', "_", t)
    t = re.sub(r"^[\d\.\s]+", "", t)
    t = re.sub(r"_+", "_", t)
    t = t.replace(" ", "_")
    return t[:80].strip("_") or "内容分析"


def next_output_serial() -> int:
    """与 video_gui generate_md 一致：output 目录已有 md 数量 + 1。"""
    try:
        root = get_output_dir()
        count = sum(1 for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".md")
        return count + 1
    except Exception:
        return 1


def build_output_md_path(
    doc_name: str,
    content_type: str = "图文",
    *,
    use_serial: bool = True,
    serial: int = 0,
    naming_rule: str = "",
) -> Tuple[str, str]:
    """
    落盘文件名：优先 file_naming_rule（须含 {doc_title} 占位符）；
    否则默认 video_gui：{序号:03d}-{月-日}-{doc_title}_{类型}分析.md
    返回 (绝对路径, basename)。
    """
    current_date = time.strftime("%m-%d")
    safe = sanitize_filename_part(doc_name)
    ctype = (content_type or "图文").strip()
    seq = serial if serial > 0 else (next_output_serial() if use_serial else 0)
    rule = (naming_rule or "").strip()
    if rule and "{doc_title}" in rule:
        basename = apply_naming_template(
            rule,
            doc_title=safe,
            content_type=ctype,
            date=current_date,
            serial=f"{seq:03d}" if seq else "",
        ).strip()
        if basename and not basename.lower().endswith(".md"):
            basename += ".md"
    elif seq > 0:
        basename = f"{seq:03d}-{current_date}-{safe}_{ctype}分析.md"
    else:
        basename = f"{current_date}-{safe}_{ctype}分析.md"
    full = get_output_dir() / basename
    return str(full.resolve()), basename


def output_basename(path_or_name: str) -> str:
    if not path_or_name:
        return ""
    return Path(path_or_name).name


def resolve_output_abs(path_or_name: str) -> Optional[Path]:
    if not path_or_name:
        return None
    p = Path(path_or_name)
    if p.is_absolute():
        return p.resolve() if p.exists() else p.resolve()
    root = get_output_dir().resolve()
    candidate = (root / p.name).resolve()
    if candidate.exists():
        return candidate
    candidate2 = (root / path_or_name).resolve()
    return candidate2


def is_under_output_dir(abs_path: Path) -> bool:
    try:
        root = get_output_dir().resolve()
        return abs_path.resolve().is_relative_to(root)
    except ValueError:
        return False
