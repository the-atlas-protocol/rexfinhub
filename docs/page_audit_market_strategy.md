# Page Audit: Market + Strategy Pillars — 2026-05-06

## Summary

- **Pages audited**: 17 routes (including redirects confirmed)
- **Healthy**: 6 | **Needs Polish**: 7 | **Broken**: 2 | **Wrong Architecture**: 2
- **Data as of**: May 06, 2026 (confirmed fresh on all market pages)

### Top 5 Findings

1. **CRITICAL — map_li column key mismatch (Broken)**: `market_fund_detail` and `compare_view` both use `d.get("map_li_underlier","")` but the master DataFrame renames these to `q_category_attributes.map_li_underlier` via `_FLAT_TO_PREFIXED`. Result: underlier, direction, and leverage badges are silently blank on every `/market/fund/{ticker}` page and the compare page's competitor filtering always produces empty results. Affects all ~300+ L&I single-stock funds.

2. **CRITICAL — Calendar contamination (Wrong Architecture)**: `/market/calendar` queries ALL trusts in the DB with 485BPOS effective dates, returning variable annuity wrappers (PRUCO LIFE VARIABLE UNIVERSAL ACCOUNT, SEPARATE ACCOUNT A OF PACIFIC LIFE, etc.) as "fund launches." These are not ETPs. The calendar as built is an SEC-pipeline artifact, not a market intelligence tool.

3. **HIGH — Strategy home/whitespace empty parquet (Broken)**: `/strategy/` and `/strategy/whitespace` both render empty-state pages ("No whitespace parquet yet") because `whitespace_v4.parquet` has not been generated on the live deployment. On-demand ticker analysis (`/strategy/ticker/{t}`) works, but the overview and ranked table are completely non-functional.

4. **HIGH — underlier_overrides.csv not applied to master DF (Correctness)**: The file at `config/rules/underlier_overrides.csv` documents 19 known Bloomberg mis-mappings (DJTU→DJT, CONX→COIN, HODU→HOOD, etc.) but there is no code path that reads or applies these overrides into the market_data service or the DB. The fix was audited but never wired. Every page that displays `map_li_underlier` data (underlier page, fund detail, compare, category view) shows the wrong underlying ticker for these 19 funds.

5. **MEDIUM — Issuer Detail buried with no mega-menu entry (Linkage)**: `/market/issuer/detail?issuer=BlackRock` is a well-built page (47 funds, AUM trend, category breakdown) that renders correctly — but the only discovery path is clicking an issuer name in the `/market/issuer` table. There is no mega-menu link, no breadcrumb on the issuer list page explaining the detail exists, and no direct URL in the nav. Users who know to look there will find it; everyone else will not.

---

## Per-Page Findings

### /market/ — Market Overview
- **Intended question**: Navigation entry point for all market views.
- **Verdict**: Healthy
- **Findings**: Immediately 302-redirects to `/market/rex`. Correct behavior; no stale state possible. Nav pills on the market base template expose all 7 sub-pages correctly.
- **Fix recommendation**: None needed. The redirect is intentional and documented in the router.

---

