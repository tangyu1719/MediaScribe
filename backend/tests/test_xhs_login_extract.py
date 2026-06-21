"""直接 Playwright 打开 XHS 页面，JS 提取登录态 token"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.xhs_local_browser import (
    _browser_config_chrome, _close_browser, _browser_running,
)

cfg = _browser_config_chrome()
print(f"Chrome profile: {cfg.profile_dir}")
print(f"Chrome running: {_browser_running(cfg)}")

# 关闭 Chrome
if _browser_running(cfg):
    print("关闭 Chrome...")
    _close_browser(cfg)
    time.sleep(3)

from playwright.sync_api import sync_playwright

print("启动 persistent_context...")
with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        str(cfg.profile_dir),
        channel="chrome",
        headless=False,
        args=["--start-maximized"],
        viewport=None,
        ignore_default_args=["--enable-automation"],
    )
    page = context.pages[0] if context.pages else context.new_page()

    print("导航到小红书...")
    page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    # 检查页面登录状态
    print("\n=== 页面状态 ===")
    logged_in = page.evaluate("""() => {
        // 检查多个登录态标志
        const hasLoginBtn = !!document.querySelector('.login-btn, [class*="login"]');
        const hasUserAvatar = !!document.querySelector('.user, .avatar, [class*="side-bar"] img');
        const hasRedId = window.__INITIAL_STATE__?.user?.redId;
        const userInfo = window.__INITIAL_STATE__?.user;
        return {
            url: location.href,
            hasLoginBtn: hasLoginBtn,
            hasUserAvatar: hasUserAvatar,
            redId: hasRedId || null,
            userLoggedIn: !!(userInfo && userInfo.loggedIn),
            title: document.title,
        };
    }""")
    print(json.dumps(logged_in, ensure_ascii=False, indent=2))

    # 提取 Cookie
    print("\n=== 浏览器 Cookie ===")
    all_cookies = context.cookies()
    xhs_cookies = {}
    for c in all_cookies:
        if "xiaohongshu" in (c.get("domain") or ""):
            xhs_cookies[c["name"]] = c["value"]

    key_names = ["a1", "web_session", "webId", "acw_tc", "xhsTrackerId", "galaxy.creator.beaker.session"]
    print(f"XHS Cookie: {len(xhs_cookies)} 个")
    for k in key_names:
        if k in xhs_cookies:
            print(f"  {k}: {xhs_cookies[k][:30]}...")

    # 通过 JS 提取 localStorage token
    print("\n=== LocalStorage Token ===")
    tokens = page.evaluate("""() => {
        const result = {};
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && (key.includes('token') || key.includes('auth') || key.includes('session') || key.includes('login'))) {
                    result[key] = localStorage.getItem(key)?.substring(0, 50);
                }
            }
        } catch(e) { result.error = e.message; }
        return result;
    }""")
    print(json.dumps(tokens, ensure_ascii=False, indent=2))

    # 截图
    page.screenshot(path="output/xhs_login_check.png")
    print("\n截图: output/xhs_login_check.png")

    # 如果有登录态，保存 Cookie
    if logged_in.get("userLoggedIn"):
        from app.services.cookie_manager import save_cookies_if_better
        result = save_cookies_if_better("xiaohongshu", xhs_cookies, owner_nickname="三点")
        print(f"\nCookie 保存: logged_in={result.get('logged_in') if isinstance(result, dict) else 'N/A'}")

    print("\n浏览器保持打开 10 秒，自己去看看是否登录状态...")
    time.sleep(10)
    context.close()
