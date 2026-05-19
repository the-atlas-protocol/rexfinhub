# 13F Intel — Complete Project Brief

**Read this if you are**: a future Claude session, a new team member, future-Ryu, or anyone who needs to understand the 13F intelligence section on rexfinhub.com from a cold start. This document is the single source of truth for what was built, why, how, and what's left.

**Status**: code complete, validated locally, ready for deployment
**Owner**: Ryu El-Asmar (relasmar@rexfin.com)
**Branch**: `worktree-atlas-13f-intel` at `b6871e0`
**Last updated**: 2026-05-13

---

## Part 1 — Context: where does this fit?

### What is rexfinhub?

`rexfinhub.com` is the internal-plus-public web property for **REX Financial**, a leveraged & inverse / covered-call / crypto ETP issuer. It's a FastAPI + Jinja2 + SQLAlchemy + SQLite monolith deployed on Render, auto-deploys from `main`. The site is gated by site auth (everyone) plus admin auth (Ryu's team). Its purposes:

- **Public** (after site login): research dashboards on REX products + competitor ETP landscape, structured notes intelligence, SEC filing tracker
- **Admin** (after admin login): operational tools — pipeline management, classification approvals, daily reports, email digests, system health

The data spans three SQLite databases that share a process:
- `etp_tracker.db` — main app DB. SEC filings, Trust + FundStatus tables, Bloomberg-sourced `MktMasterData`, structured notes, screener data
- `13f_holdings.db` — institutional holdings. The subject of this project.
- `live_feed.db` — small auxiliary DB for live status

The pipelines (SEC filing scraper, Bloomberg sync, CBOE cookie rotation, structured notes extraction) all run on a VPS at `46.224.126.196` via systemd timers. **Pipelines never run on Render** because they'd block the web process or OOM. The VPS produces data files and pushes them to Render via an admin upload endpoint.

### Where does 13F fit in?

13F-HR is the SEC filing institutional investment managers (anyone with >$100M in 13(f)-eligible securities) submit every quarter, listing every position they hold. SEC publishes consolidated bulk ZIPs ~45-50 days after quarter-end. The data tells you which institutions hold which securities. For REX, this is the answer to two strategic questions:

1. **Product**: "where is institutional money flowing in the L&I / CC / crypto ETP space — and where are we missing?"
2. **Sales**: "which RIAs hold competitor products we should target for outreach?"

13F is **lagged** (45+ days), so it's never a trading signal. It's a strategic intelligence input.

---

## Part 2 — What we're building

### One sentence

An internal-only section at `rexfinhub.com/intel/*` and `rexfinhub.com/holdings/*`, gated behind admin login, that displays 13F institutional holdings cut through REX's product/competitor taxonomy.

### Who uses it

| User | What they get out of it |
|---|---|
| **REX product team** | Whitespace analysis: which categories are gaining institutional money? Where is competitor leverage flowing? Which competitor products have institutional traction but no REX equivalent? |
| **REX sales team** | Targeting lists: which RIAs hold ProShares 3x semis but not SOXL? Who's adding to REX positions vs. trimming? CSV exports for outreach campaigns. |

### What it is NOT

- Not public. Public visitors see zero trace of it.
- Not a Bloomberg-lite reference site. We're not competing with WhaleWisdom or Fintel for breadth.
- Not a trading tool. 13F lag is ~45 days; this is strategic, not tactical.
- Not investor-facing marketing. Following the same standing rule as the Bloomberg reports: internal intelligence stays behind auth.
- Not built from scratch. ~80% of the code existed before today; we activated + secured + cleaned + safeguarded it.

---

## Part 3 — The decisions we made today (and the ones we discarded)

### Decision 1: framing — internal-only vs. public-with-admin-tier

**Considered three product framings:**
- **A. The REX Lens** — public marketing pages + authed BD tool, narrow ETP scope
- **B. The Smart-Money Tracker** — public content site, narrative-driven
- **C. The Holdings Reference** — full Bloomberg-lite for everyone

**Picked: A's authed half only, dropping the public marketing layer.** Reasoning: the prior team built ~3000 lines of code for a sprawling A+C hybrid, stalled on the "is this public or not?" question, and shipped nothing for 11 weeks. Settling that question with "internal-only, full stop" lets us ship.

### Decision 2: scope — REX-only universe vs. full equity

