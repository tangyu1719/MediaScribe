"""WebReplay CDP 注入脚本（与 extensions/webreplay 选择器/录制逻辑对齐）。"""

# 录制 + 重放共用：元素快照与解析（content script 同思路，非 CV）
SELECTOR_RUNTIME_JS = r"""
(() => {
  const STABLE_ATTRS = ['name','role','aria-label','title','placeholder','type','href'];
  const HASH_LIKE = /^[a-z]*[-_]?[a-z0-9]{6,}$/i;
  const UNSTABLE_DATA = ['data-spm-','data-track-','data-tracker-','data-trace-','data-monitor-','data-react-','data-reactid','data-v-','data-aurelia-','data-ng-'];
  function isUnstableDataAttr(name) {
    return UNSTABLE_DATA.some(p => name.startsWith(p));
  }
  function normalizeText(el) {
    const t = (el.textContent || '').trim().replace(/\s+/g, ' ');
    return t ? t.slice(0, 100) : null;
  }
  function segment(el) {
    if (el.id && /^[a-zA-Z_][\w-]*$/.test(el.id) && !HASH_LIKE.test(el.id)) {
      return '#' + CSS.escape(el.id);
    }
    let tag = el.tagName.toLowerCase();
    const classes = Array.from(el.classList)
      .filter(c => /^[a-z][a-z0-9_-]*$/i.test(c) && !HASH_LIKE.test(c) && c.length < 30)
      .slice(0, 2);
    if (classes.length) tag += '.' + classes.map(c => CSS.escape(c)).join('.');
    for (const attr of Array.from(el.attributes)) {
      if (attr.name.startsWith('data-') && !isUnstableDataAttr(attr.name) && attr.value && attr.value.length < 40) {
        tag += `[${attr.name}="${attr.value.replaceAll('"', '\\"')}"]`;
        break;
      }
    }
    return tag;
  }
  function buildCss(el) {
    const parts = [];
    let node = el;
    while (node && node !== document.body && node.nodeType === Node.ELEMENT_NODE) {
      parts.unshift(segment(node));
      if (parts.length >= 3) {
        const sel = parts.join(' > ');
        try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (_) {}
      }
      node = node.parentElement;
    }
    return parts.join(' > ');
  }
  function buildXPath(el) {
    if (el.id && /^[a-zA-Z_][\w-]*$/.test(el.id)) return `//*[@id="${el.id}"]`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && node.parentElement) {
      let idx = 1;
      let sib = node.previousElementSibling;
      while (sib) { if (sib.tagName === node.tagName) idx++; sib = sib.previousElementSibling; }
      parts.unshift(`${node.tagName.toLowerCase()}[${idx}]`);
      node = node.parentElement;
      if (node === document.body) { parts.unshift('body'); break; }
    }
    return '//' + parts.join('/');
  }
  function collectAttributes(el) {
    const out = {};
    for (const k of STABLE_ATTRS) {
      const v = el.getAttribute(k);
      if (v) out[k] = v;
    }
    for (const attr of Array.from(el.attributes)) {
      if (attr.name.startsWith('data-') && !isUnstableDataAttr(attr.name) && attr.value) out[attr.name] = attr.value;
    }
    return out;
  }
  function snapshotElement(el) {
    const rect = el.getBoundingClientRect();
    return {
      css: buildCss(el),
      xpath: buildXPath(el),
      textContent: normalizeText(el),
      tagName: el.tagName.toLowerCase(),
      attributes: collectAttributes(el),
      viewport: {
        x: Math.round(rect.x + rect.width / 2),
        y: Math.round(rect.y + rect.height / 2),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
    };
  }
  function queryCss(css, text) {
    try {
      const nodes = document.querySelectorAll(css);
      if (nodes.length === 1) return nodes[0];
      if (nodes.length > 1 && text) {
        for (const n of Array.from(nodes)) if (normalizeText(n) === text) return n;
      }
    } catch (_) {}
    return null;
  }
  function queryXPath(xpath) {
    try {
      return document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    } catch (_) { return null; }
  }
  function scoreFallback(el, sel) {
    let score = 0;
    if (sel.textContent && normalizeText(el) === sel.textContent) score += 5;
    for (const [k, v] of Object.entries(sel.attributes || {})) {
      if (el.getAttribute(k) === v) score += 2;
    }
    return score;
  }
  function resolveByCss(sel) {
    let el = queryCss(sel.css, sel.textContent);
    if (el) return el;
    const stripped = sel.css.replace(/\[data-[\w-]+="[^"]*"\]/g, '');
    if (stripped !== sel.css && stripped.trim()) el = queryCss(stripped, sel.textContent);
    return el;
  }
  function resolveElement(sel) {
    const byCss = resolveByCss(sel);
    if (byCss) return byCss;
    const byXpath = queryXPath(sel.xpath);
    if (byXpath) return byXpath;
    const candidates = document.querySelectorAll(sel.tagName || '*');
    let best = null;
    for (const el of Array.from(candidates)) {
      const score = scoreFallback(el, sel);
      if (score > 0 && (!best || score > best.score)) best = { el, score };
    }
    return best ? best.el : null;
  }
  function isVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const st = getComputedStyle(el);
    return !(st.display === 'none' || st.visibility === 'hidden');
  }
  function revealHoverChain(el) {
    let node = el;
    const base = { bubbles: true, cancelable: true, view: window };
    while (node && node !== document.body) {
      node.dispatchEvent(new MouseEvent('mouseover', base));
      node.dispatchEvent(new MouseEvent('mouseenter', { ...base, bubbles: false }));
      node = node.parentElement;
    }
  }
  return { snapshotElement, resolveElement, isVisible, revealHoverChain, normalizeText };
})();
"""

