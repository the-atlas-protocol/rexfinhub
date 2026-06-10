# Stage 4 Re-Audit — Webapp Cross-Page Consistency

Generated: 2026-05-11 (post commit 6470819)
Agent: webapp_consistency_verify
Site under audit: https://rex-etp-tracker.onrender.com
Local DB snapshot: `C:/Projects/rexfinhub/data/etp_tracker.db` (mtime 2026-05-11 18:18)
Auth: SITE_PASSWORD=rexusers26 (read-only browse, never touched admin endpoints)
Stage 1 reference: `01_webapp_consistency.md`

## TL;DR

Re-walked the same 5 funds (NVDX, MSTU, JEPI, NVDL, VOO) across 10 surfaces. **22 Stage 1 findings → 13 still broken, 4 fixed/improved, 5 verified positive (or N/A).** Net: **2 confirmed regressions** (VOO issuer rendering, fund_status duplicate count) and **1 new finding** (intermittent Render 502 on home + every page).

Net cross-page consistency score: **66 / 100** (Stage 1: 62 / 100). Marginal improvement, primarily from the AUM source-of-truth alignment (F6/F7 fixed) — not from the claimed `primary_strategy` backfill, which **did not actually run on this DB**.

The user's headline complaint — "tickers being applied to wrong funds" — is **still reproducible in exactly the same three ways** as Stage 1 (F1, F2, F4/F14). None of the three CRITICAL findings have been fixed.

## Verdict per fix claim

| Claim from caller | Reality on live site | Reality in DB |
|---|---|---|
| R1+R2 populated `primary_strategy` | ❌ FALSE — JEPI/VOO still render `nan` for sub-strategy / underlier | `primary_strategy IS NULL` for **7,361/7,361 rows (100%)** — backfill did not land |
| T2 cleaned 54 SGML poison rows | ✅ TRUE — `fund_status WHERE ticker='SGML'` = 0 rows | But duplicate-ticker count went UP, not down |
| R8 deployed (CSRF + rate-limit) | ✅ TRUE — `POST /admin/` returns 403 (no CSRF token); X-Frame-Options/X-Content-Type-Options/HSTS headers present | n/a |
| R7 TZ pinning | ⚠️ Cannot verify from page output — needs schedule-trace | n/a |
| Live site on commit 6470819 | ⚠️ Site went into intermittent 502 mid-audit (still 502 at write time) | n/a |

## Cross-page matrix — Stage 1 vs Stage 4

Legend: ✅ fixed · ❌ unchanged · ⚠️ regressed · ➕ improved

| Fund | Field | Stage 1 (live) | Stage 4 (live) | Δ |
|---|---|---|---|---|
| NVDX | Issuer chip | "REX" | "REX" | ❌ |
| NVDX | Category | LI / "Leverage & Inverse - Single Stock" | same | ❌ |
| NVDX | AUM tile | $521.1M (DB: $545.5M) | **$545.5M** (matches DB) | ✅ |
| NVDX | Sub-strategy | "Long" (from map_li_direction) | "Long" | ❌ |
| NVDX | Underlier tile | NVDA US | NVDA US | ❌ |
| NVDX | Status | EFFECTIVE | EFFECTIVE | ❌ |
| MSTU | Issuer chip | REX | REX | ❌ |
| MSTU | Category | LI | LI | ❌ |
| MSTU | AUM | $817.7M (DB: $819.4M) | **$819.4M** | ✅ |
| MSTU | Sub-strategy | "Long" | "Long" | ❌ |
| JEPI | Issuer chip | (none) | (still none) | ❌ F5 |
| JEPI | Category | "Income - Index/Basket/ETF Based" | same | ❌ |
| JEPI | AUM | $45.0B (DB: $44.7B) | **$44.7B** | ✅ |
| JEPI | Sub-strategy KPI | "nan" | **"nan"** | ❌ F3 |
| JEPI | Underlier KPI | "nan" | **"nan"** | ❌ F3 |
| JEPI | Leverage KPI | "nanx nan" | "nanx nan" | ❌ F3 |
| NVDL | Issuer chip | (none) | (still none) | ❌ F5 |
| NVDL | AUM | $3.7B (DB: $4.2B) | **$4.2B** | ✅ F6 fixed |
| NVDL | Sub-strategy | "Long" | "Long" | ❌ |
| NVDL | Underlier tile | NVDA US | NVDA US | ❌ |
| VOO  | Issuer subheader | "Vanguard · nan · ETF" | **"nan · nan · ETF"** (issuer link → `/issuers/nan`) | ⚠️ REGRESSION |
| VOO  | AUM | $935.6B (DB: $955.3B) | **$955.3B** | ✅ F7 fixed |
| VOO  | fund_status duplicates | 5 rows | **35 rows** (5 Vanguard + 30 bulk-discovery trusts inc. AB Bond, Advanced Series Trust) | ⚠️ MUCH WORSE F4 |

