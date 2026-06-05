import { frameUrlMatches } from '../shared/frame-match';

const INJECT_TIMEOUT = 10_000;
const FRAME_POLL_MS = 300;

export async function ensureContentScripts(tabId: number, frameId?: number): Promise<void> {
  const files = ['content/index.js'];
  const target = frameId === undefined ? { tabId } : { tabId, frameIds: [frameId] };
  try {
    await chrome.scripting.executeScript({ target, files });
  } catch {
    /* 可能已注入 */
  }
}

export async function sendToTab<T = unknown>(
  tabId: number,
  message: Record<string, unknown>,
  frameId = 0
): Promise<T> {
  await ensureContentScripts(tabId, frameId);
  return chrome.tabs.sendMessage(tabId, message, { frameId }) as Promise<T>;
}

export async function broadcastRefresh(tabId: number): Promise<void> {
  const frames = await chrome.scripting.executeScript({
    target: { tabId, allFrames: true },
    func: () => {
      document.dispatchEvent(new CustomEvent('__webreplay_refresh_state__'));
    },
  });
  void frames;
}

export async function resolveFrameId(tabId: number, frameUrl: string | undefined): Promise<number> {
  if (!frameUrl) return 0;
  const deadline = Date.now() + INJECT_TIMEOUT;
  while (Date.now() < deadline) {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId, allFrames: true },
        func: () => location.href,
      });
      for (const r of results) {
        if (typeof r.result === 'string' && frameUrlMatches(frameUrl, r.result)) {
          return r.frameId;
        }
      }
    } catch { /* ignore */ }
    await new Promise((r) => setTimeout(r, FRAME_POLL_MS));
  }
  throw new Error(`找不到 URL 匹配的 frame：${frameUrl}`);
}

export async function openOrFocusTab(url: string): Promise<number> {
  const tabs = await chrome.tabs.query({});
  const hit = tabs.find((t) => t.id && t.url && frameUrlMatches(url, t.url));
  if (hit?.id) {
    await chrome.tabs.update(hit.id, { active: true });
    return hit.id;
  }
  const created = await chrome.tabs.create({ url, active: true });
  if (!created.id) throw new Error('无法创建标签页');
  await waitTabComplete(created.id);
  return created.id;
}

function waitTabComplete(tabId: number): Promise<void> {
  return new Promise((resolve) => {
    const listener = (id: number, info: chrome.tabs.TabChangeInfo) => {
      if (id === tabId && info.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, 15_000);
  });
}