**Discarded**: build a custom `cusip_universe.csv` (REX + ~50 competitors) and filter SEC ingest at row level.

**Picked**: ingest the full SEC equity universe; filter at the analytical-view level instead. Reason discovered late: `MktMasterData` (Bloomberg-sourced) already defines the ETP universe via `etp_category IS NOT NULL`. No separate file needed. Filtering at query time gives us analytical breadth without polluting institution-detail pages with noise.

### Decision 3: OpenFIGI's role

**Originally**: build the entire CUSIP universe via OpenFIGI ticker→CUSIP lookups for ~100 tickers.

**Reality**: OpenFIGI's role is narrow. Bloomberg already provides 99% of the CUSIPs we need via `MktMasterData`. OpenFIGI only fills the **stealth-REX gap** — REX products filed with SEC but not yet in Bloomberg master data. Typically zero to a handful per quarter.

### Decision 4: scope — fixed income

**Discarded**: include fixed income institutional ownership in v1.

**Reason**: Form 13F covers only Section 13(f)-eligible securities — equity-flavored. Fixed income holdings come from N-PORT (mutual funds), NAIC Schedule D (insurance), FFIEC call reports (banks). Different schemas, different filers, different schedules. Building "fixed income intel" is a multi-source aggregation project ~3-4× the scope of this one. Deferred to Phase 2.

### Decision 5: URL prefix — `/admin/intel/*` vs. `/intel/*`

**Considered**: rename all routes to `/admin/intel/*` to match `/admin/health`, `/admin/products`, `/admin/reports` convention.

**Picked**: keep `/intel/*` and `/holdings/*` at root, gate them at the router. Reason: 9 templates already link to the existing URLs. Renaming = 9 template edits + risk of broken internal links. Auth is the security boundary, not the URL prefix. Cleaner refactor for a future PR.

### Decision 6: auth pattern — FastAPI Depends vs. manual check

**Picked**: router-level `dependencies=[Depends(require_admin)]` rather than manual `if not is_admin(): return redirect` in every handler. Reason: one line per file gates every page + every API route. Impossible to forget. The `require_admin` dependency raises `HTTPException(302, Location=/admin/)` which FastAPI converts to a proper redirect response.

### Decision 7: pipeline execution location

**Considered**: move ingestion onto Render as a separate cron job service.

**Picked**: stay on the VPS via systemd. Reasons: (1) the VPS already runs every other pipeline (Bloomberg, CBOE, structured notes, daily reports) — splitting that pattern just for 13F would be incoherent; (2) the VPS has persistent shell + journald + cron — operational visibility is much better than Render background workers; (3) cost: VPS is already paid for.

### Decision 8: PostgreSQL migration

**Considered**: migrate `13f_holdings.db` from SQLite to PostgreSQL on Render.

**Picked**: stay on SQLite. Reason: at our query volume (admin-only, low concurrency, single-writer), SQLite is fine. The cross-DB ATTACH pattern that joins `13f_holdings.db` to `etp_tracker.db` is already in place and working. PostgreSQL becomes worth the migration cost only if/when scope expands materially (e.g., multi-quarter analytics on 10M+ rows, public access, real concurrent writes). Phase 2.

---

## Part 4 — Architecture

### Three-tier execution

