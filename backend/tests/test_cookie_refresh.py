"""回归测试: Cookie 刷新 → 验证登录态"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== 刷新 XHS Cookie ===", flush=True)
from app.services.xhs_local_browser import refresh_xhs_cookies_from_system
t0 = time.time()
result = refresh_xhs_cookies_from_system()
print(f"耗时: {time.time()-t0:.1f}s", flush=True)
print(f"结果: {json.dumps(result, ensure_ascii=False, indent=2)}", flush=True)

print("\n=== 验证 Cookie 登录态 ===", flush=True)
from app.services.cookie_manager import load_cookies, probe_xhs_cookies_logged_in
cookies = load_cookies("xiaohongshu")
probe = probe_xhs_cookies_logged_in(cookies)
print(f"Cookie数: {len(cookies)}", flush=True)
print(f"登录态: {probe.get('logged_in')} nickname={probe.get('nickname')}", flush=True)

if probe.get("logged_in"):
    print("\n*** PASS: Cookie 已登录! ***", flush=True)
else:
    print("\n*** FAIL: Cookie 仍是访客态 ***", flush=True)
