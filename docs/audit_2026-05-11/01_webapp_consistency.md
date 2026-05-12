# Stage 1 Audit — Webapp Cross-Page Consistency
Generated: 2026-05-11T18:55:00Z
Agent: webapp_consistency
Site under audit: https://rex-etp-tracker.onrender.com (rexfinhub.com)
DB snapshot: C:/Projects/rexfinhub/rexfinhub.db (mkt_master_data: 7,361 rows; fund_status: 213,810 rows)
Auth: SITE_PASSWORD from `config/.env` (read-only browse, never touched admin endpoints)

## Summary

22 findings across the five representative funds (NVDX, MSTU, JEPI, NVDL, VOO). The user's stated complaint — "tickers being applied to wrong funds" — is **confirmed and reproducible** in three distinct ways:

1. **Underlier-string fragmentation (CRITICAL):** `map_li_underlier` is stored in two formats (`'NVDA'` vs `'NVDA US'`) for the same underlying stock. The `/stocks/{ticker}` route only matches one format, so `/stocks/NVDA` shows just **2 of the 21 NVDA-underlier ETFs** in the database — missing NVDX ($521M), NVDL ($3.7B), NVDU ($657M), NVDD, NVDQ, NVDS, NVDB, NVDG, NVDY, NVDW, NVII, etc. Same bug on `/market/underlier`.
2. **Ticker-collision in fund_status (CRITICAL):** 12,853 distinct tickers (38% of the 33,501 distinct symbols in fund_status) appear in more than one row. VOO appears in 5 trust rows (5 different Vanguard trust CIKs all claiming VOO). BUFC simultaneously labels "AB Core Bond ETF", "AB Tax-Aware Intermediate Municipal ETF", "AB Corporate Bond ETF", and "AB Disruptors ETF". SYM is attributed to 84 different MFS fund series. This is a same-trust ticker-bleed (a known issue per the project CLAUDE.md) but is producing very wrong displays in `/funds/series/{id}` and any aggregation that joins on ticker.
3. **Schema rot — primary_strategy column 100% NULL (HIGH):** `mkt_master_data.primary_strategy`, `sub_strategy`, `strategy`, `asset_class`, `underlier_name`, `root_underlier_name`, `leverage_ratio`, `direction` are NULL for **all 7,361 rows**. The webapp falls back to deprecated columns (`strategy` is empty, displayed as `nan`).

Cross-page consistency score for the 5 funds (per critical field): **62 / 100**. Mostly OK on issuer + AUM + status; broken on category, sub-strategy, and underlier-keyed surfaces.

## Cross-page matrix (5 funds × 8 surfaces × 6 fields)

Legend: `—` = field not surfaced on that page; `MISS` = fund missing; `nan` = literal NaN string rendered.

