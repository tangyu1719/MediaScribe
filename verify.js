const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:\\Users\\PC\\AppData\\Local\\ms-playwright\\chromium-1223\\chrome-win64\\chrome.exe',
    headless: false
  });

  const page = await browser.newPage();

  page.on('console', msg => {
    console.log(`[${msg.type()}] ${msg.text()}`);
  });

  page.on('pageerror', error => {
    console.log(`[PAGE ERROR] ${error.message}`);
  });

  page.on('response', response => {
    if (response.status() >= 400) {
      console.log(`[NETWORK] ${response.status()} ${response.url()}`);
    }
  });

  await page.goto('http://localhost:8000/iag');
  await page.waitForTimeout(500);

  // 设置登录token (从之前测试成功的token)
  const token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzAxOWUyYzQzMTFhODdkOTc4MmZhYjU2ODQyMzY4ZTQ5Iiwicm9sZXMiOlsiYWRtaW4iXSwiaWF0IjoxNzc5MDkzNTg2LCJleHAiOjE3NzkxNzk5ODZ9.Nlx93bs1cmt7Rdu2slKM2XkClY4Z9maRntqEL0rtd-M';
  await page.evaluate((t) => {
    localStorage.setItem('sba_token', t);
    localStorage.setItem('sba_user', JSON.stringify({
      id: 'user_019e2c4311a87d9782fab56842368e49',
      username: 'admin',
      roles: ['admin']
    }));
  }, token);

  await page.reload();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);

  // 获取HTML内容
  const html = await page.content();
  console.log('\n=== 页面HTML (部分) ===');
  console.log(html.substring(html.indexOf('<div id="app"'), html.indexOf('<div id="app"') + 2000));

  // 检查菜单
  const navButtons = await page.$$eval('.ni', btns => btns.map(b => b.textContent.trim()));
  console.log('\n=== 导航按钮 ===');
  console.log(navButtons);

  await browser.close();
})();
