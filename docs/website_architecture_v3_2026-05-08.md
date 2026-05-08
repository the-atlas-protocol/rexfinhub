# Rexfinhub — Architecture v3 (Full Implementation Audit)

**Date:** 2026-05-08
**Status:** Implementation-ready audit. Awaiting sign-off on Section 8 open questions.
**Supersedes:** `website_mindmap_2026-05-06.md`, `website_architecture_v2_2026-05-07.md`

---

## Executive summary

Ryu's v3 spec adds section prefixes (`/operations/`, `/market/`, `/sec/`, `/tools/`) to align rexfinhub with how Bloomberg, FactSet, and Pitchbook organize their intelligence platforms. This audit grounds every claim in code:

- **154 internal link occurrences** across **~23 files** need rewiring (research agent confirmed)
- **`base.html` is the single highest-risk file** — it touches 14+ prefixes
- **5 canonical detail surfaces** (not 3) emerged as the right pattern: `/funds/`, `/issuers/`, `/stocks/`, `/trusts/`, `/filings/{id}` — all stay at root, not nested
- **9 hardcoded redirect targets** in router code need updating
- **2 orphans** in Ryu's migration spec need decisions: `/filings/report` (no new home assigned), `/api/v1/*` references in 10 templates need verification
- Implementation strategy: **3-PR dual-route sequence** (add new + redirect old, rewire links, delete old) — atomic-PR rewrite would risk breaking nav

---

## Section 1 — Industry best-practice principles (foundation)

Reference systems: Bloomberg Terminal, FactSet, Pitchbook, S&P Capital IQ. Common patterns I'm extracting:

| Principle | Why it matters | Application here |
|---|---|---|
| **Detail surfaces stable at root** | Bloomberg DES, ISSU, HOLD don't change. Users memorize them. | `/funds/`, `/issuers/`, `/stocks/`, `/trusts/`, `/filings/{id}` stay at root — never nested under `/market/` or `/sec/`. They're cross-cutting. |
| **Section prefixes for LIST/dashboard pages only** | `/section/list-page` reflects mental model | Ryu's `/operations/`, `/market/`, `/sec/`, `/tools/` are correct — they're entry points, not destinations |
| **API namespace immune to UI restructure** | Renaming `/api/v1/*` breaks every consumer | `/api/v1/*` does NOT move. Ever. Period. Already 20+ template references and 3 JS files depend on it |
| **301 redirects preserve every old link** | Bookmarks, emails, cached search results | Every old URL gets a 301 to its new home. No exceptions |
| **Permalinks for filter state** | `?issuer=BlackRock&primary_strategy=L%26I` lets users bookmark filtered views | Already partial — formalize across all list pages |
| **Search-first navigation** | Ctrl+K is primary, mega-menu is discovery aid | Already exists in `app.js` lines 400-558 — needs URL update + bigger surface coverage |
| **Audit trail per page** | "Data as of X, source Y" — every intelligence platform has this | `DataFreshnessMiddleware` exists. Formalize visible footer on every page |
| **Single source of truth for routes** | Avoid template/test drift after migration | Add `webapp/routes.py` registry with named constants. Templates use `url_for(name)` only |
| **Sitemap + robots.txt** | Internal search + SEO discoverability | **Currently MISSING.** Add as part of this restructure |
| **Performance budget per page** | <500ms server-side or it's a bug | Filings pages currently exceed this — added to deferred queue |

---

## Section 2 — The locked URL scheme (Ryu spec + refinements)

### 2a. Pillar / list pages (under section prefixes)

