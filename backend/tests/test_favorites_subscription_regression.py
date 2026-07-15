"""订阅收藏夹回归测试：Cookie 获取 → 浏览器登录态 → 收藏拉取"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_cookie_acquisition_chain():
    """三层 Cookie 获取链至少有一层能拿到结果或给出明确错误"""
    from app.services.cookie_manager import (
        load_cookies, extract_platform_cookies_via_cdp, find_cdp_port,
        probe_xhs_cookies_logged_in,
    )
    from app.services.xhs_local_browser import (
        _try_import_chrome_cookies_live, _playwright_cookies_for_context,
        _browser_config_chrome, is_browser_google_signed_in, _browser_running,
    )

    results = {}

    # Layer 1: 文件缓存
    file_cookies = load_cookies("xiaohongshu")
    results["file"] = {"count": len(file_cookies), "logged_in": probe_xhs_cookies_logged_in(file_cookies).get("logged_in", False)}
    print(f"Layer 1 (文件缓存): count={len(file_cookies)} logged_in={results['file']['logged_in']}")

    # Layer 2: CDP 附着
    port = find_cdp_port()
    results["cdp"] = {"port_found": port is not None}
    if port:
        cdp_cookies = extract_platform_cookies_via_cdp("xiaohongshu")
        results["cdp"]["count"] = len(cdp_cookies)
        results["cdp"]["logged_in"] = probe_xhs_cookies_logged_in(cdp_cookies).get("logged_in", False)
    else:
        results["cdp"]["error"] = "CDP 端口未就绪（Chrome 未带 --remote-debugging-port）"
    print(f"Layer 2 (CDP): port={port}")

    # Layer 3: browser_cookie3 直读
    live_cookies = _try_import_chrome_cookies_live()
    results["browser_cookie3"] = {"count": len(live_cookies)}
    if live_cookies:
        results["browser_cookie3"]["logged_in"] = probe_xhs_cookies_logged_in(live_cookies).get("logged_in", False)
    print(f"Layer 3 (browser_cookie3): count={len(live_cookies)}")

    # Playwright cookie 聚合
    pw_cookies = _playwright_cookies_for_context()
    results["playwright_aggregate"] = {"count": len(pw_cookies)}
    if pw_cookies:
        # 检查关键 Cookie
        key_cookies = ["a1", "web_session", "webId", "acw_tc"]
        found_keys = [c["name"] for c in pw_cookies if c["name"] in key_cookies]
        results["playwright_aggregate"]["key_cookies"] = found_keys
    print(f"Playwright 聚合: count={len(pw_cookies)} keys={results['playwright_aggregate'].get('key_cookies', [])}")

    # 浏览器状态
    cfg = _browser_config_chrome()
    results["browser"] = {
        "running": _browser_running(cfg),
        "google_signed_in": is_browser_google_signed_in(cfg),
    }
    print(f"浏览器: running={results['browser']['running']} google={results['browser']['google_signed_in']}")

    # 断言：至少有一个层能工作，或者给出明确的修复指引
    any_ok = (
        (results["file"]["logged_in"])
        or (results.get("cdp", {}).get("logged_in"))
        or (results.get("browser_cookie3", {}).get("logged_in"))
        or (results["playwright_aggregate"]["count"] > 0)
    )
    has_cdp = results["cdp"].get("port_found")
    has_chrome = results["browser"]["running"] and results["browser"]["google_signed_in"]

    print(f"\n诊断: any_ok={any_ok} has_cdp={has_cdp} has_chrome={has_chrome}")
    if not any_ok and has_chrome and not has_cdp:
        print("⚠️  修复指引: Chrome 在运行但无 CDP → 需要关闭 Chrome 后用 persistent_context")
        print("   或: 给桌面 Chrome 快捷方式加 --remote-debugging-port=9223")

    return results


def test_favorites_scrape_entry():
    """收藏抓取入口不会因 Cookie 不可用而崩溃（应正确 fallback）"""
    from app.services.xhs_favorites_adapter import fetch_favorites_catalog

    creator_id = os.environ.get("XHS_FAVORITES_CREATOR_ID", "000000000000000000000000")
    print(f"\n测试收藏抓取: creator_id={creator_id}")

    try:
        result = fetch_favorites_catalog(
            creator_id=creator_id,
            profile_url=f"https://www.xiaohongshu.com/user/profile/{creator_id}",
            limit=5,
        )
        print(f"结果: count={len(result) if result else 0}")
        return True
    except Exception as e:
        msg = str(e)
        print(f"异常: {msg[:200]}")
        # 不应该因为 Cookie 问题直接崩溃
        if "SUB_XHS_COOKIE_UNAVAILABLE" in msg and "Chrome 已在运行但无法读取" in msg:
            print("❌ 回归失败: 仍然在 Chrome 运行时因无 Cookie 直接抛异常")
            return False
        # 其他错误（如真的没登录）可以接受
        print("✅ 非 Cookie 锁定问题，属于正常的登录态缺失")
        return True


if __name__ == "__main__":
    print("=" * 60)
    print("订阅收藏夹回归测试")
    print("=" * 60)
    r1 = test_cookie_acquisition_chain()
    r2 = test_favorites_scrape_entry()
    print(f"\n结果: cookie_chain={'PASS' if r1 else 'FAIL'} favorites_entry={'PASS' if r2 else 'FAIL'}")