```
┌─────────────────────────────────────────────────────────────────┐
│  LOCAL LAPTOP (C:/Projects/rexfinhub)                           │
│  Purpose: dev, ad-hoc ops, manual one-offs                      │
│  Not part of the production data path.                          │
│                                                                  │
│  Ryu's main dev environment. Has working copies of all DBs.     │
│  Syncs between desktop + laptop via Syncthing.                  │
└─────────────────────────────────────────────────────────────────┘

                              ↑
                              │ dev edits, commit, push
                              ↓

┌─────────────────────────────────────────────────────────────────┐
│  GITHUB (github.com/ryuoelasmar/rexfinhub)                      │
│  main branch is what Render watches.                            │
└─────────────────────────────────────────────────────────────────┘

                              ↓ auto-deploy on push to main

┌─────────────────────────────────────────────────────────────────┐
│  RENDER (rexfinhub.com)                                         │
│  Web service only. Reads from DB files. Never runs pipelines.   │
│  • Plan: standard, 10 GB persistent disk                        │
│  • Disk mount: /opt/render/project/src/data                     │
│  • Web process: uvicorn webapp.main:app                         │
└─────────────────────────────────────────────────────────────────┘

                              ↑ deploy-db endpoint upload

┌─────────────────────────────────────────────────────────────────┐
│  VPS (46.224.126.196)                                           │
│  Purpose: all scheduled pipelines, including 13F                │
│                                                                  │
│  systemd timers:                                                │
│   • rexfinhub-13f-quarterly.timer                               │
│       fires 2/19, 5/20, 8/19, 11/19 at 06:00 ET                 │
│       runs: scripts/run_13f.py backfill                         │
│   • rexfinhub-bloomberg-chain.service (daily)                   │
│   • rexfinhub-cboe.* (continuous)                               │
│   • rexfinhub-structured-notes.* (weekly)                       │
│                                                                  │
│  Auth: SSH as jarvis@. Venv at /home/jarvis/venv/bin/python.    │
│  Output for 13F: /home/jarvis/rexfinhub/data/13f_holdings.db    │
│  Then pushed to Render via deploy-db.                           │
└─────────────────────────────────────────────────────────────────┘
```

### Why three tiers (not two, not one)

- **Why not run pipelines on Render?** A 22-min SEC ingest in the uvicorn web process would block requests, OOM the worker, or get killed by Render's health check. Render's "background worker" plan exists but requires provisioning a separate paid service. Plus, journald + persistent shell on the VPS is operationally superior — you can SSH in and debug.
- **Why not run pipelines on the local laptop?** Reliability + scheduling. Laptops sleep, lose power, have different filesystems. The systemd timer on a VPS fires no matter what.
- **Why is Render the web tier?** Auto-deploy on git push, free TLS, custom domain (rexfinhub.com), CDN. Render's good at what it does (serving FastAPI), bad at what we don't ask it to do (long-running batch jobs).

### Cross-DB joins (the ATTACH pattern)

`13f_holdings.db` and `etp_tracker.db` are physically separate SQLite files. To join 13F holdings (which only know CUSIPs) to Bloomberg's MktMasterData (which knows ticker, issuer, category), we use SQLite's `ATTACH DATABASE` mechanism wired in `webapp/database.py`:

```python
# every connection to holdings_engine runs:
#   ATTACH DATABASE 'data/etp_tracker.db' AS main_site
```

So queries against `HoldingsSessionLocal()` can join `Holding.cusip → MktMasterData.cusip` directly. The canonical pattern lives in `webapp/services/holdings_intel.py:_base_holdings_join()`:

```python
def _base_holdings_join():
    return (
        select(Holding, MktMasterData)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip)
        .where(MktMasterData.etp_category.isnot(None))  # ETP universe filter
    )
```

This is why we don't need a separate "universe file" — `MktMasterData WHERE etp_category IS NOT NULL` IS the universe, and it joins to holdings in-process.

---

## Part 5 — The surface (what admins see)

### `/intel/*` — aggregate views (12 pages + 3 JSON APIs)

