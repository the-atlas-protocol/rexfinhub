# Page Audit — Notes + Reports + Admin + Intel + Calendar + Misc
**Audit Agent C** | Date: 2026-05-06 | Method: Static code + template analysis

---

## Audit Framework

**5 Questions per page:**
1. **Question fit** — Does the page answer the question a user would actually ask?
2. **Visual** — Is the layout clean, structured, ADHD-legible?
3. **Linkage** — Does it link to the right adjacent pages?
4. **Freshness** — Is the data coming from the right source with appropriate staleness handling?
5. **Correctness** — No broken routes, missing context vars, or wrong architecture?

**Verdicts:** Healthy / Needs Polish / Broken / Wrong Architecture

---

## NOTES PILLAR

### `/notes/` — Structured Notes Overview
1. **Question fit**: Good. Shows total products, filings, by-issuer, by-type, by-year, and recent products table. Answers "what's in the DB?".
2. **Visual**: Presumes template `notes_overview.html` exists — confirmed in templates list.
3. **Linkage**: Router links to `/notes/issuers` and `/notes/search` implicitly via the nav; no broken links in router.
4. **Freshness**: Reads from `D:/sec-data/databases/structured_notes.db` with fallback to `data/structured_notes.db`. Gracefully returns `available: False` if DB missing. On Render, D: drive doesn't exist, so fallback path activates. `data/structured_notes.db` must be uploaded separately.
5. **Correctness**: `_load_stats()` opens SQLite inside a `try/except` — safe. No ORM leak. Products count of 423K+ is correct per memory context.

**Verdict: Healthy**. Caveat: Render deployment will silently show zeroed stats if `data/structured_notes.db` is absent.

---

### `/notes/issuers` — Issuer Breakdown
1. **Question fit**: Shows per-issuer product counts — correct use case.
2. **Visual**: Template `notes_issuers.html` confirmed in templates dir.
3. **Linkage**: No obvious dead links in router. Back to `/notes/` expected in template.
4. **Freshness**: Same `_load_stats()` call as overview — same DB path logic.
5. **Correctness**: Same caveats as overview. Router code is clean.

**Verdict: Healthy**

---

### `/notes/search` — Product Search
1. **Question fit**: Filter by issuer, type, underlier. Returns up to 100 results. Appropriate scope.
2. **Visual**: Template `notes_search.html` confirmed. Results table with SEC filing URL links.
3. **Linkage**: `sec_url` and `filing_url` generated inline — both rely on `accession_number.replace('-', '')` which is standard EDGAR format.
4. **Freshness**: Only searches if filters applied (`filters_applied` guard). Filter option lists populated from DB. Smart — no accidental full-table scan on page load.
5. **Correctness**: SQL built with parameterized queries. `LIKE` on `underlier_tickers` and `underlier_names` is correct. No injection risk.

**Verdict: Healthy**

---

### `/notes/tools/autocall` — Autocall Simulator
1. **Question fit**: Full simulator shell — client-side data fetch via `/notes/tools/autocall/data`. Monte Carlo + heuristic coupon suggestion. Answers "what would a worst-of autocall look like?".
2. **Visual**: Template `notes_autocall.html` confirmed. Data loaded client-side, so initial render is fast.
3. **Linkage**: Bootstrap endpoint `/notes/tools/autocall/data` filters out `autocall_product` category correctly. Sweep endpoint `/notes/tools/autocall/sweep` and `/notes/tools/autocall/suggest-coupon` are present.
4. **Freshness**: Sweep results cached by `AutocallSweepCache` (hash-based). Max date from `AutocallIndexLevel` table surfaces data staleness.
5. **Correctness**: Input validation on refs (1-5 tickers), coupon_mode whitelist, NoteParams schema validation. Cache invalidation logic correct. BS Monte Carlo falls back to heuristic gracefully.

**Verdict: Healthy**

---

## REPORTS PILLAR

### `/reports/`, `/reports/li`, `/reports/cc`, `/reports/ss`
1. **Question fit**: All redirect 302 → `/`. Intentionally hidden (header comment: "HIDDEN pending redesign").
2. **Visual**: No template rendered.
3. **Linkage**: Redirect is to home, which is reasonable. Users who land here won't get a 404.
4. **Freshness**: N/A — redirect.
5. **Correctness**: Routes exist, return 302. This is intentional per code comment. Report data served via admin email previews instead.

