/** MAIN world：录制时拦截 fetch/XHR 响应体（与 webXport 同思路） */
const TAG_RESPONSE = 'webreplay/fetch-response';
const TAG_RECORDING = 'webreplay/recording-state';

(function init() {
  const w = window as Window & {
    __webreplay_intercepted?: boolean;
    __webreplay_recording?: boolean;
  };
  if (w.__webreplay_intercepted) return;
  w.__webreplay_intercepted = true;
  w.__webreplay_recording = false;

  window.addEventListener('message', (ev) => {
    if (ev.source !== window) return;
    const data = ev.data as { tag?: string; active?: boolean };
    if (data?.tag === TAG_RECORDING) w.__webreplay_recording = !!data.active;
  });

  hookFetch();
  hookXhr();
})();

function shouldCapture(url: string): boolean {
  return !(
    url.startsWith('chrome-extension://') ||
    url.startsWith('data:') ||
    url.startsWith('blob:')
  );
}

function isBinaryContentType(ct: string | null): boolean {
  if (!ct) return false;
  const t = ct.toLowerCase();
  return (
    t.startsWith('image/') ||
    t.startsWith('audio/') ||
    t.startsWith('video/') ||
    t.startsWith('font/') ||
    t.includes('octet-stream') ||
    t.includes('application/pdf') ||
    t.includes('application/zip')
  );
}

function emit(payload: Record<string, unknown>): void {
  try {
    window.postMessage({ tag: TAG_RESPONSE, ...payload }, window.location.origin);
  } catch { /* ignore */ }
}

function hookFetch(): void {
  const orig = window.fetch.bind(window);
  window.fetch = async function (input: RequestInfo | URL, init?: RequestInit) {
    const recording = (window as Window & { __webreplay_recording?: boolean }).__webreplay_recording;
    const startedAt = recording ? Date.now() : 0;
    const res = await orig(input, init);
    if (!recording) return res;
    let url = '';
    let method = 'GET';
    if (typeof input === 'string') {
      url = input;
      method = init?.method ?? 'GET';
    } else if (input instanceof Request) {
      url = input.url;
      method = init?.method ?? input.method ?? 'GET';
    } else {
      url = String(input);
      method = init?.method ?? 'GET';
    }
    try {
      url = new URL(url, location.href).href;
    } catch { /* ignore */ }
    captureResponse(res, url, method, startedAt);
    return res;
  };
}

function captureResponse(res: Response, url: string, method: string, startedAt: number): void {
  if (!shouldCapture(url)) return;
  const payload: Record<string, unknown> = {
    url,
    method,
    startedAt,
    status: res.status,
    contentType: res.headers.get('content-type'),
  };
  if (isBinaryContentType(res.headers.get('content-type'))) {
    payload.bodyOmitted = 'binary';
    emit(payload);
    return;
  }
  res.clone()
    .text()
    .then((text) => {
      if (text.length > 16384) {
        payload.bodyText = text.slice(0, 16384);
        payload.bodyTruncated = true;
      } else {
        payload.bodyText = text;
      }
      emit(payload);
    })
    .catch(() => {
      payload.bodyOmitted = 'error';
      emit(payload);
    });
}

function hookXhr(): void {
  const openOrig = XMLHttpRequest.prototype.open;
  const sendOrig = XMLHttpRequest.prototype.send;
  const meta = new WeakMap<XMLHttpRequest, { url: string; method: string; startedAt: number }>();

  XMLHttpRequest.prototype.open = function (method: string, url: string | URL, ...rest: unknown[]) {
    let abs = String(url);
    try {
      abs = new URL(abs, location.href).href;
    } catch { /* ignore */ }
    meta.set(this, { url: abs, method, startedAt: 0 });
    const [async = true, user, password] = rest as [boolean?, string?, string?];
    return openOrig.call(this, method, url, async, user, password);
  };

  XMLHttpRequest.prototype.send = function (body?: Document | XMLHttpRequestBodyInit | null) {
    const recording = (window as Window & { __webreplay_recording?: boolean }).__webreplay_recording;
    const m = meta.get(this);
    if (recording && m) m.startedAt = Date.now();
    if (recording) {
      this.addEventListener('loadend', () => {
        const info = meta.get(this);
        if (!info || !shouldCapture(info.url)) return;
        const payload: Record<string, unknown> = {
          url: info.url,
          method: info.method,
          startedAt: info.startedAt,
          status: this.status,
          contentType: this.getResponseHeader('content-type'),
        };
        const ct = this.getResponseHeader('content-type');
        if (isBinaryContentType(ct)) {
          payload.bodyOmitted = 'binary';
        } else {
          try {
            const rt = this.responseType;
            let text = '';
            if (rt === '' || rt === 'text') text = this.responseText ?? '';
            else if (rt === 'json' && this.response) text = JSON.stringify(this.response);
            else payload.bodyOmitted = 'binary';
            if (text) {
              if (text.length > 16384) {
                payload.bodyText = text.slice(0, 16384);
                payload.bodyTruncated = true;
              } else payload.bodyText = text;
            }
          } catch {
            payload.bodyOmitted = 'error';
          }
        }
        emit(payload);
      });
    }
    return sendOrig.call(this, body);
  };
}