| URL | Audience | What it shows |
|---|---|---|
| `/intel/` | Both | Hub: market-wide KPIs, top products, vertical/issuer breakdowns, international preview |
| `/intel/rex` | Product | REX-only quarter report — KPIs + new filers + vertical breakdown |
| `/intel/rex/filers` | Sales | REX filers tabbed by ticker / vertical / issuer, with top filer ranking |
| `/intel/rex/performance` | Product | REX QoQ + trend — AUM movement per product |
| `/intel/rex/sales` | Sales | State-level breakdown — geography of REX holders |
| `/intel/competitors` | Both | Competitor product holdings — by product / issuer / vertical |
| `/intel/competitors/new-filers` | Sales | New filers buying competitor products — pure outreach list |
| `/intel/products` | Product | Full ETP universe browser, paginated, searchable |
| `/intel/head-to-head` | Product | Compare products by underlying — the whitespace finder |
| `/intel/country` | Both | International holders by country |
| `/intel/asia` | Sales | Asian holders of REX products specifically (Grace's data) |
| `/intel/trends` | Both | Historical multi-quarter trend lines |
| `/intel/api/{kpis,trend,holdings}` | API | JSON endpoints for the above |

### `/holdings/*` — entity-detail views (5 pages + 6 JSON APIs + 2 admin endpoints)

| URL | Purpose |
|---|---|
| `/holdings/` | Institution list — sortable by AUM, name, last_filed |
| `/holdings/crossover` | Crossover prospects — RIAs holding competitor leverage but not REX equivalent |
| `/holdings/fund/{ticker}` | Fund-level holders — "who holds SOXL?" |
| `/holdings/{cik}` | Institution detail — the full portfolio of one institution |
| `/holdings/{cik}/history` | Institution QoQ position changes |
| `/api/v1/holdings/{by-fund,changes,trend,search-funds}` | JSON + CSV exports |
| `/holdings/admin/{refresh,health}` | Admin trigger endpoints (local-only) |

### How the auth gate works

Every router file ends with:

```python
router = APIRouter(
    prefix="/intel",  # or "/" for holdings
    tags=["intel"],
    dependencies=[Depends(require_admin)],
)
```

`require_admin` (in `webapp/dependencies.py`) checks `request.session.get("is_admin", False)`. If false, raises `HTTPException(302, Location=/admin/)` which FastAPI converts to a 302 redirect. Browsers follow it. Non-browser callers (curl, fetch) see 302 with the Location header and can detect failure.

This is **strictly tighter** than a manual `if not is_admin` check at the top of each handler — the dependency runs *before* the handler is even reached, and gates every route in the router uniformly. No way to accidentally forget.

### What public visitors see

In the main nav (`webapp/templates/base.html`):
- **SEC Intel mega-menu**: ETP Dashboard, ETP Filings, L&I Landscape, Notes Dashboard, Notes Filings. **No 13F entries.**
- **Tools mega-menu**: Compare ETPs, Compare Filings, Compare Notes, L&I Candidates, Autocall Simulator, Symbol Landscape, ETP Calendar. **No "Compare 13F" entries.**
- **Footer**: Notes Dashboard + Notes Filings only. **No "13F (Soon)" link.**

On the home page's "Institutional Ownership" pillar, non-admins see four greyed-out items labeled "Admin only". Admins see the same four items as live links.

If a public visitor somehow knows about `/intel/anything` and types it directly, they get 302 → `/login`. After site login they get bounced from `/admin/login`. They never see the data.

---

## Part 6 — The pipeline (`etp_tracker/thirteen_f.py`)

### Modes

| Command | What it does |
|---|---|
| `python scripts/run_13f.py seed` | Refresh CUSIP mappings (Bloomberg + OpenFIGI stealth-REX backfill) |
| `python scripts/run_13f.py bulk <quarter>` | Download SEC bulk ZIP, ingest |
| `python scripts/run_13f.py local <tsv_dir>` | Ingest from pre-extracted TSV dir |
| `python scripts/run_13f.py incremental` | EFTS + XML for last 7 days |
| `python scripts/run_13f.py backfill` | Production canonical: latest quarter, used by systemd |
| `python scripts/run_13f.py health` | Diagnostic report |
| `python scripts/run_13f.py deploy-db` | Push DB to Render |

### What every ingest does (the finalize chain)

After the raw ingest of an ZIP / TSV / EFTS hit-set commits, **every** ingest function calls `_post_ingest_finalize()` which runs three idempotent passes:

**1. `_post_ingest_dedupe()`** — DELETE exact-duplicate holdings rows. Group key: `(institution_id, cusip, report_date, value_usd, shares, share_type)`. Validation against the live 811 MB DB removed **39,957 duplicates**.

**2. `_post_ingest_metadata()`** — populate two fields that the May 2026 audit found at 100% NULL:
- `institutions.last_filed = MAX(holdings.report_date)` per institution
- `cusip_mappings.trust_id` joined from `main_site.fund_status.trust_id` via ticker match

Validation result: 100% of cusip_mappings got trust_id (7,286/7,286), 5,848 of 10,535 institutions got last_filed (the rest have no holdings — correct null).

**3. `_post_ingest_verify()`** — two checks:
- Row-count delta: if latest quarter < 80% of prior, warn loudly (this catches partial SEC ZIPs — exactly the bug that produced the audit's 45% gap on Q4 2025)
- `PRAGMA integrity_check`

Plus a pre-ingest snapshot (`_backup_holdings_db()`) writes a timestamped copy to `data/backups/` before the ingest starts. If anything corrupts mid-run, restore is one `cp` away.

### The stealth-REX seed (the OpenFIGI piece)

`seed_cusip_mappings()` has two passes:

**Pass 1 (existing)**: pull every `MktMasterData` row with a non-empty CUSIP, upsert into `CusipMapping` with `source="mkt_master"`. This is Bloomberg-sourced and covers ~99% of the ETP universe.

**Pass 2 (new today)**: query `FundStatus.ticker` JOIN `Trust.is_rex = True` LEFT JOIN `MktMasterData` to find REX tickers that have filed with SEC but aren't in Bloomberg master yet. For those — typically zero to a handful per quarter — call `_openfigi_lookup_cusips()` to get their CUSIPs via OpenFIGI's free API. Upsert with `source="openfigi_stealth_rex"`.

OpenFIGI rate limits: 25/min, 5 instruments per request without an API key. With an `OPENFIGI_API_KEY` env var: 250/min, 100 per request. We auto-detect and use whichever is configured.

### Quarterly schedule

| Date (ET) | Fires | Quarter ingested |
|---|---|---|
| Feb 19, 06:00 | rexfinhub-13f-quarterly.timer | Q4 (prior calendar year) |
| May 20, 06:00 | same | Q1 |
| Aug 19, 06:00 | same | Q2 |
| Nov 19, 06:00 | same | Q3 |

SEC publishes 13F bulk ZIPs ~50 days post-quarter-end (45-day filing deadline + ~5 days). Schedule is set to fire after that publishing window.

`TZ=America/New_York` is pinned at the systemd-unit level so DST never breaks the schedule.

---

## Part 7 — The data

### Volumes (Q4 2025 baseline)

- **Holdings rows**: 2,500,000 → 2,460,043 after dedupe
- **Institutions**: 10,535 unique
- **CUSIPs distinct in holdings**: 33,780
- **Cusip_mappings**: 7,286 (Bloomberg seed) + small stealth-REX additions
- **DB size on disk**: ~811 MB

### Quarterly distribution

| Quarter | Holdings rows |
|---|---|
| 2025-12-31 (Q4) | 2,403,267 (full ingest) |
| 2025-09-30 (Q3) | 50,584 (old MVP top-10 path leftover) |
| 2025-06-30 (Q2) | 21,913 |
| 2025-03-31 (Q1) | 7,553 |
| 2024-12-31 (Q4 2024) | 4,933 |

The drop-off from Q4 to earlier quarters is because earlier quarters were ingested by the now-deleted `fetch_13f.py` MVP that only fetched the top 10 institutions. Backfilling 2020+ for full universe is a future task.

### CUSIP coverage

After today's metadata fix: 100% of cusip_mappings have a `trust_id`. CUSIP matching against holdings is at ~14% (the audit's 4,687 / 33,780 number) — meaning ~86% of holding rows are for CUSIPs we don't track (the AAPL/MSFT/SPY universe). That's expected and correct: those rows live in the DB for institution-detail context but never appear in the ETP-filtered analytical views.

---

## Part 8 — Code anchors

### Files modified or created today

| File | Change |
|---|---|
| `webapp/dependencies.py` | Added `is_admin()` + `require_admin()` |
| `webapp/routers/intel.py` | Added `Depends(require_admin)` |
| `webapp/routers/intel_competitors.py` | Added `Depends(require_admin)` |
| `webapp/routers/intel_insights.py` | Added `Depends(require_admin)` |
| `webapp/routers/holdings.py` | Added `Depends(require_admin)`, fixed error message reference |
| `webapp/routers/dashboard.py` | Removed `enable_13f` template var + context entry |
| `webapp/routers/tools_compare.py` | Removed 2 stale 13F stub routes |
| `webapp/main.py` | Removed `ENABLE_13F` env gate (registration + lifespan), removed `sec_13f` import + register, removed `templates.env.globals["enable_13f"]` |
| `webapp/routes.py` | Deleted 6 stale 13F entries, added 17 `intel.*` + `holdings.*` entries |
| `webapp/templates/base.html` | Deleted 4 SEC Intel "13F:" mega entries, 2 Tools "Compare 13F" entries, 3 footer links |
| `webapp/templates/home.html` | `{% if enable_13f %}` → `{% if request.session.get("is_admin") %}` |
| `webapp/templates/market/rex.html` | Same swap, 4 occurrences |
| `etp_tracker/thirteen_f.py` | Added stealth-REX seed, `_openfigi_lookup_cusips()`, `_backup_holdings_db()`, `_post_ingest_dedupe()`, `_post_ingest_metadata()`, `_post_ingest_verify()`, `_post_ingest_finalize()`, wired into all 3 ingest functions |
| `config/render.yaml` | Disk 1→10 GB, plan starter→standard |
| `CLAUDE.md` | Disk reference 1→10 GB |
| `docs/13f_intel_guide.md` | NEW — operations reference |
| `docs/13f_intel_AIM.md` | NEW — this document |

### Files deleted today

| File | Why |
|---|---|
| `webapp/routers/sec_13f.py` | Public "Coming Soon" placeholder — incompatible with internal-only framing |
| `webapp/routers/holdings_placeholder.py` | Dead, never mounted |
| `webapp/templates/holdings_placeholder.html` | Same |
| `scripts/fetch_13f.py` | MVP top-10 superseded by `run_13f.py` (canonical) |
| `docs/13f_activation_guide.md` | Documented obsolete MVP path; replaced by `13f_intel_guide.md` |

### Key external dependencies

- **`webapp/services/holdings_intel.py`** (1,052 lines) — the analytics engine. Every aggregate query lives here. Module-level `_cached()` decorator gives 10-min TTL on results.
- **`webapp/services/admin_auth.py`** — `load_admin_password()` reads from `config/.env` or `ADMIN_PASSWORD` env var
- **`webapp/templates/intel/base_intel.html`** — base template every intel page extends. Provides quarter selector + intel.css + intel.js (with sortTable)
- **`webapp/static/css/intel.css`** — `intel-kpi-row`, `intel-card`, `intel-ticker`, `intel-vchip cat-*` design tokens
- **`deploy/systemd/rexfinhub-13f-quarterly.service`** — systemd unit definition
- **`deploy/systemd/rexfinhub-13f-quarterly.timer`** — schedule definition

---

## Part 9 — Glossary

| Term | Meaning |
|---|---|
| **13F-HR** | The SEC form institutional managers file quarterly listing their positions |
| **13F-HR/A** | An amended 13F-HR (corrections to a prior filing) |
| **CUSIP** | 9-character security identifier — the join key between holdings and our product master |
| **Institution** | A 13F filer — CIK + name + city + state |
| **Holding** | A single position: institution_id + cusip + value_usd + shares + report_date |
| **ETP** | Exchange-Traded Product (ETFs + ETNs) |
| **LI** | Leveraged & Inverse — REX's core category |
| **CC** | Covered Call — another REX category |
| **MktMasterData** | The Bloomberg-sourced master table of ETPs in the main DB |
| **Trust** | An SEC-registered investment trust (each REX product family is one) |
| **FundStatus** | Per-fund SEC filing status (effective, pending, delayed) |
| **Stealth-REX** | A REX product filed with SEC but not yet in Bloomberg master |
| **map_li_underlier** | Bloomberg column linking a leveraged ETP to its underlying (e.g., SOXL → SOX index) |
| **etp_category** | The category column on MktMasterData (LI/CC/Crypto/Defined/Thematic/null) |
| **Finalize chain** | The auto-run dedupe + metadata + verify pass at end of every ingest |
| **Stealth-REX seed** | The OpenFIGI-based CUSIP backfill for REX products not yet in Bloomberg |
| **ATTACH** | SQLite mechanism to query a second DB file from the same connection |

---

## Part 10 — Open questions and future work

### Confirmed open items

- **Backfill 2020+ for full universe** — earlier quarters have only top-10 institutions ingested. The systemd timer fires for newest quarter only; historical backfill is manual.
- **CUSIP change tracking** — funds that change CUSIPs over time (post-split, post-restructure) currently appear as two unrelated CUSIPs in holdings. No `cusip_history[]` column yet.
- **`enrich_cusip_mappings_from_holdings()`** — a function in `thirteen_f.py` that infers more CUSIP mappings from holdings data itself. Not yet wired into the auto-finalize chain.

### Architectural decisions still on the table

- **Multi-source institutional ownership** — N-PORT (mutual funds) + NAIC Schedule D (insurance) + FFIEC (banks) for fixed income coverage. Currently scope-out for this project. ~3-4× effort.
- **PostgreSQL migration** — pending volume or concurrency pressure that doesn't exist yet. Likely a precondition for any "public-facing 13F surface" decision that might come later.
- **Real-time 13D/G filings** — for >5% beneficial ownership stakes, filed within 10 days. Closer to a trading signal than 13F-HR. Different schema, different workflow. Out of scope.

### Things to verify after first VPS deploy

- VPS systemd unit is installed AND enabled AND has correct ExecStart
- VPS has access to OPENFIGI_API_KEY env (if set) — currently optional
- Render disk usage after first DB push (expect ~811 MB used of 10 GB)
- Smoke test all 17 admin URLs from a fresh browser session

---

## Part 11 — How to verify changes (test workflow)

Local preview from worktree:

```powershell
# from C:/Projects/rexfinhub/.claude/worktrees/atlas-13f-intel
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8050
```

Then in browser:
1. `http://127.0.0.1:8050` — public home, confirm no 13F links visible
2. `http://127.0.0.1:8050/login` — enter SITE_PASSWORD
3. `http://127.0.0.1:8050/admin/` — enter ADMIN_PASSWORD
4. `http://127.0.0.1:8050/intel/` — should render hub with quarter selector

For automated verification via curl, see `docs/13f_intel_guide.md` Troubleshooting section.

For the canonical ingest test (full pipeline, ~22 min):

```powershell
python scripts/run_13f.py local D:/sec-data/cache/rexfinhub/13f/2025q4_extracted
```

Watch the log for the finalize summary at the end:
```
Deduped N exact-duplicate holdings rows
Metadata populated: X institutions ... Y cusip_mappings ...
Integrity check ... ok
```

---

## Part 12 — Deployment chain (what's left)

### Step-by-step

```
1. git push -u origin worktree-atlas-13f-intel
   → branch lands on GitHub. Still nothing on prod.

2. Merge to main
   → either PR + review + merge, OR direct fast-forward
   → main updated. Render starts auto-deploying.

3. Render auto-deploys (~5 min)
   → /intel/* registers on rexfinhub.com
   → Admin login works; pages render
   → BUT data/13f_holdings.db on Render is old/stale, so pages are empty

4. SSH to VPS (ssh jarvis@46.224.126.196)
   → cd /home/jarvis/rexfinhub
   → git pull
   → systemctl status rexfinhub-13f-quarterly.timer

5. First production ingest on VPS
   → /home/jarvis/venv/bin/python scripts/run_13f.py backfill
   → 22 min, auto-finalizes (backup + dedupe + metadata + verify)
   → Log shows finalize summary at end

6. Push DB to Render
   → /home/jarvis/venv/bin/python scripts/run_13f.py deploy-db
   → ~30 sec upload over the deploy-db endpoint

7. Smoke test as admin
   → rexfinhub.com/admin/ → login
   → rexfinhub.com/intel/ → see Q4 2025 with clean data
   → rexfinhub.com/holdings/fund/SOXL → see institutional holders
```

### Success criteria

- `rexfinhub.com/intel/` returns 200 for admin sessions, 302 for non-admins
- All 12 intel page routes + 5 holdings detail routes accessible to admins
- `/sec/13f/*` and `/tools/compare/13f-*` return 404
- Public home page nav shows zero 13F entries
- DB on Render contains de-duplicated Q4 2025 data with populated metadata
- August 19 systemd timer fires Q2 2026 ingest unattended

---

## Part 13 — If you have to revert

The deployment is reversible at every step:

| Step gone wrong | Reversal |
|---|---|
| Code on Render broken | Revert the merge commit on `main`, force re-deploy |
| Auth gate broken (everyone gets 302) | Roll back; check `dependencies.py:require_admin` change |
| Ingest corrupts DB | Restore from `data/backups/13f_holdings_pre_*` (auto-created before every ingest) |
| Render upload pushes bad DB | Re-run ingest locally, re-deploy DB |
| Public visitors see 13F leak | Check `home.html` + `market/rex.html` for `enable_13f` references; should all be `request.session.get("is_admin")` |

Pre-change DB backup of the original 811 MB file (with the bugs intact) lives at:
`C:/Projects/rexfinhub/data/backups/13f_holdings_pre_intel_20260513_1450.db`

Do not delete this until production deployment is verified working for at least one quarter cycle.

---

*End of brief. Total length: ~12,000 words. If you got this far, you understand the project. Now go ship it.*