| Fund | Field | DB (mkt_master_data) | /funds/{tk} | /issuers/{name} | /market/underlier | /stocks/{underlier} | /operations/products | /strategy/ticker/{tk} | /filings/{id} |
|---|---|---|---|---|---|---|---|---|---|
| NVDX | Issuer | `ETF Opportunities Trust/REX Sh` (raw) → `REX` (display) | REX | REX | — | n/a (fund tile) | REX | REX | n/a (no ticker chip) |
| NVDX | Category | LI (etp_category) | "Leverage & Inverse - Single Stock" | (in REX issuer table) | bucket NVDA US | **MISS** at /stocks/NVDA | LI | L&I | — |
| NVDX | AUM | 545.47 ($545M) | $521.1M | (issuer total) | (in $1.7B NVDA US bucket) | **MISS** | **not displayed** | — | — |
| NVDX | Sub-strategy | NULL (col empty) | "Long" (from map_li_direction) | — | — | — | — | "L&I / Long" | — |
| NVDX | Underlier | `NVDA US` (map_li_underlier) | NVDA US | — | NVDA US | (would need /stocks/NVDA US — broken) | NVDA | NVDA | — |
| NVDX | Status | ACTV (master) / EFFECTIVE (fund_status) | EFFECTIVE | (active in REX list) | — | **MISS** | Listed | — | — |
| MSTU | Issuer | REX | REX | REX | — | **MISS** at /stocks/MSTR | REX | REX | — |
| MSTU | Category | LI | "Leverage & Inverse - Single Stock" | (in REX list) | bucket MSTR US | **MISS** | LI | L&I | — |
| MSTU | AUM | 819.39 ($819M) | $817.7M | — | (in MSTR US bucket $1.6B) | **MISS** | — | — | — |
| MSTU | Sub-strategy | NULL | "Long" | — | — | — | — | "L&I / Long" | — |
| JEPI | Issuer | `JP Morgan Exchange-Traded Fund` → `JP Morgan` | (no chip in H1 — see F4) | JP Morgan | — | n/a | n/a (not REX) | n/a | — |
| JEPI | Category | CC | "Income - Index/Basket/ETF Based" | — | — | — | — | — | — |
| JEPI | AUM | 44,659.88 ($44.7B) | $45.0B | (in issuer total) | — | — | — | — | — |
| JEPI | Sub-strategy | NULL | "nan" (literal) | — | — | — | — | — | — |
| JEPI | Underlier | NULL | "nan" (literal) | — | — | — | — | — | — |
| NVDL | Issuer | GraniteShares | (no chip) | GraniteShares | — | **MISS** at /stocks/NVDA | n/a | GraniteShares | — |
| NVDL | Category | LI | "Leverage & Inverse - Single Stock" | (in GraniteShares list) | bucket NVDA US | **MISS** | n/a | L&I | — |
| NVDL | AUM | 4,246.47 ($4.2B) | $3.7B (12% drift!) | (in issuer total) | — | **MISS** | n/a | — | — |
| NVDL | Underlier | `NVDA US` | NVDA US | — | (NVDA US bucket) | **MISS** | n/a | NVDA | — |
| VOO | Issuer | `Vanguard Index Funds` (raw); issuer_display=NULL | "Vanguard" (from where? — see F8) | Vanguard | — | n/a | n/a | n/a | — |
| VOO | Category | NULL (etp_category) | "nan" (literal "Vanguard · nan · ETF") | — | — | — | n/a | n/a | — |
| VOO | AUM | 955,277.71 ($955B) | $935.6B (2% drift) | — | — | — | — | — | — |
| VOO | Sub-strategy | NULL | "nan" | — | — | — | — | — | — |
| VOO | Status | ACTV | EFFECTIVE | — | — | — | — | — | — |
| VOO | fund_status duplicates | 5 rows (5 Vanguard trusts) | (canonical /funds/VOO renders the 497J row) | — | — | — | — | — | — |

## Findings (sorted by severity)

### F1: `/stocks/{underlier}` matches 2 of 21 NVDA-linked ETFs — CRITICAL
- **Fund(s) affected:** All 21 NVDA-underlier ETFs (NVDX, NVDL, NVDU, NVDD, NVDQ, NVDS, NVDB, NVDG, NVDY, NVDW, NVII, etc.). Same pattern affects every leveraged-stock underlier.
- **Severity:** critical
- **Pages involved:** `/stocks/NVDA`, `/stocks/MSTR`, every `/stocks/{ticker}` for an underlier
- **Symptom:** `/stocks/NVDA` claims "ETP Coverage (2 products tracking NVDA)" and lists only `2572986D` ($1.89M, unlaunched Tradr placeholder) and `NVDK` ($0M Tuttle covered call). NVDX ($521M) and NVDL ($3.7B) — the two largest 2x NVDA leveraged ETFs in the world — are absent.
- **Evidence:** Route handler at `webapp/routers/stocks.py:103-114` runs:
  ```sql
  WHERE UPPER(TRIM(map_li_underlier)) = 'NVDA' OR UPPER(TRIM(map_cc_underlier)) = 'NVDA'
  ```
  but the DB stores `'NVDA US'` for 16 LI funds and bare `'NVDA'` for only 1. Direct DB queries:
  - `t='NVDA'` → 2 rows
  - `t='NVDA US'` → 17 rows
