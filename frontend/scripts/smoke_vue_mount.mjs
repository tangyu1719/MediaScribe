/**
 * 无头浏览器冒烟：登录后首页 Vue 须挂载（无 {{ 占位符）
 */
import { chromium } from 'playwright';

const base = process.env.SBA_BASE || 'http://127.0.0.1:8000';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e.message || e)));
page.on('console', (msg) => {
  if (msg.type() === 'error') errors.push(msg.text());
});

await page.goto(base + '/login.html', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.fill('#pwdIdentifier', 'admin');
await page.fill('#pwdPassword', 'admin');
await page.click('#pwdSubmit');
await page.waitForURL((u) => !String(u).includes('login.html'), { timeout: 30000 });
await page.waitForFunction(() => window.__SBA_VUE_MOUNTED__ === true, null, { timeout: 45000 });

const html = await page.content();
if (/\{\{\s*displayedTaskQueue/.test(html)) {
  throw new Error('页面仍含未编译 Vue 占位符');
}
const hasNav = await page.locator('.sidebar, .nav-rail').first().isVisible().catch(() => false);
if (!hasNav) {
  throw new Error('侧边栏未渲染');
}

console.log('[smoke_vue_mount] ok; url=' + page.url());
if (errors.length) {
  console.warn('[smoke_vue_mount] console errors (non-fatal):', errors.slice(0, 5).join(' | '));
}
await browser.close();