**CRITICAL NOTE**: The report router is a stub redirect, by design. The actual report previews live at `/admin/reports/preview-*`. If external links to `/reports/li` exist anywhere (e.g., in digest emails), they will silently redirect to home with no explanation.

**Verdict: Needs Polish** — a user-facing page or at least a 302 to the digest subscribe page would be better than a silent redirect to home.

---

## ADMIN PILLAR

### `/admin/` — Admin Dashboard (auth-protected)
1. **Question fit**: Comprehensive single-page hub: trust requests, subscriber approvals, classification queue, pipeline status, email gate, BBG status, notes stats. Answers "what needs my attention right now?"
2. **Visual**: Template `admin.html` confirmed.
3. **Linkage**: Links to `/admin/health`, `/admin/products/`, `/admin/reports/preview`, `/admin/classification-stats` — all routes confirmed registered.
4. **Freshness**: Loads BBG file age from `_LOCAL_CACHE`, send log from `data/.send_log.json`, notes stats from `_load_stats()`, classification validation from `validate_classifications()`. Multiple data sources with individual try/except guards.
5. **Correctness**: Session-based admin auth (`is_admin` cookie). Correct pattern — no hardcoded password in template. `_ON_RENDER` env flag correctly gates VPS vs local behavior. Product stats use `RexProduct` model — correct.

**Verdict: Healthy**

---

### `/admin/health` — System Health Dashboard (6 NEW CARDS VERIFIED)
1. **Question fit**: Exactly what it says — one-page system health view without SSH.
2. **Visual**: Responsive grid with color-coded cards (green/amber/red). Clean, ADHD-friendly.
3. **Linkage**: Link to `/admin/health.json` for automation. Correct.
4. **Freshness**: All 6 new cards confirmed present in both router and template:
   - **BBG mtime** (`bbg` context): reads `data/DASHBOARD/bloomberg_daily_file.xlsm` mtime. Stale threshold: 12h. CONFIRMED.
   - **Preflight token age** (`preflight_token`): reads `data/.preflight_token`. CONFIRMED.
   - **Preflight decision** (`preflight_decision`): reads `data/.preflight_decision.json`. CONFIRMED.
   - **Gate log** (`gate_transitions`): reads last 3 entries from `data/.gate_state_log.jsonl`. CONFIRMED.
   - **Today's send** (`today_send`): reads `data/.send_log.json`, checks for today's date prefix. CONFIRMED.
   - **VPS code freshness** (`vps`): reads `.vps_commit` vs `git rev-parse HEAD`. CONFIRMED.
5. **Correctness**: **BUG FOUND** — Template hardcodes `prebake.files == 9` for "healthy" state, but `report_registry` now has 10 reports (`daily_filing`, `weekly_report`, `li_report`, `income_report`, `flow_report`, `autocall_report`, `stock_recs`, `intelligence_brief`, `filing_screener`, `product_status`). The health card will always show `warn` status even when all reports are baked.

   Template line 146: `{% set pb_class = 'ok' if prebake.files == 9 else ... %}`
   Template line 149: `<div class="hs-big">{{ prebake.files }} / 9</div>`
   
   Actual registry count: **10 reports**.

**Verdict: Needs Polish** — 6 new cards render correctly, but prebake count threshold is stale (9 → should be 10).

---

### `/admin/classification-stats` — Per-Strategy Breakdown (NEW PAGE)
1. **Question fit**: Correct. Shows three tables: by primary_strategy (50 buckets), by asset_class (50 buckets), by sub_strategy (top 20). Coverage KPIs on new taxonomy vs legacy.
2. **Visual**: Well-structured — KPI cards with accent-new/accent-legacy borders, three data tables with inline bar fills, REX vs competitor counts, AUM formatting macro. Reads clean.
3. **Linkage**: Back link to `/admin/` confirmed in template. No broken references.
4. **Freshness**: Queries `mkt_master_data` WHERE `market_status = 'ACTV'`. Correct — only active funds. If market sync hasn't run, shows "No data — run market sync first." gracefully.
5. **Correctness**: Raw SQL via `sa_text` with `f-string` group_col injection — **POTENTIAL RISK**: `group_col` is passed as a local variable from the calling function (not user input), so in practice this is safe (`primary_strategy`, `asset_class`, `sub_strategy`). But the pattern would be vulnerable to SQL injection if `group_col` ever became user-controlled. Low severity given current usage; worth noting.

   Coverage stats correctly compute both new taxonomy `pct` and legacy `legacy_pct` percentages with zero-division guard.

