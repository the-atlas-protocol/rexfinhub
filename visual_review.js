/**
 * Visual Review Script — renders each page and checks for REAL visual problems.
 * Not a checkbox exercise. Captures screenshots and reports what a human would notice.
 *
 * Usage: NODE_PATH="C:/Users/RyuEl-Asmar/AppData/Roaming/npm/node_modules" node visual_review.js
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const HTML_PATH = process.argv[2] || 'C:/Projects/rex-asia/report_v15.html';
const OUT_DIR = 'C:/Projects/rex-asia/reports';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 816, height: 1056 } });
  await page.goto(`file:///${HTML_PATH.replace(/\\/g, '/')}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);

  // Generate PDF
  await page.pdf({
    path: path.join(OUT_DIR, 'REX_Asia_Report_Feb26.pdf'),
    width: '8.5in', height: '11in',
    printBackground: true,
    margin: { top: 0, bottom: 0, left: 0, right: 0 }
  });

  const issues = [];
  const pageCount = await page.locator('.page').count();

  for (let p = 0; p < pageCount; p++) {
    const pageEl = page.locator('.page').nth(p);
    const pNum = p + 1;

    // ─── Check 1: Text/element overlap detection ───
    // Get all text-bearing elements and check if any two overlap
    const overlaps = await pageEl.evaluate((el, pageNum) => {
      const problems = [];
      const textEls = el.querySelectorAll('td, th, .kpi-val, .kpi-label, .kpi-sub, .section-title, svg text');
      const rects = [];
      for (const te of textEls) {
        const r = te.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const text = te.textContent?.trim().slice(0, 30) || '';
        if (!text) continue;
        rects.push({ text, top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height });
      }
      // Check SVG text elements for overlap within same SVG
      const svgs = el.querySelectorAll('svg');
      for (const svg of svgs) {
        const svgRect = svg.getBoundingClientRect();
        const texts = svg.querySelectorAll('text');
        const svgTextRects = [];
        for (const t of texts) {
          const r = t.getBoundingClientRect();
          if (r.width < 2) continue;
          svgTextRects.push({ text: t.textContent?.trim().slice(0, 25) || '', top: r.top, bottom: r.bottom, left: r.left, right: r.right });
        }
        // Check pairs for overlap
        for (let i = 0; i < svgTextRects.length; i++) {
          for (let j = i + 1; j < svgTextRects.length; j++) {
            const a = svgTextRects[i], b = svgTextRects[j];
            const hOverlap = a.left < b.right && a.right > b.left;
            const vOverlap = a.top < b.bottom && a.bottom > b.top;
            if (hOverlap && vOverlap) {
              problems.push(`SVG text overlap: "${a.text}" and "${b.text}"`);
            }
          }
        }
      }
      return problems.slice(0, 5);
    }, pNum);

    overlaps.forEach(o => issues.push({ page: pNum, type: 'OVERLAP', msg: o }));

    // ─── Check 2: Content outside page bounds ───
    const overflows = await pageEl.evaluate(el => {
      const pageRect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const padL = parseFloat(style.paddingLeft);
      const padR = parseFloat(style.paddingRight);
      const padT = parseFloat(style.paddingTop);
      const padB = parseFloat(style.paddingBottom);
      const contentLeft = pageRect.left + padL;
      const contentRight = pageRect.right - padR;
      const contentTop = pageRect.top + padT;
      const contentBottom = pageRect.bottom - padB;

      const problems = [];
      const all = el.querySelectorAll('table, svg, .kpi-strip, .section-heading');
      for (const child of all) {
        const cr = child.getBoundingClientRect();
        if (cr.right > contentRight + 3) {
          problems.push(`${child.tagName}${child.className ? '.' + child.className.split(' ')[0] : ''} extends ${Math.round(cr.right - contentRight)}px past right margin`);
        }
        if (cr.bottom > contentBottom + 3) {
          problems.push(`${child.tagName} extends ${Math.round(cr.bottom - contentBottom)}px past bottom margin`);
        }
      }
      return problems.slice(0, 5);
    });
    overflows.forEach(o => issues.push({ page: pNum, type: 'OVERFLOW', msg: o }));

    // ─── Check 3: Chart series color contrast ───
    // Check if multiple chart series are visually indistinguishable
    const colorIssues = await pageEl.evaluate(el => {
      const problems = [];
      const svgs = el.querySelectorAll('svg');
      for (const svg of svgs) {
        const fills = new Set();
        const strokes = new Set();
        svg.querySelectorAll('polygon, polyline, path, rect, line').forEach(shape => {
          const fill = shape.getAttribute('fill');
          const stroke = shape.getAttribute('stroke');
          if (fill && fill !== 'none' && fill !== 'transparent' && !fill.startsWith('rgba')) fills.add(fill);
          if (stroke && stroke !== 'none') strokes.add(stroke);
        });
        // If a chart has 3+ series but all fills are grayscale variants
        const allGray = [...fills].every(c => {
          if (c.startsWith('#')) {
            const r = parseInt(c.slice(1, 3), 16);
            const g = parseInt(c.slice(3, 5), 16);
            const b = parseInt(c.slice(5, 7), 16);
            return Math.abs(r - g) < 30 && Math.abs(g - b) < 30;
          }
          return false;
        });
        if (fills.size >= 3 && allGray) {
          problems.push(`Chart with ${fills.size} series uses only grayscale fills — series may be indistinguishable`);
        }
      }
      return problems;
    });
    colorIssues.forEach(o => issues.push({ page: pNum, type: 'COLOR', msg: o }));

    // ─── Check 4: Empty space ratio ───
    const spaceRatio = await pageEl.evaluate(el => {
      const pageRect = el.getBoundingClientRect();
      const footer = el.querySelector('.footer');
      const footerRect = footer ? footer.getBoundingClientRect() : null;

      // Find last content element before footer
      const children = Array.from(el.children).filter(c => !c.classList.contains('footer'));
      const lastContent = children[children.length - 1];
      if (!lastContent || !footerRect) return null;

      const lastRect = lastContent.getBoundingClientRect();
      const contentEnd = lastRect.bottom;
      const footerStart = footerRect.top;
      const gap = footerStart - contentEnd;
      const pageHeight = pageRect.height;
      const pct = Math.round(gap / pageHeight * 100);
      return { gap: Math.round(gap), pct };
    });
    if (spaceRatio && spaceRatio.pct > 20) {
      issues.push({ page: pNum, type: 'EMPTY', msg: `${spaceRatio.pct}% of page is empty (${spaceRatio.gap}px gap before footer)` });
    }

    // ─── Check 5: Bar chart label collision ───
    const barIssues = await pageEl.evaluate(el => {
      const problems = [];
      const svgs = el.querySelectorAll('svg');
      for (const svg of svgs) {
        const texts = [...svg.querySelectorAll('text')];
        // Check for text that extends beyond SVG bounds
        const svgRect = svg.getBoundingClientRect();
        for (const t of texts) {
          const tr = t.getBoundingClientRect();
          if (tr.left < svgRect.left - 2) {
            problems.push(`Label "${t.textContent?.trim().slice(0, 20)}" extends ${Math.round(svgRect.left - tr.left)}px past left edge`);
          }
        }
        // Check for fund name + value label collision in bar charts
        const fundLabels = texts.filter(t => {
          const content = t.textContent?.trim() || '';
          return content.length <= 6 && /^[A-Z]{2,6}$/.test(content);
        });
        const valueLabels = texts.filter(t => {
          const content = t.textContent?.trim() || '';
          return content.startsWith('$') || content.startsWith('-$') || content.startsWith('+$');
        });
        for (const fl of fundLabels) {
          const fr = fl.getBoundingClientRect();
          for (const vl of valueLabels) {
            const vr = vl.getBoundingClientRect();
            const hOverlap = fr.left < vr.right && fr.right > vr.left;
            const vOverlap = fr.top < vr.bottom && fr.bottom > vr.top;
            if (hOverlap && vOverlap) {
              problems.push(`Fund label "${fl.textContent?.trim()}" overlaps value "${vl.textContent?.trim()}"`);
            }
          }
        }
      }
      return problems.slice(0, 5);
    });
    barIssues.forEach(o => issues.push({ page: pNum, type: 'BAR_LABEL', msg: o }));
  }

  // ─── Report ───
  console.log(`\n=== VISUAL REVIEW — ${pageCount} pages ===\n`);

  if (issues.length === 0) {
    console.log('  CLEAN — no visual issues detected.\n');
  } else {
    const byPage = {};
    issues.forEach(i => {
      if (!byPage[i.page]) byPage[i.page] = [];
      byPage[i.page].push(i);
    });

    for (const [pg, pageIssues] of Object.entries(byPage)) {
      console.log(`Page ${pg}:`);
      pageIssues.forEach(i => console.log(`  [${i.type}] ${i.msg}`));
      console.log();
    }
  }

  console.log(`Total: ${issues.length} issues across ${pageCount} pages`);

  await browser.close();
  process.exit(issues.length > 0 ? 1 : 0);
})();
