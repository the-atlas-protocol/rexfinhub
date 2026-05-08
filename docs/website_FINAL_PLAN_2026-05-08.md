# Rexfinhub — Final Implementation Plan

**Date:** 2026-05-08
**Status:** Foundation locked. Implementation-ready.
**Authority:** This doc is the source of truth. Supersedes v1 / v2 / v3 architecture drafts.

---

## Section 1 — What's locked

### 1a. Foundation decisions (signed off 2026-05-08)

| # | Decision |
|---|---|
| 1 | Kill `/filings/report` orphan |
| 2 | Single-filing page lives at flat `/filings/{filing_id}` (renamed from `/analysis/filing/{id}`) |
| 3 | Trust pages stay at root `/trusts/{slug}` (4th canonical detail surface) |
| 4 | Build `/trusts/` browse-all index (currently missing) |
| 5 | Build `/issuers/` browse-all index (currently missing) |
| 6 | "Data" pillar: label cosmetic only, URL stays `/downloads/` |
| 7 | 3-PR migration sequence (dual-route → rewire → cleanup) |
| 8 | Adopt `webapp/routes.py` registry + `url_for()` everywhere |
| 9 | Add `sitemap.xml` + `robots.txt` (currently missing) |
| 10 | Defer auto-log of slow pages |
| 11 | Stub "Coming Soon" pages for the 4 unbuilt Compare tools (stable URLs from day one) |
| 12 | 13F sub-items: greyed-out non-clickable, matching existing "Coming Soon" pattern |
| 13 | `/tools/calendar` and `/operations/calendar` are **separate backends** with separate data scopes |

### 1b. The 5 canonical detail surfaces (at root)

```
/funds/{ticker}            — live ETP (Bloomberg DES + SEC breadcrumb, 15 sections)
/funds/series/{series_id}  — filed-only ETP (no ticker yet, SEC view; 301s once ticker assigned)
/issuers/{name}            — issuer rollup (read-side canonicalized)
/stocks/{ticker}           — stock signal dump + ETP coverage
/trusts/{slug}             — SEC trust entity (Schwab Strategic Trust, etc.)
/filings/{filing_id}       — single filing analysis (renamed from /analysis/filing/)
```

Plus the 3 list/index pages: `/funds/`, `/issuers/`, `/trusts/` (NEW), `/market/stocks/`.

### 1c. The 6-pillar nav (locked)

```
HOME              /
REX OPS           /operations/{products,pipeline,calendar}
MARKET INTEL      /market/{rex,category,issuer,underlier,stocks}
SEC INTEL         /sec/etp/{,filings,leverageandinverse}
                  /sec/notes/{,filings}
                  /sec/13f/* (placeholder labels, non-clickable)
TOOLS             /tools/{compare/etps,compare/filings,compare/notes,
                          compare/13f-inst,compare/13f-products,
                          li/candidates, simulators/autocall,
                          tickers, calendar}
DATA              /downloads/  (label "Data")
```

### 1d. Calendar split (clarified 2026-05-08)

| Route | Scope | Data |
|---|---|---|
| `/tools/calendar` (ETP Calendar) | Universe-wide | ETP launches + filing dates |
| `/operations/calendar` (REX Pipeline Calendar) | REX only | filings + launches + board-approval statuses + distribution calendar + fiscal year ends |

**Implication:** REX Operations Calendar requires data not yet in DB (board-approval status, fiscal year ends per product). Sub-task in Phase 2.

---

## Section 2 — Pre-flight (Phase 0)

Three items must ship before any URL or surface work begins.

### Item 0.1 — Delete the 3 bad REVIEW rows from `issuer_canonicalization.csv`

**Why:** these fuzzy-match clusters (KraneShares→GraniteShares 0.77, Amplify→Simplify 0.75, 21Shares→iShares 0.75) would silently mis-assign ~78 funds the moment we apply read-side canonicalization in Phase 2b.

**Action:** edit `config/rules/issuer_canonicalization.csv`, delete those 3 rows. Commit with rationale.

**Verify:** `python -c "import pandas as pd; df = pd.read_csv('config/rules/issuer_canonicalization.csv'); assert (df['confidence'] != 'REVIEW').all(); print(len(df))"` returns 12.

### Item 0.2 — Diagnose + fix `/calendar/` load failure

**Why:** the page was reported "not loading" on 2026-05-06. Carryover bug. Underlying view is what `/tools/calendar` will delegate to in v3 — fixing it now means the migration inherits a working view.

