"""抖音图文处理服务 v3.1 (HTML gen + ops agent)

两种抖音内容类型的识别和路由：
  /article/xxx → ARTICLE 纯文本长文 → Playwright渲染提取正文
  /note/xxx 或分享链接 → NOTE 图文混排 → 取正文 + 内容图片OCR

完整链路：
  URL → 类型检测 → 分支提取 → 原文装配 → run_document_consolidation
      → extract_title → generate_md → feishu_upload → HTML生成 → ops_agent

配置：深拷贝自 src/agent/config.json → web_rebuild_v2/backend/config.json
日志：与原项目 append_log 格式一致 [HH:MM:SS] [线程] message
"""
from __future__ import annotations
import asyncio
import json
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import unquote

import requests  # 用于直接抓取页面

_HERE = Path(__file__).resolve()
_AGENT_DIR = None
_BACKEND_DIR = None
for _p in _HERE.parents:
    if (_AGENT_DIR is None) and (_p / "src" / "agent").is_dir():
        _AGENT_DIR = (_p / "src" / "agent").resolve()
    if (_BACKEND_DIR is None) and (_p / "frontend").is_dir() and (_p / "backend").is_dir():
        _BACKEND_DIR = (_p / "backend").resolve()
if _BACKEND_DIR is None:
    _BACKEND_DIR = _HERE.parents[3] / "backend"
if _AGENT_DIR and str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from .link_hash import normalize_link_for_hash, url_hash as link_url_hash
from .task_manager import get_task, add_log as _add_log, update_task, get_output_dir
from .history_manager import add_or_update_task_in_history
from .pipeline_stages import mark_failure_from_task
from .document_consolidation import (
    run_document_consolidation, extract_title_from_summary, clean_title, get_article_text,
)

from .pipeline_executor import get_blocking_executor as _io_executor, get_llm_executor as _llm_executor

# ─── 日志 ───
def _log(task_id: str, msg: str, level: str = "INFO"):
    tid = threading.current_thread().name
    _add_log(task_id, f"[{tid}] {msg}", level)


# ─── 加载配置 ───
def _load_config() -> Dict:
    cfg_path = _BACKEND_DIR / "config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 兜底：原项目 config
    if _AGENT_DIR:
        orig = _AGENT_DIR / "config.json"
        if orig.exists():
            try:
                return json.loads(orig.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


# ─── 类型检测 ───
def _detect_douyin_type(link: str, html: str = "") -> str:
    """检测抖音链接类型：article / note / video（含短链重定向）。"""
    link_low = (link or "").lower()
    if "/article/" in link_low:
        return "article"
    if "/note/" in link_low or "modal_id=" in link_low:
        return "note"
    # 尝试从 SSR 数据判断
    if html:
        if "awemeType" in html and '"awemeType":2' in html:
            return "note"  # 图文笔记
        if "images" in html and '"images":[' in html:
            return "note"
    # 短链 v.douyin.com 等：委托 LinkAnalyzer 解析重定向后 URL
    try:
        from link_analyzer import LinkAnalyzer

        dtype = LinkAnalyzer()._detect_douyin_type(link)
        if dtype == "douyin_image":
            try:
                import requests

                resp = requests.get(
                    link,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://www.douyin.com/",
                    },
                    timeout=15,
                    allow_redirects=True,
                )
                final = (resp.url or link).lower()
                if "/article/" in final:
                    return "article"
            except Exception:
                pass
            return "note"
    except Exception:
        pass
    return "video"


# ─── ARTICLE: 纯文本长文提取 ───
def _extract_article_text(link: str, task_id: str) -> Optional[Dict]:
    """从 /article/ 页面提取纯文本正文 —— requests → Playwright 两级策略"""
    _log(task_id, "[抖音-Article] 开始抓取长文页面...")
    result = _extract_with_requests(link, task_id)
    if result:
        text = result.get("text_content", "").strip()
        # 质量检测：页脚文本含有许可证号等特征，真实文章不会只有这些
        footer_markers = ['增值电信业务经营许可证', '广播电视节目制作经营许可证',
                         'ICP备', '网络文化经营许可证', 'B2-20170846']
        footer_count = sum(1 for m in footer_markers if m in text)
        cn_count = sum(1 for c in text if '一' <= c <= '鿿')
        if cn_count > 500 and footer_count <= 2:
            _log(task_id, f"[抖音-Article] requests 提取质量OK: {cn_count} 中文, {footer_count} 页脚标记")
            return result
        _log(task_id, f"[抖音-Article] requests 内容疑似页脚({footer_count} 标记, {cn_count} 中文)，切换 Playwright...")
    else:
        _log(task_id, "[抖音-Article] requests 提取为空，切换 Playwright 浏览器渲染...")
    return _extract_with_playwright(link, task_id)


