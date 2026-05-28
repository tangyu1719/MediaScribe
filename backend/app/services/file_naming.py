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
from typing import Callable, Dict, Optional, Tuple

from .task_manager import get_output_dir
from .document_consolidation import extract_title_from_summary, clean_title


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
) -> str:
    """按 config output_template 渲染成品 Markdown。"""
    tpl = (template or "").strip()
    if not tpl:
        return ""
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
        "doc_title": doc_title,
    }
    return apply_naming_template(tpl, **ctx)


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
        if re.search(r'"awemeType"\s*:\s*68', html or ""):
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


def extract_title_from_link(link: str, log_cb: Optional[Callable[[str], None]] = None) -> str:
    """首层标题：从链接页面 title 或链接片段提取（video_gui.extract_title_from_link）。"""
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
) -> str:
    """二层标题：从摘要 Agent 输出提取（extract_title_from_summary），用于任务 doc_title 与文件名 stem。"""
    _ = naming_rule
    plat = _detect_platform_from_link(link, platform)
    name = extract_title_from_summary(ai_summary, link, log_cb=log_cb)
    if name and name not in ("内容分析", "未知标题"):
        return sanitize_filename_part(name.replace(" ", "_"))[:50]
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