RECORDER_INIT_JS = r"""
(sessionId) => {
  if (window.__webreplay_cdp_recorder__) return window.__webreplay_cdp_recorder__.sessionId;
  const api = %SELECTOR_RUNTIME%;
  const SENSITIVE_ZH = ['删除','移除','注销','解绑','退款','退订','退货','支付','付款','充值','购买','下单','提交订单','立即购买','确认提交','确认删除','确认支付','确认下单','清空','解散','停用','禁用'];
  const SENSITIVE_EN = ['delete','remove','cancel subscription','unsubscribe','pay now','purchase','place order','buy now','checkout','confirm delete','confirm payment','confirm submit','deactivate','disable account','wipe'];
  function checkSensitive(text) {
    if (!text) return { safe: true };
    const lower = text.toLowerCase();
    for (const w of SENSITIVE_ZH) if (text.includes(w)) return { safe: false, matched: w };
    for (const w of SENSITIVE_EN) if (lower.includes(w)) return { safe: false, matched: w };
    return { safe: true };
  }
  let active = true;
  let steps = [];
  let lastClickAt = 0;
  const BAR_ID = '__webreplay_cdp_bar__';
  function ensureBar() {
    if (window.top !== window) return;
    let bar = document.getElementById(BAR_ID);
    if (bar) return bar;
    bar = document.createElement('div');
    bar.id = BAR_ID;
    bar.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483646;padding:10px 14px;background:#fbbf24;color:#111827;border-radius:8px;font:13px/1.4 system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.2)';
    bar.innerHTML = '<b>WebReplay CDP 录制中</b><br><span id="__wr_cdp_cnt__">0</span> 步 · 在管理页点「完成录制」';
    document.documentElement.appendChild(bar);
    return bar;
  }
  function bumpCount() {
    const el = document.getElementById('__wr_cdp_cnt__');
    if (el) el.textContent = String(steps.length);
  }
  function pushStep(step) {
    if (!active) return;
    steps.push(step);
    bumpCount();
  }
  function onClick(ev) {
    if (!ev.isTrusted || !active) return;
    const now = Date.now();
    if (now - lastClickAt < 100) return;
    lastClickAt = now;
    const target = ev.target;
    if (!(target instanceof Element)) return;
    if (target.closest && target.closest('#' + BAR_ID)) return;
    const text = (target.textContent || '').slice(0, 2000);
    const guard = checkSensitive(text);
    pushStep({
      kind: 'click',
      selector: api.snapshotElement(target),
      recordedAt: Date.now(),
      frameUrl: location.href,
      sensitiveWarning: guard.safe ? undefined : guard.matched,
    });
  }
  function onChange(ev) {
    if (!ev.isTrusted || !active) return;
    const target = ev.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement)) return;
    if (target instanceof HTMLInputElement && (target.type === 'password' || target.type === 'hidden')) return;
    if (Date.now() - lastClickAt < 200) return;
    pushStep({
      kind: 'input',
      selector: api.snapshotElement(target),
      value: target.value,
      recordedAt: Date.now(),
      frameUrl: location.href,
    });
  }
  document.addEventListener('click', onClick, true);
  document.addEventListener('change', onChange, true);
  ensureBar();
  bumpCount();
  window.__webreplay_cdp_recorder__ = {
    sessionId,
    getSteps() { return steps.slice(); },
    getStepCount() { return steps.length; },
    stop() { active = false; document.removeEventListener('click', onClick, true); document.removeEventListener('change', onChange, true); const b = document.getElementById(BAR_ID); if (b) b.remove(); return steps.slice(); },
    isActive() { return active; },
  };
  return sessionId;
}
""".replace("%SELECTOR_RUNTIME%", SELECTOR_RUNTIME_JS)

