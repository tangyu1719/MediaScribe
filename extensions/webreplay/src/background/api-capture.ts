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
const recordingTabs = new Set<number>();
let listenersInstalled = false;

const INTERESTING = /export|download|report|excel|xlsx|csv|query|list|submit|task/i;
const MAX_HINTS = 40;
const MAX_REQUEST_SNIPPET = 2_048;
const MAX_RESPONSE_SNIPPET = 16_384;

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
      if (details.tabId < 0 || !recordingTabs.has(details.tabId)) return;
      const m = tabMap(details.tabId);
      const k = key(details.url, details.method, details.timeStamp);
      m.set(k, {
        url: details.url,
        method: details.method,
        startedAt: details.timeStamp,
        bodyText: requestBodySnippet(details.requestBody),
      });
    },
    { urls: ['<all_urls>'] },
    ['requestBody']
  );

  chrome.webRequest.onCompleted.addListener(
    (details) => {
      if (details.tabId < 0 || !recordingTabs.has(details.tabId)) return;
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

function requestBodySnippet(body: chrome.webRequest.WebRequestBodyDetails['requestBody']): string | undefined {
  if (!body) return undefined;
  try {
    if (body.formData) {
      return JSON.stringify(body.formData).slice(0, MAX_REQUEST_SNIPPET);
    }
    const bytes = body.raw?.[0]?.bytes;
    if (bytes) {
      return new TextDecoder().decode(bytes).slice(0, MAX_REQUEST_SNIPPET);
    }
  } catch {
    /* 请求体可能不是文本 */
  }
  return undefined;
}

/** 开始一次干净的录制窗口，避免混入点击“开始录制”之前的历史请求。 */
export function beginApiCapture(tabId: number): void {
  byTab.delete(tabId);
  recordingTabs.add(tabId);
}

export function cancelApiCapture(tabId: number): void {
  recordingTabs.delete(tabId);
  byTab.delete(tabId);
}

export function attachResponseBody(
  tabId: number,
  hint: { url: string; method: string; startedAt: number; status?: number; contentType?: string; bodyText?: string; bodyTruncated?: boolean; bodyOmitted?: string }
): void {
  if (!recordingTabs.has(tabId)) return;
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
  if (hint.contentType) best.contentType = hint.contentType;
  if (hint.bodyText !== undefined) {
    best.responseBodyText = hint.bodyText;
    if (hint.bodyTruncated) best.responseBodyTruncated = true;
  } else if (hint.bodyOmitted) {
    best.responseBodyOmitted = hint.bodyOmitted;
  }
  if (hint.status !== undefined) best.status = hint.status;
}

export function drainApiHints(tabId: number): ApiRequestHint[] {
  recordingTabs.delete(tabId);
  const m = byTab.get(tabId);
  if (!m) return [];
  byTab.delete(tabId);
  const rows = Array.from(m.values())
    .filter((r) => INTERESTING.test(r.url) || !!r.responseBodyText || (r.method === 'POST' && r.status === 200))
    .sort((a, b) => a.startedAt - b.startedAt)
    .slice(-MAX_HINTS);
  return rows.map((r) => ({
    url: r.url,
    method: r.method,
    startedAt: r.startedAt,
    status: r.status,
    contentType: r.contentType,
    bodySnippet: r.bodyText?.slice(0, MAX_REQUEST_SNIPPET),
    responseSnippet: r.responseBodyText?.slice(0, MAX_RESPONSE_SNIPPET),
  }));
}
