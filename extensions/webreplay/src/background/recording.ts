import type { Script, ScriptStep } from '../shared/types';
import { attachResponseBody, drainApiHints } from './api-capture';
import { broadcastRefresh, sendToTab } from './messaging';
import { getActiveRun } from './replay-runner';
import {
  clearRecordingSession,
  getRecordingSession,
  setRecordingSession,
  upsertScript,
} from './storage';

let appendChain = Promise.resolve();

function appendStep(step: ScriptStep): void {
  appendChain = appendChain.then(async () => {
    const sess = await getRecordingSession();
    if (!sess) return;
    sess.draft.steps.push(step);
    await setRecordingSession(sess);
    await sendToTab(sess.tabId, { type: 'rec/step-count', count: sess.draft.steps.length }, 0).catch(() => {});
  }).catch((e) => console.error('[webreplay] append step failed:', e));
}

export async function beginRecording(tabId: number, name: string): Promise<{ ok: boolean; error?: string }> {
  const tab = await chrome.tabs.get(tabId);
  const targetUrl = tab.url && !tab.url.startsWith('chrome') ? tab.url : '';
  if (!targetUrl) return { ok: false, error: '请在普通网页标签页中开始录制' };
  const draft = {
    scriptId: crypto.randomUUID(),
    name: name.trim().slice(0, 40) || '未命名脚本',
    targetUrl,
    steps: [] as ScriptStep[],
    startedAt: Date.now(),
  };
  await setRecordingSession({ draft, tabId });
  try {
    await sendToTab(tabId, { type: 'rec/start', name: draft.name, stepCount: 0 }, 0);
  } catch (e) {
    await clearRecordingSession();
    return { ok: false, error: `无法连接页面：${e instanceof Error ? e.message : String(e)}` };
  }
  return { ok: true };
}

export async function endRecording(): Promise<{ ok: boolean; error?: string }> {
  const sess = await getRecordingSession();
  if (!sess) return { ok: false, error: '当前没有录制' };
  try {
    await sendToTab(sess.tabId, { type: 'rec/stop' }, 0);
  } catch { /* ignore */ }
  if (sess.draft.steps.length === 0) {
    await clearRecordingSession();
    return { ok: false, error: '没有录到任何动作，已丢弃' };
  }
  const apiHints = drainApiHints(sess.tabId);
  const script: Script = {
    id: sess.draft.scriptId,
    name: sess.draft.name,
    targetUrl: sess.draft.targetUrl,
    steps: sess.draft.steps,
    apiHints: apiHints.length ? apiHints : undefined,
    schedule: { timeOfDay: '' },
    createdAt: sess.draft.startedAt,
    updatedAt: Date.now(),
    runs: [],
  };
  await upsertScript(script);
  await clearRecordingSession();
  await broadcastRefresh(sess.tabId);
  return { ok: true };
}

export async function cancelRecording(): Promise<void> {
  const sess = await getRecordingSession();
  if (!sess) return;
  try {
    await sendToTab(sess.tabId, { type: 'rec/stop' }, 0);
  } catch { /* ignore */ }
  await clearRecordingSession();
  await broadcastRefresh(sess.tabId);
}

export function onRecStep(step: ScriptStep, originTabId?: number): void {
  if (originTabId !== undefined) step = { ...step, originTabId };
  appendStep(step);
}

export function onRecResponseBody(tabId: number, body: Parameters<typeof attachResponseBody>[1]): void {
  attachResponseBody(tabId, body);
}

export async function getRecordingState(): Promise<{
  recording: boolean;
  name?: string;
  stepCount?: number;
  tabId?: number;
}> {
  const sess = await getRecordingSession();
  if (!sess) return { recording: false };
  return {
    recording: true,
    name: sess.draft.name,
    stepCount: sess.draft.steps.length,
    tabId: sess.tabId,
  };
}

export async function queryState(): Promise<{
  recording: { name: string; stepCount: number } | null;
  replay: { script: Script; fromIndex: number } | null;
}> {
  const sess = await getRecordingSession();
  const run = getActiveRun();
  return {
    recording: sess
      ? { name: sess.draft.name, stepCount: sess.draft.steps.length }
      : null,
    replay: run ? { script: run.script, fromIndex: run.lastDoneStepIndex + 1 } : null,
  };
}