| Pillar | URL | Source | Notes |
|---|---|---|---|
| **HOME** | `/` | dashboard.py | KPIs, quick links, dashboard updates |
| **REX OPERATIONS** | `/operations/products` | (was `/capm/`) | Product registry: fees, custodians, APs, LMMs |
|  | `/operations/pipeline` | (was `/pipeline/products`) | Pipeline table view |
|  | `/operations/calendar` | (was `/pipeline/`) | Pipeline calendar + distributions |
| **MARKET INTELLIGENCE** | `/market/rex` | unchanged | REX dashboard |
|  | `/market/category` | unchanged | Category dashboard |
|  | `/market/issuer` | unchanged | Issuer dashboard (rollup) |
|  | `/market/underlier` | rename of `/market/stock-coverage` | ETP underliers |
|  | `/market/stocks/` | NEW | Stocks list (browse all stocks with bbg + signal data) |
| **SEC INTELLIGENCE** | `/sec/etp/` | (was `/filings/dashboard`) | ETP dashboard — recent filings + competitor watch |
|  | `/sec/etp/filings` | (was `/filings/explorer`) | ETP filings explorer |
|  | `/sec/etp/leverageandinverse` | (was `/filings/landscape`) | L&I landscape (2x/3x/4x/5x competitive matrix) |
|  | `/sec/notes/` | (was `/notes/` + `/notes/issuers` merged) | Notes dashboard |
|  | `/sec/notes/filings` | (was `/notes/search`) | Notes filing explorer |
|  | `/sec/13f/*` | placeholder labels | Non-clickable until rebuild — REX Report, Market Report, Institution Explorer, Country Intel |
| **TOOLS** | `/tools/compare/etps` | (was `/market/compare`) | Compare ETPs |
|  | `/tools/compare/filings` | NEW | Compare filings (placeholder) |
|  | `/tools/compare/notes` | NEW | Compare notes (placeholder) |
|  | `/tools/compare/13f-inst` | NEW | Compare 13F institutions (placeholder) |
|  | `/tools/compare/13f-products` | NEW | Compare 13F products (placeholder) |
|  | `/tools/li/candidates` | merged from `/filings/candidates` + `/filings/evaluator` | Parquet-backed L&I tool, weekly_v2_report visual |
|  | `/tools/simulators/autocall` | (was `/notes/tools/autocall`) | Autocall simulator |
|  | `/tools/tickers` | (was `/filings/symbols`) | CBOE symbol landscape |
|  | `/tools/calendar` | (was `/calendar/`) | ETP calendar (universe-wide) |
| **DATA** | `/downloads/` (or rename `/data/`) | unchanged for now | Single page, exports + API docs |

### 2b. Detail surfaces (at root, NOT under section prefixes)

This is the **load-bearing best-practice deviation** from Ryu's spec. Detail surfaces are referenced from many pillars, so they must live at root:

| Surface | URL | Notes |
|---|---|---|
| **Fund (live)** | `/funds/{ticker}` | Bloomberg DES + SEC breadcrumb (15 sections per v2 spec) |
| **Fund (filed-only)** | `/funds/series/{series_id}` | SEC-only view, redirects to ticker URL once live |
| **Issuer** | `/issuers/{canonical_name}` | Read-side canonicalized rollup |
| **Stock** | `/stocks/{ticker}` | Bloomberg signal dump + ETP coverage |
| **Trust** | `/trusts/{slug}` | SEC entity detail (Schwab Strategic Trust, etc.) — see Section 3b |
| **Filing** | `/filings/{filing_id}` | Single filing analysis (renamed from `/analysis/filing/{id}`) — see Section 3a |

Plus two list/index pages for the new detail surfaces:
| List | URL | Notes |
|---|---|---|
| **Funds index** | `/funds/` | Browse all funds (extends current /funds/ list) |
| **Issuers index** | `/issuers/` | Browse all issuers — NEW, doesn't exist yet |
| **Stocks index** | `/market/stocks/` | Already in Ryu's spec under Market Intel |
| **Trusts index** | `/trusts/` | NEW — currently no list page, only `/trusts/{slug}` detail (gap) |

### 2c. Admin / utility (unchanged)

`/admin/*`, `/api/v1/*`, `/login`, `/logout`, `/health`, `/static/*` — all stable. No moves.

---

## Section 3 — Singular page placements

### 3a. `/analysis/filing/{filing_id}` → rename to `/filings/{filing_id}`

**What it actually is:** AI-analysis viewer for a single SEC filing. Header (form/date/accession/trust), meta grid (size/cost/funds), Run Analysis panel (radio buttons + daily 10/run quota), Previous Analyses chronological log.