def _extract_with_requests(link: str, task_id: str) -> Optional[Dict]:
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/144.0.0.0 Safari/537.36',
            'Referer': 'https://www.douyin.com/',
        }
        r = requests.get(link, headers=headers, timeout=20)
        html = r.text
        _log(task_id, f"[抖音-Article] requests 页面: {len(html)} bytes")

        title = ""
        text_parts = []

        # 从 __pace_f RSC 数据提取中文文本
        pace_pattern = re.compile(r'self\.__pace_f\.push\(\[(\d+),"((?:[^"\\]|\\.)*)"\]\)')
        for _, payload in pace_pattern.findall(html):
            decoded = unquote(payload)
            strings = re.findall(r'"([^"]{20,600})"', decoded)
            for s in strings:
                cn_count = sum(1 for c in s if '一' <= c <= '鿿')
                if cn_count > 5 and 'function' not in s and 'http' not in s[:10]:
                    text_parts.append(s)

        title_m = re.search(r'<title>(.*?)</title>', html)
        if title_m:
            raw_title = re.sub(r'\s*[-–—]\s*抖音.*$', '', title_m.group(1).strip())
            if raw_title and raw_title != "抖音": title = raw_title

        # SSR JSON 中的 post/article 数据
        ssr_m = re.search(r'window\._SSR_HYDRATED_DATA\s*=\s*({.*?})</script>', html, re.DOTALL)
        if ssr_m:
            try:
                ssr_data = json.loads(ssr_m.group(1))
                def _find_text(obj, depth=0):
                    if depth > 5: return []
                    texts = []
                    if isinstance(obj, str) and len(obj) > 40:
                        if sum(1 for c in obj if '一' <= c <= '鿿') > 5: texts.append(obj)
                    elif isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ('desc', 'text', 'content', 'title', 'summary', 'article',
                                     'richtext', 'paragraph', 'abstract', 'description'):
                                if isinstance(v, str) and len(v) > 20: texts.append(v)
                                elif isinstance(v, (dict, list)): texts.extend(_find_text(v, depth+1))
                            elif isinstance(v, (dict, list)): texts.extend(_find_text(v, depth+1))
                    elif isinstance(obj, list):
                        for item in obj[:20]: texts.extend(_find_text(item, depth+1))
                    return texts
                text_parts.extend(_find_text(ssr_data))
            except: pass

        # 去重过滤
        seen = set()
        unique_texts = []
        skip_phrases = ['抖音聊天桌面端', '热爱抖音', '随时随地收发', '浏览朋友分享',
                       '知晓朋友的在线状态', '管理消息记录', '添加新的朋友',
                       '社区自律公约', '综合性的动态评分', '客服查看与回复',
                       '算法推荐专项举报', '违法和不良信息举报', 'ICP备', '发布不良信息']
        for t in text_parts:
            t = t.strip()
            if t and t not in seen and len(t) > 20 and not any(sp in t for sp in skip_phrases):
                seen.add(t); unique_texts.append(t)

        text_content = "\n\n".join(unique_texts)
        return {"type": "douyin_article", "url": link, "title": title or "抖音长文",
                "text_content": text_content, "image_links": [], "image_analysis": [],
                "summary": text_content[:800] if len(text_content) > 800 else text_content}
    except Exception as e:
        _log(task_id, f"[抖音-Article] requests 异常: {e}", "ERROR")
        return None