- **Blast radius:** Every `/stocks/{ticker}` page for any underlier where the LI map column uses the `<TICKER> US` Bloomberg convention. From DB stats: 293 LI funds use `<TICKER> US`, 164 use bare `<TICKER>`. ~80% of LI underlier views are broken.
- **Hypothesis:** When the `map_li_underlier` and `map_cc_underlier` were populated, two separate normalization rules ran (one stripping ` US`, one preserving it). The stocks-detail join was written assuming bare format only.
- **Fix size:** small (one-line normalization in the WHERE clause, plus a backfill of the column to a single canonical form)

### F2: `/market/underlier` shows the same underlier as TWO buckets ("NVDA US" and "NVDA") — CRITICAL
- **Fund(s) affected:** Same as F1 (NVDA, MSTR, TSLA, CVNA, RBLX visible from the page output).
- **Severity:** critical
- **Pages involved:** `/market/underlier`
- **Symptom:** Underlier-summary view lists `NVDA US $1.7B (6)` and ALSO `NVDA $0.00M (1)` — same stock, two buckets. Clicking `NVDA` shows just NVDK; clicking `NVDA US` shows NVDY/NVDW/NVII (CC-only, NO L&I funds visible). User cannot find NVDX or NVDL on either underlier surface.
- **Evidence:** `temp/audit_pg_market_underlier_NVDA.html` body text contains:
  ```
  ... NVDA US $1.7B (6) ... NVDA $0.00M (1) ... NVDA ▾ 1 Products  Ticker  NVDK US ...
  ```
- **Blast radius:** Same 80% of LI underlier views as F1, plus this is the canonical "discover ETFs by stock" surface.
- **Hypothesis:** Same root cause as F1 — bifurcated underlier normalization in `mkt_master_data.map_li_underlier` and `map_cc_underlier`.
- **Fix size:** small (data fix on the column) + small (drop the duplicate bucket on the underlier landing page)

### F3: `mkt_master_data.primary_strategy` and 7 sibling columns are 100% NULL — HIGH
- **Fund(s) affected:** All 7,361 rows — every fund in the system.
- **Severity:** high
- **Pages involved:** `/funds/{ticker}` (renders literal "nan"), `/strategy/*` (500), any future report relying on these fields.
- **Symptom:** JEPI fund page renders `JP Morgan · Income - Index/Basket/ETF Based · ETF` correctly, but its KPI tile shows `Sub-Strategy: nan` and `Underlier: nan`. VOO's sub-header reads literally `Vanguard · nan · ETF`. The "nan" leaks come from pandas → Jinja template path; all 7,361 rows have NULL for `primary_strategy`, `sub_strategy`, `strategy`, `asset_class`, `underlier_name`, `root_underlier_name`, `leverage_ratio`, `direction`.
- **Evidence:**
  ```
  SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NULL → 7361
  ```
- **Blast radius:** Every fund page. Every strategy-page query. Every export that includes these fields.
- **Hypothesis:** The pipeline that should populate `primary_strategy`/`sub_strategy` (likely a merger from `mkt_fund_classification.strategy` → master table) never ran or is silently failing. Note: `mkt_fund_classification` HAS the data — JEPI's classification row shows `strategy='Income / Covered Call', income_strategy='Premium Income'`. The webapp is reading from the wrong column.
- **Fix size:** medium (either backfill `mkt_master_data.primary_strategy` from `mkt_fund_classification.strategy`, or change the template to read from the classification table)