**Verdict: Healthy** — Minor style note on the SQL pattern; functionally correct.

---

### `/admin/digest/preview-daily` — Daily Digest Preview
1. **Question fit**: Renders `build_digest_html_from_db()` in browser. Correct use case.
2. **Visual**: Returns raw `HTMLResponse` — no nav wrapper. This is intentional for email preview.
3. **Linkage**: Called from admin panel. Admin guard correct.
4. **Freshness**: Builds from live DB data at request time.
5. **Correctness**: Auth check is `_is_admin(request)` with redirect to `/admin/` on fail. Correct.

**Verdict: Healthy**

---

### `/admin/digest/preview-weekly` — Weekly Digest Preview
Same structure as daily. `build_weekly_digest_html` from `etp_tracker.weekly_digest`.

**Verdict: Healthy**

---

### `/admin/morning-brief/preview` — Morning Brief Preview
Calls `build_morning_brief_html`. Auth redirect goes to `/admin/login` (not `/admin/`) — minor inconsistency vs other routes which redirect to `/admin/`.

**Verdict: Needs Polish** — Redirect inconsistency: morning brief preview/send redirects to `/admin/login` but other routes redirect to `/admin/`. Should be uniform.

---

### `/admin/reports/preview-li`, `preview-cc`, `preview-flow`
All three confirmed in `admin.py`. All use try/except with informative HTML error response on failure. LI and CC return `html, _images` tuples. Flow report has `?refresh=1` cache-clearing mechanism — good.

**Verdict: Healthy**

---

### `/admin/products/` — Product Pipeline Manager
1. **Question fit**: Full CRUD for `rex_products`: filters by status/suite/urgency/date, inline cell editing via fetch(), CSV export, add/delete (soft) via status=Delisted.
2. **Visual**: Template `admin_products.html` confirmed.
3. **Linkage**: Route prefix `/admin/products` — correctly registered without additional prefix in main.py (`admin_products.router` has `prefix="/admin/products"` internally).
4. **Freshness**: Live DB query on every page load. Status/suite counts computed separately (unfiltered) for badge display.
5. **Correctness**: `_REX_UPDATE_FIELDS` whitelist prevents unauthorized field writes. `_coerce()` validates status/suite against VALID_STATUSES/VALID_SUITES. Partial update (single-field fetch) returns JSON `{"ok": True}` rather than redirect — correct for inline edits.

**Verdict: Healthy**

---

### `/admin/reports/dashboard` — Send-Day Dashboard
1. **Question fit**: Pre-send review — all 10 active reports listed with bake status, preview links, GO/HOLD buttons.
2. **Visual**: Inline HTML response (no base.html wrapper). Functional but plain — no REX styling.
3. **Linkage**: Preview links go to `/admin/reports/preview/{key}/raw` — confirmed route exists.
4. **Freshness**: Reads live prebaked HTML file sizes and preflight token/decision files.
5. **Correctness**: GO/HOLD token validated against `TOKEN_FILE`. Decision written to `data/.preflight_decision.json`. The button label says "Send all 7" — **STALE**: registry now has 10 reports (7 was an earlier count).

   `admin_reports.py` line 227: `"Send all 7"` hardcoded in inline HTML.

**Verdict: Needs Polish** — "Send all 7" label stale (should reflect actual active report count from `report_registry.get_active()`).

---

### `/admin/reports/preview` — Prebaked Reports Landing
1. **Question fit**: Lists all reports with bake status, size, baked_at.
2. **Visual**: Template `admin_reports_preview.html` confirmed.
3. **Linkage**: Each report links to `/admin/reports/preview/{key}/raw`.
4. **Freshness**: `_report_status()` checks file existence and reads `.meta.json` sidecar.
5. **Correctness**: `REPORT_CATALOG` is derived from `report_registry.as_legacy_dict()` — correctly auto-updates when registry changes.

**Verdict: Healthy**

---

## INTEL PILLAR

**CRITICAL NOTE**: All Intel pillar routes (`/intel/*`) are gated behind `ENABLE_13F=1` env var. On Render production (without that env var), ALL intel routes return 404. This is by design (data too large for Render Starter), but means the audit can only verify code correctness, not live rendering.

---

