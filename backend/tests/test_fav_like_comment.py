"""照抄评论抓取模式：文件 Cookie → launch + 注入 → 导航收藏页"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== 照抄评论抓取模式 ===")

# 1. 获取 Cookie（完全照抄 comment_scraper._auto_ensure_cookies + _load_persisted_cookies）
from app.services.cookie_manager import load_cookies, ensure_cookies
cookies = load_cookies("xiaohongshu") or {}
if not cookies:
    cookies = ensure_cookies("xiaohongshu", open_login_if_missing=False) or {}
print(f"Cookie: {len(cookies)} 个")

# 2. 照抄 comment_scraper._extract_xhs_via_playwright 的浏览器启动方式
from playwright.sync_api import sync_playwright

creator_id = os.environ.get("XHS_FAVORITES_CREATOR_ID", "000000000000000000000000")
fav_url = f"https://www.xiaohongshu.com/user/profile/{creator_id}?tab=fav&subTab=note"

t0 = time.time()
with sync_playwright() as p:
    # 照抄 comment_scraper: launch(headless=False) + new_context + add_cookies + new_page + goto
    browser = p.chromium.launch(headless=False, args=["--no-sandbox", "--disable-dev-shm-usage"])
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
    )
    if cookies:
        context.add_cookies([
            {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
            for k, v in cookies.items()
        ])
    page = context.new_page()

    print(f"导航: {fav_url}")
    page.goto(fav_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)

    # 检测登录态
    state_js = "JSON.stringify({loggedIn:!!(window.__INITIAL_STATE__?.user?.loggedIn),guest:!!(window.__INITIAL_STATE__?.user?.guest),redId:window.__INITIAL_STATE__?.user?.redId||'',nickname:window.__INITIAL_STATE__?.user?.userInfo?.nickname||''})"
    state = json.loads(page.evaluate(state_js))
    print(f"登录: loggedIn={state.get('loggedIn')} guest={state.get('guest')} nickname={state.get('nickname','')}")

    # 检查 URL（是否被重定向到登录页）
    cur_url = page.url
    print(f"URL: {cur_url[:120]}")
    if "/login" in cur_url:
        print("ERROR: 被重定向到登录页")
    elif state.get("loggedIn") and not state.get("guest"):
        print("*** PASS: 已登录! ***")
        # 简单抓取
        html = page.content()
        title = page.title()
        print(f"标题: {title}")
        # 提取笔记链接
        from app.services.creator_feed_adapter import _parse_init_state
        init = _parse_init_state(html) or {}
        notes = init.get("user", {}).get("notes") or []
        print(f"笔记数(INITIAL_STATE): {len(notes) if isinstance(notes, list) else 'N/A'}")
    else:
        print("NOT LOGGED IN")

    page.screenshot(path="output/fav_test.png")
    print("截图: output/fav_test.png")

    context.close()
    browser.close()

print(f"耗时: {time.time()-t0:.1f}s")
