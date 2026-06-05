import { frameUrlMatches } from '../shared/frame-match';
import { checkSensitiveText } from '../shared/safety';
import { isVisible, resolveElement, revealHoverChain } from '../shared/selectors';
import type { Script, ScriptStep } from '../shared/types';

const FIND_TIMEOUT_MS = 30_000;
const STEP_DELAY_MIN = 300;
const STEP_DELAY_MAX = 1500;

function stepDelay(): number {
  return STEP_DELAY_MIN + Math.random() * (STEP_DELAY_MAX - STEP_DELAY_MIN);
}

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
  (el as HTMLElement).click();
}

async function execInput(step: Extract<ScriptStep, { kind: 'input' }>): Promise<void> {
  const el = await waitForElement(step.selector);
  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) {
    throw new Error('目标元素不是输入控件');
  }
  el.focus();
  el.value = step.value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
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
        await runStep(step);
        sendBg({ type: 'replay/step-done', index: i });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        sendBg({ type: 'replay/step-failed', index: i, error: msg });
        return;
      }
      await sleep(stepDelay());
    }
    sendBg({ type: 'replay/complete' });
  } finally {
    replaying = false;
  }
}
