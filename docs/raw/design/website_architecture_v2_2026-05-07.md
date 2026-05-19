# Rexfinhub — Architecture v2 (Foundation Lock)

**Date:** 2026-05-07
**Status:** Draft for Ryu sign-off. **Nothing ships until this is locked.**
**Supersedes:** `docs/website_mindmap_2026-05-06.md`

---

## What this doc is

A single source of truth for what rexfinhub looks like after the rebuild. Built from:
- Ryu's 6-pillar nav vision (Home / REX Ops / Market Intel / SEC Intel / Tools / Data)
- Four parallel research agents that ground every claim in code (L&I engine reality, live-vs-filed data model, detail-page audit, classification status)
- Every existing route mapped to its new home — or to the kill list

If you sign this off, Phase 1 (kills) ships immediately. Phases 2–5 sequence after.

---

## Section 1 — The cohesion principle

**Three canonical detail surfaces. Every list page on the site links to one of them.**

| Surface | URL | What it is |
|---|---|---|
| **Fund detail** | `/funds/{ticker}` (live) or `/funds/series/{series_id}` (filed-only) | Bloomberg DES screen + SEC breadcrumb. The truth surface for any ETP. |
| **Issuer detail** | `/issuers/{canonical_name}` | All funds (canonicalized), AUM trend, category mix, recent filings, recent launches. |
| **Stock detail** | `/stocks/{ticker}` | Bloomberg stock signals + ETP coverage of the stock + filing whitespace. Data dump now, polish later. |

**Why three:** every meaningful click on the site eventually lands on a fund, an issuer, or an underlying stock. If those three surfaces are coherent, the rest of the site is just navigation around them.

---

## Section 2 — The 6-pillar nav (Ryu's spec, locked)