**Who links to it (today):** `dashboard.html`, `filing_explorer.html`, `filing_list.html`, `fund_detail.html`, `trust_detail.html` — every "Analyze" button across the filings hierarchy.

**Why the URL is wrong:** `/analysis/` implies a dashboard or report surface, but this is a filing artifact viewer with an AI action attached. The current path is misleading.

**Verdict:** rename to `/filings/{filing_id}` (or `/sec/etp/filings/{filing_id}` if you want strict pillar alignment — but per the best-practice principle, detail surfaces should be at root). I recommend **`/filings/{filing_id}`** — flat, mirrors the breadcrumb users already follow, and makes the AI action a page feature rather than a URL claim. Old URL 301s to new.

### 3b. `/trusts/{slug}` → keep at root, ADD `/trusts/` index

**What a trust is:** SEC-registered statutory investment trust (e.g., "Schwab Strategic Trust", "ETF Opportunities Trust"). The legal shell that holds fund series, identified by CIK. **Distinct from issuer** — issuer is the brand (Schwab, BlackRock); trust is the registrant entity. One issuer often runs multiple trusts.

**Page contents (today):** Header + REX badge + CIK + fund count, KPI row (total/effective/pending/delayed), funds table (linked to `/funds/{series_id}`), institutional interest scaffold (13F dormant), recent filings table.

**Linked from:** `dashboard.html`, `filing_explorer.html` (×2), `fund_detail.html` (×2), `filing_list.html`, `fund_list.html`, `home.html` (×3 with hardcoded slugs), `universe.html`. **Heavily used** — primary navigation destination.

**Verdict:** **keep at top-level `/trusts/{slug}` — do NOT move under `/sec/etp/trusts/`.** Trusts are a canonical SEC entity type alongside funds/issuers/stocks. Adopting the 4-detail-surfaces pattern means trusts get equal billing.

**Add `/trusts/` index page.** Currently no list exists — only the slug-detail route. The universe page (`/universe/`) has the query logic; can be cloned. Closes a discoverability gap. ~1-route, ~1-template addition.

### 3c. `/capm/` → `/operations/products` (Ryu's call confirmed correct)

**What it is:** REX's internal product operations registry. Tabs:
- **Products** — full table with ticker / fund name / Bloomberg ticker / inception / trust / exchange / creation unit size / fixed fee / variable fee / cut-off time / custodian / LMM / prospectus link. **Admin: inline cell editing. CSV export.** Suite + text-search filters.
- **Trust & APs** — AP (Authorised Participant) mapping per trust.

**Why the move makes sense:** content is operational/structural data (fees, service providers, AP agreements). Belongs under operations. The `/capm/` URL is a department code, not an IA-meaningful path.

**Implementation note:** the inline cell editing must follow to the new URL — all admin POSTs (`/capm/update/{product_id}` etc.) become `/operations/products/update/{product_id}`. The CSV export endpoint follows.

**Future consideration:** the "Trust & APs" tab could split into `/operations/trust-aps` later. Defer for now — keep both tabs together at `/operations/products`.

---

## Section 4 — URL migration table (every old → new)

### Templates / JS rewiring (occurrences inventory from research agent)

