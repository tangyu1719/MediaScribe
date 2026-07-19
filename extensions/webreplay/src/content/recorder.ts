import { checkSensitiveText } from '../shared/safety';
import { snapshotElement } from '../shared/selectors';
import type { InputStep, ScriptStep, ScrollStep } from '../shared/types';
import { isRecordingBarTarget, mountRecordingBar, type RecordingBarApi } from './recording-bar';

const TAG_RECORDING = 'webreplay/recording-state';
const INPUT_DEBOUNCE_MS = 300;
const SCROLL_DEBOUNCE_MS = 220;

type RecMode = 'inactive' | 'active' | 'stopping';
type EditableTarget = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | HTMLElement;

let mode: RecMode = 'inactive';
let stepCount = 0;
let bar: RecordingBarApi | null = null;
let lastClickAt = 0;
let pendingInput: { target: EditableTarget; recordedAt: number; timer: ReturnType<typeof setTimeout> } | null = null;
let pendingScroll: { step: ScrollStep; timer: ReturnType<typeof setTimeout> } | null = null;
const lastInputValue = new WeakMap<Element, string>();
const pendingSends = new Set<Promise<unknown>>();

function postRecordingState(active: boolean): void {
  try {
    window.postMessage({ tag: TAG_RECORDING, active }, window.location.origin);
  } catch { /* ignore */ }
}

function sendStep(step: ScriptStep): void {
  const request = chrome.runtime.sendMessage({ type: 'rec/step', step }).catch(() => undefined);
  pendingSends.add(request);
  void request.finally(() => pendingSends.delete(request));
}

function pushStep(step: ScriptStep): void {
  if (mode !== 'active') return;
  sendStep(step);
  stepCount++;
  bar?.setStepCount(stepCount);
}

function eventElement(ev: Event): Element | null {
  const composed = ev.composedPath?.()[0];
  if (composed instanceof Element) return composed;
  return ev.target instanceof Element ? ev.target : null;
}

function isEditableTarget(target: EventTarget | null): target is EditableTarget {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

function inputStep(target: EditableTarget, recordedAt: number): InputStep | null {
  if (target instanceof HTMLInputElement) {
    if (['password', 'hidden', 'file'].includes(target.type)) return null;
    const checkedControl = target.type === 'checkbox' || target.type === 'radio';
    return {
      kind: 'input',
      selector: snapshotElement(target),
      value: target.value,
      control: checkedControl ? 'checked' : 'value',
      checked: checkedControl ? target.checked : undefined,
      recordedAt,
      frameUrl: location.href,
    };
  }
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
    return {
      kind: 'input',
      selector: snapshotElement(target),
      value: target.value,
      control: 'value',
      recordedAt,
      frameUrl: location.href,
    };
  }
  return {
    kind: 'input',
    selector: snapshotElement(target),
    value: target.textContent ?? '',
    control: 'textContent',
    recordedAt,
    frameUrl: location.href,
  };
}

function captureInput(target: EditableTarget, recordedAt: number): void {
  const step = inputStep(target, recordedAt);
  if (!step) return;
  const signature = `${step.control}\u0000${step.value}\u0000${String(step.checked)}`;
  if (lastInputValue.get(target) === signature) return;
  lastInputValue.set(target, signature);
  pushStep(step);
}

function flushPendingInput(): void {
  const pending = pendingInput;
  if (!pending) return;
  pendingInput = null;
  clearTimeout(pending.timer);
  captureInput(pending.target, pending.recordedAt);
}

function scheduleInput(target: EditableTarget): void {
  if (pendingInput && pendingInput.target !== target) flushPendingInput();
  if (pendingInput) clearTimeout(pendingInput.timer);
  const recordedAt = Date.now();
  const timer = setTimeout(() => {
    if (pendingInput?.target !== target) return;
    pendingInput = null;
    captureInput(target, recordedAt);
  }, INPUT_DEBOUNCE_MS);
  pendingInput = { target, recordedAt, timer };
}

function flushPendingScroll(): void {
  const pending = pendingScroll;
  if (!pending) return;
  pendingScroll = null;
  clearTimeout(pending.timer);
  pushStep(pending.step);
}