### `/intel/` — REX Intelligence Home
1. **Question fit**: Hub home with hub KPIs, top 15 products, vertical/issuer breakdown, international top 6. Correct scope.
2. **Visual**: Template `intel/home.html` confirmed.
3. **Linkage**: `_resolve_quarter()` gracefully returns empty state when no data available.
4. **Freshness**: Reads from `holdings` DB via `get_holdings_db`. Quarter param defaults to latest.
5. **Correctness**: Empty-state handler returns all-empty context safely. No crash on missing data.

**Verdict: Healthy** (when ENABLE_13F=1)

---

### `/intel/rex`, `/intel/rex/filers`, `/intel/rex/performance`, `/intel/rex/sales`
All four routes confirmed with correct template mappings:
- `intel/rex_report.html`, `intel/rex_filers.html`, `intel/rex_performance.html`, `intel/rex_sales.html` — all present in templates/intel/.
- `rex/sales` has a tab default fallback: `momentum → concentration` when fewer than 2 quarters available. Good defensive coding.
- `rex/performance` computes `market_share_pct` with zero-division guard.

**Verdict: Healthy** (when ENABLE_13F=1)

---

### `/intel/competitors`, `/intel/competitors/new-filers`
Both confirmed. Competitor pages correctly filter out REX issuers from issuer breakdown list. New-filers page allows `?vertical=` filter. All templates confirmed.

**Verdict: Healthy** (when ENABLE_13F=1)

---

### `/intel/products` — Full Product Universe Browser
Route is `/intel/products` (not `/intel/competitors/products` as scoped in the audit brief). Mounted at `intel_competitors.py`. Template `intel/products.html` confirmed. Pagination at 100 per page. Text search across ticker/name/issuer.

**Verdict: Healthy** (when ENABLE_13F=1)

---

### `/intel/head-to-head` — Product Comparison
Route is `/intel/head-to-head` (not `/intel/competitors/head-to-head`). Template `intel/head_to_head.html` confirmed. Requires `?underlying=` param to return results.

**Verdict: Healthy** (when ENABLE_13F=1)

---

### `/intel/country`, `/intel/asia`, `/intel/trends`
All three confirmed in `intel_insights.py`. Templates all present. Asia page has KPIs for AUM/institutions/countries/products with zero-safe aggregations.

**Verdict: Healthy** (when ENABLE_13F=1)

---

## CALENDAR PILLAR

### `/pipeline/` (root), `/pipeline/{year}/{month}` — Pipeline Calendar
Router prefix is `/pipeline` (not `/calendar`). Routes:
- `GET /pipeline/` → current month
- `GET /pipeline/{year}/{month}` → specified month
- `GET /pipeline/products` → pipeline home of operations
- `GET /pipeline/summary` → 301 redirect to `/pipeline/products`

1. **Question fit**: Calendar shows filings, effectives, launches, distributions, holidays with color-coding by event type and suite. Exactly right for a launch-tracking calendar.
2. **Visual**: Template `pipeline_calendar.html` confirmed. `SUITE_COLORS` dict powers per-suite coloring. `EVENT_TYPES` with label/color per type.
3. **Linkage**: Prev/next month navigation computed correctly. Year bounds: 2020-2035.
4. **Freshness**: Live DB queries for each month. `NyseHoliday` table drives holiday set. `FundDistribution` drives distribution events.
5. **Correctness**: `_rex_only_filter()` is a comprehensive filter — checks name prefix (REX/T-REX/MICROSECTORS) and excludes known non-REX issuers (Tuttle, Defiance, GSR, etc.). However, KPI block at bottom of `_render_month` does NOT use `_rex_only_filter()` — it counts all products in `rex_products`. Minor data inconsistency: KPIs say "total" but are unfiltered by REX branding.

**IMPORTANT**: Audit brief mentions `/calendar/` but actual mount point is `/pipeline/`. No `/calendar/` routes exist. If any nav links or external docs point to `/calendar/`, they will 404.

**Verdict: Needs Polish** — Mount point mismatch vs audit scope language; KPI block not applying rex-only filter.

---

