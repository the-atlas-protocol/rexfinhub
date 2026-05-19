# Page Audit — Filings, Holdings, Screener Pillars
**Audit Date**: 2026-05-06  
**Auditor**: Audit Agent B  
**Method**: Live curl + template/router code review  
**Site**: https://rexfinhub.com (authenticated)

---

## Summary

| Pillar | Pages Audited | Healthy | Needs Polish | Broken | Wrong Architecture |
|--------|--------------|---------|--------------|--------|--------------------|
| Filings | 8 routes | 4 | 3 | 1 | 0 |
| Holdings | 5 routes | 0 | 0 | 5 | 0 |
| Screener | 7 routes | 2 | 3 | 2 | 0 |
| **Total** | **20** | **6** | **6** | **8** | **0** |

**Critical findings (top 3)**:
1. **`/filings/` hub has all 4 KPIs blank** — variable name mismatch between router and template (router sends `fund_count`/`filing_count`/`trust_count`; template expects `todays_filings`/`weekly_filings`/`effective_funds`/`trusts_monitored`).
2. **Entire Holdings pillar returns 404** — `ENABLE_13F` env var is not set on Render, so all 5 holdings routes are never registered. The nav links exist (visible in base.html menus) but go nowhere.
3. **`/screener/stock/{ticker}` always shows "No products found"** — competitive data loading is wrapped in `if not _ON_RENDER`, so every stock deep-dive page on the live site shows an "uncontested opportunity" false positive for every ticker including NVDA.

**13F Holdings status**: FULLY DORMANT. Routes exist in code but are conditioned on `ENABLE_13F` env var. Render does not have that var set. All 5 holdings URLs return `404 Not Found`. No ingestion pipeline exists. No data in DB.

**Landscape page size**: `/filings/landscape` returns 5.1 MB of HTML — all fund rows serialized into a single page. This is a performance and SEO concern.

---

## Filings Pillar

### `/filings/` — Filings Hub
**Intended question**: What is the current state of SEC filings and where do I go next?  
**HTTP**: 200  
**Verdict**: BROKEN

**Findings**:
1. **Question fit**: Intended as the landing page with KPI strip + 5 sub-page nav cards. Concept is correct.
2. **Visual — CRITICAL**: All 4 KPI cards render blank. Template uses `{{ todays_filings }}`, `{{ weekly_filings }}`, `{{ effective_funds }}`, `{{ trusts_monitored }}`. Router sends `fund_count`, `filing_count`, `trust_count`. None of these match. Four empty white boxes greet the user.
3. **Linkage**: The 5 nav cards link correctly to dashboard, landscape, candidates, evaluator, explorer. No link to `/filings/symbols` from this page.
4. **Freshness**: Cannot assess — KPIs are blank.
5. **Correctness**: N/A — no values rendered.

**Fix**: In `webapp/routers/filings.py`, `filings_hub()`, compute and pass `todays_filings`, `weekly_filings` (=`new_filings_7d`), `effective_funds`, `trusts_monitored` — or rename the template variables to match what the router already sends.

---

### `/filings/explorer` — Fund/Filing Explorer
**Intended question**: Search and browse all funds and filings in the DB.  
**HTTP**: 200  
**Verdict**: Healthy

**Findings**:
1. **Question fit**: Dual-mode (Funds / Filings tabs) with full-text search, status filter, trust dropdown. Answers the question well.
2. **Visual**: Header KPIs render correctly: 213,915 Funds / 628,091 Filings / 15,744 Trusts. Table shows fund rows with name, ticker, trust, status, filing date. Pagination works (page 1 of 3,944 for unfiltered).
3. **Linkage**: Tabs switch correctly. Trust filter dropdown is functional. Breadcrumb goes Home → Filings → Explorer.
4. **Freshness**: Includes today's data (2026-05-06 dates visible in rows). Fund count inflated (213K includes all SEC registrants, not just ETFs) — this is by design per the router comment about including all FundStatus rows.
5. **Correctness**: The default "All Trusts" dropdown includes 15,755 options (all active trusts). This is the full SEC universe, not just ETF trusts. For an ETF screener tool, this dropdown is overwhelming. Minor UX issue, not a bug.

**Fix**: Consider defaulting the trust dropdown to only ETF trusts (is_rex or entity_type=etf_trust). Low priority.

---

### `/filings/hub` — Hub Redirect
**Intended question**: N/A (redirect)  
**HTTP**: 301 → `/filings/`  
**Verdict**: Healthy

**Findings**: Clean 301 redirect to `/filings/`. No issues.

---

