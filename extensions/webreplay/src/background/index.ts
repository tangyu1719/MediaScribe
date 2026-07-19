import { installApiCapture } from './api-capture';
import {
  beginRecording,
  cancelRecording,
  endRecording,
  getRecordingState,
  onRecResponseBody,
  onRecStep,
  queryState,
} from './recording';
import { abortRun, getRunStatus, handleContentReplayMessage, startRun } from './replay-runner';
import { broadcastRefresh, openOrFocusTab } from './messaging';
import { deleteScript, getScript, loadScripts, saveScripts, upsertScript } from './storage';
import type { Script, ScriptsStore } from '../shared/types';

installApiCapture();

const ALARM_PREFIX = 'webreplay:';

async function scheduleAlarms(): Promise<void> {
  const { scripts } = await loadScripts();
  const existing = await chrome.alarms.getAll();
  for (const a of existing) {
    if (a.name.startsWith(ALARM_PREFIX)) await chrome.alarms.clear(a.name);
  }
  for (const s of scripts) {
    const t = s.schedule?.timeOfDay?.trim();
    if (!t || !/^\d{1,2}:\d{2}$/.test(t)) continue;
    const [hh, mm] = t.split(':').map(Number);
    const when = new Date();
    when.setHours(hh, mm, 0, 0);
    if (when.getTime() <= Date.now()) when.setDate(when.getDate() + 1);
    await chrome.alarms.create(`${ALARM_PREFIX}${s.id}`, { when: when.getTime(), periodInMinutes: 24 * 60 });
  }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (!alarm.name.startsWith(ALARM_PREFIX)) return;
  const id = alarm.name.slice(ALARM_PREFIX.length);
  const script = await getScript(id);
  if (!script) return;
  try {
    await startRun(script);
  } catch (e) {
    console.error('[webreplay] scheduled run failed:', e);
  }
});

/** 本地 MCP 风格：外部网页/Agent 通过 chrome.runtime.sendMessage 连接扩展 ID */
chrome.runtime.onMessageExternal.addListener((msg, _sender, sendResponse) => {
  void handleExternal(msg).then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e) }));
  return true;
});

async function handleExternal(msg: { method?: string; params?: Record<string, unknown> }): Promise<unknown> {
  const method = msg?.method;
  const params = msg?.params ?? {};
  switch (method) {
    case 'list_scripts': {
      const { scripts } = await loadScripts();
      return { ok: true, scripts: scripts.map((s) => ({ id: s.id, name: s.name, targetUrl: s.targetUrl, steps: s.steps.length })) };
    }
    case 'run_script': {
      const name = String(params.name ?? '');
      const { scripts } = await loadScripts();
      const script = scripts.find((s) => s.name === name || s.id === name);
      if (!script) return { ok: false, error: `脚本不存在: ${name}` };
      await startRun(script);
      return { ok: true, status: getRunStatus() };
    }
    case 'get_run_status':
      return { ok: true, status: getRunStatus() };
    case 'abort_run':
      await abortRun();
      return { ok: true };
    default:
      return { ok: false, error: `未知 method: ${method}` };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  void routeMessage(msg, sender).then(sendResponse).catch((e) => sendResponse({ error: String(e) }));
  return true;
});

async function routeMessage(
  msg: { type: string; [key: string]: unknown },
  sender: chrome.runtime.MessageSender
): Promise<unknown> {
  switch (msg.type) {
    case 'rec/begin': {
      const tabId = msg.tabId as number;
      const name = String(msg.name ?? '');
      return beginRecording(tabId, name);
    }
    case 'rec/end':
      return endRecording();
    case 'rec/cancel':
      await cancelRecording();
      return { ok: true };
    case 'rec/step':
      await onRecStep(msg.step as import('../shared/types').ScriptStep, sender.tab?.id);
      return { ok: true };
    case 'rec/response-body':
      if (sender.tab?.id !== undefined) {
        onRecResponseBody(sender.tab.id, {
          url: String(msg.url),
          method: String(msg.method),
          startedAt: Number(msg.startedAt),
          status: Number(msg.status),
          contentType: msg.contentType as string | undefined,
          bodyText: msg.bodyText as string | undefined,
          bodyTruncated: msg.bodyTruncated as boolean | undefined,
          bodyOmitted: msg.bodyOmitted as string | undefined,
        });
      }
      return { ok: true };
    case 'state/query':
      return queryState();
    case 'replay/step-done':
    case 'replay/step-failed':
    case 'replay/complete':
    case 'replay/frame-switch':
      handleContentReplayMessage(msg as { type: string; index?: number; error?: string });
      return { ok: true };
    case 'script/list': {
      const { scripts } = await loadScripts();
      return { scripts };
    }
    case 'script/get':
      return { script: await getScript(String(msg.id)) };
    case 'script/delete':
      await deleteScript(String(msg.id));
      await scheduleAlarms();
      return { ok: true };
    case 'script/run': {
      const script = await getScript(String(msg.id));
      if (!script) return { error: '脚本不存在' };
      await startRun(script);
      return { ok: true, status: getRunStatus() };
    }
    case 'script/save': {
      await upsertScript(msg.script as Script);
      await scheduleAlarms();
      return { ok: true };
    }
    case 'script/set-schedule': {
      const script = await getScript(String(msg.id));
      if (!script) return { error: '脚本不存在' };
      script.schedule = { timeOfDay: String(msg.timeOfDay ?? '') };
      script.updatedAt = Date.now();
      await upsertScript(script);
      await scheduleAlarms();
      return { ok: true };
    }
    case 'script/export-all':
      return { store: await loadScripts() };
    case 'script/import': {
      const payload = msg.payload as ScriptsStore;
      if (!payload?.scripts) return { error: '无效导入数据' };
      await saveScripts(payload);
      await scheduleAlarms();
      return { ok: true };
    }
    case 'run/abort':
      await abortRun();
      return { ok: true };
    case 'popup/status': {
      const rec = await getRecordingState();
      const run = getRunStatus();
      const store = await loadScripts();
      return { recording: rec, run, scripts: store.scripts };
    }
    default:
      return { error: `未知消息: ${msg.type}` };
  }
}

chrome.runtime.onInstalled.addListener(() => {
  void scheduleAlarms();
});

console.log('[webreplay] service worker ready');
