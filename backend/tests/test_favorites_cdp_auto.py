"""CDP 收藏夹同步 — 全自动：启动 CDP Chrome → 检测登录态 → 抓取"""
import os, sys, json, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== Step 1: 确保 CDP Chrome 在运行 ===")
from app.services.xhs_local_browser import (
    find_cdp_port, _browser_config_chrome, _browser_running, _close_browser,
    _start_owner_chrome_via_shortcut, cdp_list_tabs, cdp_pick_owner_tab,
    cdp_tab_eval, CDP_PORT,
)

cfg = _browser_config_chrome()
port = find_cdp_port()
print(f"CDP port: {port}")

if not port:
    if _browser_running(cfg):
        print("Chrome 在运行但无 CDP → 关闭后重新启动...")
        _close_browser(cfg)
        time.sleep(4)
    print("启动 CDP Chrome...")
    _start_owner_chrome_via_shortcut()
    for i in range(20):
        time.sleep(2)
        port = find_cdp_port()
        if port:
            print(f"CDP 就绪! port={port}")
            break
        print(f"等待 CDP... ({i+1}/20)")
    if not port:
        print("ERROR: CDP 启动失败")
        sys.exit(1)

print(f"\n=== Step 2: 检查小红书登录态 ===")
creator_id = os.environ.get("XHS_FAVORITES_CREATOR_ID", "60dc2e340000000001008a1f")
tabs = cdp_list_tabs(port)

# 找或创建 XHS tab
tab = cdp_pick_owner_tab(port, prefer_cid=creator_id)
if tab:
    ws_url = tab.get("webSocketDebuggerUrl")
else:
    # 没有 XHS tab，需要打开一个
    print("未找到 XHS tab，打开 explore 页...")
    import requests as _req
    r = _req.put(f"http://127.0.0.1:{port}/json/new?url=https://www.xiaohongshu.com/explore", timeout=10)
    time.sleep(5)
    tab = cdp_pick_owner_tab(port, prefer_cid=creator_id)
    if not tab:
        print("ERROR: 无法创建 XHS tab")
        sys.exit(1)
    ws_url = tab.get("webSocketDebuggerUrl")

print(f"Tab URL: {tab.get('url','')[:120]}")

# 检测登录态
for attempt in range(1, 4):
    print(f"\n登录检测 第{attempt}次...")
    login_raw = cdp_tab_eval(ws_url,
        "JSON.stringify({loggedIn:!!(window.__INITIAL_STATE__?.user?.loggedIn),guest:!!(window.__INITIAL_STATE__?.user?.guest||window.__INITIAL_STATE__?.user?.userInfo?.guest),redId:window.__INITIAL_STATE__?.user?.redId||'',nickname:window.__INITIAL_STATE__?.user?.userInfo?.nickname||''})",
        timeout_sec=10)
    state = json.loads(str(login_raw or "{}"))
    is_logged = state.get("loggedIn") and not state.get("guest")
    print(f"  loggedIn={state.get('loggedIn')} guest={state.get('guest')} redId={state.get('redId','')[:10]} nickname={state.get('nickname','')}")

    if is_logged:
        print("*** 已登录! ***")
        break

    if attempt < 3:
        print(f"  未登录，导航到首页重试...")
        cdp_tab_eval(ws_url, "window.location.href='https://www.xiaohongshu.com/explore'", timeout_sec=5)
        time.sleep(5)
    else:
        print("ERROR: 3次重试后仍未登录。请在 Chrome 中手动登录小红书「三点、水」后重试。")
        sys.exit(1)

print(f"\n=== Step 3: 抓取收藏夹 ===")
from app.services.xhs_favorites_adapter import fetch_favorites_catalog
profile_url = f"https://www.xiaohongshu.com/user/profile/{creator_id}?tab=fav"
print(f"URL: {profile_url}")

t0 = time.time()
try:
    items = fetch_favorites_catalog(creator_id=creator_id, profile_url=profile_url, limit=10)
    elapsed = time.time() - t0
    print(f"耗时: {elapsed:.1f}s")
    print(f"收藏数: {len(items) if items else 0}")
    for i, item in enumerate(items[:10] if items else []):
        print(f"  [{i+1}] {getattr(item, 'title', '?')[:80]} - {getattr(item, 'note_id', '?')[:16]}")
    if items:
        print(f"\n*** PASS! 抓到 {len(items)} 条收藏 ***")
    else:
        print("\n*** WARN: 收藏为空 ***")
except Exception as e:
    print(f"耗时: {time.time()-t0:.1f}s")
    print(f"ERROR: {e}")
    sys.exit(1)
