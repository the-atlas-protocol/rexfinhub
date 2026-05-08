# Rexfinhub — Website Mindmap

**Date:** 2026-05-06
**Purpose:** Lay out every page on the site so we can decide what stays, what dies, what merges, what gets promoted. **This is the foundation. Nothing else ships until this is settled.**

**Surface area found:** 35 router files, ~155 route definitions, ~88 templates. 7 nav pillars + Home + Admin + standalone (CapM, Data, Logout).

---

## Section 1 — How to read this

For every page, three columns:

| Field | Meaning |
|---|---|
| **Status** | `LIVE` (in nav, working), `ORPHAN` (works but not in nav), `REDIRECT` (kept as a 301), `STUB` (placeholder), `DEAD` (broken or never wired), `DUPLICATE` (same page, two URLs) |
| **Question it answers** | What a user opens this page to find out |
| **Issues** | Specific problems found tonight |

Verdicts at the end: **KEEP / FIX / KILL / MERGE / PROMOTE / RENAME**.

---

## Section 2 — Your 5 complaints, diagnosed

Before the mindmap itself, here's what's actually going on with each thing you flagged:

### 2a. `/screener/` and `/screener/fund/nvda`, `/screener/risk`

**Found.** `webapp/routers/screener.py` is in two pieces:

- **Lines 31–60** — `/screener/`, `/screener/3x-analysis`, `/screener/4x`, `/screener/evaluate`, `/screener/report` are all **301 redirects** to `/filings/*`. So those URLs technically don't render anything dead — they bounce.
- **Lines 67–260** — but `/screener/market`, `/screener/rex-funds`, `/screener/risk`, `/screener/stock/{ticker}` are still **fully rendered pages** that nobody links to. They're orphans. (Note: it's `/screener/stock/nvda`, not `/screener/fund/nvda` — so you were on the orphan stock-detail page.)

**Verdict:** the whole `/screener/*` namespace should die. Half-redirected, half-orphan. The L&I work that mattered is now under `/filings/*`. The remaining orphan pages (`/screener/market`, `/screener/rex-funds`, `/screener/risk`, `/screener/stock/{ticker}`) duplicate things `/market/*` and `/filings/*` already do better.

### 2b. `/filings/` index page is stale

`/filings/` (filings.py:60) renders `filings_hub.html`. The 4 actual working sub-pages (`dashboard`, `landscape`, `candidates`, `evaluator`, `symbols`, `explorer`) are all in the mega-menu directly. The hub page is a legacy landing that doesn't add anything.

**Verdict:** kill `/filings/` index. Make the mega-menu the only entry point. Or 301 it to `/filings/dashboard`.

### 2c. ETP Screener naming + missing dropdown + double-render

Three separate issues stacked:

1. **`/market/rex-performance`** (market.py:369) renders `market/rex_performance.html` — this IS your "ETP Screener". It's the interactive screener with column picker. **It is NOT in the nav.** Confirmed by reading `base.html` lines 41–115 (Market mega-panel) — there's no mega-link to it. So you have to type the URL to find it.
2. **Naming:** internally called "REX Performance" but it screens the whole ETP universe (with rex/competitors/all scope). Should be renamed.
3. **Double-render:** the bug shows up because the page mounts heavy DataTables/JS that re-paint after first render. I'll need to actually open the template to confirm root cause — but it's likely a hot-reload artifact OR a turbo-frame style double-mount.

**Verdict:** RENAME `/market/rex-performance` → `/market/etp-screener`, ADD to nav, FIX the double-render.

### 2d. Issuer Detail — BlackRock showing 47 funds

**Found the cause.** `market.py:288`:

```python
df = master[master[issuer_col].fillna("").str.strip() == issuer.strip()]
```

This is **exact-match on `issuer_display`**. So if you click "BlackRock" the system only finds funds where `issuer_display` is *literally* the string "BlackRock". Funds labeled `iShares`, `iShares Trust`, `BlackRock Asset Management`, `BlackRock Inc`, `BlackRock Fund Advisors`, etc. are excluded.

The issuer_canonicalization work tonight (3,360 rows) **maps** these variants but the `/market/issuer/detail` page never applies the mapping at query time — it just reads whatever string is on the row.

**Verdict:** detail page must canonicalize on lookup. Click on "BlackRock" should resolve to all variants. Same fix needed everywhere we filter by issuer_display.