## Stage 1 finding status

### CRITICAL persisting
- **F1: `/stocks/{underlier}` matches 2 of 21 NVDA-linked ETFs — UNFIXED.**  
  `/stocks/NVDA` still returns "2 products tracking NVDA" with NVDK as the only listed `/funds/` link. NVDX, NVDL, NVDU, all 17 LI funds keyed to `'NVDA US'` continue to be invisible. `/stocks/MSTR` is **worse — now "0 products tracking" and shows zero `/funds/` links.** DB confirms the bifurcation: `map_li_underlier='NVDA'` = 1 row, `'NVDA US'` = 10 rows; `map_li_underlier='MSTR'` = 0 rows, `'MSTR US'` = 7 rows. The route's UPPER-EQUALS join in `webapp/routers/stocks.py` was not patched.

- **F2: `/market/underlier` shows two buckets for the same underlier — UNFIXED.**  
  `/market/underlier?underlier=NVDA` text payload still contains `NVDA US $1.8B (6)` and `NVDA $0.00M (1)` as separate buckets. The "NVDA" bucket lists only NVDK; the "NVDA US" bucket lists 6 CC funds (DIPS, NVDW, NVDY, NVII, NVIT, NVYY) and **still no L&I funds**. Same on MSTR (0 funds in MSTR bucket, 9 CC funds in MSTR US, 0 LI). The user cannot reach NVDX or NVDL from any underlier surface — same as Stage 1.

- **F14 / F4: fund_status ticker bleed — REGRESSED.**  
  Stage 1 reported VOO had 5 rows; **today's DB has 35 rows for VOO** (trust IDs 227–261, including non-Vanguard trusts AB Bond Fund, Advanced Series Trust, etc.). VOO's `/funds/VOO` page now references **72 unique filings** in its rollup, all from these ticker-bled trusts. Other duplicates flat or unchanged: SYM=1498, AEAXX=1143, BUFC=51, AMG=46. **Total tickers with >1 fund_status row: 12,854** (Stage 1: 12,853). The "bulk_discovery" trust source added recently is the proximate cause — pipeline pulls VOO ticker out of every Vanguard-adjacent prospectus that mentions it.

### HIGH persisting
- **F3: `mkt_master_data.primary_strategy` 100% NULL — UNFIXED.**  
  Direct DB query: `SELECT COUNT(*) FROM mkt_master_data WHERE primary_strategy IS NULL` → **7361 / 7361** (Stage 1: 7361 / 7361). Every dependent column also still NULL: `sub_strategy`, `strategy`, `underlier_name`, `root_underlier_name`, `leverage_ratio`, `direction`. JEPI and VOO render literal `nan` in 5+ KPI tiles each. **R1+R2 did not actually backfill the column on this DB snapshot** — either the migration was reverted, only ran on a different DB file, or never executed on the local copy synced to Render.

- **F9: `/strategy` and `/strategy/whitespace` 500 — UNFIXED.**  
  Both routes still return HTTP 500 with body "Internal Server Error" (21 bytes). No try/except wrapping was added.

- **F18: 2,268 active funds with NULL etp_category include world's biggest ETFs — UNFIXED.**  
  `/funds/VOO` still degrades to "nan · nan · ETF". Compounded by the F8 regression below.

### MEDIUM persisting
- **F5: Issuer chip missing for non-REX funds — UNFIXED.** JEPI, NVDL, VOO H1 areas still emit `<h1>TICKER</h1>` with no chip. NVDX/MSTU emit chip "REX". Template guard unchanged.
- **F10: `/stocks/{underlier}` filing whitespace claim — UNFIXED** (still says "No 485APOS filings on record name NVDA"; `filed_underliers.parquet` not rebuilt).
- **F11: filing detail pages have no ticker chips — UNFIXED.** `/filings/624743` (REX 485APOS) has 0 `/funds/` links and 0 ticker chips in the body.
- **F12: `/stocks` collection root 404s — UNFIXED.** Both `/stocks` and `/stocks/` return HTTP 404.
- **F13: `/screener/3x-analysis` and `/screener/4x` redirect — UNFIXED but faster.** Both 301 to `/tools/li/candidates`. Latency dropped from 5.84s → 2.17s for `/3x-analysis` (improvement, possibly from Render warm-up not actual fix).
- **F19: /market/category JS-loaded data** — could not verify; the API endpoint that powers the table (`/market/api/category-summary?cat=LI`) returned 502 throughout audit. New finding below.

