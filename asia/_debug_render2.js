const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 816, height: 1056 } });
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', err => console.log('PAGE ERR:', err.message));

  let html = fs.readFileSync('report_v15.html', 'utf8');
  const enriched = fs.readFileSync('enriched_report_data_mar.json', 'utf8');
  html = html.replace('const REPORT_DATA = null; // INJECTED AT BUILD TIME', `const REPORT_DATA = ${enriched};`);
  html = html.replace('const SUITE_FILTER = null;', `const SUITE_FILTER = 'T-REX';`);
  fs.writeFileSync('_inject_trex.html', html, 'utf8');

  await page.goto('file:///C:/Projects/rex-asia/_inject_trex.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  const info = await page.evaluate(() => {
    const filter = window.SUITE_FILTER;
    const pageCount = document.querySelectorAll('.page').length;
    const pageTitles = Array.from(document.querySelectorAll('.page .title, .page h1')).map(e => e.textContent.substring(0, 50));
    return { filter, pageCount, pageTitles };
  });
  console.log('RESULT:', JSON.stringify(info, null, 2));
  await browser.close();
})();