| Old prefix | New prefix | Templates | JS | Top affected files |
|---|---|---|---|---|
| `/filings/dashboard` | `/sec/etp/` | 7 | 0 | `base.html`×2, `dashboard.html`×2, `filings_hub.html`×1 |
| `/filings/explorer` | `/sec/etp/filings` | 23 | 0 | `filing_explorer.html`×12, `market/rex.html`×5 |
| `/filings/landscape` | `/sec/etp/leverageandinverse` | 10 | 0 | `screener_landscape.html`×4, `base.html`×2 |
| `/filings/symbols` | `/tools/tickers` | 4 | 0 | `filings_symbols.html`×2 |
| `/filings/candidates` + `/filings/evaluator` | `/tools/li/candidates` | 12 | 0 | `base.html`×4, `filings_hub.html`×2 |
| `/filings/*` (other) | various | ~14 | 0 | `filing_explorer.html`×13 (mostly self-refs) |
| `/pipeline/products` | `/operations/pipeline` | 11 | 0 | `pipeline_products.html`×8 |
| `/pipeline/` (other) | `/operations/calendar` | 9 | 0 | `pipeline_calendar.html`×5 |
| `/calendar/` | `/tools/calendar` | 11 | 0 | `base.html`×7, `market/calendar.html`×4 |
| `/notes/` | `/sec/notes/` | 16 | 0 | `base.html`×7, `home.html`×3 |
| `/notes/issuers` (merge) | `/sec/notes/` | 3 | 0 | `base.html`×2 |
| `/notes/search` | `/sec/notes/filings` | 5 | 0 | `base.html`×2 |
| `/notes/tools/autocall` | `/tools/simulators/autocall` | 2 | 3 | `autocall_chart.js`×3 (page-scoped APIs) |
| `/market/compare` | `/tools/compare/etps` | 6 | 0 | `base.html`×2 |
| `/market/fund/{ticker}` | `/funds/{ticker}` | 4 | 1 | `app.js` line 400-558 has hardcoded URLs |
| `/market/issuer/detail` | `/issuers/{name}` | 4 | 0 | `market/issuer.html`×2 |
| `/funds/{series_id}` | `/funds/{ticker}` (canon) | 12 | (in app.js) | `fund_list.html`×6 |
| `/capm/` | `/operations/products` | 11 | 0 | `capm.html`×7 |
| `/analysis/filing/{id}` | `/filings/{id}` | (multiple) | 0 | `dashboard.html`, `filing_explorer.html`, `fund_detail.html`, `trust_detail.html` |
| `/api/v1/*` | **UNCHANGED** | 20 (refs) | 7 (refs) | `live-feed.js`, `ticker.js`, `api_docs.html`×8 — all stay |

### Router-side hardcoded redirects to update

| File | Line | Old target | New target |
|---|---|---|---|
| `filings.py` | 145 | `/filings/` | `/sec/etp/` |
| `pipeline_calendar.py` | 123 | `/pipeline/products` | `/operations/pipeline` |
| `screener.py` | 40, 45 | `/filings/candidates` | `/tools/li/candidates` |
| `screener.py` | 50, 55 | `/filings/evaluator` | `/tools/li/candidates` |
| `screener.py` | 60 | `/filings/report` | **DECISION NEEDED** — no new home in spec |
| `capm.py` | 296 | `/capm/?msg=updated` | `/operations/products?msg=updated` |
| `market.py` | 57 | `/market/rex` | unchanged (still in `/market/` prefix) |
| `market.py` | 449 | `/market/underlier` | unchanged (rename only, prefix kept) |
| `dashboard.py` | 279 | dynamic `url` | inspect — likely safe |

### Total scope

- **~23 distinct file edits**
- **~154 occurrences updated**
- **9 router redirect targets**
- **15+ new redirect routes added** (old URL 301 → new URL)
- **base.html is the dominant single file** — appears in 14+ migration prefixes

---

## Section 5 — 5 canonical detail surfaces (at root)

```
                LIST / DASHBOARD PAGES                       DETAIL SURFACES
                (live under section prefixes)                (at root, cross-cutting)
                ───────────────────────────                  ──────────────────
                                                             
   /market/rex     ─────────────────────┐                    
   /market/category ────────────────────┼──── click fund ──► /funds/{ticker}
   /market/underlier ───────────────────┤                    /funds/series/{id}
   /sec/etp/ ────────────────────────────┤                    
   /sec/etp/filings ─────────────────────┤                    
   /sec/etp/leverageandinverse ──────────┤                    
   /tools/calendar ──────────────────────┤                    
   /tools/li/candidates ─────────────────┘                    
                                                             
   /market/category ────────────────────┐                    
   /market/issuer ──────────────────────┼──── click issuer ──► /issuers/{name}
   /sec/etp/ ────────────────────────────┤                    /issuers/ (index)
   /tools/calendar ──────────────────────┘                    
                                                             
   /market/stocks/ ─────────────────────┐                    
   /market/underlier ───────────────────┼──── click stock ──► /stocks/{ticker}
   /tools/li/candidates ────────────────┘                    /market/stocks/ (list)
                                                             
   /sec/etp/ ────────────────────────────┐                    
   /sec/etp/filings ─────────────────────┼──── click trust ──► /trusts/{slug}
   /funds/{ticker} (breadcrumb) ─────────┤                    /trusts/ (NEW index)
                                                             
   /sec/etp/ ────────────────────────────┐                    
   /sec/etp/filings ─────────────────────┼──── click filing ─► /filings/{filing_id}
   /funds/{ticker} (filing history) ─────┤                    (renamed from /analysis/filing/)
   /trusts/{slug} ───────────────────────┘                    
```

