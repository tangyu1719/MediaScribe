import type { RecordingSession, Script, ScriptsStore } from '../shared/types';

export const SCRIPTS_KEY = 'webreplay.scripts.v1';
export const SESSION_KEY = 'webreplay.recording.session';

export async function loadScripts(): Promise<ScriptsStore> {
  const raw = (await chrome.storage.local.get(SCRIPTS_KEY))[SCRIPTS_KEY] as ScriptsStore | undefined;
  return raw ?? { scripts: [] };
}

export async function saveScripts(store: ScriptsStore): Promise<void> {
  await chrome.storage.local.set({ [SCRIPTS_KEY]: store });
}

export async function getScript(id: string): Promise<Script | null> {
  const { scripts } = await loadScripts();
  return scripts.find((s) => s.id === id) ?? null;
}

export async function upsertScript(script: Script): Promise<void> {
  const store = await loadScripts();
  const idx = store.scripts.findIndex((s) => s.id === script.id);
  if (idx >= 0) store.scripts[idx] = script;
  else store.scripts.push(script);
  await saveScripts(store);
}

export async function deleteScript(id: string): Promise<void> {
  const store = await loadScripts();
  store.scripts = store.scripts.filter((s) => s.id !== id);
  await saveScripts(store);
}

export async function getRecordingSession(): Promise<RecordingSession | null> {
  return ((await chrome.storage.session.get(SESSION_KEY))[SESSION_KEY] as RecordingSession) ?? null;
}

export async function setRecordingSession(sess: RecordingSession): Promise<void> {
  await chrome.storage.session.set({ [SESSION_KEY]: sess });
}

export async function clearRecordingSession(): Promise<void> {
  await chrome.storage.session.remove(SESSION_KEY);
}
