import type { RunRecord, Script } from '../shared/types';
import { upsertScript } from './storage';
import { broadcastRefresh, openOrFocusTab, resolveFrameId, sendToTab } from './messaging';

export interface ActiveRun {
  script: Script;
  tabId: number;
  fromIndex: number;
  lastDoneStepIndex: number;
  startedAt: number;
  mode: 'dom';
}

let active: ActiveRun | null = null;

export function getActiveRun(): ActiveRun | null {
  return active;
}

export function getRunStatus(): Record<string, unknown> {
  if (!active) return { running: false };
  return {
    running: true,
    scriptName: active.script.name,
    doneSteps: active.lastDoneStepIndex + 1,
    totalSteps: active.script.steps.length,
    startedAt: active.startedAt,
  };
}

export async function startRun(script: Script, tabId?: number): Promise<ActiveRun> {
  if (active) throw new Error('已有任务在运行');
  const tid = tabId ?? (await openOrFocusTab(script.targetUrl));
  active = {
    script,
    tabId: tid,
    fromIndex: 0,
    lastDoneStepIndex: -1,
    startedAt: Date.now(),
    mode: 'dom',
  };
  await sendToTab(tid, { type: 'replay/start', script, fromIndex: 0 }, 0);
  return active;
}

export async function continueFromFrame(index: number): Promise<void> {
  if (!active) return;
  const step = active.script.steps[index];
  const frameUrl = step && 'frameUrl' in step ? step.frameUrl : undefined;
  const frameId = await resolveFrameId(active.tabId, frameUrl);
  await sendToTab(active.tabId, { type: 'replay/start', script: active.script, fromIndex: index }, frameId);
}

export async function abortRun(): Promise<void> {
  if (!active) return;
  const tabId = active.tabId;
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => document.dispatchEvent(new CustomEvent('__webreplay_abort_replay__')),
    });
    await sendToTab(tabId, { type: 'replay/abort' }, 0);
  } catch { /* ignore */ }
  await finishRun('aborted', active.lastDoneStepIndex, '用户中止');
}

async function finishRun(status: RunRecord['status'], failedAtStep?: number, error?: string): Promise<void> {
  if (!active) return;
  const run: RunRecord = {
    startedAt: active.startedAt,
    endedAt: Date.now(),
    status,
    error,
    failedAtStep,
  };
  const script = { ...active.script, runs: [...(active.script.runs ?? []), run], updatedAt: Date.now() };
  await upsertScript(script);
  const tabId = active.tabId;
  active = null;
  await broadcastRefresh(tabId);
}

export function handleContentReplayMessage(msg: {
  type: string;
  index?: number;
  error?: string;
}): void {
  if (!active) return;
  if (msg.type === 'replay/step-done' && typeof msg.index === 'number') {
    if (msg.index > active.lastDoneStepIndex) active.lastDoneStepIndex = msg.index;
    return;
  }
  if (msg.type === 'replay/complete') {
    void finishRun('success');
    return;
  }
  if (msg.type === 'replay/step-failed') {
    void finishRun('failed', msg.index, msg.error ?? '未知错误');
    return;
  }
  if (msg.type === 'replay/frame-switch' && typeof msg.index === 'number') {
    void continueFromFrame(msg.index);
  }
}