### `/pipeline/products` — Pipeline Home of Operations
1. **Question fit**: KPIs (total/listed/filed/awaiting/research), cycle time stats, urgency filter pills, full product table with sortable columns. Excellent operations view.
2. **Visual**: Template `pipeline_products.html` confirmed. Suite breakdown by status. Color-coded suite bars.
3. **Linkage**: CSV export at `/pipeline/distributions/export.csv`. Sort via `?sort=&dir=` params.
4. **Freshness**: All counts from live DB. `_rex_only_filter` applied to all KPI queries.
5. **Correctness**: Server-side sort with column whitelist (`sort_map`). Urgency filter logic consistent with admin products page. `is_admin` from session passed to template for edit control visibility.

**Verdict: Healthy**

---

## MISC PILLAR

### `/` — Home Dashboard
1. **Question fit**: Morning brief text, 4 KPI cards (market/filing/ownership/notes), pillar quick-links, taxonomy strip, this-week's-filings panels, data freshness footer. Excellent ADHD-friendly overview.
2. **Visual**: Clean white grid layout (`home-page-bg`, `ha-kpis` 4-col grid). Dark mode CSS vars present.
3. **Linkage**: Pillar cards link to screener, market, intel, notes, capm sections — all confirmed routes.
4. **Freshness**: `taxonomy_summary` query from `mkt_master_data` WHERE `primary_strategy IS NOT NULL AND market_status = 'ACTV'`. Correctly silenced by `try/except`.
5. **Correctness**: CONFIRMED — taxonomy fund-count strip renders at line 783-800 of `home.html`. Passed via `taxonomy_summary` context var from `dashboard.py` line 229-248. Data sourced from `mkt_master_data` GROUP BY `primary_strategy`. Strip is conditional (`{% if taxonomy_summary %}`) — fails silently if no data.

**Verdict: Healthy** — Taxonomy strip from tonight's work is correctly wired.

---

### `/dashboard` — Redirect
Route confirmed at `dashboard.py` line 275. 301 redirect to `/filings/dashboard` preserving query string.

**Verdict: Healthy**

---

