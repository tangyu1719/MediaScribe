import type { ElementSelector } from './types';

const STABLE_ATTRS = ['name', 'role', 'aria-label', 'title', 'placeholder', 'type', 'href'] as const;
const HASH_LIKE = /^[a-z]*[-_]?[a-z0-9]{6,}$/i;
const UNSTABLE_DATA = [
  'data-spm-', 'data-track-', 'data-tracker-', 'data-trace-', 'data-monitor-',
  'data-react-', 'data-reactid', 'data-v-', 'data-aurelia-', 'data-ng-',
];

function isUnstableDataAttr(name: string): boolean {
  return UNSTABLE_DATA.some((p) => name.startsWith(p));
}

function normalizeText(el: Element): string | null {
  const t = (el.textContent ?? '').trim().replace(/\s+/g, ' ');
  return t ? t.slice(0, 100) : null;
}

function segment(el: Element): string {
  if (el.id && /^[a-zA-Z_][\w-]*$/.test(el.id) && !HASH_LIKE.test(el.id)) {
    return `#${CSS.escape(el.id)}`;
  }
  let tag = el.tagName.toLowerCase();
  const classes = Array.from(el.classList)
    .filter((c) => /^[a-z][a-z0-9_-]*$/i.test(c) && !HASH_LIKE.test(c) && c.length < 30)
    .slice(0, 2);
  if (classes.length) tag += '.' + classes.map((c) => CSS.escape(c)).join('.');
  for (const attr of Array.from(el.attributes)) {
    if (attr.name.startsWith('data-') && !isUnstableDataAttr(attr.name) && attr.value && attr.value.length < 40) {
      tag += `[${attr.name}="${attr.value.replaceAll('"', '\\"')}"]`;
      break;
    }
  }
  return tag;
}

function buildCss(el: Element): string {
  const parts: string[] = [];
  let node: Element | null = el;
  while (node && node !== document.body && node.nodeType === Node.ELEMENT_NODE) {
    parts.unshift(segment(node));
    if (parts.length >= 3) {
      const sel = parts.join(' > ');
      try {
        if (document.querySelectorAll(sel).length === 1) return sel;
      } catch { /* ignore */ }
    }
    node = node.parentElement;
  }
  return parts.join(' > ');
}

function buildXPath(el: Element): string {
  if (el.id && /^[a-zA-Z_][\w-]*$/.test(el.id)) {
    return `//*[@id="${el.id}"]`;
  }
  const parts: string[] = [];
  let node: Element | null = el;
  while (node && node.nodeType === Node.ELEMENT_NODE && node.parentElement) {
    let idx = 1;
    let sib = node.previousElementSibling;
    while (sib) {
      if (sib.tagName === node.tagName) idx++;
      sib = sib.previousElementSibling;
    }
    parts.unshift(`${node.tagName.toLowerCase()}[${idx}]`);
    node = node.parentElement;
    if (node === document.body) {
      parts.unshift('body');
      break;
    }
  }
  return '//' + parts.join('/');
}

function collectAttributes(el: Element): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of STABLE_ATTRS) {
    const v = el.getAttribute(k);
    if (v) out[k] = v;
  }
  for (const attr of Array.from(el.attributes)) {
    if (attr.name.startsWith('data-') && !isUnstableDataAttr(attr.name) && attr.value) {
      out[attr.name] = attr.value;
    }
  }
  return out;
}

export function snapshotElement(el: Element): ElementSelector {
  return {
    css: buildCss(el),
    xpath: buildXPath(el),
    textContent: normalizeText(el),
    tagName: el.tagName.toLowerCase(),
    attributes: collectAttributes(el),
  };
}

function queryCss(css: string, text: string | null): Element | null {
  try {
    const nodes = document.querySelectorAll(css);
    if (nodes.length === 1) return nodes[0];
    if (nodes.length > 1 && text) {
      for (const n of Array.from(nodes)) {
        if (normalizeText(n) === text) return n;
      }
    }
  } catch { /* ignore */ }
  return null;
}

function queryXPath(xpath: string): Element | null {
  try {
    return document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null)
      .singleNodeValue as Element | null;
  } catch {
    return null;
  }
}

function scoreFallback(el: Element, sel: ElementSelector): number {
  let score = 0;
  if (sel.textContent && normalizeText(el) === sel.textContent) score += 5;
  for (const [k, v] of Object.entries(sel.attributes)) {
    if (el.getAttribute(k) === v) score += 2;
  }
  return score;
}

function resolveByCss(sel: ElementSelector): Element | null {
  let el = queryCss(sel.css, sel.textContent);
  if (el) return el;
  const stripped = sel.css.replace(/\[data-[\w-]+="[^"]*"\]/g, '');
  if (stripped !== sel.css && stripped.trim()) {
    el = queryCss(stripped, sel.textContent);
  }
  return el;
}

export function resolveElement(sel: ElementSelector): Element | null {
  const byCss = resolveByCss(sel);
  if (byCss) return byCss;
  const byXpath = queryXPath(sel.xpath);
  if (byXpath) return byXpath;
  const candidates = document.querySelectorAll(sel.tagName);
  let best: { el: Element; score: number } | null = null;
  for (const el of Array.from(candidates)) {
    const score = scoreFallback(el, sel);
    if (score > 0 && (!best || score > best.score)) best = { el, score };
  }
  return best?.el ?? null;
}

export function isVisible(el: Element): boolean {
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return false;
  const st = getComputedStyle(el);
  return !(st.display === 'none' || st.visibility === 'hidden');
}

export function revealHoverChain(el: Element): void {
  let node: Element | null = el;
  const base = { bubbles: true, cancelable: true, view: window };
  while (node && node !== document.body) {
    node.dispatchEvent(new MouseEvent('mouseover', base));
    node.dispatchEvent(new MouseEvent('mouseenter', { ...base, bubbles: false }));
    if (typeof PointerEvent !== 'undefined') {
      const p = { ...base, pointerType: 'mouse' as const };
      node.dispatchEvent(new PointerEvent('pointerover', p));
      node.dispatchEvent(new PointerEvent('pointerenter', { ...p, bubbles: false }));
    }
    node = node.parentElement;
  }
}
