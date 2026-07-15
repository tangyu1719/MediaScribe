"""收藏夹同步 - 全自动一步到位"""
import os, sys, json, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== 1. 启动 CDP Chrome ===")
# 杀干净
os.system('taskkill /F /IM chrome.exe 2>nul')
time.sleep(4)
real = os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data')
for lock in ['SingletonLock','SingletonCookie','SingletonSocket']:
    try: os.unlink(os.path.join(real, lock))
    except: pass

# 启动 SBA CDP Chrome
shortcut = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'SBA CDP Chrome.lnk')
os.startfile(shortcut)
print('Chrome 已启动')

# 等 CDP 就绪
port = 9223
for i in range(15):
    time.sleep(2)
    try:
        r = requests.get(f'http://127.0.0.1:{port}/json/version', timeout=2)
        v = r.json()
        print(f'CDP OK! ({i*2}s) Browser={v.get("Browser","")}')
        break
    except:
        pass
else:
    print('FAIL: CDP 未就绪')
    sys.exit(1)

print("\n=== 2. 打开小红书并检测登录 ===")
from app.services.xhs_local_browser import cdp_tab_eval, cdp_tab_get_html, cdp_tab_scroll_bottom, cdp_tab_get_xhs_cookies
from app.services.xhs_favorites_adapter import parse_favorites_from_init_state
from app.services.creator_feed_adapter import _parse_init_state, is_valid_xhs_note_id
from app.services.cookie_manager import save_cookies

# 创建 XHS tab 并等待加载
ws_url = None
for attempt in range(3):
    r = requests.put(f'http://127.0.0.1:{port}/json/new?url=https://www.xiaohongshu.com/explore', timeout=10)
    time.sleep(6)
    r = requests.get(f'http://127.0.0.1:{port}/json/list', timeout=5)
    tabs = r.json()
    print(f'Tabs ({len(tabs)}):')
    for t in tabs:
        url = str(t.get('url',''))
        ws = t.get('webSocketDebuggerUrl','')
        is_xhs = 'xiaohongshu' in url.lower()
        is_login = '/login' in url
        print(f'  {is_xhs and \"XHS\" or \"   \"} {is_login and \"LOGIN\" or \"     \"} {url[:100]}')
        if ws and is_xhs and not is_login:
            ws_url = ws
    if ws_url:
        break
    print(f'Retry {attempt+1}/3...')

if not ws_url:
    print('FAIL: 未找到 XHS tab')
    sys.exit(1)

# 检测登录
raw = cdp_tab_eval(ws_url, 'JSON.stringify({loggedIn:!!(window.__INITIAL_STATE__?.user?.loggedIn),guest:!!(window.__INITIAL_STATE__?.user?.guest)})', timeout_sec=8)
s = json.loads(str(raw or '{}'))
print(f'登录: loggedIn={s.get("loggedIn")} guest={s.get("guest")}')
if not s.get('loggedIn') or s.get('guest'):
    print('FAIL: 未登录，请在打开的Chrome中登录小红书后重试')
    sys.exit(1)

print("\n=== 3. 导航收藏页 ===")
creator_id = '000000000000000000000000'
fav_url = f'https://www.xiaohongshu.com/user/profile/{creator_id}?tab=fav&subTab=note'
requests.put(f'http://127.0.0.1:{port}/json/new?url={fav_url}', timeout=10)
time.sleep(6)

# 找收藏 tab
r = requests.get(f'http://127.0.0.1:{port}/json/list', timeout=5)
for t in r.json():
    url = str(t.get('url',''))
    ws = t.get('webSocketDebuggerUrl','')
    if ws and 'profile' in url and creator_id[:12] in url:
        ws_url = ws
        print(f'收藏tab: {url[:120]}')
        break

# 收藏页登录
raw2 = cdp_tab_eval(ws_url, 'JSON.stringify({loggedIn:!!(window.__INITIAL_STATE__?.user?.loggedIn),guest:!!(window.__INITIAL_STATE__?.user?.guest)})', timeout_sec=8)
s2 = json.loads(str(raw2 or '{}'))
print(f'收藏页登录: loggedIn={s2.get("loggedIn")} guest={s2.get("guest")}')
if not s2.get('loggedIn') or s2.get('guest'):
    print('FAIL: 收藏页要求重新登录')
    sys.exit(1)

print("\n=== 4. 抓取收藏 ===")
t0 = time.time()
for _ in range(5):
    cdp_tab_scroll_bottom(ws_url, rounds=1, pause_sec=1.2)

html = cdp_tab_get_html(ws_url)
init = _parse_init_state(html) or {}
by_note = {}
for it in parse_favorites_from_init_state(init, owner_creator_id=creator_id, profile_url=fav_url, fetch_source='cdp_final'):
    if is_valid_xhs_note_id(it.note_id):
        by_note[it.note_id] = it
        title = getattr(it, 'title', '')[:80]
        author = getattr(it, 'author_name', '')
        print(f'  [{it.note_id[:16]}] {title} {author}')

# 保存 Cookie
cookies = cdp_tab_get_xhs_cookies(ws_url)
if cookies:
    save_cookies('xiaohongshu', cookies)
    print(f'\nCookie 已保存: {len(cookies)} 个')

elapsed = time.time() - t0
print(f'\n===== 耗时: {elapsed:.1f}s | 收藏数: {len(by_note)} =====')
if by_note:
    print('*** PASS! ***')
else:
    print('*** FAIL: 未抓到 ***')
