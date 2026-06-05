"""多平台评论区抓取服务。

支持：小红书 / B站 / 抖音
输出：结构化评论数据 + 线程标号（总序号 + 回复链）
"""
from __future__ import annotations

import json
import logging
import os as _os_module
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from bs4 import BeautifulSoup

_log = logging.getLogger("sba.comment_scraper")

# ─── 数据模型 ───


@dataclass
class ReplyItem:
    """嵌套回复"""
    author: str = ""
    text: str = ""
    time_str: str = ""
    location: str = ""
    likes: int = 0
    reply_to: str = ""  # 回复给某人（作者名）
    reply_to_idx: int = 0  # 回复给第几条评论（全局序号）


@dataclass
class CommentItem:
    """单条评论"""
    author: str = ""
    text: str = ""
    time_str: str = ""
    location: str = ""
    likes: int = 0
    reply_count: int = 0
    replies: List[ReplyItem] = field(default_factory=list)
    parent_idx: int = 0  # 所属话题段首评论序号（0=主评论）


@dataclass
class CommentResult:
    """评论抓取结果"""
    platform: str = ""
    note_id: str = ""
    total_count: int = 0  # 平台声称的总评论数
    fetched_count: int = 0  # 实际抓取到的数量
    comments: List[CommentItem] = field(default_factory=list)
    error: str = ""


# ═══════════════════════════════════════════════════════════════════
#  通用工具
# ═══════════════════════════════════════════════════════════════════

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _safe_int(val: Any, default: int = 0) -> int:
    try: return int(val)
    except (TypeError, ValueError): return default


def _clean_text(val: Any) -> str:
    if val is None: return ""
    return str(val).strip()


def _format_time_str(val: Any) -> str:
    if not val: return ""
    text = str(val).strip()
    if text.isdigit():
        try:
            ts = int(text)
            if ts > 1e12: ts = ts // 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError): pass
    return text


def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_DEFAULT_HEADERS)
    return s


# ═══════════════════════════════════════════════════════════════════
#  Cookie 持久化
# ═══════════════════════════════════════════════════════════════════

def _flatten_cookies(data: Dict[str, Any]) -> Dict[str, str]:
    """展平 cookie 数据：处理 {cookies: {...}, localStorage: {...}} 格式。"""
    if 'cookies' in data and isinstance(data.get('cookies'), dict):
        return {k: str(v) for k, v in data['cookies'].items()}
    # 已经是扁平的 {name: value}
    return {k: str(v) for k, v in data.items()}


def _auto_ensure_cookies(platform: str) -> Dict[str, str]:
    """自动获取平台 Cookie：文件 → Chrome提取 → 弹窗登录。"""
    try:
        from .cookie_manager import ensure_cookies
        return ensure_cookies(platform, open_login_if_missing=True)
    except ImportError:
        return _load_persisted_cookies()


def _load_persisted_cookies() -> Dict[str, str]:
    raw = (_os_module.environ.get("SBA_XHS_COOKIE") or "").strip()
    if raw:
        try:
            cookies = json.loads(raw)
            if isinstance(cookies, dict):
                _log.info("从 SBA_XHS_COOKIE 加载 %s 个 cookie", len(cookies))
                return cookies
        except json.JSONDecodeError:
            cookies = {}
            for part in raw.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k.strip()] = v.strip()
            if cookies: return cookies

    cookie_file = Path(__file__).resolve().parents[2] / "output" / ".xhs_cookies.json"
    if cookie_file.exists():
        try:
            cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
            if isinstance(cookies, dict): return cookies
        except Exception: pass
    return {}


# ═══════════════════════════════════════════════════════════════════
#  格式化输出（带线程标号）
# ═══════════════════════════════════════════════════════════════════

def _assign_thread_indices(comments: List[CommentItem]) -> List[CommentItem]:
    """给评论及其子回复分配全局序号和线程标记。"""
    idx = 0
    for c in comments:
        idx += 1
        c.parent_idx = idx  # 全局序号
        for r in c.replies:
            idx += 1
            r.reply_to_idx = c.parent_idx
    return comments


def format_comments_as_text(result: CommentResult) -> str:
    """评论结果格式化文本（含全局序号 + 回复链标记）。"""
    if result.error and not result.comments:
        return f"评论抓取失败: {result.error}"

    lines = [
        f"平台: {result.platform}",
        f"标识: {result.note_id}",
        f"总评论数: {result.total_count}",
        f"已抓取: {result.fetched_count}",
    ]
    if result.error:
        lines.append(f"提示: {result.error}")
    lines.append("")

    _assign_thread_indices(result.comments)

    for c in result.comments:
        prefix = f"[{c.parent_idx}]"
        meta = [c.author]
        if c.time_str: meta.append(c.time_str)
        if c.location: meta.append(c.location)
        meta.append(f"赞:{c.likes}")
        if c.reply_count: meta.append(f"回复:{c.reply_count}")

        lines.append(f"{prefix} {c.text}")
        lines.append(f"    {' | '.join(meta)}")

        for r in c.replies:
            rprefix = f"[{r.reply_to_idx}]"
            rmeta = [r.author]
            if r.reply_to: rmeta.append(f"回复 {r.reply_to}")
            if r.time_str: rmeta.append(r.time_str)
            if r.location: rmeta.append(r.location)
            rmeta.append(f"赞:{r.likes}")

            lines.append(f"    └─ 回复 {rprefix} {r.text}")
            lines.append(f"        {' | '.join(rmeta)}")

        lines.append("")

    lines.append("- THE END -")
    return "\n".join(lines)


def merge_text_with_comments(
    body: str,
    *,
    comments_data: Optional["CommentResult"] = None,
    comments_text: str = "",
) -> str:
    """将评论区文本并入摘要/原文整理输入（正文+评论一起给 Agent）。"""
    base = (body or "").strip()
    ct = (comments_text or "").strip()
    if not ct and comments_data is not None:
        try:
            if getattr(comments_data, "comments", None):
                ct = format_comments_as_text(comments_data).strip()
        except Exception:
            ct = ""
    if not ct:
        return base
    block = f"## 评论区\n\n{ct}"
    return f"{base}\n\n{block}" if base else block