### LOW persisting
- **F8: VOO had `issuer_display=NULL` but page rendered "Vanguard" — REGRESSED.**  
  Stage 1 said the page recovered to "Vanguard" via fallback. Stage 4 page now renders `nan · nan · ETF` and the issuer link is `<a href="/issuers/nan">nan</a>` — the strip-suffix fallback was lost. Whatever change populated AUM (F6/F7 fix) appears to have replaced the fallback chain wholesale.
- **F15, F17**: cosmetic, unchanged.
- **F16: `/trusts` 22 MB — UNFIXED.** New behavior: `/trusts` now 307-redirects to `/trusts/`, which still returns **22,647,807 bytes** (essentially identical 22.6 MB payload). Time 3.16s.
- **F20**: `/operations/products` still correctly lists NVDX and MSTU as ticker-rows in a sortable table (no `/funds/` link though — table cells are plain text + `data-field="ticker"`). ✅ POSITIVE PERSISTS.
- **F21**: REX, JP Morgan, GraniteShares issuer pages all still attribute correctly. ➕ But `/issuers/Vanguard` REGRESSED — see V1 below.
- **F22**: 5 funds → correct fund_name on canonical page, no swaps. ✅ POSITIVE PERSISTS.

### Fixed / improved
| ID | Stage 1 issue | Stage 4 status |
|---|---|---|
| F6 | NVDL AUM drift 12% ($3.7B vs $4.2B DB) | ✅ FIXED — page now shows $4.2B, matches DB exactly |
| F7 | VOO AUM drift 2% ($935.6B vs $955.3B DB) | ✅ FIXED — page now shows $955.3B |
| MSTU AUM | $817.7M vs $819.4M DB | ✅ FIXED — page now shows $819.4M |
| NVDX AUM | $521.1M vs $545.5M DB | ✅ FIXED — page now shows $545.5M |
| Other 502s/timeouts (F13 latency) | ➕ partial improvement |

R6's effect appears to be a single-source-of-truth alignment in the funds router for AUM. Side effect: it broke the issuer fallback chain on VOO.

## New findings (Stage 4)

### V1: `/issuers/Vanguard` returns "No data found" empty state — HIGH (REGRESSION)
- **Severity**: high
- **Pages**: `/issuers/Vanguard`
- **Symptom**: Body text reads literally: *"No data found for issuer 'Vanguard'. The name may not exist in the current Bloomberg snapshot."* Zero `/funds/` links on the page (Stage 1: VOO and other Vanguard funds were listed correctly).
- **Hypothesis**: Issuer-detail route now does an exact-match join on `issuer_display`, and Vanguard funds in `mkt_master_data` have `issuer_display = NULL` (confirmed for VOO + 5,093 other rows). Whatever logic Stage 1 used to recover "Vanguard" from raw `issuer` (e.g. "Vanguard Index Funds" → "Vanguard") was removed alongside the F6/F7 AUM fix.
- **Blast radius**: ~5,094 funds with NULL `issuer_display` lose their issuer-page attribution. /issuers/Vanguard, /issuers/Fidelity, /issuers/iShares likely all empty now.
- **Fix size**: small (restore strip-suffix fallback on issuer)

### V2: VOO `fund_status` duplicates expanded 7× (5 → 35 rows) — CRITICAL (REGRESSION)
- **Severity**: critical
- **Symptom**: `SELECT COUNT(*) FROM fund_status WHERE ticker='VOO'` = 35 (Stage 1: 5). New trusts feeding VOO ticker include AB Bond Fund, AB Bond Inflation Strategy, Advanced Series Trust, etc. via `source='bulk_discovery'`.
- **Why this matters**: `/funds/VOO` rolls up filings across all 35 trusts — the page now references 72 unique filings (Stage 1: ~10). Filing rollups, "recent filings on this fund" tiles, and any analytics keyed on ticker get massively inflated.
- **Hypothesis**: The bulk_discovery scraper added since Stage 1 ingests fund-name → ticker pairs out of every prospectus that mentions a popular ticker. No `(trust_id, series_id)` uniqueness gate.
- **Fix size**: medium (post-extraction dedupe + a cik-allow-list for bulk_discovery)

