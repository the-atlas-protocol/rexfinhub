const path = require('path');
const pw = path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright');
const { chromium } = require(pw);
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto('http://127.0.0.1:8050/?year=2025', { timeout: 15000 });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: 'dashboard_2025.png', fullPage: true });
  console.log('Done');
  await browser.close();
})();