def format_comments_as_json(result: CommentResult) -> str:
    """评论结果格式化为 JSON。"""
    _assign_thread_indices(result.comments)
    return json.dumps({
        "platform": result.platform,
        "note_id": result.note_id,
        "total_count": result.total_count,
        "fetched_count": result.fetched_count,
        "comments": [
            {
                "idx": c.parent_idx,
                "author": c.author,
                "text": c.text,
                "time": c.time_str,
                "location": c.location,
                "likes": c.likes,
                "reply_count": c.reply_count,
                "replies": [
                    {
                        "reply_to_idx": r.reply_to_idx,
                        "reply_to": r.reply_to,
                        "author": r.author,
                        "text": r.text,
                        "time": r.time_str,
                        "location": r.location,
                        "likes": r.likes,
                    }
                    for r in c.replies
                ],
            }
            for c in result.comments
        ],
        "error": result.error,
    }, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
#  小红书评论抓取
# ═══════════════════════════════════════════════════════════════════

def _extract_note_id_from_url(url: str) -> str:
    m = re.search(r"/explore/([a-f0-9]{24,32})", url)
    if m: return m.group(1)
    m = re.search(r"/discovery/item/([a-f0-9]{24,32})", url)
    if m: return m.group(1)
    m = re.search(r"/note/([a-f0-9]{24,32})", url)
    if m: return m.group(1)
    return ""


def _extract_xsec_token(url: str) -> str:
    m = re.search(r"xsec_token=([A-Za-z0-9_=-]+)", url)
    return m.group(1) if m else ""


def _parse_xiaohongshu_init_state(html: str) -> Optional[Dict]:
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>", html, re.DOTALL)
    if not match:
        match = re.search(r"__INITIAL_STATE__\s*=\s*(\{.+)", html, re.DOTALL)
        if match:
            raw = match.group(1)
            depth = 0
            end = 0
            for i, ch in enumerate(raw):
                if ch == '{': depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                try: return json.loads(raw[:end])
                except json.JSONDecodeError: pass
        return None
    try: return json.loads(match.group(1))
    except json.JSONDecodeError: return None


def _extract_comments_from_init_state(data: Dict, note_id: str) -> tuple:
    comments: List[CommentItem] = []
    total_count = 0
    try:
        note_map = data.get("note", {}).get("noteDetailMap", {})
        note_data = note_map.get(note_id, {})
        if not note_data:
            for key, val in note_map.items():
                if isinstance(val, dict) and val.get("note"):
                    note_data = val
                    break
        note = note_data.get("note", note_data)
        if not isinstance(note, dict): return comments, total_count
        total_count = _safe_int(note.get("commentCount", 0))
        comment_list = (note.get("commentList") or note.get("comments") or [])
        if isinstance(comment_list, dict):
            comment_list = comment_list.get("list", comment_list.get("comments", []))
        if not isinstance(comment_list, list): return comments, total_count

        for c in comment_list:
            if not isinstance(c, dict): continue
            comments.append(CommentItem(
                author=_clean_text(c.get("userName") or c.get("nickname") or c.get("name")),
                text=_clean_text(c.get("content") or c.get("text")),
                time_str=_format_time_str(c.get("createTime")),
                location=_clean_text(c.get("ipLocation") or c.get("location")),
                likes=_safe_int(c.get("likeCount") or c.get("likes")),
                reply_count=_safe_int(c.get("subCommentCount") or c.get("replyCount") or 0),
                replies=_extract_sub_comments(c),
            ))
    except Exception as e:
        _log.warning("从 __INITIAL_STATE__ 提取评论时出错: %s", e)
    return comments, total_count


def _extract_sub_comments(comment: Dict) -> List[ReplyItem]:
    replies: List[ReplyItem] = []
    sub_list = (comment.get("subComments") or comment.get("sub_comment_list") or [])
    if isinstance(sub_list, dict):
        sub_list = sub_list.get("list", sub_list.get("comments", []))
    if not isinstance(sub_list, list): return replies
    for s in sub_list:
        if not isinstance(s, dict): continue
        replies.append(ReplyItem(
            author=_clean_text(s.get("userName") or s.get("nickname") or s.get("name")),
            text=_clean_text(s.get("content") or s.get("text")),
            time_str=_format_time_str(s.get("createTime")),
            location=_clean_text(s.get("ipLocation") or s.get("location")),
            likes=_safe_int(s.get("likeCount") or s.get("likes")),
            reply_to=_clean_text(s.get("targetUserName") or s.get("replyTo") or ""),
        ))
    return replies


def _extract_comments_from_dom(page) -> List[CommentItem]:
    comments: List[CommentItem] = []
    selectors = [".comment-item", "[class*=commentItem]", ".note-comment-item", ".comments-container > div"]
    for sel in selectors:
        elements = page.query_selector_all(sel)
        if elements:
            _log.info("DOM 选择器 '%s' 匹配到 %s 个元素", sel, len(elements))
            for el in elements[:200]:
                try:
                    text = el.inner_text().strip()[:2000]
                    if len(text) < 2: continue
                    author_el = el.query_selector("[class*=author], [class*=name], [class*=user], .username, .nickname")
                    time_el = el.query_selector("[class*=time], [class*=date]")
                    loc_el = el.query_selector("[class*=location], [class*=ip]")
                    likes_el = el.query_selector("[class*=like], [class*=vote]")
                    comments.append(CommentItem(
                        author=author_el.inner_text().strip() if author_el else "",
                        text=text,
                        time_str=time_el.inner_text().strip() if time_el else "",
                        location=loc_el.inner_text().strip() if loc_el else "",
                        likes=_safe_int(likes_el.inner_text().strip()) if likes_el else 0,
                    ))
                except Exception: continue
            if comments: break
    return comments


def _parse_xhs_api_comment_item(raw: Dict) -> Optional[CommentItem]:
    """解析小红书 comment/page 或 sub/page 单条评论。"""
    if not isinstance(raw, dict):
        return None
    user = raw.get("user_info") or raw.get("user") or {}
    text = _clean_text(raw.get("content") or raw.get("text"))
    if not text:
        return None
    sub_raw = raw.get("sub_comments") or raw.get("subComments") or []
    if isinstance(sub_raw, dict):
        sub_raw = sub_raw.get("list") or sub_raw.get("comments") or []
    replies: List[ReplyItem] = []
    for s in (sub_raw if isinstance(sub_raw, list) else []):
        if not isinstance(s, dict):
            continue
        su = s.get("user_info") or s.get("user") or {}
        stext = _clean_text(s.get("content") or s.get("text"))
        if not stext:
            continue
        replies.append(ReplyItem(
            author=_clean_text(su.get("nickname") or su.get("userName") or su.get("name")),
            text=stext,
            time_str=_format_time_str(s.get("create_time") or s.get("createTime")),
            location=_clean_text(s.get("ip_location") or s.get("ipLocation")),
            likes=_safe_int(s.get("like_count") or s.get("likeCount")),
            reply_to=_clean_text(
                (s.get("target_comment") or {}).get("user_info", {}).get("nickname")
                or s.get("targetUserName") or s.get("reply_to") or ""
            ),
        ))
    return CommentItem(
        author=_clean_text(user.get("nickname") or user.get("userName") or user.get("name")),
        text=text,
        time_str=_format_time_str(raw.get("create_time") or raw.get("createTime")),
        location=_clean_text(raw.get("ip_location") or raw.get("ipLocation")),
        likes=_safe_int(raw.get("like_count") or raw.get("likeCount")),
        reply_count=_safe_int(raw.get("sub_comment_count") or raw.get("subCommentCount") or len(replies)),
        replies=replies,
    )


def _xhs_comment_quality_score(item: CommentItem) -> int:
    """评论完整度评分，合并时保留信息更全的一条。"""
    score = 0
    if item.time_str:
        score += 3
    score += len(item.replies or []) * 12
    if item.reply_count:
        score += min(item.reply_count, 20) * 4
    if item.likes:
        score += 1
    if len(item.text or "") > 20:
        score += 1
    return score


def _merge_xhs_comment_lists(*lists: List[CommentItem]) -> List[CommentItem]:
    """按作者+正文去重合并多源评论，保留信息更完整的一条。"""
    merged: Dict[tuple, CommentItem] = {}
    order: List[tuple] = []
    for items in lists:
        for c in items or []:
            key = (c.author, (c.text or "")[:120])
            if key not in merged:
                merged[key] = c
                order.append(key)
                continue
            if _xhs_comment_quality_score(c) > _xhs_comment_quality_score(merged[key]):
                merged[key] = c
    return [merged[k] for k in order]


def _click_xhs_reply_expand(page) -> int:
    """仅点击「展开 N 条回复」类按钮，避免误点页面其它「展开」。"""
    clicked = 0
    patterns = (
        re.compile(r"展开\s*\d+\s*条回复"),
        re.compile(r"查看\s*\d+\s*条回复"),
        re.compile(r"查看更多回复"),
    )
    try:
        scope = page.locator('[class*="comment"], [class*="Comment"], .comments-container').first
        if scope.count() == 0:
            scope = page.locator("body")
    except Exception:
        scope = page.locator("body")
    for pat in patterns:
        try:
            loc = scope.locator("span, div, button, a, p").filter(has_text=pat)
            count = min(loc.count(), 30)
            for i in range(count):
                try:
                    loc.nth(i).click(timeout=800)
                    clicked += 1
                    time.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass
    return clicked


def _click_expand_buttons(page, patterns: tuple = ("查看更多", "更多评论", "展开更多")) -> int:
    """点击评论区「查看更多/更多评论」按钮，返回本轮点击次数。"""
    clicked = 0
    try:
        scope = page.locator(
            '[class*="comment"], [class*="Comment"], .comments-container, .note-scroller'
        ).first
        if scope.count() == 0:
            scope = page.locator("body")
    except Exception:
        scope = page.locator("body")

    for pattern in patterns:
        try:
            loc = scope.locator("span, div, button, a, p").filter(has_text=pattern)
            count = min(loc.count(), 25)
            for i in range(count):
                try:
                    loc.nth(i).click(timeout=800)
                    clicked += 1
                except Exception:
                    try:
                        loc.nth(i).dispatch_event("click")
                        clicked += 1
                    except Exception:
                        pass
        except Exception:
            pass
    return clicked


def _scroll_comment_containers(page) -> None:
    """滚动页面及评论区独立滚动容器，触发懒加载。"""
    page.evaluate("""
        (function(){
            window.scrollTo(0, document.body.scrollHeight || document.documentElement.scrollHeight);
            var nodes = document.querySelectorAll(
                '[class*="comment"], [class*="Comment"], [class*="scroll"], [class*="Scroller"], .note-scroller'
            );
            nodes.forEach(function(el){
                try {
                    if (el.scrollHeight > el.clientHeight + 20) {
                        el.scrollTop = el.scrollHeight;
                    }
                } catch(e) {}
            });
        })()
    """)


def _extract_xhs_from_page_js(page) -> List[CommentItem]:
    """从渲染后的 DOM 提取小红书评论（含嵌套回复）。"""
    try:
        raw = page.evaluate("""
            (function(){
                var selectors = [
                    '.comment-item', '[class*="commentItem"]', '[class*="CommentItem"]',
                    '.parent-comment', '[class*="parent-comment"]'
                ];
                var nodes = [];
                selectors.forEach(function(sel){
                    document.querySelectorAll(sel).forEach(function(el){ nodes.push(el); });
                });
                if (!nodes.length) {
                    document.querySelectorAll('[class*="comment"]').forEach(function(el){
                        if ((el.innerText || '').trim().length > 5) nodes.push(el);
                    });
                }
                var out = [];
                nodes.forEach(function(el){
                    var text = (el.innerText || el.textContent || '').trim();
                    if (text.length < 3) return;
                    var lines = text.split('\\n').map(function(x){ return x.trim(); }).filter(Boolean);
                    var author = (lines[0] || '').replace(/(\\d+)?\\s*(小时前|分钟前|天前|刚刚).*$/, '').trim();
                    if (author.includes('·')) author = author.split('·')[0].trim();
                    var contentLines = [];
                    var timeStr = '', location = '', likes = 0;
                    for (var i = 1; i < lines.length; i++) {
                        var line = lines[i];
                        if (/展开\\s*\\d+\\s*条回复/.test(line) || line === '收起') continue;
                        if (line.includes('小时前') || line.includes('分钟前') || line.includes('天前') || line.includes('刚刚')) {
                            timeStr = line;
                            if (line.includes('·')) {
                                line.split('·').forEach(function(p){
                                    p = p.trim();
                                    if (/^[\\u4e00-\\u9fa5]{2,8}$/.test(p)) location = p;
                                });
                            }
                            if (i + 1 < lines.length && /^\\d+$/.test(lines[i + 1])) likes = parseInt(lines[i + 1], 10) || 0;
                            break;
                        }
                        contentLines.push(line);
                    }
                    var content = contentLines.join(' ').trim();
                    if (!content && lines.length > 1) content = lines.slice(1, 3).join(' ').trim();
                    var replies = [];
                    el.querySelectorAll('[class*="reply"], [class*="sub-comment"], [class*="SubComment"]').forEach(function(sub){
                        var st = (sub.innerText || sub.textContent || '').trim();
                        if (st.length > 3 && st.length < 2000 && !/展开\\s*\\d+\\s*条回复/.test(st)) {
                            replies.push({text: st});
                        }
                    });
                    out.push({
                        author: author.slice(0, 50),
                        text: content.slice(0, 2000),
                        time: timeStr,
                        location: location,
                        likes: likes,
                        reply_count: replies.length
                    });
                });
                return JSON.stringify(out);
            })()
        """)
        parsed = json.loads(raw)
        return [
            CommentItem(
                author=c.get("author", ""),
                text=c.get("text", ""),
                time_str=c.get("time", ""),
                location=c.get("location", ""),
                likes=c.get("likes", 0),
                reply_count=c.get("reply_count", 0),
            )
            for c in parsed
            if (c.get("text") or "").strip()
        ]
    except Exception as e:
        _log.warning("JS 提取小红书评论失败: %s", e)
        return []


def _extract_xhs_via_playwright(url: str, note_id: str, max_count: Optional[int], cookies: Dict) -> CommentResult:
    result = CommentResult(platform="xiaohongshu", note_id=note_id)
    try: from playwright.sync_api import sync_playwright
    except ImportError:
        result.error = "Playwright 未安装"; return result

    api_by_id: Dict[str, CommentItem] = {}

    def _on_response(response) -> None:
        req_url = response.url or ""
        if "comment/page" not in req_url and "comment/sub/page" not in req_url:
            return
        try:
            body = response.json()
        except Exception:
            return
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return
        for raw in data.get("comments") or []:
            item = _parse_xhs_api_comment_item(raw)
            if not item:
                continue
            cid = str((raw or {}).get("id") or f"{item.author}:{item.text[:40]}")
            prev = api_by_id.get(cid)
            if prev and len(prev.replies) >= len(item.replies):
                continue
            api_by_id[cid] = item

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent=_DEFAULT_HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        if cookies:
            context.add_cookies([
                {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
                for k, v in cookies.items()
            ])
        page = context.new_page()
        page.on("response", _on_response)
        try:
            _log.info("[评论抓取-小红书|comment_scraper._extract_xhs_via_playwright|%s|Agent执行|加载] Playwright 打开页面; url=%s", note_id, url[:120])
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)

            final_url = page.url
            if "/404" in final_url:
                result.error = "页面需要有效 Cookie + xsec_token"; return result

            # 滚动 + 展开回复，直到评论数量稳定
            prev_count = 0
            same_rounds = 0
            for round_num in range(18):
                _scroll_comment_containers(page)
                time.sleep(1.2)
                clicked = _click_xhs_reply_expand(page) + _click_expand_buttons(page)
                if clicked:
                    time.sleep(1.2)

                dom_count = page.evaluate("""
                    document.querySelectorAll(
                        '.comment-item, [class*="commentItem"], [class*="CommentItem"], [class*="parent-comment"]'
                    ).length
                """)
                api_count = len(api_by_id)
                count = max(dom_count or 0, api_count)
                _log.info("[评论抓取-小红书|comment_scraper._extract_xhs_via_playwright|%s|Agent执行|滚动] 第%s轮; dom=%s; api=%s; clicked=%s",
                          note_id, round_num + 1, dom_count, api_count, clicked)

                if count == prev_count:
                    same_rounds += 1
                    if same_rounds >= 3:
                        break
                else:
                    same_rounds = 0
                prev_count = count

            html = page.content()
            init_comments, total = _extract_comments_from_init_state(
                _parse_xiaohongshu_init_state(html) or {}, note_id
            )
            js_comments = _extract_xhs_from_page_js(page)
            api_comments = list(api_by_id.values())
            dom_comments = _extract_comments_from_dom(page)
            if api_comments:
                init_comments = _merge_xhs_comment_lists(api_comments, init_comments)
            else:
                init_comments = _merge_xhs_comment_lists(init_comments, js_comments, dom_comments)
            if not total:
                total = len(init_comments)

            if init_comments:
                if max_count and max_count > 0:
                    init_comments = init_comments[:max_count]
                result.comments = init_comments
                result.total_count = total or len(init_comments)
                result.fetched_count = len(init_comments)
                _log.info("[评论抓取-小红书|comment_scraper._extract_xhs_via_playwright|%s|Agent执行|完成] 抓取完成; fetched=%s; total=%s",
                          note_id, result.fetched_count, result.total_count)
            else:
                result.error = "未找到评论数据（请检查 Cookie 与 xsec_token，或评论区需登录）"
        except Exception as e:
            result.error = f"Playwright 异常: {e}"
        finally:
            browser.close()
    return result


def extract_xiaohongshu_comments(
    url: str,
    max_count: Optional[int] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> CommentResult:
    note_id = _extract_note_id_from_url(url)
    if not note_id:
        return CommentResult(platform="xiaohongshu", error=f"无法从 URL 提取 note_id: {url}")

    if not cookies:
        cookies = _auto_ensure_cookies("xiaohongshu")

    pw = _extract_xhs_via_playwright(url, note_id, max_count, cookies)
    if pw.comments: return pw

    # HTTP 降级
    session = _get_session()
    for k, v in cookies.items():
        session.cookies.set(k, v, domain=".xiaohongshu.com")
    try:
        resp = session.get(url, timeout=20)
        data = _parse_xiaohongshu_init_state(resp.text)
        if data:
            comments, total = _extract_comments_from_init_state(data, note_id)
            if comments:
                if max_count and max_count > 0: comments = comments[:max_count]
                return CommentResult(platform="xiaohongshu", note_id=note_id,
                    total_count=total, fetched_count=len(comments), comments=comments)
    except Exception as e:
        pw.error = pw.error or str(e)

    pw.error = pw.error or "无法获取评论"
    return pw


# ═══════════════════════════════════════════════════════════════════
#  B站评论抓取 (api.bilibili.com)
# ═══════════════════════════════════════════════════════════════════

def _resolve_bilibili_url(url: str) -> str:
    """展开 B 站短链 b23.tv。"""
    if "b23.tv" not in url.lower():
        return url
    try:
        session = _get_session()
        resp = session.get(url, timeout=15, allow_redirects=True)
        if resp.url and "bilibili.com" in resp.url:
            _log.info("[评论抓取-B站|comment_scraper._resolve_bilibili_url|b23.tv|硬编执行|展开] 短链已解析; target=%s", resp.url[:120])
            return resp.url
    except Exception as e:
        _log.warning("B站短链展开失败: %s", e)
    return url


def _extract_bilibili_oid(url: str) -> str:
    """从 B站链接提取视频 ID。"""
    url = _resolve_bilibili_url(url)
    # BV号: /video/BV1t95k6TEGw/
    m = re.search(r"/video/(BV[a-zA-Z0-9]+)", url)
    if m: return m.group(1)
    # av号: /video/av123456/
    m = re.search(r"/video/av(\d+)", url)
    if m: return f"av{m.group(1)}"
    return ""


def _bilibili_bv_to_av(bvid: str) -> str:
    """将 B站 BV 号转为 av 号（cid）。"""
    try:
        session = _get_session()
        resp = session.get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return str(data["data"]["aid"])
    except Exception as e:
        _log.warning("BV 转 av 失败: %s", e)
    return bvid


def _extract_bilibili_comments_via_api(oid: str, max_count: Optional[int]) -> CommentResult:
    """通过 B站公开 API 抓取评论。"""
    result = CommentResult(platform="bilibili", note_id=oid)

    session = _get_session()
    session.headers.update({
        "Referer": f"https://www.bilibili.com/video/{oid}/",
        "Origin": "https://www.bilibili.com",
    })

    # BV 号需要转换为 av 号
    if oid.startswith("BV"):
        av_id = _bilibili_bv_to_av(oid)
        _log.info("B站 BV→av: %s → %s", oid, av_id)
    else:
        av_id = oid.replace("av", "")

    all_comments: List[CommentItem] = []
    page = 1
    max_pages = 20

    while page <= max_pages:
        api_url = "https://api.bilibili.com/x/v2/reply"
        params = {
            "type": "1",
            "oid": av_id,
            "pn": page,
            "sort": "2",  # 热度排序
            "ps": 20,
        }

        try:
            resp = session.get(api_url, params=params, timeout=15)
            if resp.status_code != 200:
                if page == 1: result.error = f"API HTTP {resp.status_code}"
                break
            data = resp.json()
            if data.get("code") != 0:
                if page == 1: result.error = f"API 错误: {data.get('message','')}"
                break

            page_data = data.get("data", {})
            replies = page_data.get("replies", [])

            if page == 1:
                # B站 API 的 page count 结构
                page_info = page_data.get("page", {})
                result.total_count = _safe_int(page_info.get("acount", 0))

            if not replies:
                break

            for r in replies:
                if not isinstance(r, dict): continue
                member = r.get("member", {})
                content = r.get("content", {})
                rcount = _safe_int(r.get("rcount", 0))
                rpid = r.get("rpid", 0)
                # 先取内嵌的子回复，不够则递归拉取
                sub_replies = _extract_bilibili_sub_replies(r.get("replies", []))
                if len(sub_replies) < rcount and rpid:
                    more = _fetch_bilibili_all_sub_replies(av_id, rpid, rcount)
                    if more:
                        sub_replies = more

                all_comments.append(CommentItem(
                    author=_clean_text(member.get("uname", "")),
                    text=_clean_text(content.get("message", "")),
                    time_str=_format_time_str(r.get("ctime", "")),
                    location=_clean_text(r.get("location", "")),
                    likes=_safe_int(r.get("like", 0)),
                    reply_count=rcount,
                    replies=sub_replies,
                ))

            _log.info("B站 API 第 %s 页: %s 条评论", page, len(replies))

            if max_count and len(all_comments) >= max_count:
                all_comments = all_comments[:max_count]
                break

            if len(replies) < 20:
                break

            page += 1
            time.sleep(0.5)

        except Exception as e:
            if page == 1: result.error = f"API 请求失败: {e}"
            break

    result.comments = all_comments
    result.fetched_count = len(all_comments)
    if not result.total_count: result.total_count = len(all_comments)
    return result


def _fetch_bilibili_all_sub_replies(av_id: str, root_rpid: int, rcount: int) -> List[ReplyItem]:
    """递归拉取 B站楼中楼全部子回复。"""
    all_replies: List[ReplyItem] = []
    session = _get_session()
    oid = str(av_id).replace("av", "")
    page = 1
    while len(all_replies) < rcount and page <= 10:
        try:
            resp = session.get(
                "https://api.bilibili.com/x/v2/reply/reply",
                params={"type": "1", "oid": oid, "root": root_rpid, "pn": page, "ps": 20},
                headers={"Referer": "https://www.bilibili.com/"},
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                break
            replies = data.get("data", {}).get("replies", [])
            if not replies:
                break
            for s in replies:
                if not isinstance(s, dict): continue
                member = s.get("member", {})
                content = s.get("content", {})
                all_replies.append(ReplyItem(
                    author=_clean_text(member.get("uname", "")),
                    text=_clean_text(content.get("message", "")),
                    time_str=_format_time_str(s.get("ctime", "")),
                    likes=_safe_int(s.get("like", 0)),
                    reply_to="",
                ))
            if len(replies) < 20:
                break
            page += 1
            time.sleep(0.5)
        except Exception:
            break
    return all_replies


def _extract_bilibili_sub_replies(raw_list: list) -> List[ReplyItem]:
    """解析 B站子回复（楼中楼）。"""
    replies: List[ReplyItem] = []
    for s in (raw_list if isinstance(raw_list, list) else []):
        if not isinstance(s, dict): continue
        member = s.get("member", {})
        content = s.get("content", {})
        replies.append(ReplyItem(
            author=_clean_text(member.get("uname", "")),
            text=_clean_text(content.get("message", "")),
            time_str=_format_time_str(s.get("ctime", "")),
            location="",
            likes=_safe_int(s.get("like", 0)),
            reply_to="",  # B站 API 子回复层不直接暴露 reply_to name
        ))
    return replies


def extract_bilibili_comments(url: str, max_count: Optional[int] = None) -> CommentResult:
    """从 B站视频抓取评论区。"""
    oid = _extract_bilibili_oid(url)
    if not oid:
        return CommentResult(platform="bilibili", error=f"无法提取视频ID: {url}")

    _log.info("B站抓取: oid=%s", oid)
    return _extract_bilibili_comments_via_api(oid, max_count)


# ═══════════════════════════════════════════════════════════════════
#  抖音评论抓取
# ═══════════════════════════════════════════════════════════════════

def _extract_douyin_video_id(url: str) -> str:
    """从抖音链接提取视频 ID。"""
    # modal_id 在用户收藏 / 视频流链接中
    m = re.search(r"modal_id=(\d+)", url)
    if m: return m.group(1)
    # /video/{id}
    m = re.search(r"/video/(\d+)", url)
    if m: return m.group(1)
    # /note/{id} 图文
    m = re.search(r"/note/(\d+)", url)
    if m: return m.group(1)
    # 短链
    m = re.search(r"v\.douyin\.com/([a-zA-Z0-9]+)", url)
    if m: return m.group(1)
    return ""


def _extract_douyin_via_playwright(
    url: str, video_id: str, max_count: Optional[int],
    cookies: Optional[Dict[str, str]] = None,
    lsdata: Optional[Dict[str, str]] = None,
) -> CommentResult:
    """通过 Playwright 渲染抖音页面，提取评论 DOM。"""
    result = CommentResult(platform="douyin", note_id=video_id)
    try: from playwright.sync_api import sync_playwright
    except ImportError:
        result.error = "Playwright 未安装"; return result

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent=_DEFAULT_HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        if cookies:
            context.add_cookies([
                {"name": k, "value": str(v), "domain": ".douyin.com", "path": "/"}
                for k, v in cookies.items()
            ])
        page = context.new_page()
        try:
            _log.info("Playwright 加载抖音: %s", url[:120])
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # 注入 localStorage 认证数据（抖音关键！）
            if lsdata:
                for k, v in lsdata.items():
                    try:
                        page.evaluate(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)})")
                    except: pass
                page.reload(wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)

            # 滚动 + 展开回复，直到数量稳定
            prev_count = 0
            same_count_rounds = 0
            for round_num in range(30):
                page.evaluate("window.scrollTo(0, (document.documentElement.scrollHeight || document.body.scrollHeight))")
                time.sleep(1.5)

                # 展开回复
                try:
                    clicked = _click_expand_buttons(page, ("条回复", "展开", "展开更多", "查看更多"))
                    if not clicked:
                        btns = page.locator("button").filter(has_text="条回复")
                        for i in range(min(btns.count(), 20)):
                            try:
                                btns.nth(i).dispatch_event("click")
                            except Exception:
                                pass
                except Exception:
                    pass
                time.sleep(1.5)

                count = page.evaluate("document.querySelectorAll('[data-e2e=comment-item]').length")
                _log.info("抖音滚动 %s 轮: %s 条评论", round_num + 1, count)

                if count == prev_count:
                    same_count_rounds += 1
                    if same_count_rounds > 4:
                        break
                else:
                    same_count_rounds = 0
                prev_count = count

            # 提取评论数据（含点赞数、时间、属地）
            comments = _extract_douyin_from_page_js(page)
            if not comments:
                comments = _extract_douyin_comments_from_dom(page)

            if comments:
                # 获取平台声明的评论总数
                html = page.content()
                count_match = re.search(r'评论[^0-9]*\((\d+)\)', html)
                total = _safe_int(count_match.group(1)) if count_match else len(comments)

                if max_count and max_count > 0:
                    comments = comments[:max_count]
                result.comments = comments
                result.total_count = total
                result.fetched_count = len(comments)
            else:
                result.error = "未找到抖音评论（页面可能需要登录）"
        except Exception as e:
            result.error = f"抖音抓取异常: {e}"
        finally:
            browser.close()
    return result


def _extract_douyin_comments_from_dom(page) -> List[CommentItem]:
    """从抖音渲染后的 DOM 提取评论。"""
    comments: List[CommentItem] = []
    selectors = [
        "[class*=commentItem]", "[class*=CommentItem]",
        ".comment-item", "[class*=comment_item]",
        "[class*=CommentListContainer] > div",
        "[data-e2e*=comment]", "[class*=comment-list] > div",
        "[class*=replyItem]", "[class*=reply_item]",
        ".ECGgn5JG", ".comment-mainContent",
        "div[id*=comment]", "div[class*=comment-]",
    ]
    for sel in selectors:
        elements = page.query_selector_all(sel)
        if elements and len(elements) >= 2:
            _log.info("抖音选择器 '%s' 匹配 %s 个元素", sel, len(elements))
            for el in elements[:200]:
                try:
                    text = el.inner_text().strip()[:2000]
                    if len(text) < 2: continue
                    # 尝试提取作者、正文、时间等字段
                    author = ""
                    content_text = ""
                    time_str = ""
                    likes = 0
                    try:
                        author_el = el.query_selector("[class*=author], [class*=name], [class*=user], [class*=nickname]")
                        if author_el: author = author_el.inner_text().strip()[:50]
                        content_el = el.query_selector("[class*=content], [class*=text], [class*=desc], [class*=message]")
                        if content_el: content_text = content_el.inner_text().strip()
                        time_el = el.query_selector("[class*=time], [class*=date]")
                        if time_el: time_str = time_el.inner_text().strip()
                        likes_el = el.query_selector("[class*=like], [class*=digg], [class*=vote]")
                        if likes_el: likes = _safe_int(likes_el.inner_text().strip())
                    except Exception: pass

                    if not content_text:
                        lines = text.split("\n")
                        if len(lines) >= 2 and len(lines[0]) < 50:
                            author = lines[0].strip()
                            content_text = " ".join(lines[1:]).strip()
                        else:
                            content_text = text[:2000]

                    comments.append(CommentItem(
                        author=author,
                        text=content_text[:2000] if content_text else text[:2000],
                        time_str=time_str,
                        location="",
                        likes=likes,
                    ))
                except Exception: continue
            if comments: break

    _log.info("抖音 DOM 提取到 %s 条评论", len(comments))
    return comments


def _extract_douyin_from_page_js(page) -> List[CommentItem]:
    """用 JS 从 DOM 提取评论：分割作者、正文、时间、属地、点赞，包含嵌套回复。"""
    try:
        raw = page.evaluate("""
            (function(){
                var items = document.querySelectorAll('[data-e2e=comment-item]');
                var comments = [];
                items.forEach(function(el){
                    var text = (el.innerText || el.textContent || '').trim();
                    if(text.length < 3) return;

                    var lines = text.split('\\n');
                    // 第一行是作者（可能带时间戳后缀）
                    var author = (lines[0] || '').trim();
                    // 去掉时间后缀
                    author = author.replace(/(\\d+)?\\s*(小时前|分钟前|天前|刚刚).*$/, '').trim();
                    if(author.includes('·')) author = author.split('·')[0].trim();
                    author = author.slice(0, 50);

                    // 找内容：从第二行到时间行之前
                    var contentLines = [];
                    var timeStr = '', location = '';
                    var likes = 0;
                    for(var i = 1; i < lines.length; i++){
                        var line = lines[i].trim();
                        // 时间+属地行
                        if(line.includes('小时前') || line.includes('分钟前') || line.includes('天前') || line.includes('刚刚')){
                            timeStr = line;
                            if(line.includes('·')){
                                var parts = line.split('·');
                                for(var j=0; j<parts.length; j++){
                                    var p = parts[j].trim();
                                    if(p.includes('小时前')||p.includes('分钟前')||p.includes('天前')||p.includes('刚刚')){
                                        timeStr = p;
                                    } else if(/^[\\u4e00-\\u9fa5]{2,6}$/.test(p)){
                                        location = p;
                                    }
                                }
                            }
                            // 点赞数在时间行之后
                            if(i+1 < lines.length && /^\\d+$/.test(lines[i+1].trim()) && !lines[i+1].includes('小时')){
                                likes = parseInt(lines[i+1].trim());
                            }
                            break;
                        }
                        contentLines.push(line);
                    }

                    var content = contentLines.join(' ').trim();
                    if(!content && lines.length > 1) content = lines[1] || '';

                    // 提取嵌套回复
                    var replies = [];
                    var subItems = el.querySelectorAll('[class*=sub], [class*=reply], [class*=child], [class*=SubComment]');
                    subItems.forEach(function(sub){
                        var st = (sub.innerText || sub.textContent || '').trim();
                        if(st.length > 3 && st.length < 2000){
                            replies.push({text: st});
                        }
                    });

                    comments.push({
                        author: author,
                        text: content.slice(0, 2000),
                        time: timeStr,
                        location: location,
                        likes: likes,
                        reply_count: replies.length
                    });
                });
                return JSON.stringify(comments);
            })()
        """)
        parsed = json.loads(raw)
        return [
            CommentItem(
                author=c.get("author", ""),
                text=c.get("text", ""),
                time_str=c.get("time", ""),
                location=c.get("location", ""),
                likes=c.get("likes", 0),
                reply_count=c.get("reply_count", 0),
            )
            for c in parsed
        ]
    except Exception as e:
        _log.warning("JS 提取抖音评论失败: %s", e)
        return []


def _extract_douyin_via_js_eval(page) -> List[CommentItem]:
    """通过 Playwright JS 执行提取页面变量中的评论。"""
    comments: List[CommentItem] = []
    for expr in [
        "JSON.stringify(window._SSR_DATA_ || window.__INITIAL_STATE__ || window.__NEXT_DATA__ || {})",
        "JSON.stringify(window.__MIDDLEWARE_DATA__ || {})",
        "JSON.stringify(window.__NUXT__ || {})",
    ]:
        try:
            raw = page.evaluate(expr)
            if raw and isinstance(raw, str) and len(raw) > 100:
                data = json.loads(raw)
                _extract_douyin_comments_recursive(data, comments)
                if comments: break
        except Exception: pass
    return comments


def _extract_douyin_comments_from_html(html: str) -> List[CommentItem]:
    """从抖音原始 HTML 中尝试提取评论数据。"""
    comments: List[CommentItem] = []

    # 尝试各种 SSR 数据变量名
    patterns = [
        r'window\._SSR_DATA_\s*=\s*(\{.+?\});\s*\n',
        r'__NEXT_DATA__\s*=\s*(\{.+?\});',
        r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});',
        r'"comment",\s*"list":\s*(\[.+?\])',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                raw = m.group(1)
                data = json.loads(raw if raw.endswith(']') else raw)
                if isinstance(data, list):
                    comments = _parse_douyin_comment_list(data)
                else:
                    _extract_douyin_comments_recursive(data, comments)
                if comments: break
            except (json.JSONDecodeError, Exception):
                pass

    # 尝试匹配 script 中的 JSON
    if not comments:
        script_pattern = r'<script[^>]*>(.*?)</script>'
        for script_match in re.finditer(script_pattern, html, re.DOTALL):
            script_text = script_match.group(1)
            if '"comment"' in script_text and '"text"' in script_text:
                try:
                    for m in re.finditer(r'\{[^}]*"text"\s*:\s*"[^"]{2,}"[^}]*\}', script_text):
                        try:
                            obj = json.loads(m.group())
                            text = obj.get("text", "").strip()
                            if len(text) > 2:
                                comments.append(CommentItem(
                                    author=_clean_text(obj.get("user_name") or ""),
                                    text=text,
                                    time_str=_format_time_str(obj.get("create_time")),
                                    likes=_safe_int(obj.get("digg_count") or obj.get("like_count")),
                                ))
                        except: pass
                    if comments: break
                except: pass

    return comments


