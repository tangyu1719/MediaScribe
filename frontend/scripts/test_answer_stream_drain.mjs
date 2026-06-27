/**
 * 回归：answer_end 不得一次性贴全文，须按 grapheme 打字机排空 pending。
 * 用法：node scripts/test_answer_stream_drain.mjs
 */
import assert from 'node:assert/strict';

function isChatLoadingPlaceholder(text) {
  return /^(正在处理|正在准备|正在分析任务|正在生成回答|正在识别意图|正在延续|正在检索|正在编排|正在启动)/.test(String(text || ''));
}
function _clearAnswerPlaceholder(aiMsg) {
  if (!aiMsg.content || isChatLoadingPlaceholder(aiMsg.content)) aiMsg.content = '';
}
function _takeOneGrapheme(pending) {
  const s = String(pending || '');
  if (!s) return { piece: '', rest: '' };
  return { piece: s[0], rest: s.slice(1) };
}

const _answerStreamState = new WeakMap();
function _ensureAnswerStreamState(aiMsg) {
  if (!_answerStreamState.has(aiMsg)) {
    _answerStreamState.set(aiMsg, { pending: '', pumpTimer: 0, flushRequested: false, onDrainDone: null });
  }
  return _answerStreamState.get(aiMsg);
}
function _finalizeAnswerStream(aiMsg) {
  const st = _answerStreamState.get(aiMsg);
  const done = st && st.onDrainDone;
  if (st && st.pumpTimer) clearTimeout(st.pumpTimer);
  _answerStreamState.delete(aiMsg);
  if (aiMsg) aiMsg._answerStreaming = false;
  if (typeof done === 'function') done();
}
function _pumpAnswerStreamTick(aiMsg) {
  const st = _ensureAnswerStreamState(aiMsg);
  st.pumpTimer = 0;
  if (!st.pending) {
    if (st.flushRequested) _finalizeAnswerStream(aiMsg);
    return;
  }
  const { piece, rest } = _takeOneGrapheme(st.pending);
  st.pending = rest;
  if (piece) {
    _clearAnswerPlaceholder(aiMsg);
    aiMsg._answerStreaming = true;
    aiMsg.content = (aiMsg.content || '') + piece;
  }
  if (st.pending) {
    st.pumpTimer = setTimeout(() => _pumpAnswerStreamTick(aiMsg), 1);
  } else if (st.flushRequested) {
    _finalizeAnswerStream(aiMsg);
  }
}
function enqueueAnswerStream(aiMsg, chunk) {
  const st = _ensureAnswerStreamState(aiMsg);
  st.pending += String(chunk);
  aiMsg._answerStreaming = true;
  if (!st.pumpTimer) _pumpAnswerStreamTick(aiMsg);
}
function flushAnswerStream(aiMsg, opts) {
  const force = !!(opts && opts.force);
  const st = _answerStreamState.get(aiMsg);
  if (!st) return Promise.resolve();
  if (force) {
    if (st.pumpTimer) clearTimeout(st.pumpTimer);
    if (st.pending) {
      _clearAnswerPlaceholder(aiMsg);
      aiMsg.content = (aiMsg.content || '') + st.pending;
      st.pending = '';
    }
    _finalizeAnswerStream(aiMsg);
    return Promise.resolve();
  }
  st.flushRequested = true;
  if (!st.pending && !st.pumpTimer) {
    _finalizeAnswerStream(aiMsg);
    return Promise.resolve();
  }
  if (!st.pumpTimer) _pumpAnswerStreamTick(aiMsg);
  return new Promise((resolve) => { st.onDrainDone = resolve; });
}

async function testGradualDrain() {
  const aiMsg = { content: '正在启动编排引擎…', _answerStreaming: false };
  enqueueAnswerStream(aiMsg, '你好世界');
  assert.equal(aiMsg.content, '你', '首字应立即可见');
  const st0 = _answerStreamState.get(aiMsg);
  assert.ok(st0.pending.length > 0, 'pending 应仍有剩余');
  await flushAnswerStream(aiMsg);
  assert.equal(aiMsg.content, '你好世界', '排空后应完整');
  assert.equal(aiMsg._answerStreaming, false);
  console.log('testGradualDrain ok');
}

async function testPlaceholderCleared() {
  const aiMsg = { content: '正在启动编排引擎…' };
  _clearAnswerPlaceholder(aiMsg);
  assert.equal(aiMsg.content, '');
  console.log('testPlaceholderCleared ok');
}

await testPlaceholderCleared();
await testGradualDrain();
console.log('all answer stream drain tests passed');