### V3: `/market/api/category-summary?cat=LI` returned 502 throughout audit — MEDIUM (Render-side)
- **Severity**: medium
- **Symptom**: Every call to the JS-fed API endpoint (`/market/api/category-summary?cat=LI`, `/api/v1/maintenance`, `/static/js/*`, finally also `/funds/NVDX` after 18:55) returned HTTP 502 from Render's edge. The first ~25 minutes of the audit were healthy 200s; site degraded to a sustained 502 outage at the end and stayed there through repeated retries (sleep 25s).
- **Could be**: deploy in flight (caller's note about "today's late commits" + commit 6470819), OOM, or worker-restart loop.
- **Implication**: F19 (verify whether the JS-loaded LI/CC table also has the underlier-format bug) **could not be verified** because the API was down. Re-test once site recovers.

### V4: `/funds/VOO` page issuer link `/issuers/nan` is broken — LOW
- **Severity**: low
- **Symptom**: `<a href="/issuers/nan">nan</a>` is rendered in the VOO subheader, and similarly `<a href="/stocks/nan">nan</a>` in the underlier KPI. Clicking them opens 200-status pages for `/issuers/nan` and `/stocks/nan` (the routes accept any string), creating phantom URLs in search-engine crawls.
- **Fix size**: trivial (template-side null guard)

## Cross-page matrix (5 funds × 8 surfaces × 6 fields) — Stage 4

| Fund | Field | DB | /funds/{tk} | /issuers/{name} | /market/underlier | /stocks/{u} | /operations/products | /strategy/ticker/{tk} |
|---|---|---|---|---|---|---|---|---|
| NVDX | Issuer | REX | REX (chip) | REX (in list) | — | n/a | REX | REX |
| NVDX | Category | LI | "Leverage & Inverse - Single Stock" | (in REX) | NVDA US bucket | **MISS at /stocks/NVDA** | LI | L&I |
| NVDX | AUM | $545.5M | **$545.5M ✅** | (in issuer total) | (in $1.8B NVDA US bucket) | **MISS** | — | — |
| NVDX | Sub-strategy | NULL | "Long" | — | — | — | — | "L&I/Long" |
| NVDX | Underlier | NVDA US | NVDA US | — | NVDA US | (broken — F1) | NVDA | NVDA |
| NVDX | Status | ACTV/EFFECTIVE | EFFECTIVE | (active) | — | **MISS** | Listed | — |
| MSTU | Issuer | REX | REX (chip) | REX | — | **MISS at /stocks/MSTR** | REX | REX |
| MSTU | AUM | $819.4M | **$819.4M ✅** | — | (MSTR US bucket) | **MISS** | — | — |
| JEPI | Issuer | JP Morgan | "JP Morgan" (no chip) | JP Morgan | — | n/a | n/a | n/a |
| JEPI | Category | CC | "Income - Index/Basket/ETF Based" | — | — | — | — | — |
| JEPI | AUM | $44.7B | **$44.7B ✅** | (in JP Morgan total) | — | — | — | — |
| JEPI | Sub-strategy | NULL | **"nan" ❌** | — | — | — | — | — |
| JEPI | Underlier | NULL | **"nan" ❌** | — | — | — | — | — |
| NVDL | Issuer | GraniteShares | "GraniteShares" (no chip) | GraniteShares | — | **MISS at /stocks/NVDA** | n/a | GraniteShares |
| NVDL | AUM | $4.2B | **$4.2B ✅** | (in issuer total) | — | **MISS** | n/a | — |
| NVDL | Underlier | NVDA US | NVDA US | — | (NVDA US bucket) | **MISS** | n/a | NVDA |
| VOO | Issuer | issuer_display=NULL | **"nan" ⚠️ REGRESSION** | **EMPTY PAGE ⚠️ REGRESSION (V1)** | — | n/a | n/a | n/a |
| VOO | Category | NULL | **"nan"** | — | — | — | n/a | n/a |
| VOO | AUM | $955.3B | **$955.3B ✅** | — | — | — | — | — |
| VOO | Sub-strategy | NULL | "nan" | — | — | — | — | — |
| VOO | Status | ACTV | EFFECTIVE | — | — | — | — | — |
| VOO | fund_status duplicates | **35 rows ⚠️ V2** | LIMIT 1 still works on canonical | — | — | — | — | — |

## Pages re-tested

| URL | Stage 1 | Stage 4 | Δ |
|---|---|---|---|
| `/funds/NVDX` | 200, 84KB | 200, 91KB | ➕ |
| `/funds/MSTU` | 200 | 200, 89KB | — |
| `/funds/JEPI` | 200 | 200, 83KB | — |
| `/funds/NVDL` | 200 | 200, 97KB | — |
| `/funds/VOO` | 200, 301KB | 200, 295KB | — |
| `/issuers/REX` | 200 | 200, 96KB | — |
| `/issuers/JP%20Morgan` | 200 | 200, 46KB | — |
| `/issuers/GraniteShares` | 200 | 200, 89KB | — |
| `/issuers/Vanguard` | 200 (had VOO) | **200, 31KB ("No data found") ⚠️ V1** | regressed |
| `/market/underlier?underlier=NVDA` | 200 | 200, 45KB (still bifurcated) | ❌ |
| `/market/underlier?underlier=NVDA%20US` | 200 | 200, 47KB | ❌ |
| `/market/underlier?underlier=MSTR` | 200 | 200, 43KB (0 LI) | ❌ |
| `/stocks/NVDA` | 200 (2 funds) | 200, 32KB (still 2) | ❌ |
| `/stocks/MSTR` | 200 (was 6 in CC bucket) | **200, 31KB ("0 products tracking")** | ⚠️ worsened |
| `/operations/products` | 200 | 200, 157KB | — |
| `/market/category?cat=LI` | 200 | 200, 49KB | — |
| `/market/category?cat=CC` | 200 | 200, 49KB | — |
| `/strategy/ticker/NVDX` | 200, 4.6s | 200, 32KB (no latency probe) | — |
| `/strategy` | **500** | **500 ❌** | unchanged |
| `/strategy/whitespace` | **500** | **500 ❌** | unchanged |
| `/stocks` | **404** | **404 ❌** | unchanged |
| `/stocks/` | **404** | **404 ❌** | unchanged |
| `/global_search?q=NVDX` | 404 | 404 | unchanged |
| `/tools/compare` | 404 | 404 | unchanged |
| `/screener/3x-analysis` | 200 (5.84s) | 200, 40KB (2.17s) | ➕ latency only |
| `/screener/4x` | 200 (2.7s) | 200, 40KB (2.32s) | ➕ |
| `/trusts` | 200, 22MB | 307→`/trusts/`, 22.6MB, 3.16s | ❌ |
| `/filings/624743` | 200 | 200, 38KB (still no ticker chips) | ❌ |
| `/funds/series/S000002839` | n/a | **301 → `/funds/VFIAX`** (not VOO) | new — wrong canonical |
| `/api/v1/maintenance` | n/a | **502** | new V3 |
| `/market/api/category-summary?cat=LI` | n/a | **502** | new V3 |
| Site overall (during audit close) | 200 | **502 sustained** | V3 |

## Source-of-truth status

| Field | Source-of-truth | Status |
|---|---|---|
| `aum` | `mkt_master_data.aum` | ✅ FIXED — funds router now reads master table directly |
| `primary_strategy` | should be `mkt_master_data.primary_strategy` | ❌ NULL for all 7,361 rows; template still has no fallback to `mkt_fund_classification.strategy` |
| `etp_category` | `mkt_master_data.etp_category` | ❌ NULL for 2,268 rows including world's biggest ETFs |
| `issuer_display` | `mkt_master_data.issuer_display` | ⚠️ REGRESSED — fallback to `issuer` (strip suffix) was lost during AUM fix |
| `map_li_underlier` / `map_cc_underlier` | not normalized | ❌ still bifurcated `'XXX'` vs `'XXX US'` |
| `fund_status (trust_id, series_id)` | no uniqueness gate | ⚠️ REGRESSED — VOO went 5 → 35 rows |

## Recommended Stage 5 follow-ups

1. **Re-run R1 (primary_strategy backfill).** Verify it actually mutates `mkt_master_data.primary_strategy` on disk and re-uploads to Render. Right now the column is 100% NULL — the fix did not land.
2. **Ship the F1/F2 underlier normalization** (already scoped as small in Stage 1). One UPDATE + one route patch unblocks 80% of the L&I underlier surfaces.
3. **Restore the issuer strip-suffix fallback** lost during R6 (V1 + V4 + F8 regression).
4. **Add a `(trust_id, series_id)` dedupe gate** in the bulk_discovery ingestion path (V2). VOO going 5 → 35 rows means filing rollups are inflated 7×.
5. **Wrap `_load_whitespace()` in try/except** (F9 — 5-line fix that should already have been done).
6. **Add `(issuer_display IS NULL)` template fallback to use raw issuer field** (F8/V1).
7. **Investigate sustained 502** at audit close — could be unrelated deploy, OOM, or the F16 22 MB `/trusts` page eating worker memory under concurrent load.

## Surfaces NOT inspected
- Email previews (admin auth held).
- `/13f/*`, `/notes/*`, `/sec/*`, `/calendar/*`.
- JS-rendered `/market/category` LI/CC tables (V3 — API was 502).
- Dark theme, mobile breakpoints.
- `/api/v1/*` JSON contracts (502 throughout).
