import type { ApiRequestHint } from '../shared/types';

interface CapturedRequest {
  url: string;
  method: string;
  startedAt: number;
  status?: number;
  contentType?: string;
  bodyText?: string;
  responseBodyText?: string;
  responseBodyTruncated?: boolean;
  responseBodyOmitted?: string;
}

const byTab = new Map<number, Map<string, CapturedRequest>>();
let listenersInstalled = false;

const INTERESTING = /export|download|report|excel|xlsx|csv|query|list|submit|task/i;

function tabMap(tabId: number): Map<string, CapturedRequest> {
  let m = byTab.get(tabId);
  if (!m) {
    m = new Map();
    byTab.set(tabId, m);
  }
  return m;
}

function key(url: string, method: string, startedAt: number): string {
  return `${method}:${url}:${startedAt}`;
}

export function installApiCapture(): void {
  if (listenersInstalled) return;
  listenersInstalled = true;

  chrome.webRequest.onBeforeRequest.addListener(
    (details) => {
      if (details.tabId < 0) return;
      const m = tabMap(details.tabId);
      const k = key(details.url, details.method, details.timeStamp);
      m.set(k, {
        url: details.url,
        method: details.method,
        startedAt: details.timeStamp,
      });
    },
    { urls: ['<all_urls>'] },
    ['requestBody']
  );

  chrome.webRequest.onCompleted.addListener(
    (details) => {
      if (details.tabId < 0) return;
      const m = tabMap(details.tabId);
      let hit: CapturedRequest | undefined;
      for (const r of m.values()) {
        if (r.url === details.url && r.method === details.method) {
          hit = r;
          break;
        }
      }
      if (!hit) {
        hit = { url: details.url, method: details.method, startedAt: details.timeStamp };
        m.set(key(details.url, details.method, details.timeStamp), hit);
      }
      hit.status = details.statusCode;
    },
    { urls: ['<all_urls>'] }
  );
}

export function attachResponseBody(
  tabId: number,
  hint: { url: string; method: string; startedAt: number; status?: number; bodyText?: string; bodyTruncated?: boolean; bodyOmitted?: string }
): void {
  const m = tabMap(tabId);
  let best: CapturedRequest | null = null;
  let bestDelta = Infinity;
  for (const r of m.values()) {
    if (r.method !== hint.method || r.url !== hint.url) continue;
    const d = Math.abs(r.startedAt - hint.startedAt);
    if (d < 5000 && d < bestDelta) {
      bestDelta = d;
      best = r;
    }
  }
  if (!best) return;
  if (hint.bodyText !== undefined) {
    best.responseBodyText = hint.bodyText;
    if (hint.bodyTruncated) best.responseBodyTruncated = true;
  } else if (hint.bodyOmitted) {
    best.responseBodyOmitted = hint.bodyOmitted;
  }
  if (hint.status !== undefined) best.status = hint.status;
}

export function drainApiHints(tabId: number): ApiRequestHint[] {
  const m = byTab.get(tabId);
  if (!m) return [];
  byTab.delete(tabId);
  const rows = Array.from(m.values())
    .filter((r) => INTERESTING.test(r.url) || (r.method === 'POST' && (r.responseBodyText || r.status === 200)))
    .sort((a, b) => a.startedAt - b.startedAt)
    .slice(-40);
  return rows.map((r) => ({
    url: r.url,
    method: r.method,
    startedAt: r.startedAt,
    status: r.status,
    contentType: r.contentType,
    bodySnippet: r.bodyText?.slice(0, 500),
    responseSnippet: r.responseBodyText?.slice(0, 500),
  }));
}