RECORDER_POLL_JS = r"""
() => {
  const r = window.__webreplay_cdp_recorder__;
  if (!r) return { active: false, steps: [], count: 0, frameUrl: location.href };
  return { active: r.isActive(), steps: r.getSteps(), count: r.getStepCount(), frameUrl: location.href };
}
"""

RECORDER_STOP_JS = r"""
() => {
  const r = window.__webreplay_cdp_recorder__;
  if (!r) return { steps: [], frameUrl: location.href };
  const steps = r.stop();
  delete window.__webreplay_cdp_recorder__;
  return { steps, frameUrl: location.href };
}
"""

REPLAY_ONE_STEP_JS = r"""
async (step) => {
  const api = %SELECTOR_RUNTIME%;
  const SENSITIVE_ZH = ['删除','移除','注销','解绑','退款','退订','退货','支付','付款','充值','购买','下单','提交订单','立即购买','确认提交','确认删除','确认支付','确认下单','清空','解散','停用','禁用'];
  const SENSITIVE_EN = ['delete','remove','cancel subscription','unsubscribe','pay now','purchase','place order','buy now','checkout','confirm delete','confirm payment','confirm submit','deactivate','disable account','wipe'];
  function checkSensitive(text) {
    if (!text) return { safe: true };
    const lower = text.toLowerCase();
    for (const w of SENSITIVE_ZH) if (text.includes(w)) return { safe: false, matched: w };
    for (const w of SENSITIVE_EN) if (lower.includes(w)) return { safe: false, matched: w };
    return { safe: true };
  }
  const FIND_TIMEOUT_MS = 30000;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  async function waitForElement(sel) {
    const deadline = Date.now() + FIND_TIMEOUT_MS;
    let triedHover = false;
    while (Date.now() < deadline) {
      const el = api.resolveElement(sel);
      if (el) {
        if (api.isVisible(el)) return el;
        if (!triedHover) {
          triedHover = true;
          api.revealHoverChain(el);
          await sleep(400);
          const again = api.resolveElement(sel);
          if (again && api.isVisible(again)) return again;
        }
      }
      await sleep(150);
    }
    throw new Error('超时未找到元素：' + (sel.css || '').slice(0, 80));
  }
  if (step.kind === 'click') {
    const el = await waitForElement(step.selector);
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100);
    const guard = checkSensitive(text);
    if (!guard.safe) throw new Error('安全守护拒绝点击：' + guard.matched);
    el.click();
    return { ok: true };
  }
  if (step.kind === 'input') {
    const el = await waitForElement(step.selector);
    if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) {
      throw new Error('目标元素不是输入控件');
    }
    el.focus();
    el.value = step.value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true };
  }
  if (step.kind === 'wait') {
    await sleep(Math.min(step.timeoutMs || 500, 5000));
    return { ok: true };
  }
  throw new Error('未知步骤类型：' + step.kind);
}
""".replace("%SELECTOR_RUNTIME%", SELECTOR_RUNTIME_JS)
