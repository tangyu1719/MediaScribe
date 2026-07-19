import type { Script, ScriptsStore } from '../shared/types';

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} not found`);
  return el;
}

function toast(msg: string): void {
  const t = $('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  setTimeout(() => t.classList.add('hidden'), 2800);
}

async function refresh(): Promise<void> {
  const data = await chrome.runtime.sendMessage({ type: 'popup/status' }) as {
    recording: { recording: boolean; name?: string; stepCount?: number };
    run: { running?: boolean; scriptName?: string };
    scripts: Script[];
  };
  const nameInput = $('script-name') as HTMLInputElement;
  const btnRec = $('btn-start-rec') as HTMLButtonElement;
  const recStatus = $('rec-status');
  const list = $('script-list');
  const empty = $('empty-hint');
  const count = $('script-count');

  const rec = data.recording;
  if (rec.recording) {
    btnRec.textContent = '停止并保存';
    btnRec.classList.add('recording');
    nameInput.disabled = true;
    recStatus.textContent = `录制中：${rec.name}（${rec.stepCount ?? 0} 步）`;
    recStatus.classList.remove('hidden');
  } else {
    btnRec.textContent = '开始录制';
    btnRec.classList.remove('recording');
    nameInput.disabled = false;
    recStatus.classList.add('hidden');
  }

  if (data.run?.running) {
    recStatus.textContent = `重放中：${data.run.scriptName}`;
    recStatus.classList.remove('hidden');
  }

  const scripts = data.scripts ?? [];
  count.textContent = `(${scripts.length})`;
  list.innerHTML = '';
  if (!scripts.length) {
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  for (const s of scripts) {
    const li = document.createElement('li');
    li.innerHTML = `
      <div class="title">${escapeHtml(s.name)}</div>
      <div class="meta">${s.steps.length} 步 · ${new URL(s.targetUrl).hostname}</div>
      <div class="actions"></div>`;
    const actions = li.querySelector('.actions')!;
    const runBtn = document.createElement('button');
    runBtn.textContent = '重放';
    runBtn.className = 'primary';
    runBtn.onclick = async () => {
      try {
        const result = await chrome.runtime.sendMessage({ type: 'script/run', id: s.id }) as { ok?: boolean; error?: string };
        if (result?.error || result?.ok === false) throw new Error(result.error || '重放启动失败');
        toast(`已开始重放：${s.name}`);
        window.close();
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e));
      }
    };
    const delBtn = document.createElement('button');
    delBtn.textContent = '删除';
    delBtn.className = 'danger';
    delBtn.onclick = async () => {
      if (!confirm(`删除脚本「${s.name}」？`)) return;
      await chrome.runtime.sendMessage({ type: 'script/delete', id: s.id });
      await refresh();
    };
    actions.append(runBtn, delBtn);
    list.appendChild(li);
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

$('btn-start-rec').addEventListener('click', async () => {
  const btn = $('btn-start-rec') as HTMLButtonElement;
  btn.disabled = true;
  try {
    const status = await chrome.runtime.sendMessage({ type: 'popup/status' }) as { recording: { recording: boolean } };
    if (status.recording?.recording) {
      const res = await chrome.runtime.sendMessage({ type: 'rec/end' }) as { ok?: boolean; error?: string };
      if (res?.error || res?.ok === false) toast(res.error || '脚本保存失败');
      else toast('脚本已保存');
      await refresh();
      return;
    }
    const name = ( $('script-name') as HTMLInputElement).value.trim();
    if (!name) {
      toast('请输入脚本名称');
      return;
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      toast('无法获取当前标签页');
      return;
    }
    const res = await chrome.runtime.sendMessage({ type: 'rec/begin', tabId: tab.id, name }) as { ok?: boolean; error?: string };
    if (res?.error || res?.ok === false) toast(res.error || '录制启动失败');
    else {
      toast('录制已开始，在页面右下角可点完成');
      await refresh();
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e));
  } finally {
    btn.disabled = false;
  }
});

$('btn-export').addEventListener('click', async () => {
  const { store } = await chrome.runtime.sendMessage({ type: 'script/export-all' }) as { store: ScriptsStore };
  const blob = new Blob([JSON.stringify(store, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `webreplay-scripts-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

($('file-import') as HTMLInputElement).addEventListener('change', async (ev) => {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const text = await file.text();
    const payload = JSON.parse(text) as ScriptsStore;
    await chrome.runtime.sendMessage({ type: 'script/import', payload });
    toast('导入成功');
    await refresh();
  } catch (e) {
    toast('导入失败：' + (e instanceof Error ? e.message : String(e)));
  }
  (ev.target as HTMLInputElement).value = '';
});

void refresh();
