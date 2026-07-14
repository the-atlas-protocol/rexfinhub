const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 816, height: 1056 } });
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', err => console.log('PAGE ERR:', err.message));
  await page.goto('file:///C:/Projects/rex-asia/_test_trex.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  const info = await page.evaluate(() => {
    const filter = window.SUITE_FILTER;
    const pages = document.querySelectorAll('.page').length;
    return { filter, pages };
  });
  console.log('RESULT:', JSON.stringify(info));
  await browser.close();
})();