def _extract_with_playwright(link: str, task_id: str) -> Optional[Dict]:
    """使用 Playwright 无头浏览器渲染 JS 页面后提取正文"""
    try:
        _log(task_id, "[抖音-Article] 启动 Playwright 无头浏览器...")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(link, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            title = page.title()
            # 用 innerText 获取可见文本（自动排除 script/style/隐藏元素）
            body_text = page.evaluate('() => document.body.innerText')
            browser.close()

        _log(task_id, f"[抖音-Article] Playwright 渲染完成: title={title[:50]}, body={len(body_text)} chars")

        # 清理标题
        title = re.sub(r'\s*[-–—]\s*抖音.*$', '', title).strip() if title else ""
        if title == "抖音": title = ""

        # 过滤页脚/UI文案
        skip_phrases = ['抖音聊天桌面端', '热爱抖音', '社区自律公约', '违法和不良信息举报',
                       'ICP备', '发布不良信息', '客服查看与回复', '机器刷量', '诱导互动']
        lines = body_text.split('\n')
        clean_lines = []
        for line in lines:
            line = line.strip()
            if line and len(line) > 15 and not any(sp in line for sp in skip_phrases):
                clean_lines.append(line)

        text_content = '\n\n'.join(clean_lines)
        cn_count = sum(1 for c in text_content if '一' <= c <= '鿿')
        _log(task_id, f"[抖音-Article] Playwright 提取: {len(text_content)} chars, {cn_count} 中文")

        return {"type": "douyin_article", "url": link, "title": title or "抖音长文",
                "text_content": text_content, "image_links": [], "image_analysis": [],
                "summary": text_content[:800] if len(text_content) > 800 else text_content}

    except ImportError:
        _log(task_id, "[抖音-Article] Playwright 未安装", "ERROR")
        return None
    except Exception as e:
        _log(task_id, f"[抖音-Article] Playwright 异常: {e}", "ERROR")
        return None


# ─── NOTE: 图文混排提取（复用 link_analyzer 但过滤 UI 图标） ───
def _extract_note_content(link: str, task_id: str) -> Optional[Dict]:
    """从 /note/ 或分享链接提取图文混排内容"""
    try:
        from link_analyzer import LinkAnalyzer
        analyzer = LinkAnalyzer()
        _log(task_id, "[抖音-Note] 调用 link_analyzer 提取图文...")
        result = analyzer.analyze_link(link)

        if not result or result.get("error"):
            _log(task_id, f"[抖音-Note] analyze_link 失败: {(result or {}).get('error', '未知')}", "WARNING")
            result = analyzer._analyze_douyin_image(link)

        if not result or result.get("error"):
            return None

        # 过滤 UI 图标：只保留真实内容图片
        image_links = result.get("image_links", []) or []
        content_images = []
        ui_patterns = ['chat_days', 'gold_coin', 'spring_chat', 'ai_mix', 'edit_group',
                       'plant_tree', 'after_reply', 'aweme-client-static-resource',
                       'emoji', 'icon']
        for img_url in image_links:
            if not any(p in img_url for p in ui_patterns):
                content_images.append(img_url)

        _log(task_id, f"[抖音-Note] 图片过滤: {len(image_links)} → {len(content_images)} 张（排除UI图标）")
        result["image_links"] = content_images

        # 如果无内容图片，清空 image_analysis
        if not content_images:
            result["image_analysis"] = []

        _log(task_id,
            f"[抖音-Note] text_len={len((result.get('text_content') or '').strip())} | "
            f"images={len(content_images)}")
        return result

    except Exception as e:
        _log(task_id, f"[抖音-Note] 提取异常: {e}", "ERROR")
        return None


# ─── 节点1: 统一提取入口 ───
def _extract_content(link: str, task_id: str) -> Optional[Dict]:
    douyin_type = _detect_douyin_type(link)
    _log(task_id, f"[抖音-路由] 检测到类型: {douyin_type}")

    if douyin_type == "article":
        return _extract_article_text(link, task_id)
    elif douyin_type == "note":
        return _extract_note_content(link, task_id)
    else:
        # video 类型回退
        _log(task_id, "[抖音-路由] 视频类型，回退到视频下载链路", "WARNING")
        return None


# ─── 节点2: OCR 补偿（仅 NOTE 类型需要） ───
def _ocr_compensation(result: Dict, task_id: str) -> Dict:
    image_links = list(result.get("image_links", []) or [])
    image_analysis = list(result.get("image_analysis", []) or [])

    # ARTICLE 类型或没有图片 → 跳过
    if result.get("type") == "douyin_article" or not image_links:
        return result
    if image_analysis:
        return result

    _log(task_id, f"[抖音-Note][OCR补偿] {len(image_links)} 张图片待OCR...")
    from link_analyzer import LinkAnalyzer
    analyzer = LinkAnalyzer()
    recovered = []

    for idx, img_url in enumerate(image_links, 1):
        try:
            _log(task_id, f"[OCR] {idx}/{len(image_links)}: {img_url[:80]}...")
            img_data = self.download_image(img_url, referer="https://www.douyin.com/")
            if not img_data: continue
            ocr_result = analyzer.ocr_image(img_data)
            if not ocr_result: continue
            img_text = (analyzer.extract_text_from_ocr(ocr_result) or "").strip()
            if img_text:
                recovered.append({"url": img_url, "text": img_text, "index": idx})
            if idx < len(image_links):
                time.sleep(1.0)
        except Exception as e:
            _log(task_id, f"[OCR] {idx} 失败: {e}", "WARNING")

    if recovered:
        result["image_analysis"] = recovered
        _log(task_id, f"[OCR补偿] 完成: {len(recovered)} 张")
    return result


# ─── 节点3: 原文装配（参照 _build_xiaohongshu_raw_text） ───
def _build_raw_text(result: Dict) -> str:
    result = result or {}
    title = (result.get("title") or "").strip()
    text_content = (result.get("text_content") or "").strip()
    image_analysis = list(result.get("image_analysis", []) or [])

    lines = []
    if title:
        lines.append(f"# {title}")
    if text_content:
        lines.append("## 正文\n" + text_content)
    else:
        fallback = (result.get("summary") or "").strip()
        if fallback:
            lines.append("## 正文\n" + fallback)

    if image_analysis:
        ocr_parts = []
        for i, img in enumerate(image_analysis, 1):
            t = (img.get("text") or "").strip()
            idx = img.get("index", i)
            u = (img.get("url") or "").strip()
            ocr_parts.append(f"[图片{idx}]\n来源：{u}\n{t}" if t else f"[图片{idx}]\n来源：{u}")
        lines.append("## 图片OCR\n" + "\n\n".join(ocr_parts))

    return "\n\n".join([x for x in lines if x]).strip() or (result.get("summary") or "").strip()


# ─── 节点4: 文档沉淀（AI润色 + AI摘要） ───
# ─── 生成 Markdown ───
def _generate_md(result_data: Dict, link: str, task_id: str, cfg: Optional[Dict] = None) -> str:
    cfg = cfg or _load_config()
    title = (result_data.get("title") or "抖音内容").strip()
    ai_summary = result_data.get("ai_summary", "")
    article = result_data.get("article", ai_summary)
    link_title = (result_data.get("link_title") or "").strip()
    comments_viewpoint = (result_data.get("comments_viewpoint") or "").strip()
    comments_file_path = (result_data.get("comments_file_path") or "").strip()

    content_type = result_data.get("content_type", "图文")
    doc_name = (title or "抖音内容").strip()
    transcribe_source = (result_data.get("transcribe_source") or "").strip() or "link_analyzer"
    naming_rule = (cfg.get("file_naming_rule") or "").strip()
    from .file_naming import build_output_md_path, render_output_template
    from .pipeline_comments import (
        append_comments_section_to_md,
        format_comments_file_link,
        render_comments_section,
    )

    doc_path, _ = build_output_md_path(
        doc_name,
        content_type,
        naming_rule=naming_rule if "{doc_title}" in naming_rule else "",
    )

    comments_section = render_comments_section(
        cfg.get("comments_section_template") or "",
        comments_analysis=comments_viewpoint,
        comments_file_path=comments_file_path,
    )
    output_tpl = (cfg.get("output_template") or "").strip()
    from .link_meta_extract import format_meta_json_block, get_meta_extract_config

    meta_cfg = get_meta_extract_config(cfg)
    meta_block = format_meta_json_block(
        result_data.get("extracted_metadata") or {},
        fields=meta_cfg.get("fields") or [],
    )
    md = render_output_template(
        output_tpl,
        platform="抖音",
        link=link,
        article=article,
        summary=ai_summary,
        content_type=content_type,
        transcribe_source=transcribe_source,
        link_title=link_title,
        doc_title=title,
        comments_section=comments_section,
        comments_analysis=comments_viewpoint,
        comments_file_link=format_comments_file_link(comments_file_path),
        meta_json=meta_block,
        task_note=str(result_data.get("task_note") or "").strip(),
        task_keywords=str(result_data.get("task_keywords") or "").strip(),
    )
    if not md.strip():
        md = f"""# 抖音{content_type}分析

## 分析信息
- 分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 原始链接: {link}
- 平台: 抖音
- 类型: {content_type}
- 转写链路: {transcribe_source}

## 原始内容
正文：
{article}

## AI分析摘要
{ai_summary}
"""
    md = append_comments_section_to_md(
        md,
        cfg,
        comments_analysis=comments_viewpoint,
        comments_file_path=comments_file_path,
    )
    md += """
---
*由多模态文档化助手自动生成*
"""
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(md)

    _log(task_id, f"[文档生成] 成功: {doc_path}")
    return doc_path


# ─── 主流水线 ───
async def process_douyin_article_pipeline(task_id: str, user_prompt: str = "", comments_data = None):
    from .feishu_pipeline import start_feishu_upload_async
    from .pipeline_finalize import complete_task_after_md, mark_pipeline_running
    task = get_task(task_id)
    if not task: return
    link = task["link"]
    loop = asyncio.get_running_loop()
    # TODO: 在文档生成时集成 user_prompt 和 comments_data

    update_task(task_id, pipeline_route="douyin_graphic")
    mark_pipeline_running(task_id)
    try:
        update_task(task_id, status="extracting", stage="提取抖音内容", progress=10)
        _log(task_id, "=" * 50)
        _log(task_id, f"开始处理抖音链接: {link}")

        result = await loop.run_in_executor(_io_executor(), lambda: _extract_content(link, task_id))
        if not result:
            update_task(task_id, status="failed", error="抖音内容提取失败")
            mark_failure_from_task(task_id, "抖音内容提取失败", route="douyin_graphic", stage_id="extract")
            return
        from .file_naming import resolve_link_title, preview_from_analyzer_result, resolve_doc_title, output_basename
        link_title = resolve_link_title(
            link,
            platform="抖音",
            analyzer_title=(result.get("title") or ""),
            log_cb=lambda msg: _log(task_id, msg),
        )
        if link_title:
            update_task(task_id, link_title=link_title, **{k: v for k, v in preview_from_analyzer_result(result, link, "抖音").items() if v})
            _log(task_id, f"首层标题（链接）: {link_title}")
        else:
            update_task(task_id, **{k: v for k, v in preview_from_analyzer_result(result, link, "抖音").items() if v})
        update_task(task_id, progress=30)

        # ── 节点2: OCR 补偿（仅 NOTE 类型） ──
        update_task(task_id, status="ocr", stage="OCR补偿", progress=40)
        result = await loop.run_in_executor(_io_executor(), lambda: _ocr_compensation(result, task_id))

        # ── 节点3: 原文装配 ──
        update_task(task_id, status="assembling", stage="原文装配", progress=55)
        source_text = await loop.run_in_executor(_io_executor(), lambda: _build_raw_text(result))
        _log(task_id, f"[原文装配] {len(source_text)} 字符")
        if not source_text:
            update_task(task_id, status="failed", error="原文装配为空")
            mark_failure_from_task(task_id, "原文装配为空", route="douyin_graphic", stage_id="assemble")
            return

        # ── 节点4: 文档沉淀（使用共享 _run_document_consolidation） ──
        update_task(task_id, status="consolidating", stage="AI润色+摘要", progress=70)
        cfg = _load_config()

        def _ops_cb(link, error_message, stage, error_type):
            try:
                from .ops import ops_monitor_task
                ops_monitor_task(
                    link=link, task_id=task_id, status="failed",
                    logs=[], error_info=f"{error_type}: {error_message}",
                )
            except Exception:
                pass

        from .pipeline_comments import resolve_comments_text

        comments_text = resolve_comments_text(comments_data=comments_data)
        task_snap_pre = get_task(task_id) or {}
        consolidation = await loop.run_in_executor(
            _llm_executor(), lambda: run_document_consolidation(
                text=source_text, llm_cfg={
                    **cfg,
                    "_task_id": task_id,
                    "_log_chain": "链接沉淀文档-抖音图文",
                    "_task_note": str(task_snap_pre.get("task_note") or ""),
                    "_task_keywords": str(task_snap_pre.get("task_keywords") or ""),
                },
                stage_label="抖音图文沉淀",
                comments_text=comments_text,
                log_cb=lambda msg, lvl="INFO": _log(task_id, msg, lvl),
                ops_cb=_ops_cb,
            ))
        extracted_metadata = consolidation.get("extracted_metadata") or {}
        if extracted_metadata:
            update_task(task_id, extracted_metadata=extracted_metadata)
        if not consolidation.get("ai_summary"):
            update_task(task_id, status="failed", error="AI摘要失败")
            mark_failure_from_task(task_id, "AI摘要失败", route="douyin_graphic", stage_id="ai_analysis")
            return

        # ── 节点5: 标题提取（原项目 extract_title_from_summary） ──
        ai_summary = consolidation["ai_summary"]
        task_snap = get_task(task_id) or {}
        link_title = (task_snap.get("link_title") or "").strip()
        title = await loop.run_in_executor(
            _io_executor(),
            lambda: resolve_doc_title(
                ai_summary,
                link,
                link_title=link_title,
                fallback=(result.get("title") or "抖音内容"),
                log_cb=lambda msg: _log(task_id, msg),
                platform="抖音",
            ),
        )
        _log(task_id, f"二层标题（AI摘要）: {title}")
        update_task(task_id, doc_title=title)

        # ── 节点6: 生成 MD（参照原 generate_md 格式） ──
        update_task(task_id, status="generating", stage="生成Markdown", progress=90)
        task_snap_md = get_task(task_id) or {}
        result_data = {
            "ai_summary": ai_summary,
            "article": consolidation.get("article", ""),
            "title": title,
            "link_title": link_title,
            "comments_viewpoint": (consolidation.get("comments_viewpoint") or "").strip(),
            "comments_file_path": str((task.get("comments") or {}).get("comments_file_path") or ""),
            "extracted_metadata": task_snap_md.get("extracted_metadata") or {},
            "task_note": task_snap_md.get("task_note") or "",
            "task_keywords": task_snap_md.get("task_keywords") or "",
        }
        doc_path = await loop.run_in_executor(
            _io_executor(), lambda: _generate_md(result_data, link, task_id, cfg=cfg))
        if not doc_path:
            update_task(task_id, status="failed", error="文档生成失败")
            mark_failure_from_task(task_id, "文档生成失败", route="douyin_graphic", stage_id="generate_md")
            return

        # ── MD 完成即任务完成；飞书/HTML 后台继续 ──
        complete_task_after_md(
            task_id,
            doc_path=doc_path,
            link=link,
            platform="抖音",
            title=title,
            url_hash=(task.get("url_hash") or ""),
        )
        start_feishu_upload_async(
            doc_path,
            task_id,
            link=link,
            user_prompt=user_prompt,
            pipeline_route="douyin_graphic",
            log_cb=lambda msg, lvl="INFO": _log(task_id, msg, lvl),
        )
        _log(task_id, f"处理完成! 文档: {doc_path}")

    except Exception as e:
        _log(task_id, f"处理异常: {e}", "ERROR")
        import traceback
        _log(task_id, traceback.format_exc(), "ERROR")
        # ── 运维Agent分析失败原因 ──
        try:
            from .ops import ops_monitor_task
            ops_monitor_task(
                link=task.get("link", ""), task_id=task_id, status="failed",
                logs=[], error_info=str(e),
            )
        except Exception:
            pass
        update_task(task_id, status="failed", error=str(e))
        mark_failure_from_task(task_id, str(e), route="douyin_graphic")