You're correct: the categorization work didn't reach this page.

### 2e. Calendar pages not loading

Three calendar URLs, all wired:

| URL | File | What it does |
|---|---|---|
| `/calendar/` | `calendar_router.py:23` | Top-level pillar — delegates to `market_advanced.calendar_view` |
| `/market/calendar` | `market_advanced.py:68` | The original calendar — same view |
| `/pipeline/` | `pipeline_calendar.py:65` | DIFFERENT page — internal REX product pipeline, not SEC calendar |

The delegation in `calendar_router.py` is fine in code. If "none are loading" — most likely the `market_advanced.calendar_view` import is throwing on Render (e.g., missing data column, or a recent commit broke it). I haven't reproduced the failure yet because I stopped to build this mindmap. **Top of my fix list once we settle the IA.**

---

## Section 3 — The mindmap (every page)

### PILLAR 1: HOME (`/`)

| URL | Status | Question | Issues |
|---|---|---|---|
| `/` | LIVE | Daily snapshot — REX AUM, goals, recent activity | Heavy KPI loads — slow on cold start |
| `/dashboard` | ORPHAN | Old SEC dashboard | DUPLICATE of `/filings/dashboard` |
| `/api/v1/home-kpis` | API | Fuels home cards | — |
| `/api/v1/aum-goals/history/{slug}` | API | Goals modal chart data | — |
| `/api/v1/ticker-strip` | API | Top-of-page ticker scroll | — |

**Verdict:** KEEP `/`, KILL `/dashboard` orphan.

---

### PILLAR 2: MARKET (`/market/*`)

**The biggest pillar — 13 routes, 4 in nav, 9 orphans/utilities.**

| URL | In Nav? | Status | Question | Issues |
|---|---|---|---|---|
| `/market/` | no | ORPHAN | Landing | redundant — mega-menu replaces it |
| `/market/rex` | yes | LIVE | REX suite AUM, flows, positioning | — |
| `/market/category` | yes | LIVE | 8 ETP categories, market share | filter chips don't expose new sub_strategy |
| `/market/issuer` | yes | LIVE | Rank issuers by AUM/flows | doesn't canonicalize → variant issuers shown separately |
| `/market/issuer/detail?issuer=...` | yes | **BROKEN** | Single-issuer roster + AUM trend | **BlackRock 47 funds** — exact-match, no canonicalization |
| `/market/stock-coverage` | yes | LIVE | ETP coverage by underlying stock | renamed from `/underlier` recently — both URLs alive |
| `/market/underlier` | no | DUPLICATE | same as stock-coverage | should 301 → stock-coverage |
| `/market/compare` | yes | LIVE | Side-by-side fund comparison | needs structural rework per prior plan |
| `/market/calendar` | yes | LIVE | Launch calendar (in Market pillar) | DUPLICATE of `/calendar/` — both render same view |
| `/market/monitor` | yes | LIVE | Live indices/commodities/crypto | — |
| `/market/rex-performance` | **NO** | ORPHAN | **The "ETP Screener" you keep asking about** | not in nav, double-renders, wrong name |
| `/market/treemap` | no | ORPHAN | Treemap viz | — |
| `/market/share` | no | REDIRECT | 302 → `/market/issuer` | safe to leave |
| `/market/fund/{ticker}` | no | ORPHAN | Per-fund deep-dive | linked from inside other pages |
| `/market/api/*` | n/a | API | — | — |

**Verdict cluster:**
- KILL `/market/`, `/market/underlier` (dup)
- RENAME `/market/rex-performance` → `/market/etp-screener` and ADD to nav
- FIX `/market/issuer/detail` canonicalization
- DECIDE: `/market/calendar` vs `/calendar/` — pick one, redirect the other
- KEEP rest

---

### PILLAR 3: FILINGS (`/filings/*`)

| URL | In Nav? | Status | Question | Issues |
|---|---|---|---|---|
| `/filings/` | no | STALE | Old hub landing | you said "get rid of this page" |
| `/filings/dashboard` | yes | LIVE | Recent filings + competitor watch | — |
| `/filings/landscape` | yes | LIVE | 2x/3x/4x/5x competitive matrix | — |
| `/filings/candidates` | yes | LIVE | Scored candidate pipeline | — |
| `/filings/evaluator` | yes | LIVE | 4-pillar ticker evaluator | — |
| `/filings/symbols` | yes | LIVE | CBOE symbol reservations | — |
| `/filings/explorer` | yes | LIVE | Search funds/filings | — |
| `/filings/hub` | no | DUPLICATE | Same as `/filings/` | kill both |
| `/filings/report` | no | ORPHAN | PDF report download endpoint | utility — keep |
| `/filings/landscape/export` | no | API | CSV export | — |

