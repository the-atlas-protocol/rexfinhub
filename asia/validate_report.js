/**
 * REX Asia Report — Visual Validation Script
 * Runs after HTML generation, BEFORE PDF capture.
 * Catches overflow, clipping, sizing, and overlap issues programmatically.
 *
 * Usage: NODE_PATH="C:/Users/RyuEl-Asmar/AppData/Roaming/npm/node_modules" node validate_report.js [path-to-html]
 */
const { chromium } = require('playwright');
const path = require('path');

const HTML_PATH = process.argv[2] || 'C:/Projects/rexfinhub/asia/report_v14.html';
const OUTPUT_DIR = 'C:/Projects/rexfinhub/asia/reports';

// 8.5in x 11in at 96dpi
const PAGE_W = 816;
const PAGE_H = 1056;
const TOLERANCE = 2; // px tolerance for boundary checks

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: PAGE_W, height: PAGE_H * 8 } });
  await page.goto(`file:///${HTML_PATH.replace(/\\/g, '/')}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  const issues = [];

  // ─── 1. Page Boundary Check ───
  // Every .page div's children must be within the page bounds
  const pageCount = await page.locator('.page').count();
  console.log(`\n=== REX Report Visual Validation ===`);
  console.log(`Pages found: ${pageCount}\n`);

  for (let p = 0; p < pageCount; p++) {
    const pageEl = page.locator('.page').nth(p);
    const pageBox = await pageEl.boundingBox();
    if (!pageBox) continue;

    const pageTop = pageBox.y;
    const pageBottom = pageBox.y + pageBox.height;
    const pageLeft = pageBox.x;
    const pageRight = pageBox.x + pageBox.width;

    // Check all direct children
    const children = pageEl.locator('> *');
    const childCount = await children.count();
    for (let c = 0; c < childCount; c++) {
      const child = children.nth(c);
      const box = await child.boundingBox();
      if (!box) continue;

      if (box.y + box.height > pageBottom + TOLERANCE) {
        const overflow = Math.round(box.y + box.height - pageBottom);
        const tag = await child.evaluate(el => el.tagName + '.' + el.className.split(' ')[0]);
        issues.push({
          page: p + 1,
          type: 'OVERFLOW_BOTTOM',
          severity: 'ERROR',
          msg: `Element ${tag} overflows page bottom by ${overflow}px`
        });
      }
      if (box.x + box.width > pageRight + TOLERANCE) {
        const overflow = Math.round(box.x + box.width - pageRight);
        const tag = await child.evaluate(el => el.tagName + '.' + el.className.split(' ')[0]);
        issues.push({
          page: p + 1,
          type: 'OVERFLOW_RIGHT',
          severity: 'ERROR',
          msg: `Element ${tag} overflows page right by ${overflow}px`
        });
      }
    }
  }

  // ─── 2. Overflow Detection on chart-cards ───
  const cards = page.locator('.chart-card');
  const cardCount = await cards.count();
  for (let i = 0; i < cardCount; i++) {
    const card = cards.nth(i);
    const overflow = await card.evaluate(el => ({
      scrollH: el.scrollHeight,
      clientH: el.clientHeight,
      scrollW: el.scrollWidth,
      clientW: el.clientWidth,
      id: el.id || el.querySelector('[id]')?.id || `card-${el.closest('.page')?.querySelector('.section-title')?.textContent?.slice(0,30) || 'unknown'}`
    }));

    if (overflow.scrollH > overflow.clientH + TOLERANCE) {
      const diff = overflow.scrollH - overflow.clientH;
      // Find which page this card is on
      const pageNum = await card.evaluate(el => {
        const pages = document.querySelectorAll('.page');
        for (let i = 0; i < pages.length; i++) {
          if (pages[i].contains(el)) return i + 1;
        }
        return '?';
      });
      issues.push({
        page: pageNum,
        type: 'CARD_OVERFLOW_V',
        severity: 'WARNING',
        msg: `Chart card "${overflow.id}" content overflows vertically by ${diff}px (scrollH=${overflow.scrollH} > clientH=${overflow.clientH})`
      });
    }
    if (overflow.scrollW > overflow.clientW + TOLERANCE) {
      const diff = overflow.scrollW - overflow.clientW;
      const pageNum = await card.evaluate(el => {
        const pages = document.querySelectorAll('.page');
        for (let i = 0; i < pages.length; i++) {
          if (pages[i].contains(el)) return i + 1;
        }
        return '?';
      });
      issues.push({
        page: pageNum,
        type: 'CARD_OVERFLOW_H',
        severity: 'WARNING',
        msg: `Chart card "${overflow.id}" content overflows horizontally by ${diff}px`
      });
    }
  }

  // ─── 3. SVG Fill Ratio ───
  // Check what % of each chart container is actually used by SVG content
  const svgContainers = page.locator('.chart-card svg');
  const svgCount = await svgContainers.count();
  for (let i = 0; i < svgCount; i++) {
    const svg = svgContainers.nth(i);
    const info = await svg.evaluate(el => {
      const svgRect = el.getBoundingClientRect();
      const parent = el.closest('.chart-card');
      const parentRect = parent ? parent.getBoundingClientRect() : svgRect;
      const fillRatio = (svgRect.width * svgRect.height) / (parentRect.width * parentRect.height);
      const pageNum = (() => {
        const pages = document.querySelectorAll('.page');
        for (let i = 0; i < pages.length; i++) {
          if (pages[i].contains(el)) return i + 1;
        }
        return '?';
      })();
      return {
        fillRatio: Math.round(fillRatio * 100),
        svgW: Math.round(svgRect.width),
        svgH: Math.round(svgRect.height),
        parentW: Math.round(parentRect.width),
        parentH: Math.round(parentRect.height),
        pageNum
      };
    });

    if (info.fillRatio < 60) {
      issues.push({
        page: info.pageNum,
        type: 'SVG_UNDERFILL',
        severity: 'WARNING',
        msg: `SVG fills only ${info.fillRatio}% of its chart-card (svg: ${info.svgW}x${info.svgH}, card: ${info.parentW}x${info.parentH})`
      });
    }
  }

  // ─── 4. Text Clipping Detection ───
  // Check if any text in SVGs extends beyond the SVG viewBox
  const svgTexts = page.locator('svg text');
  const textCount = await svgTexts.count();
  let clippedTexts = 0;
  for (let i = 0; i < textCount; i++) {
    const txt = svgTexts.nth(i);
    const clipped = await txt.evaluate(el => {
      const svg = el.closest('svg');
      if (!svg) return null;
      const svgRect = svg.getBoundingClientRect();
      const txtRect = el.getBoundingClientRect();
      if (txtRect.right > svgRect.right + 1 || txtRect.left < svgRect.left - 1) {
        return {
          text: el.textContent?.slice(0, 30),
          overflow: Math.round(Math.max(txtRect.right - svgRect.right, svgRect.left - txtRect.left))
        };
      }
      return null;
    });

    if (clipped) {
      clippedTexts++;
      if (clippedTexts <= 10) { // Limit output
        const pageNum = await txt.evaluate(el => {
          const pages = document.querySelectorAll('.page');
          for (let i = 0; i < pages.length; i++) {
            if (pages[i].contains(el)) return i + 1;
          }
          return '?';
        });
        issues.push({
          page: pageNum,
          type: 'TEXT_CLIPPED',
          severity: 'ERROR',
          msg: `SVG text "${clipped.text}" clipped by ${clipped.overflow}px`
        });
      }
    }
  }
  if (clippedTexts > 10) {
    issues.push({
      page: '?',
      type: 'TEXT_CLIPPED',
      severity: 'ERROR',
      msg: `... and ${clippedTexts - 10} more clipped text elements`
    });
  }

  // ─── 5. Section Overlap Detection ───
  // Check if sibling sections within a page overlap vertically
  for (let p = 0; p < pageCount; p++) {
    const pageEl = page.locator('.page').nth(p);
    const sections = pageEl.locator('> div:not(.footer):not(.header):not(.mini-header)');
    const secCount = await sections.count();
    const boxes = [];
    for (let s = 0; s < secCount; s++) {
      const box = await sections.nth(s).boundingBox();
      if (box && box.height > 5) boxes.push({ index: s, ...box });
    }
    for (let i = 1; i < boxes.length; i++) {
      const prev = boxes[i - 1];
      const curr = boxes[i];
      if (curr.y < prev.y + prev.height - TOLERANCE) {
        const overlap = Math.round(prev.y + prev.height - curr.y);
        issues.push({
          page: p + 1,
          type: 'SECTION_OVERLAP',
          severity: 'WARNING',
          msg: `Sections ${prev.index} and ${curr.index} overlap by ${overlap}px`
        });
      }
    }
  }

  // ─── 6. Empty Space Analysis ───
  // Check for large unused areas within pages
  for (let p = 0; p < pageCount; p++) {
    const pageEl = page.locator('.page').nth(p);
    const footer = pageEl.locator('.footer');
    const footerBox = await footer.boundingBox();
    const pageBox = await pageEl.boundingBox();

    if (footerBox && pageBox) {
      // Find the last content element before footer
      const lastContent = await pageEl.evaluate(el => {
        const children = Array.from(el.children);
        const footer = el.querySelector('.footer');
        const contentChildren = children.filter(c => c !== footer && c.getBoundingClientRect().height > 5);
        if (contentChildren.length === 0) return null;
        const last = contentChildren[contentChildren.length - 1];
        const rect = last.getBoundingClientRect();
        return { bottom: rect.y + rect.height };
      });

      if (lastContent) {
        const gap = Math.round(footerBox.y - lastContent.bottom);
        if (gap > 80) {
          issues.push({
            page: p + 1,
            type: 'EMPTY_SPACE',
            severity: 'INFO',
            msg: `${gap}px empty gap between last content and footer`
          });
        }
      }
    }
  }

  // ─── 7. SVG Content Overflow ───
  const allSvgs = page.locator('svg');
  const svgElCount = await allSvgs.count();
  for (let i = 0; i < svgElCount; i++) {
    const svgNode = allSvgs.nth(i);
    const overflows = await svgNode.evaluate(el => {
      const svgRect = el.getBoundingClientRect();
      const problems = [];
      const children = el.querySelectorAll('line, polyline, circle, text, rect, path, polygon');
      for (const child of children) {
        const cr = child.getBoundingClientRect();
        if (cr.width === 0 && cr.height === 0) continue;
        const overR = cr.right - svgRect.right;
        const overL = svgRect.left - cr.left;
        const overB = cr.bottom - svgRect.bottom;
        const overT = svgRect.top - cr.top;
        const maxOver = Math.max(overR, overL, overB, overT);
        if (maxOver > 3) {
          problems.push({
            tag: child.tagName,
            text: child.textContent?.slice(0, 25) || '',
            overflow: Math.round(maxOver),
            direction: overR > 3 ? 'right' : overL > 3 ? 'left' : overB > 3 ? 'bottom' : 'top'
          });
        }
      }
      return problems;
    });

    if (overflows.length > 0) {
      const pageNum = await svgNode.evaluate(el => {
        const pages = document.querySelectorAll('.page');
        for (let i = 0; i < pages.length; i++) {
          if (pages[i].contains(el)) return i + 1;
        }
        return '?';
      });
      for (const o of overflows.slice(0, 5)) {
        issues.push({
          page: pageNum,
          type: 'SVG_CONTENT_OVERFLOW',
          severity: 'ERROR',
          msg: `<${o.tag}> "${o.text}" overflows SVG ${o.direction} by ${o.overflow}px`
        });
      }
      if (overflows.length > 5) {
        issues.push({
          page: pageNum,
          type: 'SVG_CONTENT_OVERFLOW',
          severity: 'ERROR',
          msg: `... and ${overflows.length - 5} more overflow elements in this SVG`
        });
      }
    }
  }

  // ─── 8. Table Overflow Check ───
  const allTables = page.locator('table');
  const tableCount = await allTables.count();
  for (let i = 0; i < tableCount; i++) {
    const table = allTables.nth(i);
    const overflow = await table.evaluate(el => {
      const tableRect = el.getBoundingClientRect();
      const pg = el.closest('.page');
      if (!pg) return null;
      const pageRect = pg.getBoundingClientRect();
      const style = getComputedStyle(pg);
      const padRight = parseFloat(style.paddingRight);
      const rightBound = pageRect.right - padRight;
      if (tableRect.right > rightBound + 2) {
        return { overflow: Math.round(tableRect.right - rightBound) };
      }
      return null;
    });
    if (overflow) {
      const pageNum = await table.evaluate(el => {
        const pages = document.querySelectorAll('.page');
        for (let i = 0; i < pages.length; i++) {
          if (pages[i].contains(el)) return i + 1;
        }
        return '?';
      });
      issues.push({
        page: pageNum,
        type: 'TABLE_OVERFLOW',
        severity: 'ERROR',
        msg: `Table overflows page content area by ${overflow.overflow}px`
      });
    }
  }

  // ─── 9. Cross-Page Chart Consistency Check ───
  const timelineHeadings = page.locator('.section-heading:has(.section-title)');
  const headingCount = await timelineHeadings.count();
  const chartHeights = [];
  for (let i = 0; i < headingCount; i++) {
    const heading = timelineHeadings.nth(i);
    const titleText = await heading.evaluate(el => {
      const title = el.querySelector('.section-title');
      return title ? title.textContent : '';
    });
    if (titleText.includes('AUM Timeline')) {
      const nextSibling = await heading.evaluate(el => {
        const next = el.nextElementSibling;
        if (next && next.id && next.id.includes('chart')) {
          return { id: next.id, height: next.getBoundingClientRect().height };
        }
        return null;
      });
      if (nextSibling) {
        const pg = await heading.evaluate(el => {
          const pages = document.querySelectorAll('.page');
          for (let i = 0; i < pages.length; i++) {
            if (pages[i].contains(el)) return i + 1;
          }
          return '?';
        });
        chartHeights.push({ page: pg, id: nextSibling.id, height: nextSibling.height });
      }
    }
  }
  if (chartHeights.length > 1) {
    const avgHeight = chartHeights.reduce((s, c) => s + c.height, 0) / chartHeights.length;
    for (const ch of chartHeights) {
      const deviation = Math.abs(ch.height - avgHeight) / avgHeight;
      if (deviation > 0.10) {
        issues.push({
          page: ch.page,
          type: 'CHART_HEIGHT_OUTLIER',
          severity: 'WARNING',
          msg: `Chart "${ch.id}" height ${Math.round(ch.height)}px deviates ${Math.round(deviation * 100)}% from average ${Math.round(avgHeight)}px`
        });
      }
    }
  }

  // ─── Report ───
  console.log('─── ISSUES FOUND ───\n');

  const errors = issues.filter(i => i.severity === 'ERROR');
  const warnings = issues.filter(i => i.severity === 'WARNING');
  const infos = issues.filter(i => i.severity === 'INFO');

  if (errors.length) {
    console.log(`ERRORS (${errors.length}):`);
    errors.forEach(i => console.log(`  [P${i.page}] ${i.type}: ${i.msg}`));
    console.log();
  }
  if (warnings.length) {
    console.log(`WARNINGS (${warnings.length}):`);
    warnings.forEach(i => console.log(`  [P${i.page}] ${i.type}: ${i.msg}`));
    console.log();
  }
  if (infos.length) {
    console.log(`INFO (${infos.length}):`);
    infos.forEach(i => console.log(`  [P${i.page}] ${i.type}: ${i.msg}`));
    console.log();
  }

  if (issues.length === 0) {
    console.log('  ALL CLEAR — no visual issues detected.\n');
  }

  console.log(`Summary: ${errors.length} errors, ${warnings.length} warnings, ${infos.length} info`);
  console.log(`─────────────────────\n`);

  await browser.close();
  process.exit(errors.length > 0 ? 1 : 0);
})();
