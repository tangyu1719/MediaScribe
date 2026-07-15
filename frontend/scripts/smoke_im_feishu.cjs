/** 登录后打开设置 → IM 机器人 → 飞书，验证页面可渲染 */
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

  // 侧栏进入 设置 → IM 机器人
  await page.evaluate(() => {
    const settingsBtn = [...document.querySelectorAll('button.ni-parent')].find((b) => (b.textContent || '').includes('设置'));
    if (settingsBtn) settingsBtn.click();
  });
  await new Promise((r) => setTimeout(r, 300));
  await page.evaluate(() => {
    const imBtn = [...document.querySelectorAll('button.ni-sub')].find((b) => (b.textContent || '').includes('IM 机器人'));
    if (imBtn) imBtn.click();
  });
  await page.waitForSelector('.im-platform-grid, .im-intro', { timeout: 15000 });

  // 点击飞书平台卡片
  await page.evaluate(() => {
    const card = document.querySelector('.im-platform-card[data-platform="feishu"], .im-platform-card .im-platform-icon[data-platform="feishu"]');
    const target = card?.closest?.('.im-platform-card') || card;
    if (target) target.click();
  });
  await page.waitForSelector('.im-detail', { timeout: 15000 });

  const result = await page.evaluate(() => ({
    hasWebhook: !!document.body.innerText.match(/Webhook URL/i),
    hasFeishuCfg: document.body.innerText.includes('飞书群消息事件订阅'),
    hasAppId: !!document.querySelector('input[placeholder="cli_xxx"]'),
    imFsTab: document.querySelector('.sn-i')?.innerText || '',
  }));
  console.log('[im-feishu-smoke]', JSON.stringify(result));
  if (errs.length) console.warn('[im-feishu-smoke] page errors:', errs.join(' | '));
  await browser.close();

  if (!result.hasWebhook || !result.hasFeishuCfg) process.exit(2);
})().catch((e) => {
  console.error('[im-feishu-smoke] failed:', e.message || e);
  process.exit(1);
});
