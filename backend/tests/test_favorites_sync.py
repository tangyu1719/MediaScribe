"""订阅收藏夹同步回归测试"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== 收藏夹同步 ===")
from app.services.xhs_favorites_adapter import fetch_favorites_catalog

creator_id = os.environ.get("XHS_FAVORITES_CREATOR_ID", "60dc2e340000000001008a1f")
profile_url = f"https://www.xiaohongshu.com/user/profile/{creator_id}?tab=fav"

print(f"Creator: {creator_id}")
print(f"URL: {profile_url}")

t0 = time.time()
try:
    items = fetch_favorites_catalog(
        creator_id=creator_id,
        profile_url=profile_url,
        limit=10,
    )
    elapsed = time.time() - t0
    print(f"耗时: {elapsed:.1f}s")
    print(f"收藏数: {len(items) if items else 0}")
    for i, item in enumerate(items[:5] if items else []):
        print(f"  [{i+1}] {getattr(item, 'title', '?')[:60]} - {getattr(item, 'note_id', '?')[:16]}")
    if items and len(items) > 5:
        print(f"  ... 共 {len(items)} 条")
    if items:
        print("\n*** PASS: 收藏夹同步成功! ***")
    else:
        print("\n*** WARN: 收藏夹为空 ***")
except Exception as e:
    elapsed = time.time() - t0
    print(f"耗时: {elapsed:.1f}s")
    print(f"异常: {e}")
    print("\n*** FAIL ***")
