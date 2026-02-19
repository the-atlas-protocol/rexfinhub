# FIXES Agent - Webapp Fixes & Improvements

## Status: NOT STARTED

## Mission
Fix existing issues and improve UX:
1. Fix Screener "No Bloomberg Data" issue (HIGHEST PRIORITY)
2. Improve Downloads page UI (pagination, search)
3. Identify 33 Act products
4. Add loading indicator to dashboard

## My Files (I own these)
```
webapp/routers/screener.py           # Edit - Fix data loading
webapp/routers/downloads.py          # Edit - Add pagination
webapp/routers/dashboard.py          # Edit - 33 Act display
webapp/services/screener_3x_cache.py # Edit - Fix caching
webapp/services/screener_service.py  # Edit if needed
webapp/templates/downloads.html      # Edit - Pagination UI
webapp/templates/dashboard.html      # Edit - Loading skeleton
webapp/templates/screener_*.html     # Edit if needed
screener/data_loader.py              # Edit - Fix loading
screener/config.py                   # Edit if needed
etp_tracker/trusts.py                # Edit - Add 33 Act flags
webapp/static/css/style.css          # Edit - Loading animations
```

## Do Not Touch
- `webapp/routers/market.py` (MARKET agent will create)
- `webapp/services/market_data.py` (MARKET agent will create)
- `webapp/templates/market/*` (MARKET agent will create)
- `data/DASHBOARD/*` (MARKET agent's data)

## Current Task
> Not started - Begin with Task 1: Fix Screener

---

## Task 1: Fix Screener "No Bloomberg Data" (HIGHEST PRIORITY)

### Problem
Screener pages show "No Bloomberg Data available" even though file exists.

### Verified Facts
- File exists: `data/SCREENER/data.xlsx` (5MB)
- Path resolves correctly in `screener/config.py`
- Sheets are correct: `stock_data` (6,479 rows), `etp_data` (5,074 rows)

### Investigation Path
1. Check `webapp/routers/screener.py` - `_data_available()` function
2. Check `webapp/services/screener_3x_cache.py` - `compute_and_cache()` function
3. Check `screener/data_loader.py` - `load_all()` function
4. Look for silent exception handling that swallows errors
5. Add logging to trace the data loading path

### Files to Check
```
webapp/routers/screener.py          # _data_available() function
webapp/services/screener_3x_cache.py # compute_and_cache() function
screener/data_loader.py             # load_all() function
screener/config.py                  # DATA_FILE path
```

### Fix Approach
1. Add detailed logging to trace data loading
2. Ensure exceptions are logged, not swallowed
3. Test `/screener/` route after server restart
4. Verify "Score Data" admin button works

---

## Task 2: Downloads Page UI

### Problem
Too many funds (7,000+) displayed at once. Overwhelming and slow.

### Files
```
webapp/routers/downloads.py
webapp/templates/downloads.html
```

### Improvements
1. **Pagination**: 50 funds per page with page navigation
2. **Search Box**: Filter by fund name, ticker, or trust name
3. **Category Collapse**: Group by category with expandable sections
4. **Quick Stats**: Show total count, filtered count

### Implementation Notes
- Search can be client-side JavaScript for speed
- Pagination can be server-side or client-side
- Consider existing patterns in codebase for tables

---

## Task 3: Identify 33 Act Products

### Problem
Some trusts file N-1A forms (1933 Act) instead of 485 forms (1940 Act). These show as "no ETFs" on dashboard.

### Scope (Minimal)
- Identification only, NOT full N-1A parsing
- Update display to show "33 Act Filer - N-1A" instead of "No 485 filings"

### Approach
1. Query SEC EDGAR for each trust in `etp_tracker/trusts.py`
2. Check if they have 485 filings OR N-1A filings
3. Create mapping: `{cik: "1940" | "1933"}`
4. Update `webapp/routers/dashboard.py` display logic

### Files
```
etp_tracker/trusts.py      # Add act_type field or mapping
webapp/routers/dashboard.py # Update display logic
```

---

## Task 4: Loading Indicator

### Problem
Dashboard loads 122 trust cards with no visual feedback.

### Solution
Add CSS skeleton animation while trust data loads.

### Files
```
webapp/templates/dashboard.html
webapp/static/css/style.css
```

### Implementation
1. Add CSS skeleton animation classes
2. Show skeleton cards initially
3. Replace with real data when loaded

---

## Progress Log
- [ ] Read PLAN.md for full context
- [ ] Task 1: Investigate screener data loading path
- [ ] Task 1: Add logging to identify failure point
- [ ] Task 1: Fix the issue
- [ ] Task 1: Test `/screener/` shows data
- [ ] Task 2: Add pagination to downloads
- [ ] Task 2: Add search/filter
- [ ] Task 3: Query SEC EDGAR for 33 Act trusts
- [ ] Task 3: Update trusts.py with act_type
- [ ] Task 3: Update dashboard display
- [ ] Task 4: Add loading skeleton CSS
- [ ] Task 4: Update dashboard template

## Notes / Context for Next Session
- Task 1 is highest priority - it's a broken feature
- The screener data file DOES exist and path resolves correctly
- Problem is likely in cache logic or silent exception handling
- Look for try/except blocks that might swallow errors

## Blockers
None currently
