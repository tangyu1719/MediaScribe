import type { Script, ScriptStep } from '../shared/types';
import { attachResponseBody, beginApiCapture, cancelApiCapture, drainApiHints } from './api-capture';
import { broadcastRefresh, sendToAllFrames, sendToTab } from './messaging';
import { getActiveRun } from './replay-runner';
import {
  clearRecordingSession,
  getRecordingSession,
  setRecordingSession,
  upsertScript,
} from './storage';

let appendChain = Promise.resolve();

function appendStep(step: ScriptStep, originTabId?: number): Promise<void> {
  appendChain = appendChain.then(async () => {
    const sess = await getRecordingSession();
    if (!sess || (originTabId !== undefined && originTabId !== sess.tabId)) return;
    sess.draft.steps.push(step);
    await setRecordingSession(sess);
    await sendToTab(sess.tabId, { type: 'rec/step-count', count: sess.draft.steps.length }, 0).catch(() => {});
  }).catch((e) => console.error('[webreplay] append step failed:', e));
  return appendChain;
}

export async function beginRecording(tabId: number, name: string): Promise<{ ok: boolean; error?: string }> {
  await appendChain;
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
  beginApiCapture(tabId);
  try {
    // 先完成插件 -> 当前网页的明确启动握手；返回成功时顶层页面已进入录制态。
    await sendToTab(tabId, { type: 'rec/start', name: draft.name, stepCount: 0 }, 0);
  } catch (e) {
    cancelApiCapture(tabId);
    await clearRecordingSession();
    return { ok: false, error: `无法连接页面：${e instanceof Error ? e.message : String(e)}` };
  }
  // 再同步现有 iframe；子 frame 同步失败不应推翻已成功的顶层录制。
  await broadcastRefresh(tabId).catch((e) => {
    console.warn('[webreplay] iframe recording state sync failed:', e);
  });
  return { ok: true };
}

export async function endRecording(): Promise<{ ok: boolean; error?: string }> {
  let sess = await getRecordingSession();
  if (!sess) return { ok: false, error: '当前没有录制' };
  try {
    await sendToAllFrames(sess.tabId, { type: 'rec/stop' });
  } catch { /* ignore */ }
  await appendChain;
  sess = await getRecordingSession();
  if (!sess) return { ok: false, error: '录制会话已结束' };
  if (sess.draft.steps.length === 0) {
    cancelApiCapture(sess.tabId);
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
    await sendToAllFrames(sess.tabId, { type: 'rec/stop' });
  } catch { /* ignore */ }
  await appendChain;
  cancelApiCapture(sess.tabId);
  await clearRecordingSession();
  await broadcastRefresh(sess.tabId);
}

export function onRecStep(step: ScriptStep, originTabId?: number): Promise<void> {
  if (originTabId !== undefined) step = { ...step, originTabId };
  return appendStep(step, originTabId);
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