### /market/rex — REX Market Share
- **Intended question**: How are REX suites performing vs. each other and the market?
- **Verdict**: Healthy
- **Findings**: Data as of May 06, 2026 (fresh). Suite breakdown, trend charts, and category filter all render. The `product_type` and `fund_structure` query params are wired but the product_type filter has no visible UI on the page (it's accepted server-side but there is no toggle rendered in the template). Filter for category is working.
- **Fix recommendation**: Either surface the `product_type` filter in the UI or remove it from the route signature to avoid confusion.

---

### /market/category — Category Deep-Dive
- **Intended question**: What is the competitive landscape within a specific ETP category?
- **Verdict**: Needs Polish
- **Findings**:
  - **Linkage**: Redirects from bare `/market/category` to the first category (L&I - Single Stock) — good default. All categories accessible via URL params.
  - **Treemap**: The treemap was merged into Category View (formerly its own page). The redirect from `/market/treemap` lands on the correct category. Good consolidation.
  - **Slicers**: `q_category_attributes.*` filter params work server-side but the UX for applying them is not obvious — slicer values are URL-encoded strings with dot-notation keys; no form autocomplete. Advanced users only.
  - **Linkage gap**: Category page shows a ranked fund table but no ticker in the table links to `/market/fund/{ticker}`. Users see fund names and AUM but cannot drill into a specific fund.
- **Fix recommendation**: Add `<a href="/market/fund/{{ product.ticker }}">` links to ticker cells in the category product table.

---

### /market/issuer — Issuer Analysis
- **Intended question**: Which issuers dominate by AUM, flows, and product count?
- **Verdict**: Needs Polish
- **Findings**:
  - Data is fresh (May 06). BlackRock leads at $135.9B / 47 products — data correct.
  - Issuer names in the table DO link to `/market/issuer/detail?issuer=...` via `iss.issuer_name`. This is good, but the link is only on the name text (not a visible "→" CTA or button), making it easy to miss.
  - **Share page merged**: `/market/share` redirects to `/market/issuer` — documented redirect, fine.
  - **No cat=All behavior**: The donut/share chart only populates when `cat_arg` is set. With the default "All" category, `share_data = {}` — the chart is empty by design but the UI shows a blank chart area with no explanation.
- **Fix recommendation**: Add an explicit "→ Detail" button column in the issuer table. For the share chart, either hide it when `cat=All` or show a "Select a category to view market share breakdown" placeholder.

---

### /market/issuer/detail?issuer=BlackRock — Issuer Deep-Dive
- **Intended question**: Full product roster, AUM trend, and category breakdown for a specific issuer.
- **Verdict**: Needs Polish
- **Findings**:
  - Page renders correctly for BlackRock: 47 products, correct AUM, AUM trend chart, category breakdown.
  - **Buried**: Not reachable from mega-menu, footer, or any direct navigation outside the issuer table row click. The breadcrumb correctly shows `Market / Issuers / BlackRock` but there's no way to share or bookmark this page from the UI.
  - **Linkage gap**: Product tickers in the table do not link to `/market/fund/{ticker}`. The issuer detail is a dead end — no onward navigation to individual fund pages.
  - **Sort**: Table sorting works client-side via `sortTable()` — fine for 47 rows.
- **Fix recommendation**: Add mega-menu entry ("Issuer Detail" or make "Issuer Analysis" expand to show searched issuer). Add ticker → `/market/fund/{ticker}` links in the product table.

---

### /market/treemap — Treemap
- **Intended question**: (Formerly standalone; now merged.)
- **Verdict**: Healthy (redirect)
- **Findings**: `/market/treemap` 302-redirects to `/market/category?cat=Leverage+%26+Inverse+-+Single+Stock`. Correct consolidation. The treemap is now embedded within the Category View. No orphaned template.
- **Fix recommendation**: None. The redirect is clean. If the category page treemap embed ever gets removed, this redirect should also go.

---

### /market/share — Market Share Timeline
- **Intended question**: (Formerly standalone; now merged.)
- **Verdict**: Healthy (redirect)
- **Findings**: `/market/share` 302-redirects to `/market/issuer`. Confirmed by curl. The `share_timeline.html` template is still present but not directly referenced by any active route — it's dead code.
- **Fix recommendation**: Delete `webapp/templates/market/share_timeline.html` if confirmed unused.

---

### /market/rex-performance — ETP Screener
- **Intended question**: Interactive screener across all ETPs with column picker, presets, and export.
- **Verdict**: Needs Polish
- **Findings**:
  - **Double-render bug (CONFIRMED, but benign)**: `rex_performance.html` line 119 has `{% if not available %}` guard inside `{% block market_content %}`. But `market/base.html` wraps the entire content block in `{% if not available %} alert {% else %} content {% endif %}`. When `available=False`, the base shows the alert and never renders the block, so line 119-121 is dead code. No visual bug exists currently, but it is confusing defensive code that will mislead future editors.
  - **Functional**: Screener loads ~81 REX funds cleanly, 7 presets work, sidebar toggle works, CSV export functions, horizontal scroll with Alt+scroll documented.
  - **Naming inconsistency**: Tab shows "ETP Screener" in nav; route name in URL is `rex-performance`. These don't match and cause confusion for users trying to bookmark.
  - **Data scope**: `scope=all` loads 81 REX funds (only REX is `is_rex=1`). To see all ETPs, user must select "All ETPs" scope — but the screener then only loads non-REX and REX combined. The scope dropdown wording "All ETPs" / "REX Only" / "Competitors" is correct.
- **Fix recommendation**: Remove dead `if not available` guard from rex_performance.html (line 119-121). Rename the nav pill from "ETP Screener" to match either the page title or route, consistently.

---

### /market/compare — Fund Comparison
- **Intended question**: Side-by-side comparison of up to 10 funds across AUM, returns, flows, and total return history.
- **Verdict**: Broken (silent data loss)
- **Findings**:
  - **map_li key mismatch bug (critical)**: `compare_view` builds fund data using `d.get("map_li_underlier","")` from the master DataFrame — but the DF uses prefixed key `q_category_attributes.map_li_underlier`. Result: `fund.underlier = ""` always. Competitor filtering at line 428-433 uses the same wrong key, so when comparing two L&I funds, the "find similar underlier" competitor filter always returns `underlier=""` and falls back to category match — silently wrong.
  - **stale script block at line 390**: A `market_scripts` block exists referencing `tickerInput` (line 393) which does not exist in the template — the real input is `tickerHidden`. This JS block is dead code.
  - **Compare page empty state**: Works correctly when no tickers given — shows search input and "Enter ticker symbols" prompt.
  - **Total returns chart**: When `total_returns` data is populated, the full growth/drawdown/stats view is rich and well-implemented. The `scrape_total_returns` dependency (external scrape) adds latency on first load.
  - **Linkage**: There is no "View full profile" link from the compare table to `/market/fund/{ticker}`. Users comparing two funds cannot click through to either fund's detail page.
- **Fix recommendation**: Fix map_li key references to use `q_category_attributes.map_li_underlier` (and `q_category_attributes.map_li_direction`, `q_category_attributes.map_li_leverage_amount`). Remove dead `tickerInput` JS block. Add ticker links to fund detail in the compare table.

---

### /market/calendar — Fund Activity Calendar
- **Intended question**: What ETPs launched recently, and what filings are effective soon?
- **Verdict**: Wrong Architecture
- **Findings**:
  - **Data contamination (critical)**: The calendar queries ALL trusts monitored by the SEC pipeline — including variable annuity separate accounts (PRUCO LIFE, PACIFIC LIFE, MEMBERS HORIZON VARIABLE SEPARATE ACCOUNT, EVERLAKE, etc.). These are NOT ETPs. Every "launch" on the May calendar is an insurance wrapper, not an ETF. The calendar is populated with ~45+ entries per day, all non-ETP.
  - **Correct data source does exist**: The calendar events include some genuine ETP launches (`2x Avalanche ETF`, `FT Vest U.S. Equity Dual Directional Buffer ETF - May`, `Defiance Daily Target 2X Long AMPX ETF`, `Texas Equity Opportunity ETF`) but they are buried among dozens of VA wrapper re-filings.
  - **Effective date column renders "--"**: The template at line 93 tries `item.filing.effective_date` — the `Filing` model does not have an `effective_date` attribute (that lives on `FundExtraction`). The fallback `item.effective_date` works, but Jinja evaluates both branches in sequence so the "if filing.effective_date" always resolves to None/empty, and the displayed date cell shows "--" for all rows. The dates ARE in the calendar grid popup events (from the `event_by_date` dict built from `extraction.effective_date`) but NOT in the table listing.
  - **Calendar pillar question**: The concept (compliance calendar for ETP launches) is valuable. The implementation is wrong — it needs to filter for known ETP issuers or use the `is_rex=True` + `etp_category IS NOT NULL` flag from `mkt_master_data` as a trust whitelist.
  - **No link to fund detail**: Fund names link to `/funds?q={fund_name}` (the SEC pipeline search) — not to `/market/fund/{ticker}`. For genuine ETP launches, this could cross-link to the market fund page.
- **Fix recommendation**:
  1. Filter `FundExtraction` joins to exclude trusts whose names match variable annuity patterns (or better: join through `mkt_master_data` to only return trusts with at least one active ETP).
  2. Fix effective date column: use `item.effective_date` directly (remove `item.filing.effective_date` branch which is always None).
  3. Add `is_rex` badge on calendar entries where the trust has a REX product.
  4. **Pillar promotion consideration**: If cleaned up, Calendar deserves its own top-level pillar in the mega-menu ("Pipeline" or "Launch Tracker") rather than sitting under Market.

---

### /market/underlier — Underlier Deep-Dive
- **Intended question**: Which underliers have the most ETP coverage by AUM and product count?
- **Verdict**: Needs Polish
- **Findings**:
  - Page loads correctly. AUM trend chart per underlier works. AJAX panel update on underlier click works.
  - **Name awkward**: "Underlier Deep-Dive" in the breadcrumb; nav tab says "Underlier". The term "underlier" is internal jargon — users outside REX may find "Underlying Asset" or "Single-Stock Exposure" clearer.
  - **underlier_overrides.csv NOT applied**: The 19 Bloomberg mis-mappings documented in `config/rules/underlier_overrides.csv` are not wired into the market_data service (the override mechanism only exists for ETN MicroSectors, not for L&I/CC underliers). This means `DJTU` appears under its own ticker (`DJTU UA`) instead of `DJT US`, `CONX` under `BULL US` instead of `COIN US`, etc. — the underlier groupings are wrong for these 19 funds.
  - **Type toggle**: "Income" and "L&I Single Stock" tabs work. The Income tab correctly shows CC funds by underlier; L&I shows direction/leverage columns.
  - **Linkage**: Selecting an underlier shows products but the ticker cells have no links to `/market/fund/{ticker}`.
- **Fix recommendation**:
  1. Wire `underlier_overrides.csv` into the DB sync process or market_data load (apply the `corrected_value` for each `ticker`/`column_name` pair at load time).
  2. Add ticker → `/market/fund/{ticker}` links in the underlier product table.
  3. Consider renaming the nav tab to "Exposure" or "Single-Stock" to reduce jargon.

---

### /market/fund/NVDX — Fund Detail
- **Intended question**: Bloomberg DES-style overview of a single ETP.
- **Verdict**: Broken (silent data loss)
- **Findings**:
  - Basic data renders: AUM $503.2M, expense ratio 1.05%, spread 0.01%, volume 14.1M, inception Oct 18, 2023.
  - **map_li key mismatch (critical)**: `fund.underlier = d.get("map_li_underlier","")` and `fund.direction = d.get("map_li_direction","")` always return `""` because master DF uses prefixed key `q_category_attributes.map_li_underlier`. Result: the L&I badge section is completely blank for ALL L&I funds including NVDX — no "2x Long" direction badge, no "NVDA US" underlier tag, no leverage amount shown.
  - **Competitors section**: Because `underlier=""`, competitor filtering falls back to category match — which happens to work for NVDX (returns similar L&I funds). But the competitor section header says "Competitors ()" instead of "Competitors (NVDA US)".
  - **Total return chart**: `allSeries = {"NVDX": []}` — the series is empty. The scrape_total_returns call returns empty data for NVDX. Chart area is present but blank.
  - **SEC Filings cross-link**: Works correctly — `/funds/S000080899` link present (series_id lookup from FundStatus table).
  - **REX badge**: Renders correctly (green "REX" badge).
- **Fix recommendation**: Fix map_li key references. The total return empty series likely requires investigation of the scrape_total_returns script for NVDX specifically.

---

### /market/fund/DJTU — Fund Detail
- **Intended question**: Same as above for T-REX 2X Long DJT ETF.
- **Verdict**: Broken (silent data loss)
- **Findings**:
  - Same map_li key mismatch — no underlier/direction/leverage badges shown.
  - AUM shows $10.6M, expense ratio 1.05% — correct.
  - Screener API confirms `map_li_underlier = "DJT US"` (override IS in DB via prior fix) but not surfaced in the UI due to wrong key lookup.
  - SEC Filings link present: `/funds/S000087468`.
  - Total return series also empty.
- **Fix recommendation**: Same as NVDX above. The underlier_overrides.csv fix is already in the DB — the only blocker is the wrong key name in the router.

---

### /market/fund/ATCL — Fund Detail
- **Intended question**: Same as above for an ATCL fund.
- **Verdict**: Needs Polish
- **Findings**:
  - Page renders with correct title and basic metrics.
  - `map_li_underlier = None` from screener API — this fund has no L&I underlier mapping (possibly a CC or thematic fund).
  - Same absence of underlier badges as other funds.
  - Total return series empty.
- **Fix recommendation**: Confirm ATCL product category — if not L&I, the empty underlier badge section is correct. Fix the general key bug regardless.

---

### /strategy/ — Strategy Overview
- **Intended question**: What are the top whitespace opportunities for new L&I products?
- **Verdict**: Broken
- **Findings**:
  - Renders the `strategy/empty.html` template with message: "No whitespace parquet yet. Run /li-report or whitespace_v4 first."
  - The `whitespace_v4.parquet` file does not exist on the live deployment. The fallback to `whitespace_v1.parquet` also fails.
  - On-demand analysis (`/strategy/ticker/NVDA`) works fine because it calls `rank_against_universe()` directly without depending on the parquet.
  - **Data age**: Not applicable — no parquet, no data.
- **Fix recommendation**: Run `/li-report` or `whitespace_v4` pipeline on live deployment. Consider generating a nightly parquet as part of `run_daily.py` so this page never shows empty state.

---

### /strategy/whitespace — Whitespace Candidates
- **Intended question**: Ranked table of stock underliers with no existing REX or competitor L&I product.
- **Verdict**: Broken
- **Findings**: Same empty parquet condition as strategy home. Shows "No whitespace data. Run /li-report first."
  - The filters (sector, min_mcap, mentions, thematic) are all implemented server-side — they'll work once parquet exists.
  - Ticker column links to `/strategy/ticker/{ticker}` — correct pattern for drill-down.
- **Fix recommendation**: Same as strategy home — run the pipeline.

---

### /strategy/race — Filing Race Clock
- **Intended question**: Which competitor 485APOS filings are expected to launch imminently, giving REX a window to react?
- **Verdict**: Broken
- **Findings**:
  - `CADENCE_PARQUET` does not exist → "No cadence data. Run filing_race.py."
  - `RACE_PARQUET` also does not exist → the race table is empty.
  - The page renders (no 500 error) but shows only the empty-state message.
  - Both parquets are generated by `screener/li_engine/` pipeline — not part of `run_daily.py`.
- **Fix recommendation**: Add `filing_race.py` to the daily pipeline or provide a manual trigger from the admin panel.

---

### /strategy/ticker/NVDA — Ticker Deep-Dive
- **Intended question**: What is NVDA's whitespace score, and which REX/competitor products exist on it?
- **Verdict**: Healthy
- **Findings**:
  - Composite Score: +1.17, Rank 52 of 1494 — 97th percentile. Renders correctly.
  - Coverage block shows REX active products and competitor products.
  - Signal data (market cap, price, returns, options OI) populates on-demand via `rank_against_universe()`.
  - Page header says "NVDA — Information Technology" — sector correctly resolved.
  - **Linkage gap**: Coverage block shows `ticker — fund_name ($XXXM)` as text only (line 101 in strategy/ticker.html). No link to `/market/fund/{ticker}` or `/market/underlier?underlier=NVDA`.
- **Fix recommendation**: Make product coverage tickers clickable → `/market/fund/{ticker}`. Add "View underlier market" link → `/market/underlier?type=li&underlier=NVDA+US`.

---

### /strategy/ticker/COIN — Ticker Deep-Dive
- **Intended question**: Same as NVDA but for Coinbase.
- **Verdict**: Healthy
- **Findings**:
  - Composite Score: +0.49, Rank 185 of 1494 — 88th percentile. Renders correctly.
  - Same linkage gap as NVDA — product coverage is plain text.
  - Note: CONX (GraniteShares 2x Long COIN) is listed in `underlier_overrides.csv` as having `BULL US` instead of `COIN US` in the DB. The strategy ticker's product coverage may not correctly show CONX as a COIN underlier product because of this mis-mapping.
- **Fix recommendation**: Same as NVDA. The CONX underlier_overrides fix is required for correct coverage data here.

---

## Information Architecture Observations

### Calendar Pillar Promotion
**Verdict: Yes, but only after cleanup.**

The calendar concept — tracking ETP launch dates against a true 30/60-day compliance window — is genuinely useful and distinct from the Market Intelligence pillar. However, the current implementation is contaminated with variable annuity data and is therefore unsuitable for promotion in its current state.

After filtering for ETP-only entries (using the `mkt_master_data` trust whitelist or an ETP-specific flag), the Calendar would warrant its own top-level pillar, positioned between "Market" and "Strategy" in the mega-menu under a name like **"Pipeline"** or **"Launch Tracker"**.

Prerequisites for promotion:
1. Filter to ETP-only trusts (exclude `SEPARATE ACCOUNT`, `VARIABLE`, `APPRECIABLE ACCOUNT` patterns)
2. Fix effective date column (use `item.effective_date`, not `item.filing.effective_date`)
3. Add REX badge for known REX trust entries
4. Cross-link fund names to `/market/fund/{ticker}` where a match exists in `mkt_master_data`

### Issuer Detail Elevation
The issuer detail page works well but is completely undiscoverable. Recommended surfacing:
1. Add a "→" or "View Profile" button in the issuer table (not just the name as a link)
2. Add `/market/issuer/detail?issuer=REX` as a named entry in the Market mega-menu under a "Deep Dives" section
3. On the fund detail page (`/market/fund/{ticker}`), add an "Issuer Profile" link that goes to `/market/issuer/detail?issuer={fund.issuer}`

### Underlier Page Rename
Current names: "Underlier Deep-Dive" (breadcrumb) / "Underlier" (nav pill).

Both are functional but jargon-heavy. Suggested alternatives:
- **"Single-Stock Exposure"** — accurate for the L&I tab, but misleading for the Income tab
- **"Exposure Analysis"** — covers both L&I and CC, more intuitive
- **"By Underlying Asset"** — descriptive and self-explanatory

Recommendation: Rename nav pill to **"Exposure"** and breadcrumb to **"Exposure Analysis"**.

The underlier_overrides.csv fix is higher priority than the rename — the page currently shows wrong groupings for 19 funds.

### Compare Page: Rebuild vs. Polish
**Verdict: Polish, but fix the map_li bug first — that is a rebuild-level correctness issue.**

The page architecture is sound: autocomplete search, pill-based ticker selection, multi-period flow bar chart, total return growth chart with drawdown, stats table. The implementation is well-structured.

Issues that require targeted fixes (not a rebuild):
1. Fix `d.get("map_li_underlier","")` → `d.get("q_category_attributes.map_li_underlier","")` across all three occurrences
2. Remove dead `tickerInput` JS block in `market_scripts`
3. Add fund name → `/market/fund/{ticker}` links in the comparison table header row
4. Consider adding a "View in Screener" button that pre-populates the ETP Screener with the compared tickers

---

## Cross-Pillar Linkage Findings

### Market → Strategy
**What exists**: None. Market pages have no links to strategy pages.

**What's missing**:
- `/market/fund/{ticker}`: no "Whitespace Score" or "L&I Analysis" link to `/strategy/ticker/{ticker}`
- `/market/underlier`: no "Assess whitespace" CTA linking to `/strategy/ticker/{underlier_ticker}`
- `/market/category`: no "See whitespace candidates in this category" link to `/strategy/whitespace?...`

**Recommended minimum**: On `/market/fund/{ticker}`, add a sidebar card: "L&I Analysis — View {ticker}'s whitespace score →" linking to `/strategy/ticker/{ticker}`.

### Strategy → Market
**What exists**: Only via the site-wide mega-menu (all market pages accessible from any page). No direct Strategy → Market links in strategy templates.

**What's missing**:
- `/strategy/whitespace`: ticker cells link to `/strategy/ticker/{t}` (good) but not to `/market/fund/{t}` (which shows whether an ETP already exists)
- `/strategy/ticker/{t}`: coverage section lists fund names as plain text with no links to `/market/fund/{ticker}`
- `/strategy/race`: upcoming launches have no link to the relevant trust's SEC filing page or to calendar

**Recommended minimum**: In `strategy/ticker.html`, make coverage product tickers clickable → `/market/fund/{ticker}`.

### Strategy → Holdings (13F)
**Status**: Dormant. The strategy pages have no reference to the 13F holdings data. The mega-menu shows 13F Holdings as a separate pillar but there is no cross-link from strategy ticker analysis to "which institutions hold ETPs on this underlier."

This is architecturally correct (13F is its own pillar) but represents a missed insight connection: knowing NVDA's institutional ownership profile is directly relevant to assessing L&I product demand.

---

## Priority Fix Queue

| Priority | Page | Issue | Fix |
|----------|------|-------|-----|
| P0 | `/market/fund/{t}`, `/market/compare` | map_li key mismatch — silent blank badges | Change `d.get("map_li_underlier","")` to `d.get("q_category_attributes.map_li_underlier","")` in both routers |
| P0 | `/market/calendar` | VA wrapper contamination | Filter trust joins to ETP-only using mkt_master_data whitelist |
| P0 | `/market/calendar` | Effective date column always `--` | Use `item.effective_date` directly; remove `item.filing.effective_date` branch |
| P1 | All L&I pages | `underlier_overrides.csv` not applied | Wire CSV overrides into DB sync or market_data load path |
| P1 | `/strategy/`, `/strategy/whitespace`, `/strategy/race` | Empty parquet state | Run pipeline on live deployment; add to `run_daily.py` |
| P2 | `/market/issuer/detail` | Not discoverable | Add mega-menu entry; add → button in issuer table |
| P2 | Category, fund detail, issuer detail, underlier | No ticker → fund page links | Add `<a href="/market/fund/{{ ticker }}">` to table cells |
| P2 | `/strategy/ticker/{t}` | Coverage section plain text | Make product tickers linkable → `/market/fund/{ticker}` |
| P3 | `/market/rex-performance` | Dead available guard; naming mismatch | Remove lines 119-121; align nav pill and route naming |
| P3 | `/market/compare` | Dead `tickerInput` JS block | Remove dead script block from market_scripts |
| P3 | `/market/underlier` | Page name is jargon | Rename nav pill to "Exposure" |
| P3 | `/market/issuer` | Donut chart blank when cat=All | Hide chart or show placeholder when no category selected |
