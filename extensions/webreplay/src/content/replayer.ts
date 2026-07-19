import { frameUrlMatches } from '../shared/frame-match';
import { checkSensitiveText } from '../shared/safety';
import { replayDelayMs } from '../shared/replay-timing';
import { isVisible, resolveElement, revealHoverChain } from '../shared/selectors';
import type { Script, ScriptStep } from '../shared/types';

const FIND_TIMEOUT_MS = 30_000;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function sendBg(msg: Record<string, unknown>): void {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

let replaying = false;
let abortFlag = false;

export function isReplaying(): boolean {
  return replaying;
}

export function abortReplay(): void {
  abortFlag = true;
}

async function waitForElement(sel: import('../shared/types').ElementSelector): Promise<Element> {
  const deadline = Date.now() + FIND_TIMEOUT_MS;
  let triedHover = false;
  while (Date.now() < deadline) {
    const el = resolveElement(sel);
    if (el) {
      if (isVisible(el)) return el;
      if (!triedHover) {
        triedHover = true;
        revealHoverChain(el);
        await sleep(400);
        const again = resolveElement(sel);
        if (again && isVisible(again)) return again;
      }
    }
    await sleep(150);
  }
  throw new Error(`超时未找到元素（${FIND_TIMEOUT_MS}ms）：${sel.css.slice(0, 80)}`);
}

async function execClick(step: Extract<ScriptStep, { kind: 'click' }>): Promise<void> {
  const el = await waitForElement(step.selector);
  const text = (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 100);
  const guard = checkSensitiveText(text);
  if (!guard.safe) throw new Error(`安全守护拒绝点击：元素文本含「${guard.matched}」`);
  const rect = el.getBoundingClientRect();
  const init = {
    bubbles: true,
    cancelable: true,
    view: window,
    clientX: rect.left + rect.width / 2,
    clientY: rect.top + rect.height / 2,
  };
  if (typeof PointerEvent !== 'undefined') {
    el.dispatchEvent(new PointerEvent('pointerdown', { ...init, pointerType: 'mouse', isPrimary: true }));
  }
  el.dispatchEvent(new MouseEvent('mousedown', init));
  if (typeof PointerEvent !== 'undefined') {
    el.dispatchEvent(new PointerEvent('pointerup', { ...init, pointerType: 'mouse', isPrimary: true }));
  }
  el.dispatchEvent(new MouseEvent('mouseup', init));
  (el as HTMLElement).click();
}

function setNativeProperty(el: Element, property: 'value' | 'checked', value: string | boolean): void {
  const proto = el instanceof HTMLInputElement
    ? HTMLInputElement.prototype
    : el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLSelectElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, property)?.set;
  if (setter) setter.call(el, value);
  else (el as Element & Record<string, unknown>)[property] = value;
}

async function execInput(step: Extract<ScriptStep, { kind: 'input' }>): Promise<void> {
  const el = await waitForElement(step.selector);
  const control = step.control ?? 'value';
  const contentEditable = el instanceof HTMLElement && el.isContentEditable;
  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement || contentEditable)) {
    throw new Error('目标元素不是输入控件');
  }
  (el as HTMLElement).focus();
  if (control === 'textContent' && contentEditable) {
    el.textContent = step.value;
  } else if (control === 'checked' && el instanceof HTMLInputElement) {
    setNativeProperty(el, 'checked', !!step.checked);
  } else if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) {
    setNativeProperty(el, 'value', step.value);
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

async function execKey(step: Extract<ScriptStep, { kind: 'key' }>): Promise<void> {
  const el = await waitForElement(step.selector);
  if (el instanceof HTMLElement) el.focus();
  const init: KeyboardEventInit = {
    key: step.key,
    code: step.code,
    altKey: step.altKey,
    ctrlKey: step.ctrlKey,
    metaKey: step.metaKey,
    shiftKey: step.shiftKey,
    bubbles: true,
    cancelable: true,
  };
  const shouldRunDefault = el.dispatchEvent(new KeyboardEvent('keydown', init));
  el.dispatchEvent(new KeyboardEvent('keyup', init));
  if (
    step.key === 'Enter' &&
    shouldRunDefault &&
    el instanceof HTMLElement &&
    !(el instanceof HTMLTextAreaElement) &&
    !el.isContentEditable
  ) {
    el.closest('form')?.requestSubmit();
  }
}

async function execScroll(step: Extract<ScriptStep, { kind: 'scroll' }>): Promise<void> {
  if (!step.selector) {
    window.scrollTo({ left: step.x, top: step.y, behavior: 'auto' });
    return;
  }
  const el = await waitForElement(step.selector);
  el.scrollTo({ left: step.x, top: step.y, behavior: 'auto' });
}

async function execWait(step: Extract<ScriptStep, { kind: 'wait' }>): Promise<void> {
  if (step.reason === 'mutation-quiet') {
    await waitMutationQuiet(Math.min(step.timeoutMs, 5000));
  } else {
    await sleep(Math.min(step.timeoutMs, 5000));
  }
}

function waitMutationQuiet(timeoutMs: number, quietMs = 500): Promise<void> {
  return new Promise((resolve) => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const done = () => {
      obs.disconnect();
      if (timer) clearTimeout(timer);
      resolve();
    };
    const bump = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(done, quietMs);
    };
    const obs = new MutationObserver(bump);
    obs.observe(document.body, { childList: true, subtree: true, characterData: true });
    bump();
    setTimeout(done, timeoutMs);
  });
}

async function runStep(step: ScriptStep): Promise<void> {
  switch (step.kind) {
    case 'click':
      return execClick(step);
    case 'input':
      return execInput(step);
    case 'key':
      return execKey(step);
    case 'scroll':
      return execScroll(step);
    case 'wait':
      return execWait(step);
  }
}

export async function runReplay(script: Script, fromIndex: number): Promise<void> {
  if (replaying) return;
  replaying = true;
  abortFlag = false;
  try {
    for (let i = fromIndex; i < script.steps.length; i++) {
      if (abortFlag) {
        sendBg({ type: 'replay/step-failed', index: i, error: '用户中止' });
        return;
      }
      const step = script.steps[i];
      const frameUrl = 'frameUrl' in step ? step.frameUrl : undefined;
      if (!frameUrlMatches(frameUrl, location.href)) {
        sendBg({ type: 'replay/frame-switch', index: i });
        return;
      }
      try {
        const delay = replayDelayMs(i > 0 ? script.steps[i - 1] : undefined, step);
        if (delay) await sleep(delay);
        await runStep(step);
        sendBg({ type: 'replay/step-done', index: i });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        sendBg({ type: 'replay/step-failed', index: i, error: msg });
        return;
      }
    }
    sendBg({ type: 'replay/complete' });
  } finally {
    replaying = false;
  }
}
