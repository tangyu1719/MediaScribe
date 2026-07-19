import { frameUrlMatches } from '../shared/frame-match';
import type { Script } from '../shared/types';
import {
  attachRecorderListeners,
  setModeInactive,
  setStepCount,
  startRecording,
  stopRecordingAndFlush,
  stopRecordingUi,
} from './recorder';
import { abortReplay, isReplaying, runReplay } from './replayer';

const FETCH_HOOK_PATH = 'content/fetch-hook.js';

async function injectFetchHook(): Promise<void> {
  if ((window as Window & { __webreplay_fetch_hook_injected__?: boolean }).__webreplay_fetch_hook_injected__) {
    return;
  }
  // 仅在完整扩展上下文注入；避免 Cursor/其它环境 mock 了 chrome 但无 getURL 时抛错阻断页面
  const rt = typeof chrome !== 'undefined' ? chrome.runtime : undefined;
  if (!rt || typeof rt.getURL !== 'function') {
    return;
  }
  try {
    const src = rt.getURL(FETCH_HOOK_PATH);
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => {
      (window as Window & { __webreplay_fetch_hook_injected__?: boolean }).__webreplay_fetch_hook_injected__ = true;
      s.remove();
    };
    (document.documentElement || document.head).appendChild(s);
  } catch {
    /* 扩展资源不可达时静默跳过，不影响宿主页 */
  }
}

function refreshState(): void {
  const rt = typeof chrome !== 'undefined' ? chrome.runtime : undefined;
  if (!rt || typeof rt.sendMessage !== 'function') {
    stopRecordingUi();
    return;
  }
  rt.sendMessage({ type: 'state/query' }).then((state) => {
    if (!state) {
      stopRecordingUi();
      return;
    }
    if (state.recording) {
      startRecording(state.recording.name, state.recording.stepCount);
    } else {
      stopRecordingUi();
    }
    if (state.replay && !isReplaying()) {
      const script = state.replay.script as Script;
      const step = script.steps[state.replay.fromIndex];
      const frameUrl = step && 'frameUrl' in step ? step.frameUrl : undefined;
      if (frameUrlMatches(frameUrl, location.href)) {
        runReplay(script, state.replay.fromIndex).catch((e) => {
          chrome.runtime.sendMessage({
            type: 'replay/step-failed',
            index: state.replay.fromIndex,
            error: e instanceof Error ? e.message : String(e),
          }).catch(() => {});
        });
      }
    }
  }).catch(() => stopRecordingUi());
}

function installMessageListeners(): void {
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    switch (msg.type) {
    case 'rec/start':
      startRecording(msg.name, msg.stepCount ?? 0);
      sendResponse({ ok: true });
      return false;
    case 'rec/stop':
      void stopRecordingAndFlush()
        .then(() => sendResponse({ ok: true }))
        .catch((e) => sendResponse({ ok: false, error: e instanceof Error ? e.message : String(e) }));
      return true;
    case 'rec/step-count':
      setStepCount(msg.count);
      sendResponse({ ok: true });
      return false;
    case 'replay/start':
      if (!isReplaying()) {
        runReplay(msg.script, msg.fromIndex ?? 0).catch((e) => {
          chrome.runtime.sendMessage({
            type: 'replay/step-failed',
            index: msg.fromIndex ?? 0,
            error: e instanceof Error ? e.message : String(e),
          }).catch(() => {});
        });
      }
      sendResponse({ ok: true });
      return false;
    case 'replay/abort':
      abortReplay();
      sendResponse({ ok: true });
      return false;
      default:
        return false;
    }
  });

  window.addEventListener('message', (ev) => {
    if (ev.source !== window) return;
    const data = ev.data as { tag?: string; url?: string; method?: string; startedAt?: number; status?: number; contentType?: string; bodyText?: string; bodyTruncated?: boolean; bodyOmitted?: string };
    if (data?.tag !== 'webreplay/fetch-response') return;
    chrome.runtime.sendMessage({
      type: 'rec/response-body',
      url: data.url,
      method: data.method,
      startedAt: data.startedAt,
      status: data.status,
      contentType: data.contentType,
      bodyText: data.bodyText,
      bodyTruncated: data.bodyTruncated,
      bodyOmitted: data.bodyOmitted,
    }).catch(() => {});
  });

  document.addEventListener('__webreplay_refresh_state__', refreshState);
  document.addEventListener('__webreplay_abort_replay__', () => abortReplay());
}

const w = window as Window & { __webreplay_content_initialized__?: boolean };
if (!w.__webreplay_content_initialized__) {
  w.__webreplay_content_initialized__ = true;
  installMessageListeners();
  attachRecorderListeners();
  injectFetchHook();
  setModeInactive();
  refreshState();
}