```
┌─────────────────────────────────────────────────────────────────┐
│ HOME                                                             │
│   • KPIs (REX AUM, daily flows, alerts)                          │
│   • Dashboard-style updates                                      │
│   • Quick link navigations                                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ REX OPERATIONS                                                   │
│   • REX Products    → /market/rex (renamed mega-link)            │
│   • REX Pipeline    → /pipeline/products + filings tracking      │
│   • REX Calendar    → /calendar/?issuer=REX  + distributions     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ MARKET INTELLIGENCE                                              │
│   • REX Dashboard       → /market/rex                            │
│   • Category Dashboard  → /market/category                       │
│   • Issuer Dashboard    → /market/issuer                         │
│   • ETP Underliers      → /market/stock-coverage                 │
│   • Stocks (NEW)        → /stocks/                               │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ SEC INTELLIGENCE  (largest pillar)                               │
│                                                                  │
│   ETPs:                                                          │
│   • ETP Dashboard       → /filings/dashboard                     │
│   • ETP Filings         → /filings/explorer                      │
│   • L&I Landscape       → /filings/landscape                     │
│                                                                  │
│   Notes:                                                         │
│   • Notes Dashboard     → /notes/ (merged Overview + Issuer)     │
│                                                                  │
│   13F:  (placeholder labels — non-clickable until rebuild)       │
│   • REX Report          (label only)                             │
│   • Market Report       (label only)                             │
│   • Institution Explorer (label only)                            │
│   • Country Intel       (label only)                             │
│   • → future /13F/institutions/{cik} like /funds/                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ TOOLS                                                            │
│                                                                  │
│   Market Intelligence:                                           │
│   • Compare ETPs                → /tools/compare/etps            │
│                                                                  │
│   SEC Intelligence:                                              │
│   • Compare Filings             → /tools/compare/filings         │
│   • Compare Notes               → /tools/compare/notes           │
│   • Compare 13F Institutions    → /tools/compare/13f-inst        │
│   • Compare 13F Products        → /tools/compare/13f-products    │
│                                                                  │
│   Leverage & Inverse:                                            │
│   • Filing/Launch Candidates    → /tools/li/candidates           │
│     (merged with Evaluator, parquet-backed, weekly_v2 visual)    │
│                                                                  │
│   Tickers:                                                       │
│   • CBOE Symbol Landscape       → /tools/cboe                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ DATA                                                             │
│   • Single page, exports + API docs                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Section 3 — The three detail surfaces, specified

### 3a. `/funds/{ticker}` (and `/funds/series/{series_id}`)

**The architectural problem and its answer:**

You asked: *"if a fund is live, it should show what? Vs if a fund is just a filing?"* — this is real. Bloomberg has no concept of SEC `series_id`. SEC has no concept of Bloomberg `ticker`. The bridge is one column (`fund_status.ticker`) and it's only populated when a fund actually trades.

**Answer:**

| Fund state | URL | Page renders |
|---|---|---|
| **LIVE** (effective + ticker + Bloomberg row) | `/funds/{ticker}` | Full DES screen + SEC breadcrumb. 15 sections (see below). |
| **FILED** (pending, no ticker yet) | `/funds/series/{series_id}` | SEC-only view: filings, name history, prospectus, status, expected effective date. No Bloomberg block. |
| **EFFECTIVE-PRE-LAUNCH** (effective, no Bloomberg yet) | `/funds/series/{series_id}` until ticker confirmed → 301 to `/funds/{ticker}` once Bloomberg row appears | Same as FILED until pipeline catches up. |

**Why two URL shapes, not one:** Bloomberg keys on ticker; SEC keys on series_id. Trying to force one URL scheme requires a placeholder for filed funds (TBD-tickers, fund-name slugs) that breaks the moment a real ticker is assigned. Two shapes with a 301 transition is the honest design.

**Layout (15 sections, top to bottom):**

| # | Section | Source | Notes |
|---|---|---|---|
| 1 | Breadcrumb: Home → Filings → Trust → {ticker} | SEC | The four-crumb structure you love — preserved exactly |
| 2 | Header: ticker, REX badge, fund name, canonical issuer link, category, fund_type, leverage/direction/underlier pills | Bloomberg + SEC | Issuer link uses canonicalization map |
| 3 | Action buttons: SEC Filings (anchor), Compare | both | |
| 4 | SEC Meta grid: Status, Effective Date, Latest Form, Latest Filing Date, Status Reason, Prospectus | SEC | Status badge with confidence level |
| 5 | Key Metrics row: AUM, Expense Ratio, Spread, Avg Vol 30D, Short Interest, Inception | Bloomberg | Renders only when Bloomberg available |
| 6 | 3-Axis Taxonomy: Strategy / Asset Class / Sub-Strategy + Legacy Category | Bloomberg | |
| 7 | Total Returns table: 1D…3Y | Bloomberg | Color-coded |
| 8 | Fund Flows table: 1D…1Y | Bloomberg | Color-coded |
| 9 | Additional Metrics: Yield, Premium/Discount, Tracking Error, Open Interest | Bloomberg | |
| 10 | AUM Chart: 12-month | Bloomberg | Chart.js |
| 11 | Total Return Chart: Growth of $10K, range pills 1M/3M/6M/1Y/YTD/Max | Bloomberg | Scraper data |
| 12 | Name History | SEC | |
| 13 | Filing History (with form-type filter pills, confidence badges, View/Analyze) | SEC | The core SEC story |
| 14 | Institutional Holders (13F scaffold) | dormant | Renders empty until 13F rebuild |
| 15 | Competitors table: same-underlier or category peers | Bloomberg | Each row links → /funds/{competitor_ticker} |

Sections 5–11 + 15 render only when Bloomberg data exists. Sections 1–4 + 12–14 always render. This means `/funds/{ticker}` for a fund that just lost Bloomberg coverage gracefully degrades to the SEC view — no broken page.

### 3b. `/issuers/{canonical_name}`

**The bug it fixes:** today `/market/issuer/detail?issuer=BlackRock` runs `master[issuer_col] == "BlackRock"` (exact-match string equality). Funds labeled `iShares`, `BlackRock Inc`, `BlackRock Asset Management`, `BlackRock Fund Advisors` are excluded. That's the "BlackRock 47 funds" problem.

**Root cause:** `issuer_canonicalization.csv` is applied at **write-time only** (in `apply_issuer_brands.py` and `services/market_sync.py:532`). The read-side queries in `services/market_data.py`, `routers/market.py:288`, `routers/downloads.py:682`, `routers/market_advanced.py:126,156` never consult the canon map.

**Fix:** apply the canon map at read time in `services/market_data.py:get_master_data()` and at the filter call sites. Filter to `confidence == "AUTO"` only — the 3 REVIEW rows are bad fuzzy matches (KraneShares→GraniteShares 0.77, Amplify→Simplify 0.75, 21Shares→iShares 0.75) and would silently mis-assign funds. **Those 3 must be manually corrected before any read-side application.**

**Page contents:**
- Header: canonical issuer name + "Also known as: {variants}" sub-line
- KPI row: total AUM, # of funds (canonicalized), # of categories, # of recent filings (90d)
- 12-month AUM trend chart
- Category breakdown (donut + table)
- Recent filings (last 90d) — table with links to `/funds/{ticker}`
- Recent launches (last 90d) — table with links
- Full product roster sorted by AUM — every row links to `/funds/{ticker}`

**Open question:** when a user clicks "iShares" from a list, do they land at `/issuers/iShares` or `/issuers/BlackRock`? Recommendation: always 301 to canonical (`/issuers/iShares` → `/issuers/BlackRock`) so canonical is the only stable URL. Variant shows up as the "Also known as" line.

### 3c. `/stocks/{ticker}`

**Per your spec:** data-dump now, polish later. Vision = Bloomberg DES screen for stocks (mirroring funds page).

**Minimum viable page:**
- Header: ticker, company name, sector, exchange, market cap
- Key signals from `whitespace_v4.parquet` if present: composite_score, score_pct, mentions_24h + z-score, RVol 30d/90d, returns 1m/3m/1y, SI ratio, insider_pct, inst_own_pct, theme tags, hot-theme badge
- ETP coverage of this stock: every ETP that has this ticker as `map_li_underlier` or `map_cc_underlier` (from `mkt_master_data`) — table with links to `/funds/{ticker}`
- Filing whitespace: any 485APOS filings naming this underlier (from `filed_underliers.parquet`)
- "No data yet" banner if not in any of the above

Build it as a route + template. Do not over-engineer. We extend as we add new stock data sources.

---

## Section 4 — Live route inventory mapped to the rebuild

Every existing route. Where it goes, or whether it dies.

### KEEP (with rewiring) — 23 routes

| Current URL | Verdict | Lands in pillar | Notes |
|---|---|---|---|
| `/` | KEEP | Home | Add quick links per spec |
| `/api/v1/home-kpis`, `/api/v1/aum-goals/*`, `/api/v1/ticker-strip` | KEEP | Home APIs | |
| `/market/rex` | KEEP | REX Ops + Market Intel | Two nav entries, same page (or split if data needs differ) |
| `/market/category` | KEEP | Market Intel | Add canonicalization on issuer-group columns |
| `/market/issuer` | KEEP | Market Intel | Apply read-side canonicalization |
| `/market/stock-coverage` | KEEP | Market Intel | Renamed to "ETP Underliers" in nav |
| `/market/compare` | KEEP | Tools / Market Intel | "Compare ETPs" — leave as-is per Ryu, deferred for rework |
| `/calendar/` | KEEP | REX Ops + standalone | Single canonical calendar; verify Render load failure |
| `/pipeline/`, `/pipeline/products` | KEEP | REX Ops | |
| `/filings/dashboard` | KEEP | SEC Intel / ETPs | "Competitor watch by fund type" — query rewrite needed |
| `/filings/landscape` | KEEP | SEC Intel / ETPs | |
| `/filings/symbols` | KEEP | Tools / Tickers → "CBOE Symbol Landscape" | |
| `/filings/explorer` | KEEP | SEC Intel / ETPs → "ETP Filings" | |
| `/notes/`, `/notes/issuers`, `/notes/search`, `/notes/tools/autocall` | KEEP | SEC Intel / Notes | Notes Dashboard merges `/notes/` + `/notes/issuers` |
| `/capm/` | KEEP | Standalone | Revisit later |
| `/downloads/` | KEEP | Data pillar | |
| `/admin/*` | KEEP | Admin | Operator only |

### MERGE — 4 routes collapsed into others

| Current URL | Merged into | How |
|---|---|---|
| `/funds/{series_id}` | `/funds/{ticker}` (canonical) + `/funds/series/{series_id}` (filed-only) | Detail page A keeps everything, gains Bloomberg DES |
| `/market/fund/{ticker}` | `/funds/{ticker}` | Detail page B's Bloomberg sections layered onto A |
| `/market/issuer/detail?issuer=X` | `/issuers/{canonical_name}` | New clean URL, canonicalization on lookup |
| `/filings/evaluator` | `/tools/li/candidates` | Inline evaluator panel inside the merged candidates page |
| `/notes/issuers` | `/notes/` | Single Notes Dashboard combines both |

### NEW — 6 surfaces being built

| URL | What it is |
|---|---|
| `/funds/series/{series_id}` | SEC-only view for filed funds without ticker |
| `/issuers/{canonical_name}` | Canonical issuer detail |
| `/stocks/{ticker}` | Stock DES dump |
| `/tools/li/candidates` | Merged Filing+Launch+Evaluator, parquet-backed (weekly_v2_report visual) |
| `/tools/compare/{etps,filings,notes,13f-inst,13f-products}` | Compare backbone, 5 entry points |
| `/13F/institutions/{cik}` | Future — labeled in nav now, built later |

### KILL — 18 routes (Phase 1, pure deletion)

| URL | Why killed |
|---|---|
| `/market/` | Redundant landing |
| `/market/treemap` | Just goes to category view |
| `/market/share` | Already redirects to `/market/issuer` — kill the alias too |
| `/market/calendar` | DUP of `/calendar/` |
| `/market/underlier` | DUP of `/market/stock-coverage` |
| `/market/monitor` + `webapp/routers/monitor.py` | "Auto-refreshes every 60s" claim is factually wrong; revisit later |
| `/market/rex-performance` | Merged into the new candidates page; the "ETP Screener" function is replaced by parquet-backed tools |
| `/dashboard` | DUP of `/filings/dashboard` |
| `/screener/` + `/screener/3x-analysis` + `/screener/4x` + `/screener/evaluate` + `/screener/report` | Half-redirect graveyard — kill router file |
| `/screener/market` + `/screener/rex-funds` + `/screener/risk` + `/screener/stock/{ticker}` | Orphan rendering pages, all duplicated by Market or Filings |
| `/strategy` + `/strategy/whitespace` + `/strategy/race` + `/strategy/ticker/{ticker}` | Pillar invisible from nav; consumed by new `/tools/li/candidates` instead |
| `/filings/` + `/filings/hub` + `filings_hub.html` | Stale landings; mega-menu replaces them |
| `/holdings/*` UI + `/intel/*` UI | Per Ryu — drop UI, keep ingestion. 13F dropdown items become non-links until rebuild. |
| `/funds/`, `/universe/` legacy lists | Replaced by `/filings/explorer` |
| `/search/*` legacy pages | If unused; check before kill |

### FILES DELETED in Phase 1

- `webapp/routers/screener.py` (entire file)
- `webapp/routers/strategy.py` (entire file)
- `webapp/routers/monitor.py` (entire file)
- `webapp/routers/holdings.py`, `holdings_placeholder.py`, `intel.py`, `intel_competitors.py`, `intel_insights.py` — UI routes only; **keep** the data pipelines that feed `13f_holdings.db`
- 9 screener templates: `screener_market.html`, `screener_rex.html`, `screener_risk.html`, `screener_stock.html`, `screener_landscape.html`, `screener_3x.html`, `screener_4x.html`, `screener_rankings.html`, `screener_evaluate.html`
- 4 strategy templates: `strategy/home.html`, `whitespace.html`, `ticker.html`, `race.html`, `empty.html`
- `monitor.html`
- `filings_hub.html`
- 13F templates under `intel/*` and the holdings templates: keep on disk for the rebuild, just don't link

---

## Section 5 — Classification + canonicalization status

**State today (per Agent D):**

| Rule file | Rows | Status |
|---|---|---|
| `fund_master.csv` | 7,231 | Live, drives classification |
| `issuer_canonicalization.csv` | 15 clusters | 12 AUTO + **3 REVIEW (BAD — must reject)** |
| `issuer_brand_overrides.csv` | 3,360 | Live, ticker-level brand overrides |
| `underlier_overrides.csv` | 47 | Live, ticker-level underlier corrections |
| `attributes_CC.csv` | 342 | Live, CC-specific attributes |

**The 3 REVIEW rows are wrong:**
- KraneShares → GraniteShares (similarity 0.77) ❌
- Amplify → Simplify (0.75) ❌
- 21Shares → iShares (0.75) ❌

These would silently mis-assign ~78 funds if applied. **Action:** delete those 3 rows from the CSV (or hard-set their confidence to REJECT) before any read-side canonicalization ships.

**AI workflow state:** **DORMANT.** The `ClassificationProposal` table exists and the admin scan/approve UI works, but it runs a local rule-engine scan (`tools.rules_editor.classify_engine.scan_unmapped`), not a model call. Zero invocations of `claude_service` or the Anthropic API in the classification routes. The "AI" in the workflow today is a regex/dictionary system + human approval. Live model proposals would be a future feature, not a current one.

**Where canonicalization is applied vs not:**

| Surface | Applies canon map? | File / line |
|---|---|---|
| `apply_issuer_brands.py` (write-side) | YES | scripts/apply_issuer_brands.py |
| `services/market_sync.py:532` (write-side) | YES | services/market_sync.py:532 |
| `services/market_data.py` issuer rollup (~line 1107) | **NO** | `groupby("issuer_display")` raw |
| `services/market_data.py` issuer filter (~line 1244) | **NO** | exact-string equality |
| `routers/market.py:288` (`/market/issuer/detail`) | **NO** | the BlackRock-47 bug |
| `routers/downloads.py:682` CSV filter | **NO** | SQL exact match |
| `routers/market_advanced.py:126,156` advanced search | PARTIAL | `LIKE` substring, not canonical |

**Fix in Phase 2b:**

```python
# webapp/services/market_data.py — apply once after _load_master_from_db
canon_csv = Path(RULES_DIR) / "issuer_canonicalization.csv"
canon_map = pd.read_csv(canon_csv, engine="python", on_bad_lines="skip")
canon_map = canon_map[canon_map["confidence"] == "AUTO"]  # critical filter
variant_to_canon = dict(zip(canon_map["variant"], canon_map["canonical"]))
df["issuer_display"] = df["issuer_display"].map(
    lambda v: variant_to_canon.get(str(v).strip(), str(v).strip()) if pd.notna(v) else v
)
```

Apply at `get_master_data()` so every downstream consumer (rollup, detail, downloads) gets canonical strings without each having to repeat the lookup.

---

## Section 6 — Engine sync (the merged Filing+Launch Candidates+Evaluator)

**The sync gap is total, not partial.** `/filings/candidates` and `/filings/evaluator` today read `data/SCREENER/data.xlsx` via `screener_helpers.get_3x_data()` — they have **zero awareness** of the four parquets the L&I engine actually writes (`whitespace_v4.parquet`, `launch_candidates.parquet`, `competitor_counts.parquet`, `filed_underliers.parquet`).

The only webapp route that already reads parquets correctly is `webapp/routers/strategy.py` (which we're killing). That pattern (load parquet → sort → slice → pass rows to template) is the model for the new candidates page.

**The new `/tools/li/candidates` page mirrors `weekly_v2_report.py` exactly:**

| Section | Source | Notes |
|---|---|---|
| Hero KPI bar | `weekly_v2_report.load_filings_count_this_week()` | L&I 485APOS this week, REX filings, new underliers, top-mention ticker |
| Launch Queue (cards × 12) | `launch_candidates.parquet` filtered to `has_signals=True`, sorted by `composite_score` | Card per ticker: sector, HOT THEME badge, theme badges, 1-line desc via `_resolve_company_line`, signal strip (Mkt Cap / Vol90 / 1m / 1y / OI / SI / Mentions), Filers row (`competitor_filed_total`), projected effective date |
| Filing Whitespace (cards × 12) | `whitespace_v4.parquet`, top 12 by `composite_score` | Same card layout. `filed_underliers.parquet` provides `n_filings_total` |
| Inline Evaluator panel | input box + POST → `compute_score_v3()` directly | Replaces standalone `/filings/evaluator`. Returns composite_score, score_pct, top drivers — same fields as cards |
| Money Flow table | `bbg_timeseries_panel.parquet` if present | 12 rows, ranked by abs(4w net flow) |

**Reuse the email's formatters:** `_resolve_company_line`, `_pretty_themes`, `_fmt_mcap`, `_fmt_pct`, `_fmt_oi` — extract these from `weekly_v2_report.py` to a shared `screener/li_engine/analysis/formatters.py` so web + email share identical rendering.

**`has_signals` filter is mandatory** for launch candidates — rows without bbg signals have NaN everywhere and render blank cards.

---

## Section 7 — Phased execution

**Sequence:**

```
PHASE 0 (you sign this off)
   ↓
PHASE 1 (kill list — pure deletion, no risk, ships fast)
   ↓
PHASE 2 (canonical detail surfaces — the foundation)
   ├─ 2a. /funds/{ticker} merge
   ├─ 2b. /issuers/{name} with read-side canonicalization
   └─ 2c. /stocks/{ticker} data dump
   ↓
PHASE 3 (rewire all list pages to canonical surfaces — mechanical)
   ↓
PHASE 4 (nav restructure to 6-pillar — base.html rewrite)
   ↓
PHASE 5 (engine sync — merged /tools/li/candidates from parquets)
   ↓
PHASE 6 (deferred — compare polish, monitor rebuild spec, filings slowness)
```

**No phase starts until the previous one is verified live on Render.**

---

## Section 8 — Open architectural decisions (your call)

| # | Decision | Recommendation |
|---|---|---|
| 1 | When variant clicked (e.g., "iShares"), redirect to canonical URL or stay on variant? | **Recommend:** 301 to canonical. Single stable URL per issuer. Show "Also known as: iShares" sub-line. |
| 2 | Should `/funds/series/{series_id}` exist as a public URL, or only as an internal redirect target? | **Recommend:** public, accessible. Filed funds are real and linkable from `/calendar/` and `/filings/landscape`. |
| 3 | 13F UI items in nav — non-clickable labels, or hide entirely until rebuild? | **Recommend:** non-clickable per your spec. Keeps the IA visible so users know it's coming. |
| 4 | `/stocks/{ticker}` for stocks NOT in any parquet — render skeleton with "no signals yet" or 404? | **Recommend:** skeleton page. Gives a stable URL we can deep-link to from anywhere. |
| 5 | The 3 bad REVIEW rows in `issuer_canonicalization.csv` — delete them or set REJECT flag? | **Recommend:** delete. Cleanest, no future risk of accidentally re-promoting them. |
| 6 | Filings dashboard "competitor watch by fund type" — fold into Phase 5 (engine sync) or break out as its own task? | **Recommend:** break out as Phase 5b. It's a distinct query rewrite. |
| 7 | Tools section is 7 entries (5 compares + 1 candidates + 1 CBOE) — single mega-menu or sub-categorized? | **Recommend:** sub-categorized exactly as you laid out (Market Intel / SEC Intel / L&I / Tickers). |
| 8 | Does `/calendar/` need a single fix-the-load-bug pass before Phase 1 ships, or after? | **Recommend:** before. It's a blocker for users today. ~30 min investigation, likely a data import error. |

---

## Section 9 — Sign-off

If you approve this doc:
1. I delete the 3 REVIEW rows from `issuer_canonicalization.csv`
2. I diagnose + fix `/calendar/` load failure
3. Phase 1 ships (kill list — pure deletion, can land in one commit)
4. We sequence Phases 2–5 in order

If you want to break it apart, redline this doc and I rewrite. **Do not begin Phase 1 deletions until you say go.**

---

**Foundation candidate.** Mark up Section 8 (or the rest of it) and I lock it.
