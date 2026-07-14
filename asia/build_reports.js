/**
 * Build Reports — generates full + suite-specific PDFs from the report template.
 *
 * Usage: NODE_PATH="C:/Users/RyuEl-Asmar/AppData/Roaming/npm/node_modules" node build_reports.js
 *
 * Edit MONTH below each period. Output goes to reports/YYYY-MM/.
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

// ── Config: pass via CLI args, e.g.: node build_reports.js 2026-03 Mar26 ──
const MONTH = process.argv[2] || '2026-03';
const MONTH_LABEL = process.argv[3] || 'Mar26';
const ENRICHED_JSON = process.argv[4] || 'enriched_report_data.json';
const HTML_SOURCE = 'C:/Projects/rexfinhub/asia/report_v15.html';
// ─────────────────────────────────────
console.log(`Building reports for ${MONTH} (${MONTH_LABEL}), source=${ENRICHED_JSON}`);

const OUT_DIR = path.join('C:/Projects/rexfinhub/asia/reports', MONTH);
const TEMP_DIR = 'C:/Projects/rexfinhub/asia/temp';

const BUILDS = [
  { filter: null,             filename: `REX_Asia_Report_${MONTH_LABEL}.pdf` },
  { filter: "'T-REX'",        filename: `REX_TREX_Asia_Report_${MONTH_LABEL}.pdf` },
  { filter: "'MicroSectors'", filename: `REX_MicroSectors_Asia_Report_${MONTH_LABEL}.pdf` }
];

(async () => {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });
  if (!fs.existsSync(TEMP_DIR)) fs.mkdirSync(TEMP_DIR, { recursive: true });

  const htmlSource = fs.readFileSync(HTML_SOURCE, 'utf8');
  const browser = await chromium.launch();

  for (const build of BUILDS) {
    const label = build.filter || 'full';
    console.log(`Building ${label}...`);

    let html = htmlSource;
    // Inject enriched report data
    const enrichedPath = path.resolve(ENRICHED_JSON);
    if (fs.existsSync(enrichedPath)) {
      const enrichedData = fs.readFileSync(enrichedPath, 'utf8');
      html = html.replace(
        'const REPORT_DATA = null; // INJECTED AT BUILD TIME',
        `const REPORT_DATA = ${enrichedData};`
      );
    } else {
      console.warn('  WARNING: enriched_report_data.json not found — using hardcoded data');
    }
    if (build.filter) {
      html = html.replace(
        'const SUITE_FILTER = null;',
        `const SUITE_FILTER = ${build.filter};`
      );
    }

    const tempFile = path.join(TEMP_DIR, `report_${label.replace(/'/g, '')}.html`);
    fs.writeFileSync(tempFile, html, 'utf8');

    const page = await browser.newPage({ viewport: { width: 816, height: 1056 } });
    await page.goto(`file:///${tempFile.replace(/\\/g, '/')}`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);

    const outPath = path.join(OUT_DIR, build.filename);
    await page.pdf({
      path: outPath,
      width: '8.5in', height: '11in',
      printBackground: true,
      margin: { top: 0, bottom: 0, left: 0, right: 0 }
    });

    const pageCount = await page.locator('.page').count();
    const stats = fs.statSync(outPath);
    console.log(`  -> ${build.filename} (${pageCount} pages, ${Math.round(stats.size / 1024)}KB)`);

    await page.close();
  }

  // Clean up temp files
  BUILDS.forEach(b => {
    const label = (b.filter || 'full').replace(/'/g, '');
    const f = path.join(TEMP_DIR, `report_${label}.html`);
    if (fs.existsSync(f)) fs.unlinkSync(f);
  });

  await browser.close();
  console.log(`\nDone. Reports in: ${OUT_DIR}`);
})();