**Action:**
1. Reproduce locally: `uvicorn webapp.main:app --reload --port 8000` → visit `http://localhost:8000/calendar/`
2. Check server log for the actual exception
3. Likely culprits: (a) `market_advanced.calendar_view` import error, (b) missing parquet/CSV column referenced by query, (c) Bloomberg data column rename
4. Fix the root cause. No silent except clauses.

**Verify:** `curl -sI http://localhost:8000/calendar/` returns 200 (not 500), page renders with non-empty event list.

### Item 0.3 — Kill `/filings/report` orphan

**Why:** referenced only by `screener.py:60` redirect. Dead path.

**Action:** delete the redirect in `screener.py`. Will be covered by Phase 1's full screener.py deletion anyway, but doing now removes confusion.

**Verify:** `grep -r "/filings/report" webapp/` returns zero hits.

---

## Section 3 — Phased PR sequence

Each PR has: scope, files touched, **explicit verification gate**, rollback plan.

### PR 1 — Dual-route phase: add all v3 URLs, 301 the old ones

**Scope:** every v3 URL goes live as a fully functional route. Every old URL becomes a 301 redirect to the new URL. **No template changes yet.** Site fully functional on both old and new paths.

**New router files created:**
- `webapp/routers/operations.py` (products, pipeline, calendar)
- `webapp/routers/sec_etp.py` (dashboard, filings, leverageandinverse)
- `webapp/routers/sec_notes.py` (dashboard, filings)
- `webapp/routers/sec_13f.py` (placeholder route handlers serving 503 "Coming Soon")
- `webapp/routers/tools_compare.py` (etps + 4 stub routes)
- `webapp/routers/tools_li.py` (candidates — initially proxies old route, becomes parquet-backed in PR 3)
- `webapp/routers/tools_simulators.py` (autocall)
- `webapp/routers/tools_tickers.py` (cboe symbols)
- `webapp/routers/tools_calendar.py` (universe-wide ETP calendar)

**Old routers shrink to redirect-only stubs:**
- `filings.py`, `pipeline_calendar.py`, `notes.py`, `notes_autocall.py`, `screener.py`, `calendar_router.py`, `capm.py`, `analysis.py` — each route becomes a one-line 301 redirect to the new URL

**`webapp/main.py`:** register all new routers; old ones retained for redirect handling.

**Verification gate (must pass before merge):**

| Check | Method | Pass criteria |
|---|---|---|
| Every v3 URL renders 200 | smoke test (Python script: `for url in V3_URLS: assert requests.get(url).status_code == 200`) | All 30+ new URLs return 200 |
| Every v2 URL 301s to expected v3 URL | same script, check `Location` header | All 25+ old URLs return 301 with correct target |
| `/calendar/` renders non-empty | manual browser load | Page loads, event list populated |
| `/api/v1/*` paths unchanged | grep + smoke test | Zero changes to API surface |
| No template changes | `git diff --name-only` | No `.html` files modified |

**Rollback:** revert single PR. Old URLs were never broken.

### PR 2 — Build the 5 detail surfaces

**Scope:** the load-bearing work. The 5 truth surfaces become real.

#### 2a. `/funds/{ticker}` merge

- Take SEC fund detail (currently `/funds/{series_id}`) — keep ALL 6 sections (breadcrumb, header, meta grid, name history, filing history, 13F scaffold)
- Add Bloomberg DES from `/market/fund/{ticker}` — 9 new sections layered on top (KPI row, taxonomy, returns table, flows table, additional metrics, AUM chart, total return chart, competitors table)
- URL canon: `/funds/{ticker}` for live; `/funds/series/{series_id}` for filed-only
- 301: `/funds/{series_id}` (legacy) → `/funds/{ticker}` if live, → `/funds/series/{series_id}` if filed
- Issuer link uses canonicalized name
- Sections 5–11+15 render only when Bloomberg data exists (graceful degradation)

**Files:** `webapp/routers/funds.py`, `webapp/templates/fund_detail.html` (new merged template), 301 logic.

#### 2b. `/issuers/{name}` with read-side canonicalization