**Rule:** any place a fund / issuer / stock / trust / filing is mentioned anywhere on the site, the link goes to its canonical detail surface. No exceptions.

---

## Section 6 — Implementation strategy (3-PR sequence)

Big-bang refactor of 154 occurrences across 23 files in a single PR is too risky. Industry pattern is **dual-route migration**: old URLs stay live with 301s while new URLs go up; templates rewire in a separate PR; old routes deleted last.

### PR 1 — Add new routes (old still works)

**Scope:** every new URL goes live as a **fully functional route**. Every old URL gets a 301 redirect to the new URL. Templates are NOT touched yet — site still works on old URLs.

**Files modified:**
- `webapp/main.py` — register new routers (`operations`, `sec_etp`, `sec_notes`, `tools`)
- New router files: `webapp/routers/operations.py`, `sec_etp.py`, `sec_notes.py`, `tools_compare.py`, `tools_li.py`, `tools_simulators.py`, `tools_tickers.py`, `tools_calendar.py`
- Old router files (`filings.py`, `pipeline_calendar.py`, `notes.py`, `notes_autocall.py`, `screener.py`, `calendar_router.py`, `capm.py`, `market.py`, `market_advanced.py`, `analysis.py`) become **redirect-only stubs** for the old paths

**Verification:** every old URL 301s to expected new URL. Every new URL renders. No template changes. **Site fully functional.**

**Estimated risk:** LOW — additive change only, old paths preserved.

### PR 2 — Rewire all internal links

**Scope:** update all `<a href>` in templates + JS hardcoded URLs to point to new URLs. Update router-side hardcoded redirects.

**Files modified:** ~23 files (15 templates + 3 JS + 5 routers).

**Strategy:** ground truth is `webapp/routes.py` (NEW) — a registry of named URL constants. Templates use `url_for(name)` everywhere instead of hardcoded paths. **This eliminates future drift.**

**Verification:** zero references to old prefixes in templates / JS / Python source. Old URLs still work via PR 1's redirects (so external bookmarks/emails preserved), but no internal link uses them anymore.

**Estimated risk:** MEDIUM — `base.html` carries most of the load. Pre-flight: verify locally on every page before deploy.

### PR 3 — Delete old route stubs (after monitoring)

**Scope:** after PR 2 has been live for ≥ 7 days with zero traffic on old paths (monitor `/admin/health` access logs), delete the redirect-only stubs.

**Files modified:** delete (or shrink to nothing) the old router files: `filings.py`, `pipeline_calendar.py`, `notes.py`, `calendar_router.py`, `capm.py`, etc.

**Verification:** Render deploys without errors. 404 monitoring confirms no inbound traffic on old paths.

**Estimated risk:** LOW (defensive — only after data confirms safe).

---

## Section 7 — Best-practice infrastructure adds (parallel to URL migration)

These are independently scoped enhancements that aren't strictly part of the URL refactor but should land as part of the foundation.

### 7a. `webapp/routes.py` — single source of truth

```python
# webapp/routes.py — every URL has a name, every template uses url_for()
ROUTES = {
    "home": "/",
    "operations.products": "/operations/products",
    "operations.pipeline": "/operations/pipeline",
    # ...
    "funds.detail": "/funds/{ticker}",
    "funds.series": "/funds/series/{series_id}",
    "issuers.detail": "/issuers/{name}",
    # ... etc
}
```

Templates: `<a href="{{ url('funds.detail', ticker=row.ticker) }}">`

Tests: assert every `ROUTES` key has a matching FastAPI route. Lint script catches drift.