**Verdict:** KILL `/filings/` and `/filings/hub`. Keep the 5 actual sub-pages.

---

### PILLAR 4: SCREENER (`/screener/*`) — THE KILL ZONE

| URL | Status | Question | Verdict |
|---|---|---|---|
| `/screener/` | REDIRECT | → `/filings/landscape` | KILL the redirect, kill the file |
| `/screener/3x-analysis` | REDIRECT | → `/filings/candidates` | KILL |
| `/screener/4x` | REDIRECT | → `/filings/candidates` | KILL |
| `/screener/evaluate` | REDIRECT | → `/filings/evaluator` | KILL |
| `/screener/report` | REDIRECT | → `/filings/report` | KILL |
| `/screener/market` | ORPHAN | "Market Landscape" — underlier popularity | DUP of `/market/category` insights — KILL |
| `/screener/rex-funds` | ORPHAN | REX fund portfolio + T-REX track record | DUP of `/market/rex` — KILL |
| `/screener/risk` | ORPHAN | Volatility risk watchlist | KILL or fold into `/market/etp-screener` as a tab |
| `/screener/stock/{ticker}` | ORPHAN | Per-stock competitive deep dive | DUP of `/filings/evaluator?ticker=...` — KILL |

**Verdict:** delete `webapp/routers/screener.py` entirely + 6 templates (`screener_market.html`, `screener_rex.html`, `screener_risk.html`, `screener_stock.html`, `screener_landscape.html`, `screener_3x.html`, `screener_4x.html`, `screener_rankings.html`, `screener_evaluate.html`). The L&I screener IS the filings pillar now.

---

### PILLAR 5: STRATEGY (`/strategy/*`) — INVISIBLE PILLAR