- New URL replaces `/market/issuer/detail?issuer=...`
- Apply `issuer_canonicalization.csv` AT READ TIME in `webapp/services/market_data.py:get_master_data()`
- **Filter to `confidence == "AUTO"` only** (12 rows; the 3 bad REVIEW rows already deleted in Phase 0.1)
- Same fix applied at: `services/market_data.py:1107` (rollup), `services/market_data.py:1244` (filter), `routers/market.py:288` (the BlackRock-47 bug location), `routers/downloads.py:682`, `routers/market_advanced.py:126,156`
- Page sections: header (canonical name + "Also known as: variants"), KPI row (total AUM, # funds canonicalized, # categories, # recent filings 90d), 12-month AUM chart, category breakdown donut+table, recent filings (90d), recent launches (90d), full product roster
- Variants 301 to canonical: `/issuers/iShares` → `/issuers/BlackRock`
- Add `/issuers/` browse-all index page

**Files:** `webapp/routers/issuers.py` (new), `webapp/templates/issuers/{detail,index}.html` (new), `webapp/services/market_data.py` (canonicalization layer).

#### 2c. `/stocks/{ticker}` data dump

- Header: ticker, company name, sector, exchange, market cap
- Signals (if in `whitespace_v4.parquet` or `launch_candidates.parquet`): composite_score, score_pct, mentions_24h+z, RVol 30d/90d, returns 1m/3m/1y, SI, insider_pct, inst_own_pct, theme tags
- ETP coverage table: every ETP with this ticker as `map_li_underlier` or `map_cc_underlier` — links to `/funds/{ticker}`
- Filing whitespace: 485APOS filings naming this underlier (from `filed_underliers.parquet`)
- "No data yet" banner if not in any parquet
- Index page: `/market/stocks/`

**Files:** `webapp/routers/stocks.py` (new), `webapp/templates/stocks/{detail,index}.html` (new).

#### 2d. `/trusts/{slug}` already exists — verify + add index

- Existing route works (`webapp/routers/trusts.py`). Verify still rendering correctly.
- Add `/trusts/` browse-all index page using query logic from `/universe/`
- Update template to link funds → `/funds/{ticker}`, filings → `/filings/{filing_id}`

**Files:** `webapp/routers/trusts.py` (extend), `webapp/templates/trusts/index.html` (new).

#### 2e. `/filings/{filing_id}` rename

- Rename `/analysis/filing/{id}` → `/filings/{filing_id}` (flat detail surface)
- Old URL 301s to new
- Update template links from 5 source pages: `dashboard.html`, `filing_explorer.html`, `filing_list.html`, `fund_detail.html`, `trust_detail.html`

**Files:** `webapp/routers/analysis.py` → rename to `webapp/routers/filings_detail.py` (or merge into existing filings router), template links.

**Verification gate (must pass before merge):**

| Check | Method | Pass criteria |
|---|---|---|
| `/funds/{ticker}` renders for 5 representative tickers | smoke + visual: NVDX (REX flagship), DJTU (REX recent), NVDL (competitor), JEPI (CC), filed-only series ID | All 5 render with appropriate sections; SEC breadcrumb intact |
| `/issuers/BlackRock` returns >100 funds (not 47) | manual + DB count | iShares + BlackRock + BlackRock Inc all roll up |
| `/stocks/NVDA` shows ETP coverage table | manual | At least 5 NVDA-tracking ETPs listed and clickable |
| `/trusts/schwab-strategic-trust` works + `/trusts/` index lists all 122+ trusts | manual | Both pages load |
| `/filings/628498` renders (the URL Ryu sent) + old URL 301s | curl + manual | New URL works, old URL redirects |
| No regressions on existing pages | smoke test 30 URLs | All return 200 |

**Rollback:** revert PR. Old detail pages still live (this PR builds new in parallel; PR 4 rewires links).

### PR 3 — Engine sync: `/tools/li/candidates` parquet-backed

**Scope:** rewrite the L&I tools page to read parquets directly (currently reads stale Bloomberg xlsx). Mirror `weekly_v2_report.py` visual exactly.

**Sections (ordered):**
1. Hero KPI bar (this week's L&I 485APOS count, REX filings, new underliers, top retail mention) — from `weekly_v2_report.load_filings_count_this_week()`
2. Launch Queue cards × 12 (from `launch_candidates.parquet`, `has_signals=True`, sort by composite_score)
3. Filing Whitespace cards × 12 (from `whitespace_v4.parquet`, top 12 by composite_score)
4. Inline Evaluator panel (POST → `compute_score_v3()` directly; folds in old `/filings/evaluator`)
5. Money Flow table (from `bbg_timeseries_panel.parquet` if present)

**Refactor:** extract formatters from `weekly_v2_report.py` to `screener/li_engine/analysis/formatters.py`. Reuse from web + email.

**Files:** `webapp/routers/tools_li.py` (replaces stub from PR 1), `webapp/templates/tools/li_candidates.html` (new), `screener/li_engine/analysis/formatters.py` (new).

**Verification gate:**

| Check | Method | Pass criteria |
|---|---|---|
| Reads from parquet, not xlsx | grep | No `screener_helpers.get_3x_data()` calls in new route |
| Card output matches `weekly_v2_report.py` for same data | spot-check 5 cards | Hero KPIs, top launch tickers, top whitespace tickers identical to current week's email |
| Inline evaluator returns composite_score for arbitrary ticker | POST test | `compute_score_v3()` invoked; result panel renders |
| Money Flow renders if parquet present, gracefully hides if not | conditional test | No "blank section" or error if `bbg_timeseries_panel.parquet` missing |

**Rollback:** revert PR. Old `/filings/candidates` and `/filings/evaluator` URLs still work (PR 1's redirects redirect to PR 3's route, but if PR 3 is reverted those redirects point at a stub).

### PR 4 — Rewire all internal links + add registry + sitemap

**Scope:** the mechanical pass. ~154 occurrences across ~23 files.

**4a. Add `webapp/routes.py` registry**

```python
# webapp/routes.py
from typing import Any
ROUTES = {
    "home":                   "/",
    "operations.products":    "/operations/products",
    "operations.pipeline":    "/operations/pipeline",
    "operations.calendar":    "/operations/calendar",
    "market.rex":             "/market/rex",
    "market.category":        "/market/category",
    "market.issuer":          "/market/issuer",
    "market.underlier":       "/market/underlier",
    "market.stocks":          "/market/stocks/",
    "sec.etp":                "/sec/etp/",
    "sec.etp.filings":        "/sec/etp/filings",
    "sec.etp.leverageandinverse": "/sec/etp/leverageandinverse",
    "sec.notes":              "/sec/notes/",
    "sec.notes.filings":      "/sec/notes/filings",
    "tools.compare.etps":     "/tools/compare/etps",
    "tools.compare.filings":  "/tools/compare/filings",
    "tools.compare.notes":    "/tools/compare/notes",
    "tools.compare.13f_inst": "/tools/compare/13f-inst",
    "tools.compare.13f_products": "/tools/compare/13f-products",
    "tools.li.candidates":    "/tools/li/candidates",
    "tools.simulators.autocall": "/tools/simulators/autocall",
    "tools.tickers":          "/tools/tickers",
    "tools.calendar":         "/tools/calendar",
    "data":                   "/downloads/",
    # Detail surfaces
    "funds.detail":           "/funds/{ticker}",
    "funds.series":           "/funds/series/{series_id}",
    "funds.index":            "/funds/",
    "issuers.detail":         "/issuers/{name}",
    "issuers.index":          "/issuers/",
    "stocks.detail":          "/stocks/{ticker}",
    "trusts.detail":          "/trusts/{slug}",
    "trusts.index":           "/trusts/",
    "filings.detail":         "/filings/{filing_id}",
}

def url(name: str, **kwargs: Any) -> str:
    """Resolve a named route to its URL with parameters substituted."""
    template = ROUTES[name]
    return template.format(**kwargs)
```

Templates use `{{ url('funds.detail', ticker=row.ticker) }}` instead of hardcoded paths.

CI lint: every key in `ROUTES` has a matching FastAPI route registered.

**4b. Update all 154 link occurrences**

Per agent inventory:
- 15 templates (`base.html` is dominant — 14+ prefix touches)
- 3 JS files (`app.js` lines 400-558, `autocall_chart.js`, `market.js`)
- 5 Python files (`filings.py`, `pipeline_calendar.py`, `screener.py`, `capm.py`, `market.py`) — hardcoded redirect targets

**4c. Add `sitemap.xml` + `robots.txt`**

- `webapp/main.py` adds `/sitemap.xml` route auto-generated from `ROUTES` (only public list pages + key detail surfaces; excludes `/admin/*`, `/api/v1/*`)
- `webapp/static/robots.txt` — disallow `/admin/*` and `/api/v1/*` (or rate-limit API)

**Verification gate:**

| Check | Method | Pass criteria |
|---|---|---|
| Zero hardcoded old paths in templates/JS/Python | `grep -rE "(filings/dashboard|filings/explorer|filings/landscape|pipeline/products|notes/issuers|market/compare|market/fund|capm/|analysis/filing)" webapp/` | Zero hits in templates and JS; old redirects in routers (Phase 5 cleanup target) preserved |
| `webapp/routes.py` registry matches FastAPI routes | CI script asserts each ROUTES key resolves to a real route | All keys resolve |
| Smoke test all 30+ URLs | Python script | All return 200 |
| `base.html` mega-menu loads on representative page | manual browser | Nav renders correctly across REX OPS / MARKET / SEC / TOOLS / DATA |
| `sitemap.xml` lists expected URLs | `curl /sitemap.xml \| xmllint` | XML valid; ≥ 25 `<url>` entries |
| `robots.txt` blocks /admin and rate-limits /api | `curl /robots.txt` | Contains `Disallow: /admin/` |

**Rollback:** revert PR. PR 1's redirects keep external bookmarks/emails working. Detail surfaces from PR 2 still reachable; URLs just won't be reflected in nav.

### PR 5 — Delete old route stubs (after 7-day monitoring)

**Scope:** defensive cleanup. After PR 4 has been live ≥ 7 days with **zero traffic** on old paths (verify via Render access logs or `/admin/health`), delete the redirect-only stubs.

**Files deleted:**
- `webapp/routers/screener.py` (entire file)
- `webapp/routers/strategy.py` (entire file)
- `webapp/routers/monitor.py` (entire file — `/market/monitor` killed per Ryu)
- `webapp/routers/holdings.py`, `holdings_placeholder.py`, `intel.py`, `intel_competitors.py`, `intel_insights.py` — UI routes only; **keep** the data ingestion that feeds `13f_holdings.db`
- 9 screener templates, 4 strategy templates, `monitor.html`, `filings_hub.html`
- Old router files retained as redirect-only stubs from PR 1: shrink to nothing or remove entirely

**Verification gate:**

| Check | Method | Pass criteria |
|---|---|---|
| Render access logs show zero hits on old paths over preceding 7 days | log review | Zero traffic on old prefixes |
| Smoke test new URLs still work | full smoke suite | All 200 |
| `/admin/health` reports green across all data sources | manual | All systems nominal |

**Rollback:** revert PR. PR 4's URL state continues to work; just the dead stubs reappear.

---

## Section 4 — Verification harness (input → output audit)

This is the part Ryu specifically asked for: **inspection that wiring + display actually work, end-to-end.**

### 4a. Static checks (CI — runs on every commit)

```python
# tests/test_routes_registry.py
def test_every_routes_key_has_real_endpoint():
    """ROUTES registry must match FastAPI's actual routes."""
    from webapp.main import app
    from webapp.routes import ROUTES
    fastapi_paths = {route.path for route in app.routes}
    for name, template in ROUTES.items():
        # Strip {params} for comparison
        canonical = re.sub(r"\{[^}]+\}", "{}", template)
        assert canonical in {re.sub(r"\{[^}]+\}", "{}", p) for p in fastapi_paths}, \
            f"Route {name}={template} has no FastAPI handler"

def test_no_hardcoded_old_urls_in_templates():
    """No template should reference deprecated URL prefixes."""
    DEAD_PATTERNS = [
        "/filings/dashboard", "/filings/explorer", "/filings/landscape",
        "/pipeline/products", "/notes/issuers", "/market/compare",
        "/market/fund/", "/capm/", "/analysis/filing/",
    ]
    for tmpl in pathlib.Path("webapp/templates").rglob("*.html"):
        text = tmpl.read_text()
        for pattern in DEAD_PATTERNS:
            assert pattern not in text, f"{tmpl} contains dead URL: {pattern}"
```

**Adopted before PR 4 merges.** CI fail = PR blocked.

### 4b. Per-route smoke test

```python
# tests/smoke_test.py — runs against local + Render
TEST_URLS = [
    # Pillars (list / dashboard pages)
    "/",
    "/operations/products", "/operations/pipeline", "/operations/calendar",
    "/market/rex", "/market/category", "/market/issuer", "/market/underlier", "/market/stocks/",
    "/sec/etp/", "/sec/etp/filings", "/sec/etp/leverageandinverse",
    "/sec/notes/", "/sec/notes/filings",
    "/tools/compare/etps", "/tools/compare/filings", "/tools/compare/notes",
    "/tools/compare/13f-inst", "/tools/compare/13f-products",
    "/tools/li/candidates", "/tools/simulators/autocall",
    "/tools/tickers", "/tools/calendar",
    "/downloads/",
    # Detail surfaces — use real values
    "/funds/NVDX", "/funds/DJTU", "/funds/JEPI", "/funds/series/S000074123",
    "/issuers/BlackRock", "/issuers/REX",
    "/stocks/NVDA", "/stocks/MSTR",
    "/trusts/schwab-strategic-trust", "/trusts/",
    "/filings/628498",
    # Old URL 301s (verify backwards compat)
    "/filings/dashboard", "/filings/explorer", "/filings/landscape",
    "/pipeline/products", "/calendar/", "/notes/", "/notes/issuers",
    "/notes/search", "/notes/tools/autocall", "/market/compare",
    "/capm/", "/analysis/filing/628498",
]

def test_all_urls_reachable(authenticated_session):
    for url in TEST_URLS:
        resp = authenticated_session.get(url, allow_redirects=False)
        assert resp.status_code in (200, 301, 302), \
            f"{url} returned {resp.status_code}"
        if resp.status_code == 301:
            # Verify redirect target also works
            target = resp.headers["Location"]
            target_resp = authenticated_session.get(target)
            assert target_resp.status_code == 200, \
                f"{url} → {target} returned {target_resp.status_code}"
```

**Runs before each PR merges.** Fail = PR blocked.

### 4c. Per-detail-surface end-to-end audit

Each detail surface gets a one-fund/issuer/stock walk-through that traces data from DB to rendered HTML.

**`/funds/{ticker}` audit (NVDX):**

```
1. DB has NVDX row in mkt_master_data?
   → SELECT * FROM mkt_master_data WHERE ticker = 'NVDX US' LIMIT 1;
   → Verify: aum, expense_ratio, total_return_1month populated, issuer_display = 'REX Shares' (canonical)

2. Service reads correctly?
   → from webapp.services.market_data import get_master_data
   → df = get_master_data(db); row = df[df['ticker_clean'] == 'NVDX'].iloc[0]
   → Verify: same values as step 1

3. SEC side has corresponding fund_status?
   → SELECT * FROM fund_status WHERE ticker = 'NVDX' LIMIT 1;
   → Verify: series_id populated, status='EFFECTIVE', filings list non-empty

4. Router resolves both?
   → curl http://localhost:8000/funds/NVDX -H "Cookie: site_auth=..."
   → Verify: HTTP 200, response body contains: ticker badge, fund name, AUM card, returns table, filing history table

5. Template renders all 15 sections?
   → Visual inspection in browser
   → Checklist: breadcrumb, header, action buttons, SEC meta grid, KPI row, taxonomy, returns, flows, additional metrics, AUM chart, total return chart, name history, filing history, 13F scaffold, competitors

6. Cross-links work?
   → Click issuer link → lands at /issuers/REX
   → Click underlier link → lands at /stocks/NVDA
   → Click "Analyze" on filing row → lands at /filings/{filing_id}

7. Bloomberg-absent fallback?
   → Pick a filed-only series (no ticker yet)
   → Visit /funds/series/{series_id}
   → Verify: SEC sections render, Bloomberg sections gracefully hidden
```

Documented in `tests/audit_funds_e2e.md`.

**`/issuers/BlackRock` audit (the canonicalization fix):**

```
1. CSV has correct AUTO mapping?
   → grep "BlackRock" config/rules/issuer_canonicalization.csv
   → Verify: iShares-related variants map to BlackRock, no REVIEW rows present

2. Service applies canonicalization?
   → from webapp.services.market_data import get_master_data
   → df = get_master_data(db); blackrock_funds = df[df['issuer_display'] == 'BlackRock']
   → Verify: count > 100 (not 47 — variants now rolled up)

3. URL resolves canonical?
   → curl http://localhost:8000/issuers/iShares -i
   → Verify: 301 redirect to /issuers/BlackRock

4. Detail page shows variants?
   → Visit /issuers/BlackRock
   → Verify: header has "Also known as: iShares, BlackRock Inc, BlackRock Asset Management"
   → Verify: fund count card shows 100+
   → Verify: every fund row links to /funds/{ticker}

5. Recent filings populate from canonical group?
   → Check "Recent filings" section
   → Verify: includes filings from any variant (iShares Trust filings appear under BlackRock)

6. Index page works?
   → Visit /issuers/
   → Verify: all unique canonical issuers listed; clicking any goes to detail
```

Documented in `tests/audit_issuers_e2e.md`.

**`/stocks/NVDA` audit, `/trusts/schwab-strategic-trust` audit, `/filings/628498` audit** — same pattern. Each gets a documented walkthrough in `tests/audit_*_e2e.md`.

**Run frequency:** every PR that touches a detail surface, plus full re-run before PR 5 cleanup.

### 4d. Cross-pillar walk (5 representative tickers × every page)

Pick: **NVDX** (REX flagship), **DJTU** (REX recent launch), **NVDL** (GraniteShares competitor), **JEPI** (CC), and a **filed-only series** (pre-launch).

For each ticker, walk every page that mentions it:

| Page | Verify |
|---|---|
| `/` (home) | Ticker appears in KPI/recent activity? Click → lands at `/funds/{ticker}` |
| `/market/rex` (if REX) | Listed in suite table; click → `/funds/{ticker}` |
| `/market/category` | Listed under correct category; click → `/funds/{ticker}` |
| `/market/issuer` (issuer rollup) | Counted under correct canonical issuer |
| `/market/underlier` | Listed under its underlier; click → `/funds/{ticker}` |
| `/sec/etp/` (filings dashboard) | Recent filings appear if any |
| `/sec/etp/filings` | Filterable by ticker; results link to `/funds/{ticker}` |
| `/sec/etp/leverageandinverse` (if L&I) | Listed in 2x/3x/4x matrix |
| `/tools/calendar` | Launch + filing dates visible |
| `/tools/li/candidates` (if competitor whitespace) | Card visible if has signals |
| `/funds/{ticker}` | Full DES screen renders |
| `/funds/{ticker}` competitors table | Click competitor → lands at competitor's `/funds/{ticker}` |
| Issuer link from `/funds/{ticker}` | Lands at `/issuers/{canonical}` |
| Underlier link from `/funds/{ticker}` | Lands at `/stocks/{ticker}` |

**Acceptance:** every link works for every ticker. No 404s, no broken cross-references.

Documented in `tests/audit_cross_pillar_walk.md`. Runs before PR 5 (the cleanup) — a full pass means the foundation is locked and ready for stub deletion.

### 4e. Render production verification (post-deploy)

After each PR merges to main and Render auto-deploys:

```bash
# 1. Health check
curl https://rexfinhub.com/health
# Expect: {"status":"ok",...}

# 2. Smoke test against prod
TEST_URLS=$(cat tests/test_urls.txt)
for url in $TEST_URLS; do
  status=$(curl -sI -o /dev/null -w "%{http_code}" -b "site_auth=..." "https://rexfinhub.com$url")
  if [ "$status" != "200" ] && [ "$status" != "301" ]; then
    echo "FAIL: $url returned $status"
  fi
done

# 3. Spot-check each detail surface
for ticker in NVDX DJTU NVDL JEPI; do
  curl -sI -b "site_auth=..." "https://rexfinhub.com/funds/$ticker" | head -1
done
```

Documented in `tests/render_smoke.sh`.

---

## Section 5 — Rollback plans

| PR | If broken | Rollback command | Recovery time |
|---|---|---|---|
| PR 1 | Old URLs return 500 instead of 301 | `git revert <pr1-merge>` + force-deploy | < 5 min |
| PR 2 | Detail surface 500 errors | revert single PR; old `/funds/{series_id}` and `/market/fund/{ticker}` still served by their old routers (PR 1 left them as redirect stubs but the underlying route handlers still exist until PR 5) | < 5 min |
| PR 3 | `/tools/li/candidates` broken | revert; PR 1's redirect from `/filings/candidates` → `/tools/li/candidates` will then redirect to a stub serving 503 — bad UX but not a site-wide outage. Mitigation: keep the parquet-backed code path behind a feature flag during rollout | < 5 min |
| PR 4 | Nav broken / templates show old URLs | revert PR 4; nav reverts to old prefixes; site fully functional | < 5 min |
| PR 5 | Old URL was still being hit (data anomaly in 7-day monitoring) | revert PR 5 (re-add the stubs); zero user impact | < 10 min |

**Pre-deploy gate for every PR:**
1. CI passes (registry test + no-hardcoded-old-urls test + smoke test)
2. Render preview deploy succeeds
3. Manual click-through on preview (5 representative URLs minimum)
4. Approve + merge

---

## Section 6 — Acceptance criteria

The foundation is "done" when ALL of these are true:

| # | Criterion | Verifier |
|---|---|---|
| 1 | All 30+ v3 URLs return 200 on prod | smoke test + manual |
| 2 | All 25+ legacy URLs return 301 to correct v3 target on prod | smoke test |
| 3 | Zero hardcoded old paths in templates / JS / Python source | CI lint |
| 4 | `/issuers/BlackRock` shows >100 funds (not 47) | manual count |
| 5 | All 5 detail surfaces (`funds/`, `issuers/`, `stocks/`, `trusts/`, `filings/{id}`) render correctly for representative test data | E2E audits 4c |
| 6 | Cross-pillar walk passes for 5 representative tickers — every link works | manual walk |
| 7 | `/tools/li/candidates` reads parquets, mirrors weekly_v2_report.py output | spot-check |
| 8 | `webapp/routes.py` registry exists and CI enforces it | CI |
| 9 | `sitemap.xml` and `robots.txt` are live | curl |
| 10 | Old route stubs deleted (PR 5 ships) | repo audit |
| 11 | Render `/health` reports OK after every PR merge | curl |
| 12 | Daily report send pipeline unaffected throughout (decoupled from webapp work) | observation: daily 17:45 send completes |

---

## Section 7 — Tracker mapping

| Task # | What | Maps to |
|---|---|---|
| #64 (Phase 0) | Foundation lock | this doc — DONE on sign-off |
| #65 (Phase 1: kills) | → PR 5 (after 7-day monitoring) |
| #66 (2a: /funds merge) | → PR 2.a |
| #67 (2b: /issuers + canon) | → PR 2.b |
| #68 (2c: /stocks dump) | → PR 2.c |
| #69 (Phase 3: rewire) | → PR 4 |
| #70 (Phase 4: nav restructure) | → PR 1 |
| #71 (Phase 5: engine sync) | → PR 3 |
| #72 (Phase 6: deferred polish) | → post-foundation |
| #73 (/trusts + /issuers indexes) | → PR 2.d + PR 2.b |
| #74 (routes registry) | → PR 4.a |
| #75 (sitemap + robots) | → PR 4.c |
| #76 (analysis/filing rename) | → PR 2.e |
| #77 (PR 1: dual-route) | → PR 1 |
| #78 (PR 2: rewire) | → PR 4 (revised) |
| #79 (PR 3: cleanup) | → PR 5 (revised) |
| #80 (compare placeholders) | → PR 1 (stub routes), PR 4 (linked) |

---

## Section 8 — Execution sequence (concrete)

**Today (post sign-off):**
1. Phase 0 items 0.1, 0.2, 0.3 (delete bad CSV rows, fix /calendar/, kill /filings/report)
2. Cut PR 1 (new routes + 301 stubs); deploy to Render; smoke test
3. Daily report send unaffected — 17:45 dispatch continues uninterrupted

**Day 2-4:**
4. Cut PR 2 (5 detail surfaces); E2E audits per surface; deploy; smoke test
5. Cut PR 3 (engine sync — `/tools/li/candidates`); spot-check vs weekly email; deploy

**Day 5-6:**
6. Cut PR 4 (rewire links + routes registry + sitemap + robots); CI lint; deploy
7. Begin 7-day monitoring window for PR 5

**Day 13:**
8. Cut PR 5 (delete stubs); final smoke test; foundation locked

**Throughout:**
- Render preview deploy on every PR
- Daily report 17:45 send: unaffected
- Bloomberg sync: unaffected
- 13F ingestion: unaffected (UI dark, data still flowing)

**Sub-task identified during planning:**
- REX Operations Calendar (`/operations/calendar`) requires data not yet in DB: board-approval status field, fiscal year ends per product. Source TBD. **Surfaces in PR 1 as stub; full data wiring deferred to a Phase 7 task post-foundation.**

---

## Section 9 — Sign-off & start

If this plan is approved, I begin Phase 0 (the three pre-flight items) immediately. Phase 0 has no risk to prod — CSV row deletion + a calendar bug fix + a dead redirect removal.

Then PR 1. Then the rest in sequence.

**You hold one final question:** is there anything in Section 4 (verification harness) you want stricter or looser? E2E audits per surface are optional polish; smoke tests + cross-pillar walk are non-negotiable.
