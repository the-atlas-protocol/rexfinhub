# AGENT: Home-Nav-Screener-V2
**Task**: TASK-E — Home Page + Nav + Screener CSS Fixes
**Branch**: feature/home-nav-screener-v2
**Status**: DONE

## Progress Reporting
Write timestamped progress to: `.agents/progress/Home-Nav-Screener-V2.md`

## Your Files (ONLY modify these)
- `webapp/templates/home.html`
- `webapp/templates/base.html`
- `webapp/static/css/style.css`
- `webapp/templates/screener/screener_3x.html`
- `webapp/templates/screener/screener_4x.html`
- `webapp/templates/screener/screener_rankings.html`
- `webapp/templates/screener/screener_rex.html`
- `webapp/templates/screener/screener_risk.html`
- `webapp/templates/screener/screener_market.html`

## CRITICAL: Read First
Read ALL files listed above completely before writing anything.
These files were modified by previous agents — READ the current state, don't use assumptions.

## TASK E.1 — Home: Tagline + Section Names

In `home.html`:

**Find and replace tagline** (remove "real-time" and "structured and leveraged"):
Old: "...Real-time access to market positioning...across the structured and leveraged ETP universe."
New:
```
REX's central intelligence platform — market positioning, SEC filing activity, and product analytics across the full ETP universe.
```
Search for the tagline text and replace carefully. Do not change surrounding HTML structure.

**Section renames**:
1. Find "FILINGS & COMPLIANCE" (or "Filings & Compliance") → change to **"FILINGS & MONITORING"**
2. Find "OPERATIONS" (or "Operations") → change to **"ADMIN & DATA"**

Also update any section CSS class names if needed (e.g., if the section uses `home-card--ops`, that can stay — just change the visible text).

Check if section title text is in a `<div class="home-section-title">` or similar — just update the text content.

## TASK E.2 — Nav: Simplify to 5 Items

In `base.html`, find the navigation links section.

**Current nav** (likely 8-9 links): Home, Dashboard, Funds, Search, Downloads, Market, Screener, Subscribe, Admin

**New nav** (5 links):
```html
<a href="/" class="nav-link {{ 'active' if request.url.path == '/' else '' }}">Home</a>
<a href="/dashboard" class="nav-link {{ 'active' if '/dashboard' in request.url.path else '' }}">Filings</a>
<a href="/market/rex" class="nav-link {{ 'active' if '/market' in request.url.path else '' }}">Market</a>
<a href="/screener/" class="nav-link {{ 'active' if '/screener' in request.url.path else '' }}">Screener</a>
<a href="/admin/" class="nav-link {{ 'active' if '/admin' in request.url.path else '' }}">Admin</a>
```

Read base.html carefully — the active class logic may be different (use `request.url.path` or check existing pattern).
IMPORTANT: Remove Funds, Search, Downloads, Subscribe from main nav — they stay as pages but not in top nav.
Keep the brand/logo link as-is.

## TASK E.3 — Screener Table Header Fix (CRITICAL)

### Root Cause
Two CSS bugs cause screener table headers to disappear under the navbar when scrolling:
1. `th` has `z-index: 10` but navbar has `z-index: 100` — headers hide behind navbar
2. `<div style="overflow-x:auto">` wrapper around tables breaks `position: sticky` (sticky only sticks within its scroll parent, and the div creates a new scroll parent)

### Fix in style.css

**Step 1**: Find the CSS rule for `th` that sets `position: sticky`. It looks like:
```css
th {
  position: sticky;
  top: var(--nav-height);
  z-index: 10;   /* ← THIS IS WRONG */
  ...
}
```
Change `z-index: 10` to `z-index: 101`.

**Step 2**: Find any conflicting rule. There may be a `.data-table th { top: 0; z-index: 2; }` rule that overrides. If found, remove it or change to match: `top: var(--nav-height); z-index: 101;`.

**Step 3**: Add `.table-scroll-wrap` class to style.css:
```css
.table-scroll-wrap {
  overflow-x: auto;
  /* NOTE: Do NOT add overflow-y here — it would break sticky headers */
}
```

### Fix in all 6 screener templates

For EACH of the 6 screener files, find ALL instances of:
```html
<div style="overflow-x:auto;">
```
or:
```html
<div style="overflow-x: auto;">
```
And replace with:
```html
<div class="table-scroll-wrap">
```

There may be multiple tables per file. Replace ALL occurrences.

### Why this fixes it
The `overflow-x:auto` div creates a scroll container. `position: sticky` on table headers sticks them relative to their nearest scrolling ancestor. When that ancestor is a small div (not the viewport), the headers stick within the div — which can be above the viewport — making them invisible. By removing `overflow-x` from the div OR using a CSS-only approach, headers stick to the viewport top (minus nav height).

The actual horizontal scroll for wide tables works differently: set `min-width` on the table itself or use `width: max-content` on the table, and let the `.table-scroll-wrap` handle horizontal overflow properly. The key is that `.table-scroll-wrap` must only set `overflow-x: auto`, NOT `overflow-y`.

### Test after fixing
1. Navigate to `/screener/`
2. Scroll down past the table header
3. Header should remain visible at the top of the viewport (below the navbar)
4. Table content scrolls normally beneath it

## Implementation Notes

### For style.css changes
Read the full style.css first. Look for:
- Line with `th {` that includes `position: sticky`
- Any `.data-table th` overrides
- Current `z-index` values

Make surgical edits — only change what's needed. Don't reorganize the file.

### For base.html nav changes
Read the full base.html. The nav structure might use Flask/Jinja2 `active` class logic differently. Preserve the exact class names and structure — only change which links appear and their text.

### For home.html changes
Read home.html completely. Only change text strings:
- "real-time" → remove from tagline (keep rest of sentence intact)
- "structured and leveraged ETP universe" → "full ETP universe"
- Section title text strings

Do NOT change CSS classes, div structure, or card layout.

## Commit Convention
```
git add webapp/templates/home.html webapp/templates/base.html webapp/static/css/style.css webapp/templates/screener/screener_3x.html webapp/templates/screener/screener_4x.html webapp/templates/screener/screener_rankings.html webapp/templates/screener/screener_rex.html webapp/templates/screener/screener_risk.html webapp/templates/screener/screener_market.html
git commit -m "feat: Home nav screener v2 - tagline fix, nav simplified to 5 items, screener sticky header fix"
```

## Done Criteria
- [x] Home tagline: no "real-time" or "structured and leveraged"
- [x] Home sections: "Filings & Monitoring" and "Admin & Data" (not "Compliance" or "Operations")
- [x] Nav: exactly 5 items (Home, Filings, Market, Screener, Admin)
- [x] Nav: "Filings" links to /dashboard (old Dashboard page)
- [x] style.css: `th` has `z-index: 101` (was 10)
- [x] style.css: `.table-scroll-wrap` class defined with `overflow-x: auto`
- [x] All 6 screener files: `<div style="overflow-x:auto">` replaced with `<div class="table-scroll-wrap">`
- [x] No broken HTML in any template
- [x] Server starts without errors

## Log
- Commit 79ec610: feat: home tagline + section names + nav simplified to 5 items
- Commit 8b133b9: fix: screener sticky header z-index and .table-scroll-wrap CSS class
- Commit 6ddf3bd: fix: replace inline overflow-x:auto with table-scroll-wrap in 6 screener templates
- Commit 63e10da: chore: mark AGENT.md as DONE
