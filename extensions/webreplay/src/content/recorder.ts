import { checkSensitiveText } from '../shared/safety';
import { snapshotElement } from '../shared/selectors';
import type { ScriptStep } from '../shared/types';
import { isRecordingBarTarget, mountRecordingBar, type RecordingBarApi } from './recording-bar';

const TAG_RECORDING = 'webreplay/recording-state';

type RecMode = 'inactive' | 'active' | 'unknown';

let mode: RecMode = 'unknown';
let buffer: ScriptStep[] = [];
let stepCount = 0;
let bar: RecordingBarApi | null = null;
let lastClickAt = 0;

function postRecordingState(active: boolean): void {
  try {
    window.postMessage({ tag: TAG_RECORDING, active }, window.location.origin);
  } catch { /* ignore */ }
}

function pushStep(step: ScriptStep): void {
  if (mode === 'active') {
    chrome.runtime.sendMessage({ type: 'rec/step', step }).catch(() => {});
    stepCount++;
    bar?.setStepCount(stepCount);
  } else {
    buffer.push(step);
  }
}

function onClick(ev: MouseEvent): void {
  if (!ev.isTrusted || mode === 'inactive') return;
  const now = Date.now();
  if (now - lastClickAt < 100) return;
  lastClickAt = now;
  const target = ev.target;
  if (!(target instanceof Element) || isRecordingBarTarget(target)) return;
  const text = (target.textContent ?? '').slice(0, 2000);
  const guard = checkSensitiveText(text);
  if (!guard.safe && guard.matched) {
    bar?.flashWarning(`⚠️ 录到敏感操作「${guard.matched}」— 重放时会被拒绝`);
  }
  pushStep({
    kind: 'click',
    selector: snapshotElement(target),
    recordedAt: Date.now(),
    frameUrl: location.href,
  });
}

function onChange(ev: Event): void {
  if (!(ev as Event & { isTrusted?: boolean }).isTrusted || mode === 'inactive') return;
  const target = ev.target;
  if (
    !(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement)
  ) {
    return;
  }
  if (isRecordingBarTarget(target)) return;
  if (target instanceof HTMLInputElement && (target.type === 'password' || target.type === 'hidden')) return;
  if (Date.now() - lastClickAt < 200) return;
  pushStep({
    kind: 'input',
    selector: snapshotElement(target),
    value: target.value,
    recordedAt: Date.now(),
    frameUrl: location.href,
  });
}

export function attachRecorderListeners(): void {
  const w = window as Window & { __webreplay_listeners_attached__?: boolean };
  if (w.__webreplay_listeners_attached__) return;
  w.__webreplay_listeners_attached__ = true;
  document.addEventListener('click', onClick, true);
  document.addEventListener('change', onChange, true);
}

export function startRecording(name: string, existingCount: number): void {
  mode = 'active';
  stepCount = existingCount;
  for (const s of buffer) {
    chrome.runtime.sendMessage({ type: 'rec/step', step: s }).catch(() => {});
    stepCount++;
  }
  buffer = [];
  postRecordingState(true);
  if (window.top === window) {
    bar?.remove();
    bar = mountRecordingBar(name, stepCount);
  }
}

export function stopRecordingUi(): void {
  mode = 'inactive';
  buffer = [];
  stepCount = 0;
  bar?.remove();
  bar = null;
  postRecordingState(false);
}

export function setStepCount(n: number): void {
  stepCount = n;
  bar?.setStepCount(n);
}

export function setModeInactive(): void {
  mode = 'inactive';
}