function onClick(ev: MouseEvent): void {
  if (!ev.isTrusted || mode !== 'active') return;
  const now = Date.now();
  if (now - lastClickAt < 100) return;
  const target = eventElement(ev);
  if (!target || isRecordingBarTarget(target)) return;
  flushPendingInput();
  flushPendingScroll();
  lastClickAt = now;
  const text = (target.textContent ?? '').slice(0, 2000);
  const guard = checkSensitiveText(text);
  if (!guard.safe && guard.matched) {
    bar?.flashWarning(`⚠️ 录到敏感操作「${guard.matched}」— 重放时会被拒绝`);
  }
  pushStep({
    kind: 'click',
    selector: snapshotElement(target),
    recordedAt: now,
    frameUrl: location.href,
  });
}

function onInput(ev: Event): void {
  if (!ev.isTrusted || mode !== 'active' || !isEditableTarget(ev.target)) return;
  if (isRecordingBarTarget(ev.target)) return;
  scheduleInput(ev.target);
}

function onChange(ev: Event): void {
  if (!ev.isTrusted || mode !== 'active' || !isEditableTarget(ev.target)) return;
  if (isRecordingBarTarget(ev.target)) return;
  if (pendingInput?.target === ev.target) flushPendingInput();
  else captureInput(ev.target, Date.now());
}

function onKeyDown(ev: KeyboardEvent): void {
  if (!ev.isTrusted || mode !== 'active' || ev.repeat || (ev.key !== 'Enter' && ev.key !== 'Escape')) return;
  const target = eventElement(ev);
  if (!target || isRecordingBarTarget(target)) return;
  if (target instanceof HTMLInputElement && target.type === 'password') return;
  flushPendingInput();
  flushPendingScroll();
  pushStep({
    kind: 'key',
    selector: snapshotElement(target),
    key: ev.key,
    code: ev.code,
    altKey: ev.altKey,
    ctrlKey: ev.ctrlKey,
    metaKey: ev.metaKey,
    shiftKey: ev.shiftKey,
    recordedAt: Date.now(),
    frameUrl: location.href,
  });
}

function onScroll(ev: Event): void {
  if (!ev.isTrusted || mode !== 'active') return;
  const raw = ev.target;
  const root = document.scrollingElement;
  const element = raw instanceof Element && raw !== root && raw !== document.documentElement && raw !== document.body
    ? raw
    : null;
  const step: ScrollStep = {
    kind: 'scroll',
    selector: element ? snapshotElement(element) : undefined,
    x: element ? element.scrollLeft : window.scrollX,
    y: element ? element.scrollTop : window.scrollY,
    recordedAt: Date.now(),
    frameUrl: location.href,
  };
  if (pendingScroll) clearTimeout(pendingScroll.timer);
  const timer = setTimeout(() => {
    if (pendingScroll?.step !== step) return;
    pendingScroll = null;
    pushStep(step);
  }, SCROLL_DEBOUNCE_MS);
  pendingScroll = { step, timer };
}

export function attachRecorderListeners(): void {
  const w = window as Window & { __webreplay_listeners_attached__?: boolean };
  if (w.__webreplay_listeners_attached__) return;
  w.__webreplay_listeners_attached__ = true;
  document.addEventListener('click', onClick, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onChange, true);
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('scroll', onScroll, true);
}

export function startRecording(name: string, existingCount: number): void {
  mode = 'active';
  stepCount = existingCount;
  postRecordingState(true);
  if (window.top === window) {
    bar?.remove();
    bar = mountRecordingBar(name, stepCount);
  }
}

export async function stopRecordingAndFlush(): Promise<void> {
  if (mode === 'active') {
    flushPendingInput();
    flushPendingScroll();
  }
  mode = 'stopping';
  while (pendingSends.size) {
    await Promise.all(Array.from(pendingSends));
  }
  stopRecordingUi();
}

export function stopRecordingUi(): void {
  mode = 'inactive';
  if (pendingInput) clearTimeout(pendingInput.timer);
  if (pendingScroll) clearTimeout(pendingScroll.timer);
  pendingInput = null;
  pendingScroll = null;
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