### `/filings/dashboard` — Filings Dashboard
**Intended question**: What filed this week? What are competitors doing? What's the overall pipeline health?  
**HTTP**: 200  
**Verdict**: Needs Polish

**Findings**:
1. **Question fit**: Covers today's brief (54 filings today, 5,780 this week), a recent filings table with filters, and a Competitor Watch panel. Answers the question.
2. **Visual**: KPI strip renders: Today=54, This Week=5,780, Effective=93,018, Trusts=15,744. Competitor Watch shows 9 non-REX trusts with recent 485BPOS/485APOS filings. Recent filings table renders real rows with date, form badge, trust, fund names. HOWEVER: the router computes a full `trust_list`, `all_trusts`, `trust_filter`, `status_counts` and trust grid pagination — none of this is used by the template. The template only shows a `filterTrusts()` JS search box. There's dead code in the router.
3. **Linkage**: Filing rows link to `/trusts/{slug}` and `/analysis/filing/{id}`. Competitor items link to `/filings/explorer?trust_id=X&date_range=7`. The "Analyze" link for each filing goes to `/analysis/filing/{id}` — valid route. The "View" link goes to SEC directly.
4. **Freshness**: Data is current (today's date visible). Competitor Watch covers last 7 days. The 5,780 "this week" figure appears very high — likely counting all prospectus forms from all 15K+ trusts (not just ETF trusts). No evidence of stale data.
5. **Correctness**: "This Week" count of 5,780 filings is plausible given 15K+ trusts in the DB. The Competitor Watch filter explicitly excludes REX/T-REX/MicroSectors — correct behavior.

**Fix**: Remove dead code in router (trust grid, trust_filter logic, paginated trust_list, status_counts) since the template doesn't use them. Reduces server-side query overhead significantly.

---

### `/filings/landscape` — L&I Landscape
**Intended question**: What leveraged product landscape exists? Which underliers are contested/contested? What has Rex filed vs competitors?  
**HTTP**: 200  
**Verdict**: Needs Polish

**Findings**:
1. **Question fit**: Filings mode shows a 1,160-underlier matrix with issuer presence markers. Products mode shows 470–737 Bloomberg products. Both answer the question.
2. **Visual — CRITICAL PERFORMANCE**: Page is 5.1 MB HTML. All 1,160 fund rows are serialized into the HTML in a JS data blob for client-side filtering. This is the source of the size. Slow initial load; no SEO indexing of fund data.
3. **Linkage**: Subnav links to Dashboard, Candidates, Evaluator, Explorer, Symbols all present. Mode toggle between "filings" and "products" works. Export link exists (`/filings/landscape/export`).
4. **Freshness**: "as of: May 06, 2026 | May 05, 2026 21:05" — data is current. Generated at timestamp visible.
5. **Correctness**: Products mode has Bloomberg data (470 products shown) — this contradicts `/screener/rex-funds` which shows "No data loaded." The cached analysis from `get_3x_data()` has Bloomberg data; the `data_loader.load_etp_data()` path used by rex-funds does not. Inconsistency in data source routing.

**Fix**: Server-side paginate the fund rows table; pass only the visible page (50 rows) to HTML instead of all 1,160+. Reduce HTML from 5.1 MB to ~100 KB. The JS filtering currently works client-side but the payload is too large.

---

### `/filings/candidates` — L&I Filing Candidates
**Intended question**: Which stocks should REX file leveraged ETFs on next?  
**HTTP**: 200  
**Verdict**: Healthy

**Findings**:
1. **Question fit**: Shows tiered candidates (Tier 1/2/3) with scores, plus 4x and 2x market context. Answers the question clearly.
2. **Visual**: Tier 1 has 1 row (TSLA, score=95). Full 1,212 data rows across all tiers. 4x candidates section shows 0 rows (no 4x opportunities identified). 2x Market Context shows 100 products. Data renders correctly.
3. **Linkage**: No deep link from a candidate ticker to `/screener/stock/{ticker}` for the competitive deep-dive. A user sees TSLA as Tier 1 but can't click to understand the competitive landscape for it.
4. **Freshness**: Data date: May 05, 2026 21:05 — yesterday's Bloomberg pull. Acceptable.
5. **Correctness**: Tier labels exist in the template as "Tier 1 - File Immediately" etc. However, Tier 1 having only 1 candidate (TSLA) and 4x section being empty may warrant investigation — could be correct scoring, could be a filtering bug.

**Fix**: Add per-ticker links to `/screener/stock/{ticker}` from the candidate rows. This is a missing navigation link, not a data bug.

---

### `/filings/evaluator` — L&I Stock Evaluator
**Intended question**: Can I evaluate any ticker on demand for ETF filing viability?  
**HTTP**: 200  
**Verdict**: Needs Polish

**Findings**:
1. **Question fit**: Provides an interactive form to evaluate tickers. On Render, falls back to pre-cached evaluations (top 100 tickers).
2. **Visual**: Form renders correctly (ticker input, Evaluate button). On Render, the `data_available` variable is passed as `False` (Bloomberg scorer can't run) but the template's `{% elif not data_available %}` block shows "Using cached evaluations. Top 100 tickers pre-scored." — this notice is NOT rendering on the live site (not found in HTML). This means users may not know they're getting cached results.
3. **Linkage**: Breadcrumb goes Home → Filings → Evaluator. No link back to Candidates for context. `Evaluation Error` heading is in the JS error handler template (correct) — not a visible rendered error.
4. **Freshness**: Cached evaluations are based on yesterday's Bloomberg data (May 05). Acceptable.
5. **Correctness**: The `data_available()` function call in the router returns what `get_3x_data()` returns — if 3x analysis cache has data, `data_available` could be `True` even on Render, which would hide the cached-results notice. Needs verification of the exact state.

**Fix**: Audit `data_available()` return value on Render — if it's returning `True` incorrectly, the cached-evaluations notice is suppressed and users get no feedback about Render limitations.

---

### `/filings/symbols` — CBOE Symbol Reservations
**Intended question**: Which tickers are available, reserved, or active in the CBOE ticker universe?  
**HTTP**: 200  
**Verdict**: BROKEN

**Findings**:
1. **Question fit**: Correct concept — shows CBOE ticker reservation state for competitor intelligence.
2. **Visual — CRITICAL**: Zero data. KPI cards show all zeros. Table shows "0 tickers." Last scan: "No scans yet." The CBOE bulk scan has never been run on the production DB. The page renders correctly structurally but contains no data.
3. **Linkage**: Page is linked from the filings subnav and the mega-menu. Users arrive to an empty state with no onboarding message explaining how to populate it.
4. **Freshness**: No data whatsoever — not stale, just absent.
5. **Correctness**: N/A — no data to verify.

**Fix**: Two paths: (a) run the initial CBOE bulk scan locally and upload the result to Render's persistent disk, or (b) add a clear empty-state message: "No CBOE scan data yet. Run the nightly CBOE scraper to populate." Currently the page shows a structurally correct but completely empty shell, which is confusing.

---

## Holdings Pillar

**Pillar status: FULLY DORMANT**

All 5 holdings routes (`/holdings/`, `/holdings/crossover`, `/holdings/fund/SOXL`, `/holdings/{cik}/history`, `/holdings/{cik}`) return **404 Not Found** on the live site. This is by design: the router is only registered when `ENABLE_13F=1` is set as an environment variable. Render does not have this variable set.

The holdings router code (`webapp/routers/holdings.py`) is fully implemented — institution list, crossover analysis, fund holders, institution history, institution detail — all with complete ORM queries and template renders. The templates also exist (`holdings.html`, `crossover.html`, `holdings_fund.html`, `institution.html`, `institution_history.html`). There is simply no data, and the routes are not registered.

**Navigation issue**: The base nav/mega-menu likely has links to holdings pages that go nowhere. These 404s are visible to authenticated users.

---

### `/holdings/` — Institution List
**Verdict**: BROKEN (dormant — 404)

**Findings**: Route not registered. Would show institution list with AUM sort. No ingestion pipeline to populate `institutions` or `holdings` tables. Would render correctly if data existed and `ENABLE_13F` were set.

---

### `/holdings/crossover` — Crossover Analysis
**Verdict**: BROKEN (dormant — 404)

**Findings**: Route not registered. Designed to show institutions holding competitors but not REX products — high-value sales intelligence. Depends on Bloomberg master data AND 13F holdings data simultaneously. Double dependency on data sources that are both absent on Render.

---

### `/holdings/fund/SOXL` — Fund Holders View
**Verdict**: BROKEN (dormant — 404)

**Findings**: Route not registered. Would show institutional holders of SOXL with QoQ changes. CUSIP mapping tables also empty.

---

### `/holdings/{cik}/history` — Institution History
**Verdict**: BROKEN (dormant — 404)

**Findings**: Route not registered. Would show quarterly position change history for a specific institution.

---

### `/holdings/{cik}` — Institution Detail
**Verdict**: BROKEN (dormant — 404)

**Findings**: Route not registered. Would show all positions held by an institution with REX product highlighting.

---

## Screener Pillar

**Note**: Several screener routes are 301 redirects to filings URLs (old URLs moved). The redirects work correctly.

---

### `/screener/` — (Redirect)
**HTTP**: 301 → `/filings/landscape`  
**Verdict**: Healthy

Old screener landing now redirects to the landscape page. Working.

---

### `/screener/3x-analysis` — (Redirect)
**HTTP**: 301 → `/filings/candidates`  
**Verdict**: Healthy

301 redirect to candidates page. Working.

---

### `/screener/4x` — (Redirect)
**HTTP**: 301 → `/filings/candidates`  
**Verdict**: Healthy

301 redirect to candidates page. Working.

---

### `/screener/evaluate` — (Redirect)
**HTTP**: 301 → `/filings/evaluator`  
**Verdict**: Healthy

301 redirect to evaluator page. Working.

---

### `/screener/rex-funds` — REX Track Record
**Intended question**: How is the REX product portfolio performing? What are fund flows?  
**HTTP**: 200  
**Verdict**: BROKEN

**Findings**:
1. **Question fit**: Correct concept — portfolio health dashboard for REX products.
2. **Visual — CRITICAL**: Shows "No data loaded. Run the daily pipeline to score Bloomberg data." The `product_groups` dict is empty. All KPIs are zero. This is because `/screener/rex-funds` uses `load_etp_data()` directly (path: `data/SCREENER/data.xlsx`) which is wrapped in `if not _ON_RENDER` — so on Render, the ETP data load never executes. Only the `get_3x_data()` cache is consulted (for `rex_track`), but without the direct Bloomberg load the product groups are empty.
3. **Linkage**: Subnav present. No links to individual fund pages.
4. **Freshness**: Not applicable — no data rendered.
5. **Correctness**: Inconsistency: `/filings/landscape?mode=products` shows 470–737 Bloomberg products (from `get_3x_data()` cache), but `/screener/rex-funds` shows nothing because it uses a different data-loading path. Both should use the cache.

**Fix**: Wrap `product_groups` population to use `get_3x_data()` cache (same as `risk_watchlist`, `tiers`, etc.) rather than `load_etp_data()` directly. The 3x analysis cache already contains `li_products` and related data — extend it to include `rex_products`.

---

### `/screener/risk` — Risk Watchlist
**Intended question**: Which underliers carry high/extreme volatility risk for leveraged ETF launch decisions?  
**HTTP**: 200  
**Verdict**: Healthy

**Findings**:
1. **Question fit**: Shows 103 rows of risk-assessed underliers (EXTREME, HIGH, MEDIUM, LOW). Clear methodology.
2. **Visual**: Table renders with 103 data rows. Risk levels color-coded. Sample rows: TSLA ($5.7B, 43.3% vol), NVDA ($4.9B, 37.8% vol), MU ($2.5B, 77.9% vol). Data is from yesterday's Bloomberg pull (May 05, 2026 21:05).
3. **Linkage**: Each ticker in the risk table links to `/screener/stock/{ticker}` (e.g. `/screener/stock/TSLA US`). However, the `/screener/stock/{ticker}` page shows "No products" on Render (see below), making these links lead to false positives. The URL format includes " US" suffix (Bloomberg format) — may cause issues with some tickers.
4. **Freshness**: Data date clearly shown: "as of May 05, 2026". One-day lag is acceptable.
5. **Correctness**: Risk levels and vol figures appear consistent with known data (TSLA high vol = expected). No apparent misclassification from the sample viewed.

---

### `/screener/stock/NVDA` — Stock Competitive Deep Dive
**Intended question**: What leveraged products exist on NVDA? What's the competitive landscape?  
**HTTP**: 200  
**Verdict**: BROKEN

**Findings**:
1. **Question fit**: Correct concept — per-underlier competitive analysis.
2. **Visual — CRITICAL CORRECTNESS BUG**: Page renders heading "No existing leveraged products found for NVDA" and body text "This is an uncontested opportunity - no competing leveraged ETFs exist on this underlier." This is factually wrong. NVDA has NVDL, NVDU, NVDD, NVDX and other leveraged products. The `products = []` because the competitive data loading is in `if not _ON_RENDER` block — so on Render it never executes. The page displays a false "opportunity" signal.
3. **Linkage**: The risk watchlist links here with Bloomberg-format tickers (e.g., `/screener/stock/TSLA US`). The router uses `ticker.replace(" US", "")` for display but the underlying data lookup uses `underlier_bb = f"{ticker_clean} US"` — both of which are Render-blocked.
4. **Freshness**: N/A — no data loads on Render.
5. **Correctness — CRITICAL**: Displaying "uncontested opportunity" for NVDA is factually incorrect and could mislead filing decisions. This is not just missing data — it's actively wrong data.

**Fix**: Either (a) use the `get_3x_data()` cache to serve competitive data on Render (the cache has `li_products` which contains all products), or (b) add an explicit "This page is not available on the web version. Run locally to see competitive analysis" message when `_ON_RENDER` is True. Option (b) is the immediate fix; option (a) is the correct long-term fix.

---

## Pillar-Specific Observations

### Filings Pillar
- **Architecture is sound**: The migration of screener pages into `/filings/` routes (landscape, candidates, evaluator) was well-executed. The 301 redirects from old URLs are clean.
- **Router/template variable drift**: The `filings_hub()` function is clearly a recent addition or refactor that introduced a variable name mismatch. This should have been caught at integration time.
- **Dashboard over-queries**: The router computes full trust grid statistics (`_trust_stats()` — a 3-join SQL query across all active trusts) but the template only uses the data in a JS array for a search box. The trust grid pagination code is dead. This is at least one unnecessary heavy query per dashboard load.
- **Symbols page needs first-run data**: CBOE bulk scan has never been executed against production. Page is structurally complete but empty.

### Holdings Pillar
- **Dormant by design**: `ENABLE_13F=1` environment variable is the activation switch. The code is complete. The pillar needs: (1) env var on Render, (2) `13f_holdings.db` populated with actual 13F filings, (3) CUSIP mappings built.
- **Nav links to dead routes**: The mega-menu and/or nav likely exposes holdings links that 404. This is user-facing breakage even if the dormancy is intentional.
- **Crossover analysis is high-value**: The crossover page (find institutions holding competitors but not REX) is the most strategically important holdings page. Once activated, it directly supports sales team prospecting.

### Screener Pillar
- **`_ON_RENDER` guard is too broad**: The same pattern (`if not _ON_RENDER`) is blocking both `/screener/rex-funds` and `/screener/stock/{ticker}`. Rather than returning empty/broken pages on Render, these should fall back to cached analysis data. The `get_3x_data()` cache is already present and populated — it's just not being used by these routes.
- **Data source split**: Two data sources for Bloomberg data: (1) `get_3x_data()` cache (works on Render), (2) `load_etp_data()` from xlsx (local only). Routes inconsistently use one or the other.
- **Risk page is the screener's best page**: Clean data, correct linkage, clear risk levels. The only issue is that the links go to broken stock detail pages.

---

## Cross-Pillar Linkage Findings

1. **Landscape → Candidates**: The landscape issuer scorecard has no link to the candidates page. A user viewing the landscape matrix naturally wants to see "which of these should we file on?" — that link is missing.
2. **Dashboard → Symbols**: The filings dashboard has no mention of CBOE symbol reservation status. This is relevant context when a competitor files a new fund — is the ticker reserved?
3. **Risk → Stock Detail**: Risk watchlist links to stock detail pages, but stock detail pages are broken on Render. This is a broken internal navigation chain.
4. **Holdings → Funds**: When 13F is activated, the holdings crossover page will need to link back to `/filings/explorer` for funds on the same underlier. This link is planned in the template but will need validation once holdings data exists.
5. **Screener subnav**: The `_screener_subnav.html` and `_filings_subnav.html` are distinct includes. Some filings pages include the filings subnav; some screener pages include the screener subnav. There is no unified pillar navigation — a user at `/filings/candidates` can't easily see `/screener/risk`.

---

## Fix Priority Queue

| Priority | Page | Issue | Effort |
|----------|------|-------|--------|
| P0 | `/filings/` | 4 KPI cards blank (variable mismatch) | 15 min |
| P0 | `/screener/stock/{ticker}` | False "uncontested opportunity" on Render | 1 hr |
| P1 | `/filings/symbols` | No data — first CBOE scan needed | 30 min (run scan) |
| P1 | `/screener/rex-funds` | "No data" — use `get_3x_data()` cache instead | 2 hrs |
| P2 | `/filings/landscape` | 5.1 MB HTML — paginate server-side | 4 hrs |
| P2 | `/filings/dashboard` | Dead router code (trust grid queries) | 1 hr cleanup |
| P3 | Holdings pillar | Set `ENABLE_13F=1`, populate DB, remove nav links until ready | Phase 3 |
| P3 | `/filings/evaluator` | Cached-results notice not rendering — audit `data_available()` | 1 hr |

---

*Audit Agent B — 2026-05-06*