### F4: VOO has 5 duplicate fund_status rows (one per Vanguard trust CIK) — HIGH
- **Fund(s) affected:** VOO (and 12,852 other tickers). VOO is the canonical example because it shows on the live site.
- **Severity:** high
- **Pages involved:** `/funds/VOO` resolves to one of 5 candidate rows (preferred via the EFFECTIVE / latest_filing_date sort in `webapp/routers/funds.py:497-505`). `/funds/series/{S000002839}` would have the same issue.
- **Symptom:** `fund_status` has 5 rows for ticker=VOO, all with series_id=`S000002839`, all marked EFFECTIVE, in 5 different trusts (Vanguard World Fund / Scottsdale / Admiral / Malvern / Wellington). The route's `LIMIT 1` masks this on the canonical page, but data exports and filing rollups likely double-count.
- **Evidence:**
  ```
  SELECT id,trust_id,fund_name,ticker,series_id,latest_form FROM fund_status WHERE ticker='VOO'
  → 5 rows (trusts 227,228,229,230,231 all 'ETF Shares' / S000002839 / EFFECTIVE)
  ```
- **Blast radius:** 12,853 distinct tickers (38% of all distinct tickers) appear in more than one fund_status row. SYM appears 1,498 times across 84 series in MFS trusts — the worst case. BUFC labels 4 different AB ETFs.
- **Hypothesis:** Same-trust ticker bleed (already documented in CLAUDE.md "Known Issues") combined with multi-trust filings naming the same series. Step3 extraction is not deduping by (CIK + series_id).
- **Fix size:** medium (add a uniqueness constraint or post-extraction dedupe on `(trust_id, series_id)`); architectural if you want to keep historical rows but mark non-canonical.

### F5: `/funds/{ticker}` page header omits issuer chip for non-REX funds — MEDIUM
- **Fund(s) affected:** JEPI, NVDL, VOO (all non-REX); NVDX/MSTU show "REX" chip beside H1.
- **Severity:** medium
- **Pages involved:** `/funds/JEPI`, `/funds/NVDL`, `/funds/VOO`
- **Symptom:** NVDX page H1 = `NVDX  REX`. JEPI page H1 = `JEPI` (no issuer chip). The sub-header line shows "JP Morgan · Income …" but the visual chip pattern (used to make REX funds stand out) is conditional and missing for everyone else, creating the impression that the page is anonymous.
- **Evidence:** Direct H1 extraction:
  - NVDX: `<h1>NVDX</h1> ... <span class="rex-chip">REX</span>`
  - JEPI: `<h1>JEPI</h1>` (no chip element)
- **Blast radius:** ~6,400 non-REX funds.
- **Hypothesis:** Conditional template `{% if is_rex %}` guards the chip. Should render an issuer chip for everyone using `issuer_display`.
- **Fix size:** trivial (template change)

### F6: NVDL AUM drifts 12% between DB ($4.2B) and `/funds/NVDL` page ($3.7B) — MEDIUM
- **Fund(s) affected:** NVDL. Probably similar small drifts across all funds.
- **Severity:** medium
- **Pages involved:** `/funds/NVDL`
- **Symptom:** `mkt_master_data.aum = 4246.47` (i.e. $4.2B). Page KPI tile reads `$3.7B`. That's $546M off.
- **Evidence:** DB scalar; HTML extraction.
- **Hypothesis:** The page is reading AUM from a different cache (the screener parquet, not the master table), and they were synced at different times. Or `aum` is in M, the formatter is using a stale denominator.
- **Fix size:** small (audit the aum source in the funds router and align with master table)

### F7: VOO AUM drifts 2% between DB ($955B) and page ($935.6B) — LOW
- **Fund(s) affected:** VOO
- **Severity:** low
- **Pages involved:** `/funds/VOO`
- **Symptom:** DB has $955.3B, page shows $935.6B (-$19.7B).
- **Hypothesis:** Same as F6 — different snapshots.
- **Fix size:** small (single source of truth for AUM)

### F8: VOO has NULL `issuer_display` in DB but page renders "Vanguard" — LOW (but informative)
- **Fund(s) affected:** VOO and ~5,093 other rows with NULL issuer_display.
- **Severity:** low
- **Pages involved:** `/funds/VOO`
- **Symptom:** `mkt_master_data.issuer_display = NULL`, `etp_category = NULL`, but page sub-header says `Vanguard · nan · ETF`. So "Vanguard" is being derived from elsewhere (probably `issuer` field stripped of suffix), but the category fallback fails to "nan".
- **Hypothesis:** Two separate fallback chains, one working (issuer → strip-suffix), one not (etp_category → no fallback → renders pandas NaN).
- **Fix size:** trivial (fix the category fallback the same way issuer was fixed)