### 7b. Add sitemap.xml + robots.txt

Currently MISSING. For internal search + Google indexing:
- `/sitemap.xml` auto-generated from routes registry — only public list pages + key detail surfaces (not admin)
- `/robots.txt` — disallow `/admin/*`, `/api/v1/*` (or allow with rate limit)

### 7c. Permalink filter state on every list page

Every list page accepts query params for filter state. Examples:
- `/sec/etp/leverageandinverse?leverage=3&direction=long`
- `/issuers/?country=US&min_aum=1B`
- `/tools/calendar?primary_strategy=L%26I&date_from=2026-05-01`

Currently partial — formalize across all list routes. Means every filtered view is a shareable URL.

### 7d. Page-level audit trail footer

`DataFreshnessMiddleware` already populates `request.state.data_freshness`. The `_data_freshness.html` partial exists but is conditional. Make it visible on every public page — small footer line: "Data as of 2026-05-08 14:32 UTC · SEC EDGAR (last 24h) · Bloomberg (this morning's xlsm)".

### 7e. JSON API parity

Every public list/detail page should have a `/api/v1/{equivalent}` JSON endpoint returning the same data. Already partial:
- `/market/api/screener-data` ✓
- `/market/api/issuer` ✓
- but `/funds/{ticker}` has no JSON twin yet

Adding parity is a Phase-by-phase polish — not a blocker.

### 7f. Performance budget enforcement

