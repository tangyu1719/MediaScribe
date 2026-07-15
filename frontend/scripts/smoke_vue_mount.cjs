const puppeteer = require('puppeteer-core');

(async () => {
  const chrome = process.env.CHROME;
  if (!chrome) throw new Error('CHROME env required');
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(e.message));
  await page.goto('http://127.0.0.1:8000/login.html', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.type('#pwdIdentifier', 'admin');
  await page.type('#pwdPassword', 'admin');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 90000 }),
    page.click('#pwdSubmit'),
  ]);
  await page.waitForFunction(() => window.__SBA_VUE_MOUNTED__ === true, { timeout: 90000 });
  const result = await page.evaluate(() => ({
    hasMustache: document.body.innerText.includes('{{'),
    hasNav: !!document.querySelector('.sidebar,.nav-rail'),
    title: document.title,
  }));
  console.log('[smoke]', JSON.stringify(result));
  if (errs.length) console.warn('[smoke] page errors:', errs.slice(0, 3).join(' | '));
  await browser.close();
  if (result.hasMustache) process.exit(2);
  if (!result.hasNav) process.exit(3);
})().catch((e) => {
  console.error('[smoke] failed:', e.message || e);
  process.exit(1);
});