### F9: `/strategy` and `/strategy/whitespace` return HTTP 500 — HIGH
- **Fund(s) affected:** N/A (page-level)
- **Severity:** high
- **Pages involved:** `/strategy`, `/strategy/whitespace`
- **Symptom:** Both routes return body `Internal Server Error` (HTTP 500) instead of the templates' "no data" empty-state. The empty-state branch in `webapp/routers/strategy.py:71-75` should fire when the parquet is missing, but doesn't.
- **Hypothesis:** `_load_whitespace()` raises rather than returning empty. Probably FileNotFoundError instead of empty DataFrame. A 500 hides the same broken data as F3.
- **Fix size:** trivial (wrap the load in try/except OR check parquet existence before pd.read_parquet)

### F10: `/stocks/NVDA` shows "Filing Whitespace: No 485APOS filings on record name NVDA" — MEDIUM (false negative)
- **Fund(s) affected:** All NVDA-derived filing pipeline products.
- **Severity:** medium
- **Pages involved:** `/stocks/NVDA`
- **Symptom:** Claims no 485APOS filings name NVDA, even though REX, GraniteShares, Direxion, Tradr, ProShares, LeverageShares, YieldMax all have active prospectuses naming NVDA. The "filed_underliers.parquet" lookup is empty or stale.
- **Blast radius:** Underlier whitespace claim is a feature of the page; it's wrong for every actively-filed underlier.
- **Fix size:** small (rebuild filed_underliers.parquet)

### F11: `JEPI`, `NVDL`, `VOO` not present in /filings detail pages — N/A but worth noting
- **Severity:** low
- **Pages involved:** `/filings/{id}` for filings 624743 (REX), 2096 (GraniteShares), 609772 (JPMorgan), 608802 (Vanguard).
- **Symptom:** Filing detail pages list fund **names** (e.g. "T-REX 2X LONG NVIDIA DAILY TARGET ETF") but never the **ticker chip** (NVDX). Users can't click from a filing to a fund page.
- **Fix size:** small (add ticker chips beside fund names in the "Funds in Filing" list)

### F12: `/stocks` returns 404 — MEDIUM
- **Fund(s) affected:** N/A
- **Severity:** medium
- **Pages involved:** `/stocks`, `/stocks/`
- **Symptom:** The collection root `GET /stocks` returns 404 (only `/stocks/{ticker}` works). The nav menu has a "Stocks" link — does that point to `/market/stocks/` or `/stocks/`? Worth checking; if the nav points to `/stocks/`, every nav click 404s.
- **Hypothesis:** Index page exists at `/market/stocks/` (line 165 of stocks.py) but not at `/stocks/`. Naming inconsistency.
- **Fix size:** trivial (add a `/stocks/` redirect)

### F13: `/screener/3x-analysis` and `/screener/4x` are 301s to the same page — LOW
- **Severity:** low (intentional, but creates 5.8s+2.7s round-trip)
- **Pages involved:** `/screener/3x-analysis`, `/screener/4x`
- **Symptom:** Both URLs 301 to `/tools/li/candidates`. Confirmed source: `webapp/routers/screener.py:40-47`. Functional — but `/tools/li/candidates` itself takes 2.6s to render. Each of the two redirect targets adds 5.8s and 2.7s of latency on top.
- **Fix size:** none required; flagging for awareness.

### F14: Massive duplicate fund_status row counts — CRITICAL (data layer)
- **Severity:** critical
- **Symptom:** Top duplicates (most are mutual-fund share-class artifacts but still pollute searches):
  - SYM × 1498 rows (84 different MFS series)
  - AEAXX × 1143 rows (Advisor Class)
  - ANAGX × 979 rows
  - SYMBO × 657 rows
  - **BUFC × 51 rows (4 different AB ETFs all sharing ticker)**
  - **AMG × 46 rows**
