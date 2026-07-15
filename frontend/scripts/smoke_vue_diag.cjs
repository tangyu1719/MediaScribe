const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

(async () => {
  const chrome = process.env.CHROME;
  if (!chrome) throw new Error('CHROME env required');
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const logs = [];
  page.on('console', (msg) => logs.push(`[${msg.type()}] ${msg.text()}`));
  page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));

  await page.goto('http://127.0.0.1:8000/login.html', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.type('#pwdIdentifier', 'admin');
  await page.type('#pwdPassword', 'admin');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 90000 }),
    page.click('#pwdSubmit'),
  ]);

  // 等待挂载或失败页
  try {
    await page.waitForFunction(
      () => window.__SBA_VUE_MOUNTED__ === true || (document.body && document.body.innerText.includes('应用加载失败')),
      { timeout: 120000 },
    );
  } catch (_) {}

  const diag = await page.evaluate(() => ({
    mounted: !!window.__SBA_VUE_MOUNTED__,
    vue: typeof Vue,
    auth: typeof AuthManager,
    bodyHead: (document.body && document.body.innerText || '').slice(0, 500),
    appHtml: (document.getElementById('app') && document.getElementById('app').innerHTML || '').slice(0, 300),
  }));

  const outDir = path.join(__dirname, '..', '..', 'reports');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'smoke_vue_diag.json'), JSON.stringify({ diag, logs: logs.slice(-40) }, null, 2));

  console.log('[diag]', JSON.stringify(diag, null, 2));
  console.log('[logs last 15]');
  logs.slice(-15).forEach((l) => console.log(l));

  await browser.close();
  if (!diag.mounted) process.exit(1);
})().catch((e) => {
  console.error('[smoke-diag] failed:', e.message || e);
  process.exit(1);
});