Every public page should render server-side in < 500ms. Filings pages currently exceed this (per Ryu's "insanely slow" feedback). Add a middleware that logs `request_duration_ms` per route. Pages > 500ms go into the slowness queue (deferred phase).

---

## Section 8 — Open architectural decisions (need your call before PR 1 ships)

| # | Question | My recommendation |
|---|---|---|
| 1 | `/filings/report` (orphan from `screener.py:60`) — kill or remap? | **Kill.** No clear consumer. Old screener path. |
| 2 | `/analysis/filing/{id}` — rename to `/filings/{id}` (flat) or `/sec/etp/filings/{id}` (nested under pillar)? | **Flat `/filings/{id}`.** Detail surfaces at root per best-practice principle. |
| 3 | `/trusts/{slug}` — confirm staying at root (4th detail surface), not moving under `/sec/etp/trusts/`? | **Confirm at root.** It's a canonical SEC entity. |
| 4 | Add `/trusts/` index page (currently no list exists)? | **Yes.** Closes a discoverability gap. ~1-route addition, query logic exists in `/universe/`. |
| 5 | Add `/issuers/` index page (currently no list exists)? | **Yes.** Same logic — issuers are a canonical entity, deserve a browse-all surface. |
| 6 | `/downloads/` — rename to `/data/` per nav spec, or keep `/downloads/` URL with "Data" label only? | **Keep `/downloads/` URL, change label.** No internal links break, label is purely cosmetic. |
| 7 | Rewire strategy: 3-PR sequence (recommended) or atomic single-PR? | **3-PR.** Safer. Old links preserved during transition. |
| 8 | `webapp/routes.py` registry + `url_for()` — adopt, or stick with hardcoded paths? | **Adopt.** Eliminates future drift. One-time refactor, permanent benefit. |
| 9 | Sitemap.xml + robots.txt — add as part of this restructure or defer? | **Add now.** They're foundation. |
| 10 | Performance budget enforcement (auto-log slow pages) — adopt or defer? | **Defer to Phase 6.** Not on critical path. |
| 11 | Compare placeholders (`/tools/compare/filings`, `/notes`, `/13f-inst`, `/13f-products`) — render "Coming Soon" stubs or hide from nav until built? | **Stub pages.** Stable URLs from day one means future builds don't break inbound links. |
| 12 | The 13F sub-section labels in nav — non-clickable plain text or muted-link styling like the existing "Coming Soon" pattern? | **Match existing "Coming Soon" pattern** (greyed out, no href). Consistent visual idiom. |

---

## Section 9 — Risks + gotchas (audit findings)

| # | Risk | Mitigation |
|---|---|---|
| 1 | **`base.html` single point of failure** — touches 14+ prefixes. One bad merge breaks every page's nav. | Test base.html on every public page locally before any PR 2 merge. CI smoke test that loads 20+ representative URLs. |
| 2 | **`app.js` hardcoded search URLs (lines 400-558)** | Update in PR 2. Add JS console assertion if old paths appear in result URLs. |
| 3 | **`autocall_chart.js` page-scoped APIs** (`/notes/tools/autocall/suggest-coupon`, `.../sweep`) — these are NOT `/api/v1/*` and DO move with the router | Update JS in PR 2 alongside the router move |
| 4 | **`/api/v1/*` references in 10 template files** — agent flagged need to verify these aren't constructing old page URLs as substrings | Manual scan during PR 2 |
| 5 | **`/capm/` admin inline editing** — POST endpoints `update/{id}`, `add`, `sync-from-sec` must follow to `/operations/products` | Verified — same router file, just prefix change |
| 6 | **Render auto-deploys from main branch** — if PR 2 lands broken, prod is broken | Pre-merge: full Render preview deploy + manual smoke test of 20 URLs |
| 7 | **External email links to `/filings/dashboard`, `/calendar/`, etc.** | PR 1's 301 redirects keep them functional. **Do not skip PR 1.** |
| 8 | **Hardcoded slugs in `home.html`** (×3 trust links) | Update in PR 2 |
| 9 | **`fund_status.ticker` format mismatch** (bare vs `AAPB US`) — affects `/funds/{ticker}` lookup | Already documented in v2 spec; resolve via `ticker_clean` column in `mkt_master_data` |
| 10 | **3 bad REVIEW rows in `issuer_canonicalization.csv`** (KraneShares→GraniteShares, Amplify→Simplify, 21Shares→iShares) — would silently mis-assign ~78 funds if read-side canonicalization filters by all rows instead of AUTO-only | Delete from CSV before PR 2 ships |

---

## Section 10 — Phase tracker delta

Existing tasks #64-72 still apply but get re-sequenced to align with v3:

| Task | New mapping |
|---|---|
| #64 (Phase 0: Lock foundation) | This doc — awaiting Section 8 sign-off |
| #65 (Phase 1: Kill list) | Becomes part of PR 3 (after monitoring) |
| #66 (Phase 2a: /funds merge) | Stays — independent of URL prefix work |
| #67 (Phase 2b: /issuers + canonicalization) | Stays |
| #68 (Phase 2c: /stocks dump) | Stays |
| #69 (Phase 3: Rewire links) | Becomes PR 2 in v3 strategy |
| #70 (Phase 4: Nav restructure) | Becomes PR 1 in v3 strategy |
| #71 (Phase 5: Engine sync) | Stays — `/tools/li/candidates` build |
| #72 (Phase 6: Deferred) | Add: performance budget, sitemap, JSON parity |

**New tasks needed:**
- Add `/trusts/` index page
- Add `/issuers/` index page
- Add `webapp/routes.py` registry + `url_for()` migration
- Add `sitemap.xml` + `robots.txt`
- Move `/analysis/filing/{id}` → `/filings/{filing_id}`
- Add 4 compare-tool placeholder routes (filings/notes/13f-inst/13f-products)

---

## Section 11 — Sign-off

If you approve Section 8's recommendations, here's what ships first (before any phase code):

1. **Delete the 3 bad REVIEW rows** from `issuer_canonicalization.csv`
2. **Diagnose + fix `/calendar/` load failure** (carryover from v2) — won't be `/calendar/` after migration but the underlying view bug needs fixing
3. **Cut PR 1** — add new routes + 301 stubs (15+ new routers, no template changes)
4. **Cut PR 2** — rewire all internal links + add `webapp/routes.py` + sitemap + robots
5. **Monitor 7 days, cut PR 3** — delete old route stubs

Then the Phase 2 detail-surface builds (`/funds/`, `/issuers/`, `/stocks/`, `/trusts/`, `/filings/{id}`) — these are independent of the URL prefix work and can run in parallel after PR 1.

**Foundation candidate v3.** Mark up Section 8 and I lock it.