| URL | In Nav? | Status | Question | Issues |
|---|---|---|---|---|
| `/strategy` | **NO** | ORPHAN | L&I strategy engine landing | not in nav |
| `/strategy/whitespace` | **NO** | ORPHAN | Whitespace candidates | not in nav |
| `/strategy/race` | **NO** | ORPHAN | Filing race | not in nav |
| `/strategy/ticker/{ticker}` | **NO** | ORPHAN | Per-ticker deep-dive | linked from /strategy/* only |

**Verdict:** an entire pillar is hidden from the user. Either ADD to nav or KILL. Tonight's L&I ranking work feeds these pages — they have value.

---

### PILLAR 6: OWNERSHIP (`/holdings/*`, `/intel/*`) — 13F GATED

All routes are wrapped in `if os.environ.get("ENABLE_13F")`. On Render this is OFF, so the entire pillar shows "Coming Soon" badges. Locally it's ON.

| URL | Status (Render) | Question |
|---|---|---|
| `/intel/`, `/intel/rex`, `/intel/rex/sales`, `/intel/rex/performance`, `/intel/rex/filers` | DARK | 13F intelligence dashboards |
| `/intel/competitors`, `/intel/country`, `/intel/trends`, `/intel/asia` | DARK | Competitor/geo analysis |
| `/holdings/`, `/holdings/crossover`, `/holdings/fund/{ticker}`, `/holdings/{cik}`, `/holdings/{cik}/history` | DARK | Institution browser |
| `/holdings/admin/*` | DARK | 13F refresh |

**Verdict:** decide — flip `ENABLE_13F=1` on Render and ship the 13F MVP, OR consciously keep it dark. Right now it's neither — half the nav says "Soon" forever.

---

### PILLAR 7: PIPELINE (`/pipeline/*`) — internal product pipeline

| URL | In Nav? | Status | Question |
|---|---|---|---|
| `/pipeline/` | yes | LIVE | Pipeline calendar (filings/effectives/launches/distributions) |
| `/pipeline/products` | yes | LIVE | 470 products with status |
| `/pipeline/summary` | no | API | Counts |
| `/pipeline/distributions/export.csv` | no | API | CSV |
| `/pipeline/{year}/{month}` | no | DEEP | Month-specific calendar |

**Verdict:** KEEP. This is the operator-facing pipeline view, distinct from the public /calendar/.

---

### PILLAR 8: CALENDAR (`/calendar/*`) — newly promoted

| URL | In Nav? | Status | Question | Issues |
|---|---|---|---|---|
| `/calendar/` | yes | **REPORTED BROKEN** | ETP Launch Calendar | "not loading" — needs reproduction |
| `/calendar/?primary_strategy=L%26I` | yes | filtered | L&I launches only | same |
| `/calendar/?primary_strategy=Income` | yes | filtered | Income launches only | same |

**Code:** `calendar_router.py` delegates to `market_advanced.calendar_view`. If broken on Render it's almost certainly an import or query error in `calendar_view`. **First thing to verify after this mindmap is signed off.**

**Verdict:** FIX immediately. Then DECIDE: should `/market/calendar` redirect to `/calendar/` or vice versa? Right now both render the same view — pure duplication.

---

### PILLAR 9: STRUCTURED NOTES (`/notes/*`)

| URL | In Nav? | Status | Question |
|---|---|---|---|
| `/notes/` | yes | LIVE | 594K products overview |
| `/notes/issuers` | yes | LIVE | Issuer dashboard, league tables |
| `/notes/search` | yes | LIVE | Filter by issuer/type/underlier |
| `/notes/tools/autocall` | yes | LIVE | Autocall simulator |
| `/notes/tools/autocall/data`, `/sweep`, `/suggest-coupon` | no | API | Simulator backend |

**Verdict:** KEEP. Solid pillar.

---

### STANDALONE LINKS

| URL | In Nav? | Status | Question | Issues |
|---|---|---|---|---|
| `/capm/` | yes | LIVE | Capital Markets dashboard | — |
| `/downloads/` | yes | LIVE | Data + API | — |
| `/admin/*` | yes (admin) | LIVE | Operator panel | 50+ routes — see below |

---

### ADMIN (`/admin/*`)

50+ routes. They serve operator workflows (gate toggle, recipients, classification approvals, digest send, reports preview/send-test/send, ticker QC, trust/subscriber approvals). **Not user-facing.** Out of scope for mindmap cleanup but see `/admin/health` and `/admin/classification-stats` — both LIVE and useful for ops.

---

### ORPHANED / UNCATEGORIZED ROUTES

These exist in code but aren't in the main nav, footer, or any pillar:

| URL | What it is | Verdict |
|---|---|---|
| `/dashboard` | Old SEC dashboard (dashboard.py:275) | DUP of `/filings/dashboard` — KILL |
| `/search/`, `/search/verify/{cik}`, `/search/request` | Trust search | linked from /admin? KEEP if used; KILL if not |
| `/universe/` | Trust universe browser | DUP of `/funds/` and `/trusts/{slug}` |
| `/funds/`, `/funds/{series_id}` | Fund browser (legacy) | DUP of `/filings/explorer` |
| `/trusts/{slug}` | Trust detail | KEEP — deep-link target |
| `/analytics` | Site analytics | OPS — KEEP if useful |
| `/analysis/filing/{filing_id}` | Per-filing AI analysis | KEEP — deep-link target |
| `/digest/`, `/digest/subscribe` | Email digest signup | KEEP |
| `/reports/`, `/reports/li`, `/reports/cc`, `/reports/ss` | Email report previews | OPS — keep accessible from admin only |
| `/monitor/*` | Market monitor (also at /market/monitor) | DUP — KILL one |
| `/global_search` | Global Ctrl+K search backend | API — KEEP |
| `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me` | OAuth | API — KEEP |
| `/api/v1/*` | All public API endpoints | KEEP |

---

## Section 4 — Cross-pillar data flow

```
                  ┌──────────────────────────────────┐
                  │  SEC EDGAR (5-step pipeline)     │
                  │  Bloomberg .xlsm (Graph API)     │
                  └──────────────┬───────────────────┘
                                 ↓
                  ┌──────────────────────────────────┐
                  │  webapp/database (SQLite)        │
                  │   - mkt_master_data              │
                  │   - filings, fund_status         │
                  │   - issuer_canonicalization      │
                  │   - fund_master, attributes_CC   │
                  │   - ScreenerResult/Upload        │
                  └──────────────┬───────────────────┘
                                 ↓
       ┌─────────────────────────┼──────────────────────────┐
       ↓                         ↓                          ↓
   FILINGS PILLAR           MARKET PILLAR              CALENDAR PILLAR
   (filings.py)            (market.py +               (calendar_router.py +
   - dashboard              market_advanced.py)        market_advanced.py)
   - landscape              - rex                      delegates to market_advanced
   - candidates             - category
   - evaluator              - issuer ────╮
   - symbols                - issuer/detail (BROKEN — no canonicalization)
   - explorer               - rex-performance (orphan, double-renders)
                            - calendar (DUP of /calendar/)
                            - compare
                            - stock-coverage
       ↑                         ↑
       │                         │
       └────────── NOT linked to OWNERSHIP/INTEL pillar (13F gated dark on Render)
                                 │
                                 ↓
                            STRATEGY PILLAR (orphan — not in nav)
                            (strategy.py)
                            - whitespace
                            - race
                            - ticker/{ticker}
```

**Cohesion gaps surfaced:**

1. **Issuer canonicalization** is in the DB but **only the `/market/issuer` rollup uses it**. `/market/issuer/detail` doesn't. Neither do per-fund pages. This is the BlackRock 47 problem.
2. **Strategy pillar** has working pages but no nav entry. Users can't reach the L&I engine output.
3. **13F pillar** is dark on Render but takes up half the nav (greyed "Coming Soon" links). Either turn it on or hide the menu items.
4. **Two calendars** (`/market/calendar` and `/calendar/`) render the same view from the same backend function. One must die.
5. **Dashboard duplication**: `/`, `/dashboard`, `/filings/dashboard` all live, only `/` and `/filings/dashboard` are linked.
6. **Screener namespace** is a graveyard — half the URLs redirect to `/filings/`, the other half are unlinked orphans rendering ancient templates.

---

## Section 5 — Decision matrix (your input needed)

Pick yes/no for each. These are the foundation decisions. Once these are settled I can dispatch fixes coherently.

### KILL list (delete files + routes)

| # | Item | Why | Y/N |
|---|---|---|---|
| 1 | Delete `webapp/routers/screener.py` entirely + 9 screener templates | Half-redirect, half-orphan; replaced by `/filings/*` and `/market/etp-screener` | ? |
| 2 | Kill `/filings/` and `/filings/hub` index pages | Stale landings; mega-menu replaces them | ? |
| 3 | Kill `/dashboard` (old SEC dashboard) | Duplicate of `/filings/dashboard` | ? |
| 4 | Kill `/market/`, `/market/underlier` (dup of stock-coverage) | Redundant | ? |
| 5 | Kill `/funds/`, `/universe/` if unused | Replaced by `/filings/explorer` | ? |
| 6 | Kill one of `/market/calendar` OR `/calendar/` | Same view rendered twice | ? — pick which |

### RENAME list

| # | Item | New name | Y/N |
|---|---|---|---|
| 7 | `/market/rex-performance` → `/market/etp-screener` | Actual function = ETP screener | ? |
| 8 | `/market/stock-coverage` (current) — fine? Or rename? | Was "Underlier" before tonight | ? |

### PROMOTE list (add to nav)

| # | Item | Where | Y/N |
|---|---|---|---|
| 9 | Add `/market/etp-screener` to Market mega-menu | Tools column | ? |
| 10 | Add Strategy pillar (`/strategy/*`) to top nav | New mega-trigger | ? |
| 11 | Hide 13F nav items entirely until ENABLE_13F=1 on Render | Stop greying-out | ? |

### FIX list (verified bugs)

| # | Item | Severity | Owner |
|---|---|---|---|
| 12 | `/market/issuer/detail` exact-match bug — apply canonicalization on lookup | **HIGH** — the BlackRock 47 issue | me |
| 13 | `/calendar/*` not loading on Render — reproduce + fix | **HIGH** | me |
| 14 | `/market/etp-screener` double-render | MED | me |
| 15 | Apply canonicalization everywhere we filter by issuer_display | MED | me |

---

## Section 6 — What I'm NOT doing until you respond

- No more page audits.
- No more dispatched fix bots.
- No new features.
- No commits.

I'll sit on this until you mark up the decision matrix in Section 5. Then I fix in priority order: canonicalization → calendar load → kill list → nav additions → double-render.

---

**Ryu — break this apart. Tell me what's wrong, what to add, which decisions to flip. I'll re-cut the doc until it matches your mental model of the site, then we execute.**
