const BAR_ID = '__webreplay_recording_bar';

export interface RecordingBarApi {
  setStepCount(n: number): void;
  flashWarning(msg: string): void;
  remove(): void;
}

export function mountRecordingBar(name: string, stepCount: number): RecordingBarApi {
  document.getElementById(BAR_ID)?.remove();
  const host = document.createElement('div');
  host.id = BAR_ID;
  host.style.cssText =
    'all:initial;position:fixed;bottom:24px;right:24px;z-index:2147483647;';
  const shadow = host.attachShadow({ mode: 'closed' });
  shadow.innerHTML = `
    <style>
      .bar{background:rgba(245,158,11,.96);color:#fff;padding:10px 14px;border-radius:10px;
        box-shadow:0 6px 20px rgba(0,0,0,.25);font:13px/1.25 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;
        display:flex;align-items:center;gap:10px;user-select:none}
      .dot{width:8px;height:8px;border-radius:50%;background:#ef4444;animation:pulse 1.4s ease-in-out infinite}
      @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
      .name{font-weight:600;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .count{font-size:11px;opacity:.9}
      .warn{font-size:11px;margin-top:4px;padding:4px 8px;background:rgba(239,68,68,.95);border-radius:6px;max-width:280px;display:none}
      button{background:rgba(255,255,255,.22);border:0;color:#fff;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:12px}
      button.cancel{background:rgba(0,0,0,.18)}
    </style>
    <div class="bar">
      <div class="dot"></div>
      <div>
        <div class="name"></div>
        <div class="count"></div>
        <div class="warn"></div>
      </div>
      <button class="finish">完成</button>
      <button class="cancel">取消</button>
    </div>`;
  const nameEl = shadow.querySelector('.name') as HTMLElement;
  const countEl = shadow.querySelector('.count') as HTMLElement;
  const warnEl = shadow.querySelector('.warn') as HTMLElement;
  nameEl.textContent = name;
  countEl.textContent = `${stepCount} 步`;
  let warnTimer: ReturnType<typeof setTimeout> | null = null;
  shadow.querySelector('.finish')!.addEventListener('click', () => {
    host.remove();
    chrome.runtime.sendMessage({ type: 'rec/end' }).catch(() => {});
  });
  shadow.querySelector('.cancel')!.addEventListener('click', () => {
    host.remove();
    chrome.runtime.sendMessage({ type: 'rec/cancel' }).catch(() => {});
  });
  document.documentElement.appendChild(host);
  return {
    setStepCount(n) {
      countEl.textContent = `${n} 步`;
    },
    flashWarning(msg: string) {
      warnEl.textContent = msg;
      warnEl.style.display = 'block';
      if (warnTimer) clearTimeout(warnTimer);
      warnTimer = setTimeout(() => {
        warnEl.style.display = 'none';
        warnTimer = null;
      }, 5000);
    },
    remove() {
      if (warnTimer) clearTimeout(warnTimer);
      host.remove();
    },
  };
}

export function isRecordingBarTarget(el: Element | null): boolean {
  return !!el?.closest?.(`#${BAR_ID}`);
}