def _parse_douyin_comment_list(data: list) -> List[CommentItem]:
    """解析抖音评论列表。"""
    comments: List[CommentItem] = []
    for item in data:
        if isinstance(item, dict):
            text = _clean_text(item.get("text", ""))
            if len(text) > 2:
                comments.append(CommentItem(
                    author=_clean_text(item.get("user_name") or item.get("user", {}).get("nickname", "")),
                    text=text,
                    time_str=_format_time_str(item.get("create_time")),
                    location=_clean_text(item.get("ip_label")),
                    likes=_safe_int(item.get("digg_count") or item.get("like_count")),
                    reply_count=_safe_int(item.get("reply_comment_total") or item.get("reply_count")),
                ))
    return comments


def _extract_douyin_comments_recursive(obj: Any, out: List[CommentItem]):
    """递归搜索抖音 JSON 数据中的评论结构。"""
    if isinstance(obj, dict):
        # 寻找评论数据特征
        if "comment" in obj and isinstance(obj.get("text"), str):
            text = obj["text"].strip()
            if len(text) > 2 and len(text) < 5000:
                out.append(CommentItem(
                    author=_clean_text(obj.get("user_name") or obj.get("nickname") or
                        (obj.get("user", {}) or {}).get("nickname", "")),
                    text=text,
                    time_str=_format_time_str(obj.get("create_time") or obj.get("time")),
                    location=_clean_text(obj.get("ip_label") or obj.get("location")),
                    likes=_safe_int(obj.get("digg_count") or obj.get("like_count") or 0),
                    reply_count=_safe_int(obj.get("reply_comment_total") or obj.get("reply_count") or 0),
                ))
        # 继续递归
        for v in obj.values():
            _extract_douyin_comments_recursive(v, out)
    elif isinstance(obj, list):
        for item in obj[:500]:
            _extract_douyin_comments_recursive(item, out)