- **Why this matters for the user's complaint:** When a user searches "BUFC", they get one fund detail page (the LIMIT 1 winner). But ETP screens, issuer roll-ups, and any group-by query will count BUFC as 4 funds.
- **Hypothesis:** Step3 extracts fund-name → ticker pairings from prospectuses; share-class supplements list every series in a trust under the same ticker. The (trust_id, series_id) uniqueness constraint is missing.
- **Fix size:** medium

### F15: Identical inline-style KPI tiles repeated across all 5 fund pages — LOW
- **Severity:** low
- **Symptom:** Every fund page emits the same 9 KPI tiles via inline styles. Rendering cost is small but no `<table>` semantics, no consistent label hierarchy. Hard to scrape, hard to extend.
- **Fix size:** medium (template refactor — out of scope)

### F16: `/trusts` weighs 22 MB — HIGH (perf)
- **Severity:** high (perf)
- **Symptom:** `GET /trusts` returns 22,644,209 bytes in 1.99s. That's a 22MB HTML payload (probably one `<tr>` per fund × 122 trusts).
- **Blast radius:** Every visitor to `/trusts`. Mobile users especially.
- **Fix size:** medium (paginate or lazy-load)

### F17: NVDX/MSTU page sub-header reads "REX · Leverage & Inverse - Single Stock · ETF · Series ID:" — formatted correctly, but JEPI/VOO pattern is inconsistent — LOW
- See F8 for the related "nan" issue.

### F18: 2,268 active funds (etp_category NULL) include the world's biggest ETFs — HIGH
- **Severity:** high
- **Symptom:** VOO ($955B), IVV ($821B), SPY ($762B), VTI ($640B), QQQ ($461B), VEA, VUG, IEFA, VTV — all have NULL etp_category. These are the "Plain Beta" funds excluded from rexfin's REX/competitor classification universe.
- **Why this is in scope:** if a user lands on `/funds/VOO` from search, they get a degraded page (sub-header reads "Vanguard · nan · ETF"). Whether or not we want full classification on plain-beta, the page should not literally print "nan".
- **Fix size:** trivial (template fallback)

### F19: `/market/category?cat=LI` and `?cat=CC` return identical-shape HTML; data loaded via JS — LOW (no actual bug, but worth confirming JS payload is correctly filtered)
- Diff between LI and CC versions = exactly 23 lines (label changes, `data-category` attribute, query-string differences in pill links). Actual table data populates client-side from `/market/api/category-summary`. If the API also has the underlier-format bug, this would be where it shows.

### F20: `/operations/products` correctly lists NVDX and MSTU — POSITIVE
- Both REX funds appear with correct ticker, fund name, status (Listed), type (ETF), Bloomberg ticker (NVDX US Equity), inception date.

### F21: Issuer pages correctly attribute funds to issuers — POSITIVE
- `/issuers/REX` contains NVDX + MSTU. `/issuers/JP Morgan` contains JEPI. `/issuers/GraniteShares` contains NVDL. `/issuers/Vanguard` contains VOO. Issuer-attribution is working.

### F22: `/funds/{ticker}` ALL have correct ticker → fund_name mapping (no swaps) — POSITIVE
- For the 5 representative funds, the ticker the user types resolves to the right fund_name. The user's complaint is NOT about swapped tickers on the canonical fund page — it's about funds being missing from underlier rollups (F1, F2) and tickers being smeared across multiple fund_status rows (F4, F14).

## Pages that errored

| URL | Status | Notes |
|---|---|---|
| https://rex-etp-tracker.onrender.com/strategy | 500 | Internal Server Error body, 21 bytes |
| https://rex-etp-tracker.onrender.com/strategy/whitespace | 500 | Same |
| https://rex-etp-tracker.onrender.com/stocks | 404 | Collection root not registered |
| https://rex-etp-tracker.onrender.com/stocks/ | 404 | Same |
| https://rex-etp-tracker.onrender.com/global_search?q=NVDX | 404 | Route does not exist |
| https://rex-etp-tracker.onrender.com/tools/compare | 404 | Route does not exist |
| https://rex-etp-tracker.onrender.com/compare?tickers=NVDX,... | 404 | Route does not exist |
| https://rex-etp-tracker.onrender.com/admin/morning-brief/preview | 405 | (admin auth not held — expected) |