### `/search/` — Global EDGAR Search (trust search, not Ctrl+K)
Route at `search.py GET /search/`. NOT the global Ctrl+K search (that's `/api/v1/search` from `global_search.py`). Template `search.html` confirmed. Searches SEC for trust name and allows monitoring request submissions.

**Verdict: Healthy**

---

### `/analytics` — Analytics Dashboard
1. **Question fit**: Charts for filing volume (24m), trust growth (cumulative), fund status distribution, top form types, entity type breakdown. Good operational overview.
2. **Visual**: Template `analytics.html` confirmed. 5 chart series.
3. **Linkage**: No external links expected — purely data visualization page.
4. **Freshness**: All 5 queries run live on page load. No caching.
5. **Correctness**: `strftime("%Y-%m", ...)` in SQLite for month grouping — correct. Trust growth uses cumulative running sum correctly.

**Verdict: Healthy**

---

### `/downloads/` — Downloads Hub
1. **Question fit**: Lists outputs/ CSV/XLSX files, per-trust filing exports, and API endpoints. Good developer/analyst page.
2. **Visual**: Template `downloads.html` confirmed. Priority sort puts REX trusts first.
3. **Linkage**: Download links go through `GET /downloads/file?path=...` with path traversal protection (`_safe_path` with `startswith` check).
4. **Freshness**: `outputs/` is ephemeral on Render — likely empty in production. API endpoints always available.
5. **Correctness**: Market export routes (`/downloads/export/market/master`, `rex-only`, `li`, `cc`, `category-summary`, `issuer-summary`, `underlier-summary`, `timeseries`, `adhoc`) all exist and use `_dedup_query` by `ticker_clean`. Streaming CSV avoids memory buffer on large exports.

**Verdict: Healthy**

---

## SUMMARY TABLE

| Page | Verdict | Key Issue |
|------|---------|-----------|
| `/notes/` | Healthy | Render: needs structured_notes.db on persistent disk |
| `/notes/issuers` | Healthy | — |
| `/notes/search` | Healthy | — |
| `/notes/tools/autocall` | Healthy | — |
| `/reports/`, `/reports/li`, `/reports/cc`, `/reports/ss` | Needs Polish | Silent redirect to home; email links may confuse |
| `/admin/` | Healthy | — |
| `/admin/health` | Needs Polish | Prebake count hardcoded as 9; registry now has 10 |
| `/admin/classification-stats` | Healthy | Minor SQL injection pattern risk (non-exploitable) |
| `/admin/digest/preview-daily` | Healthy | — |
| `/admin/digest/preview-weekly` | Healthy | — |
| `/admin/morning-brief/preview` | Needs Polish | Redirect inconsistency: → `/admin/login` not `/admin/` |
| `/admin/reports/preview-li` | Healthy | — |
| `/admin/reports/preview-cc` | Healthy | — |
| `/admin/reports/preview-flow` | Healthy | — |
| `/admin/products/` | Healthy | — |
| `/admin/reports/dashboard` | Needs Polish | "Send all 7" label stale (registry: 10 reports) |
| `/admin/reports/preview` | Healthy | — |
| `/intel/` | Healthy (ENABLE_13F) | 404 on Render production |
| `/intel/rex` | Healthy (ENABLE_13F) | — |
| `/intel/rex/filers` | Healthy (ENABLE_13F) | — |
| `/intel/rex/performance` | Healthy (ENABLE_13F) | — |
| `/intel/rex/sales` | Healthy (ENABLE_13F) | — |
| `/intel/competitors` | Healthy (ENABLE_13F) | — |
| `/intel/competitors/new-filers` | Healthy (ENABLE_13F) | — |
| `/intel/products` | Healthy (ENABLE_13F) | Actual URL: /intel/products not /intel/competitors/products |
| `/intel/head-to-head` | Healthy (ENABLE_13F) | Actual URL: /intel/head-to-head not /intel/competitors/head-to-head |
| `/intel/country` | Healthy (ENABLE_13F) | — |
| `/intel/asia` | Healthy (ENABLE_13F) | — |
| `/intel/trends` | Healthy (ENABLE_13F) | — |
| `/pipeline/` (calendar) | Needs Polish | Mount: /pipeline/ not /calendar/; KPI not rex-filtered |
| `/pipeline/{year}/{month}` | Needs Polish | Same as above |
| `/pipeline/products` | Healthy | — |
| `/` (home) | Healthy | Taxonomy strip CONFIRMED working |
| `/dashboard` | Healthy | 301 → /filings/dashboard |
| `/search/` | Healthy | EDGAR trust search, not Ctrl+K |
| `/analytics` | Healthy | — |
| `/downloads/` | Healthy | outputs/ empty on Render (expected) |

**Pages audited: 36**

---

## TOP 3 CRITICAL FINDINGS

### 1. Admin Health Prebake Count Off-by-One (admin_health.html:146-149)
The health dashboard shows the prebake card as `warn` (amber) even when all reports are successfully baked. Template hardcodes `files == 9` but `report_registry` now has **10 active reports**. Admin will misread system health daily.

**Fix**: Change template lines 146 and 149:
```
# Was:
{% set pb_class = 'ok' if prebake.files == 9 else ('warn' if prebake.files > 0 else 'fail') %}
<div class="hs-big">{{ prebake.files }} / 9</div>

# Should be (or pass count dynamically from router):
{% set pb_class = 'ok' if prebake.files == 10 else ('warn' if prebake.files > 0 else 'fail') %}
<div class="hs-big">{{ prebake.files }} / 10</div>
```

### 2. Send-Day Dashboard "Send all 7" Label (admin_reports.py:227)
The GO button on `/admin/reports/dashboard` says "Send all 7" but the registry has 10 reports. This is cosmetic but misleading on the highest-stakes page in the admin panel — the one Ryu clicks on send day.

**Fix**: Make the button label dynamic:
```python
n = len(active)
f"GO &mdash; Send all {n}"
```

### 3. Calendar Mount Point Mismatch (/pipeline vs /calendar)
The audit scope and any documentation referencing `/calendar/` will 404. All routes are mounted at `/pipeline/`. No `/calendar/` prefix exists anywhere in main.py or router files. If marketing materials, internal docs, or email digests link to `/calendar/`, they break silently.

**Fix**: Either add an alias router at `/calendar/` that proxies to `/pipeline/`, or update all documentation references.

---

## TONIGHT'S SPECIFIC VERIFICATIONS

- `/admin/classification-stats`: **PASS** — page exists, per-strategy breakdown tables render correctly with REX/competitor split and AUM columns.
- `/admin/health` 6 new cards: **PASS** — all 6 cards (BBG mtime, preflight token, preflight decision, gate log, today's send, VPS commit) confirmed present in both router context and template rendering.
- Home page taxonomy strip: **PASS** — `taxonomy_summary` passed from `dashboard.py` home_page(), rendered at `home.html:784-800` with per-strategy fund count and REX count sub-label.