def _extract_douyin_via_api(
    video_id: str, max_count: Optional[int], cookies: Optional[Dict[str, str]] = None
) -> CommentResult:
    """通过抖音 API 抓取评论。"""
    result = CommentResult(platform="douyin", note_id=video_id)

    session = _get_session()
    session.headers.update({
        "Referer": f"https://www.douyin.com/video/{video_id}",
        "Origin": "https://www.douyin.com",
    })
    if cookies:
        for k, v in cookies.items():
            session.cookies.set(k, v, domain=".douyin.com")

    api_url = "https://www.douyin.com/aweme/v1/web/comment/list/"
    cursor = 0
    all_comments: List[CommentItem] = []

    for _ in range(10):
        params = {
            "aweme_id": video_id,
            "cursor": cursor,
            "count": 20,
            "item_type": "0",
        }
        try:
            resp = session.get(api_url, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            if data.get("status_code") != 0:
                if data.get("status_msg"):
                    result.error = f"抖音 API: {data['status_msg']}"
                break

            comment_list = data.get("comments", [])
            if not comment_list:
                break

            for c in comment_list:
                if not isinstance(c, dict): continue
                user = c.get("user", {})
                all_comments.append(CommentItem(
                    author=_clean_text(user.get("nickname", "")),
                    text=_clean_text(c.get("text", "")),
                    time_str=_format_time_str(c.get("create_time", "")),
                    location=_clean_text(c.get("ip_label", "")),
                    likes=_safe_int(c.get("digg_count", 0)),
                    reply_count=_safe_int(c.get("reply_comment_total", 0)),
                    replies=_parse_douyin_sub_replies(c.get("reply_comments", [])),
                ))

            if result.total_count == 0:
                result.total_count = _safe_int(data.get("total_count", 0))

            cursor = data.get("cursor", 0)
            if cursor == 0 or not cursor:
                break

            if max_count and len(all_comments) >= max_count:
                all_comments = all_comments[:max_count]
                break

            time.sleep(0.5)

        except Exception as e:
            if not all_comments:
                result.error = f"API 请求失败: {e}"
            break

    result.comments = all_comments
    result.fetched_count = len(all_comments)
    if not result.total_count: result.total_count = len(all_comments)
    return result


def _parse_douyin_sub_replies(raw_list: list) -> List[ReplyItem]:
    """解析抖音子回复。"""
    replies: List[ReplyItem] = []
    for s in (raw_list if isinstance(raw_list, list) else []):
        if not isinstance(s, dict): continue
        user = s.get("user", {})
        replies.append(ReplyItem(
            author=_clean_text(user.get("nickname", "")),
            text=_clean_text(s.get("text", "")),
            time_str=_format_time_str(s.get("create_time", "")),
            location=_clean_text(s.get("ip_label", "")),
            likes=_safe_int(s.get("digg_count", 0)),
            reply_to=_clean_text(s.get("reply_to_username", "")),
        ))
    return replies


def extract_douyin_comments(
    url: str,
    max_count: Optional[int] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> CommentResult:
    """从抖音视频抓取评论区。"""
    video_id = _extract_douyin_video_id(url)
    if not video_id:
        return CommentResult(platform="douyin", error=f"无法提取视频ID: {url}")

    _log.info("抖音抓取: video_id=%s", video_id)

    # 加载完整认证状态（含 localStorage）
    auth_data = _auto_ensure_cookies("douyin") if not cookies else cookies
    if isinstance(auth_data, dict) and 'cookies' in auth_data:
        lsdata = auth_data.get('localStorage', {})
        cookies = _flatten_cookies(auth_data)
    else:
        lsdata = {}
        cookies = _flatten_cookies(auth_data) if not cookies else cookies

    # 如果外部传入了 cookies 但没有 localStorage，尝试从文件补加载
    if cookies and not lsdata:
        try:
            raw = _auto_ensure_cookies("douyin")
            if isinstance(raw, dict) and 'localStorage' in raw:
                lsdata = raw.get('localStorage', {})
        except: pass

    # 方法1: API 直调（有 Cookie 时带 cookie）
    api_result = _extract_douyin_via_api(video_id, max_count, cookies=cookies)
    if api_result.comments:
        return api_result
    _log.info("抖音 API 失败: %s, 降级到 Playwright", api_result.error)

    # 方法2: Playwright 浏览器渲染（注入完整认证态）
    return _extract_douyin_via_playwright(url, video_id, max_count, cookies=cookies, lsdata=lsdata)


# ═══════════════════════════════════════════════════════════════════
#  统一入口
# ═══════════════════════════════════════════════════════════════════

def _sort_comments(result: CommentResult, sort_by: str) -> CommentResult:
    """对评论结果排序。"""
    if sort_by == "likes":
        result.comments.sort(key=lambda c: c.likes, reverse=True)
    elif sort_by == "time":
        result.comments.sort(
            key=lambda c: c.time_str if c.time_str else "0000",
            reverse=True,
        )
    # default: 保持原始顺序，不排序
    return result


def scrape_comments(
    url: str,
    platform: str = "",
    max_count: Optional[int] = None,
    sort_by: str = "default",
    cookies: Optional[Dict[str, str]] = None,
) -> CommentResult:
    """多平台评论抓取统一入口。

    Args:
        url: 目标链接
        platform: 平台标识（空则自动检测）
        max_count: 最多抓取条数（None=不限制）
        sort_by: 排序方式 — default（原始）/ likes（点赞数降序）/ time（最新在前）
        cookies: Cookie 字典（仅小红书需要）

    Returns:
        CommentResult
    """
    lower = url.lower()

    result: CommentResult
    if platform == "xiaohongshu" or "xiaohongshu.com" in lower:
        result = extract_xiaohongshu_comments(url, max_count=max_count, cookies=cookies)
    elif platform == "bilibili" or "bilibili.com" in lower or "b23.tv" in lower:
        result = extract_bilibili_comments(url, max_count=max_count)
    elif platform == "douyin" or "douyin.com" in lower:
        result = extract_douyin_comments(url, max_count=max_count)
    # 自动检测
    elif "xiaohongshu.com" in lower:
        result = extract_xiaohongshu_comments(url, max_count=max_count, cookies=cookies)
    elif "bilibili.com" in lower or "b23.tv" in lower:
        result = extract_bilibili_comments(url, max_count=max_count)
    elif "douyin.com" in lower:
        result = extract_douyin_comments(url, max_count=max_count)
    else:
        return CommentResult(platform="unknown", error=f"不支持的平台: {url}")

    return _sort_comments(result, sort_by)