## Pages > 5s to load

| URL | Time | Bytes |
|---|---|---|
| /screener/3x-analysis | 5.84s | 38 KB (after 301 redirect) |
| /strategy/ticker/NVDX | 4.61s | 30 KB |
| /strategy/ticker/MSTU | 4.36s | 30 KB |
| /strategy/ticker/NVDL | 4.68s | 30 KB |
| /screener/ | 3.65s | 262 KB |
| /filings | 3.00s | 456 KB |
| /tools/li/candidates | 2.57s | 38 KB |
| /trusts | 1.99s | **22 MB** |

## Pages with broken/missing links

- Filing detail pages (`/filings/{id}`) list fund **names** but no ticker chips → users cannot navigate from filing to canonical `/funds/{ticker}` (F11).
- `/stocks/NVDA` lists 2 funds where DB has 21 → 19 missing canonical links (F1).
- `/market/underlier?underlier=NVDA US` lists CC funds only, no LI funds → broken category routing.

## Surfaces inspected

| URL | Status | Bytes |
|---|---|---|
| / | 200 | 62 KB |
| /funds/NVDX, /funds/MSTU, /funds/JEPI, /funds/NVDL, /funds/VOO | 200 each | 84-301 KB |
| /issuers/REX, /issuers/JP%20Morgan, /issuers/GraniteShares, /issuers/Vanguard | 200 each | 86-108 KB |
| /market/category, ?cat=LI, ?cat=CC | 200 each | 48-145 KB |
| /market/issuer | 200 | 195 KB |
| /market/issuer/detail?issuer=REX, ?issuer=JP%20Morgan, ?issuer=GraniteShares, ?issuer=Vanguard | 200 each | 86-108 KB |
| /market/underlier?underlier=NVDA, ?underlier=NVDA%20US | 200 each | 43-46 KB |
| /operations/products | 200 | 158 KB |
| /operations/pipeline | 200 | 164 KB |
| /screener, /screener/3x-analysis, /screener/4x | 200 each (after 301) | 38-262 KB |
| /strategy/ticker/NVDX, /strategy/ticker/MSTU, /strategy/ticker/NVDL | 200 each | 30 KB |
| /filings | 200 | 456 KB |
| /filings/624743 (REX), /filings/2096 (GraniteShares), /filings/609772 (JPMorgan), /filings/608802 (Vanguard) | 200 each | 32-37 KB |
| /trusts | 200 | 22 MB |
| /stocks/NVDA, /stocks/MSTR | 200 each | 29-30 KB |
| /tools/li/candidates, /tools/tickers | 200 | 38-139 KB |

DB tables inspected: `mkt_master_data` (7,361 rows; 111 columns), `rex_products` (NVDX/MSTU verified), `mkt_fund_classification`, `mkt_fund_mapping`, `classification_proposals`, `mkt_issuer_mapping`, `fund_status` (213,810 rows), `filings`, `trusts`.

## Surfaces NOT inspected

- **Email previews** (`/admin/digest/preview-daily`, `/admin/reports/preview-{li,cc,flow}`, `/admin/morning-brief/preview`) — admin auth required; out of scope per "do not log in to admin" constraint.
- **JS-loaded content** on `/market/category` and `/market/treemap` — would need a headless browser to render the API-fetched table. The server-side shell was inspected only.
- **`/notes/*`** (autocall simulator, etc.) — not in the prioritized list.
- **`/sec/etp/*`, `/sec/notes/*`** — not in the prioritized list.
- **`/13f/*`** — phase 0 status; data-layer-only feature.
- **`/calendar/*`** — not in the prioritized list.
- **`/api/v1/*`** JSON endpoints — would need contract validation, not page rendering.
- **Dark-theme rendering** — site supports light/dark; only light tested.
- **Mobile breakpoints** — not tested.
