# Ownership Pillar — Developer Guide

This document is the complete reference for developing the Ownership (13F Institutional Holdings) pillar of REX FinHub. If you are using Claude Code, this file gives your AI assistant full context on scope, data contracts, and integration points.

## Your Scope

**You own everything under `/ownership/...` on the website.** You have full creative freedom to add, remove, or redesign any page within this URL prefix.

### Files you own (can create, edit, delete freely)

```
webapp/routers/holdings.py              # Page routes + API routes for ownership
webapp/routers/holdings_placeholder.py  # Render stub (when 13F data unavailable)
webapp/routers/intel.py                 # Intelligence analysis pages
webapp/routers/intel_competitors.py     # Competitor holdings analysis
webapp/routers/intel_insights.py        # Country/region/trend insights
webapp/templates/holdings*.html         # All holdings templates
webapp/templates/crossover.html         # Crossover analysis template
webapp/templates/institution*.html      # Institution detail/history templates
webapp/templates/intel*.html            # Intelligence templates (if they exist)
webapp/static/js/holdings*.js           # Any ownership-specific JS
webapp/static/js/intel*.js              # Any intel-specific JS
etp_tracker/thirteen_f.py              # 13F data pipeline
scripts/run_13f.py                     # 13F CLI runner
```

### Files you must NOT edit

```
webapp/routers/filings.py              # Filings pillar (Ryu)
webapp/routers/screener.py             # Screener/Market pillar (Ryu)
webapp/routers/dashboard.py            # Home page + dashboard (Ryu)
webapp/routers/admin.py                # Admin panel (Ryu)
webapp/routers/api.py                  # Core API (Ryu)
webapp/routers/trusts.py               # Trust detail pages (Ryu)
webapp/routers/funds.py                # Fund detail pages (Ryu)
webapp/services/*                      # All service modules (Ryu)
webapp/templates/base.html             # Site-wide layout/nav (Ryu)
webapp/templates/home.html             # Home page (Ryu)
config/rules/*.csv                     # Classification rules (Ryu - read only)
config/.env                            # Secrets (never committed)
scripts/run_daily.py                   # Daily sync pipeline (Ryu)
etp_tracker/*.py (except thirteen_f.py) # Filing pipeline (Ryu)
screener/*                             # Screener engine (Ryu)
market/*                               # Market data engine (Ryu)
```

### Files that require PR review (shared touchpoints)

These are integration points. You may need to make small additions, but changes must be reviewed:

```
webapp/models.py                       # Add new models or fields — additive only
webapp/database.py                     # DB connection setup — rarely needs changes
webapp/main.py                         # Router registration — one-time change
webapp/dependencies.py                 # DB session dependencies
requirements.txt                       # New dependencies — additive only
```

---

## URL Structure

All ownership pages must live under `/ownership/`. **The current codebase has routes at `/holdings/` and `/intel/` — your first task is to migrate them to `/ownership/`.** The existing route handlers in `holdings.py` use hardcoded paths like `@router.get("/holdings/")`. The intel routers use `prefix="/intel"`. You need to change these to `/ownership/...`.

Add 301 redirects from the old URLs to the new ones (same pattern used in `screener.py` for the filing URL migration).

You decide the sub-route structure. Example:

```
/ownership/                             Landing / overview
/ownership/institutions                 Institution list
/ownership/institutions/{cik}           Institution detail
/ownership/institutions/{cik}/history   QoQ changes
/ownership/funds/{ticker}               Fund-level holders
/ownership/crossover                    Sales prospects
/ownership/intel/...                    Intelligence views
```

### Pages you can link TO (other pillars)

| URL | What it shows | When to link |
|-----|--------------|-------------|
| `/funds/{series_id}` | Fund detail (filing history, status) | When showing a fund ticker in your tables |
| `/trusts/{slug}` | Trust detail (fund roster) | When showing a trust name |
| `/filings/dashboard` | Filing activity dashboard | General "see filings" link |
| `/filings/explorer?q={query}` | Search funds/filings | When user might want filing details |
| `/filings/landscape` | L&I filing competitive matrix | When showing leveraged product context |
| `/screener/stock/{ticker}` | Per-stock competitive deep dive | When showing single-stock analysis |
| `/` | Home page | Breadcrumbs |

### Pages that already link TO you (integration stubs)

These pages already have placeholder variables waiting for your data:

**`/funds/{series_id}` (fund detail page):**
Template receives: `holders_13f`, `holders_count`, `holders_total_value`, `holders_ticker`, `holders_quarter` — currently all empty/zero. When `ENABLE_13F` is on, these should be populated. The route handler is in `webapp/routers/funds.py` — Ryu will wire these when your data is ready.

**`/trusts/{slug}` (trust detail page):**
Template receives: `inst_13f_count`, `inst_13f_value`, `inst_13f_quarter` — currently zeroed. Same approach.

**Home page (`/`):**
The Ownership pillar section is wrapped in `{% if enable_13f %}`. When on, it links to your pages. The KPI API (`/api/v1/home-kpis`) returns `institutions_count` and `total_13f_value` — reads from your DB when available.

---

## The ENABLE_13F Gate

All ownership code is behind an environment variable: `ENABLE_13F=1`.

**When OFF (Render production today):**
- Your routers are never imported or mounted in `main.py`
- Those URLs simply don't exist (404) — `holdings_placeholder.py` exists in the repo but is NOT mounted currently (dead code)
- `init_holdings_db()` is skipped, BUT the `holdings_engine` SQLAlchemy object IS created at import time in `database.py` regardless — it just isn't used. If `etp_tracker.db` is missing, the ATTACH statement in the engine's connect handler will fail silently.
- Home page shows greyed-out ownership links
- Fund/trust detail pages pass zero values for 13F stubs

**When ON (local development):**
- All four routers mount normally
- `init_holdings_db()` creates tables in `data/13f_holdings.db` if they don't exist
- Home page shows live ownership links
- All ownership pages are accessible

**To develop locally:** Add both of these to your `config/.env`:
```
ENABLE_13F=1
SITE_PASSWORD=dev123
```
`SITE_PASSWORD` is required to log into the site. Without it, the default is `"123"`. You also need both `data/etp_tracker.db` and `data/13f_holdings.db` on disk.

---

## Database Architecture

### Two separate databases

| Database | File | Contents | Who owns |
|----------|------|----------|----------|
| Main site DB | `data/etp_tracker.db` | Trusts, funds, filings, classification, market data | Ryu |
| Holdings DB | `data/13f_holdings.db` | Institutions, holdings, CUSIP mappings | You |

These are separate SQLite files with separate SQLAlchemy engines.

**You do NOT need a copy of `etp_tracker.db` to develop.** If the file is missing, SQLAlchemy creates an empty one on startup. Your pages will work — they just won't have cross-referenced fund/classification data until you connect to the live site's API or get a copy of the DB.

### Accessing Ryu's data (classification, funds, trusts)

You have three options — choose whichever fits your workflow:

**Option 1 — API calls (recommended):**
Call the existing REST API endpoints to get fund/trust/classification data:
- `GET /api/v1/trusts` — all monitored trusts
- `GET /api/v1/funds` — fund list with status, ticker, series_id
- `GET /api/v1/filings/recent` — recent filings
These work on the live site and locally. Requires `X-API-Key` header.

**Option 2 — CSV files (already in the repo):**
Classification rules are in `config/rules/` — fund_mapping.csv, rex_funds.csv, rex_suite_mapping.csv, etc. Read these directly with pandas. No DB or API needed.

**Option 3 — Dual DB sessions (for production):**
In production, your code and Ryu's code run in the same FastAPI app. You can import both `get_holdings_db()` (your data) and `get_db()` (his data) as separate dependencies and query both:
```python
from webapp.dependencies import get_holdings_db, get_db

@router.get("/ownership/funds/{ticker}")
def fund_holders(ticker: str, hdb=Depends(get_holdings_db), main_db=Depends(get_db)):
    # Query holdings from your DB
    holders = hdb.execute(select(Holding).where(...)).scalars().all()
    # Query fund info from main DB
    fund = main_db.execute(select(FundStatus).where(FundStatus.ticker == ticker)).scalar()
    ...
```

### Legacy ATTACH mechanism
The current `database.py` ATTACHes etp_tracker.db to the holdings engine at connection time. This is a SQLite-specific convenience for cross-DB JOINs. It works but creates a file dependency. You may keep it, replace it with one of the approaches above, or remove it entirely.

### Your tables (in 13f_holdings.db)

**`institutions`**

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | Auto |
| cik | String(20) | SEC CIK, unique |
| name | String(300) | Institution name |
| city | String(100) | Nullable |
| state_or_country | String(10) | Nullable |
| manager_type | String(50) | Not populated currently |
| aum_total | Float | Not populated currently |
| filing_count | Integer | Incremented on each ingestion |
| last_filed | Date | Nullable |
| created_at | DateTime | |
| updated_at | DateTime | |

**`holdings`** (3.47M rows for Q4 2025 data)

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | Auto |
| institution_id | Integer FK | -> institutions.id |
| report_date | Date | Period of report (quarter end) |
| filing_accession | String(30) | SEC accession number (dedup key) |
| issuer_name | String(300) | Security issuer name |
| cusip | String(12) | CUSIP identifier |
| value_usd | Float | **Always full dollars** (pre-2023 data multiplied by 1000 at ingestion) |
| shares | Float | Share count |
| share_type | String(10) | SH (shares) or PRN (principal) |
| investment_discretion | String(10) | SOLE / DFND / OTR |
| voting_sole | Integer | Voting authority counts |
| voting_shared | Integer | |
| voting_none | Integer | |
| is_tracked | Boolean | True when CUSIP matches fund universe |
| created_at | DateTime | |

**Key index:** `is_tracked` + `report_date`. All web queries filter `is_tracked=True` first — this keeps queries fast on the large table.

**`cusip_mappings`** (bridge table)

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | Auto |
| cusip | String(12) | Unique |
| ticker | String(20) | Bloomberg ticker (e.g., "SOXL US") |
| fund_name | String(300) | |
| trust_id | Integer | References main DB trust (no FK constraint cross-DB) |
| source | String(30) | `mkt_master` / `manual` / `holdings_enrichment` |
| created_at | DateTime | |

### Main DB tables you can READ (via ATTACH)

**`fund_status`** — Every ETF/ETN fund tracked from SEC filings

| Key columns | What they mean |
|-------------|---------------|
| `ticker` | Fund ticker (may be null for pending funds) |
| `fund_name` | Full SEC fund name |
| `series_id` | SEC Series ID — use this for `/funds/{series_id}` links |
| `trust_id` | FK to trusts table |
| `status` | EFFECTIVE / PENDING / DELAYED |
| `effective_date` | When the fund became effective |
| `latest_form` | Most recent SEC form type |

**`trusts`** — Trust/issuer entities

| Key columns | What they mean |
|-------------|---------------|
| `cik` | SEC CIK |
| `name` | Full trust name |
| `slug` | URL-safe name — use for `/trusts/{slug}` links |
| `is_rex` | Boolean — is this a REX/T-REX trust |
| `is_active` | Boolean — actively monitored |

**`mkt_master_data`** — Bloomberg-enriched market data (one row per ticker+category)

| Key columns | What they mean |
|-------------|---------------|
| `ticker` | Bloomberg ticker (e.g., "SOXL US") |
| `fund_name` | Full fund name |
| `aum` | Assets under management (Float) |
| `etp_category` | Classification: LI, CC, Crypto, Defined, Thematic |
| `is_rex` | Boolean |
| `rex_suite` | REX branded suite (T-REX, MicroSectors, etc.) |
| `market_status` | ACTV, DLST, LIQU, etc. |
| `issuer_nickname` | Short display name for the issuer |

See "Classification System" section below for full details.

---

## Classification System (READ ONLY)

Ryu maintains a 5-category classification for all tracked ETPs. Your pages should use these categories for grouping, filtering, and display. **Never modify these files — changes propagate from Ryu's pipeline.**

### The 5 categories

| Code | Full name | What it is |
|------|-----------|-----------|
| `LI` | Leveraged & Inverse | Funds with daily leverage (2x, 3x, 4x, 5x) — long or short |
| `CC` | Covered Call / Income | Funds writing options for income (JEPI, FEPI, etc.) |
| `Crypto` | Crypto | Spot BTC/ETH, crypto equity, crypto derivatives |
| `Defined` | Defined Outcome | Buffer, floor, barrier — structured payoff ETFs |
| `Thematic` | Thematic | AI, clean energy, cybersecurity, etc. |

### Key CSV files (in `config/rules/`)

**`fund_mapping.csv`** — Master category assignment
- Columns: `ticker`, `etp_category`, `is_primary`, `source`
- 2,259 entries mapping every tracked ticker to one of the 5 categories
- A ticker CAN appear in multiple categories (composite key: ticker + category)

**`rex_funds.csv`** — Which tickers are REX products
- Single column: `ticker`
- 99 entries (REX, T-REX, MicroSectors products)
- Drives `is_rex=True` on MktMasterData

**`rex_suite_mapping.csv`** — Which REX suite each product belongs to
- Columns: `ticker`, `rex_suite`
- Suite values: `T-REX`, `MicroSectors`, `REX`, `Osprey`, etc.

**`issuer_mapping.csv`** — Display names for issuers
- Columns: `etp_category`, `issuer`, `issuer_nickname`
- Maps Bloomberg trust names to clean display names

**`competitor_groups.csv`** — REX vs competitor pairings
- Columns: `group_name`, `rex_ticker`, `peer_ticker`
- Useful for the crossover analysis page

**Category attribute files** (`attributes_LI.csv`, `attributes_CC.csv`, etc.):
- Per-category enrichment fields (leverage amount, underlier, direction, etc.)
- See the MktMasterData model for all `map_*` fields

### How to use classification in your pages

```python
# Read from the attached main DB via the holdings session
from webapp.models import MktMasterData
from sqlalchemy import select

# Example: get all REX products with their categories
rex_products = db.execute(
    select(MktMasterData).where(
        MktMasterData.is_rex == True,
        MktMasterData.market_status == 'ACTV'
    )
).scalars().all()

# Group by category
by_category = {}
for p in rex_products:
    by_category.setdefault(p.etp_category, []).append(p)
```

**Important:** `MktMasterData` lives in the main DB (`etp_tracker.db`), NOT the holdings DB. This works because `database.py` ATTACHes the main DB as `main_site` when the holdings engine connects. The table is accessible as `main_site.mkt_master_data` in raw SQL. Through SQLAlchemy ORM, the ATTACH makes it transparent — just use the model class normally via the holdings session. If you get "no such table" errors, ensure `data/etp_tracker.db` exists at the project root (the ATTACH path is resolved from `database.py:DB_PATH`).

---

## 13F Data Pipeline

### What it does

Downloads quarterly 13F-HR institutional holdings filings from SEC EDGAR, parses them, matches CUSIPs to our fund universe, and stores everything in `13f_holdings.db`.

### Data source

SEC publishes bulk 13F data quarterly as TSV files:
- `SUBMISSION.tsv` — Filing metadata (accession, CIK, dates)
- `COVERPAGE.tsv` — Institution info (name, city, state)
- `INFOTABLE.tsv` — Individual positions (CUSIP, value, shares, voting)

These are large files (hundreds of MB). Downloaded from SEC EDGAR bulk data endpoints.

### Existing pipeline (scripts/run_13f.py)

The current pipeline has these commands:
- `bulk <quarter>` — Full quarterly ingest from SEC bulk TSV files
- `local <path>` — Ingest from pre-downloaded TSV files
- `seed` — Initialize CUSIP mappings from market data
- `backfill` — Repair `is_tracked` flags
- `health` — Database health report
- `deploy-db <path>` — Export lean DB for Render (strips untracked holdings)

### Your responsibilities

You own this pipeline. You can rebuild it, extend it, or modify it as needed. The constraints are:

1. **Output schema must match** — The web layer (holdings.py, intel routers) reads from the three tables above (institutions, holdings, cusip_mappings). If you change the schema, update the web layer too.

2. **The script must be runnable by Ryu** — When Q1 2026 data is released, either of you should be able to run the ingestion. Document your script's usage clearly (CLI help text or README).

3. **`is_tracked` must be maintained** — This boolean flag on `holdings` is what makes web queries fast. A holding is "tracked" when its CUSIP exists in `cusip_mappings`. After any ingestion, run a backfill pass to keep this in sync.

4. **`value_usd` must be in full dollars** — SEC changed from thousands to full dollars in Jan 2023. Pre-2023 data must be multiplied by 1000 at ingestion. The existing pipeline handles this.

5. **SEC bulk URL format changes** — SEC changed their URL naming in 2024 (rolling date windows instead of calendar quarters). The `_FILING_WINDOWS_2024` dict in `thirteen_f.py` maps quarter tuples to SEC slugs. This needs updating for new quarters as SEC publishes them.

6. **Rate limiting** — SEC allows 10 requests/second. The entire rexfinhub project shares this limit. Use `User-Agent: REX-ETP-Tracker/2.0` header. Add 0.35s pause between requests.

### If you use PostgreSQL locally

You can develop with PostgreSQL as your local database. The deploy contract is:

**For Render deployment:** Export a SQLite file matching the schema above. The web layer reads SQLite on Render.

**For local development:** Either:
- Use SQLite directly (matches prod)
- Use PostgreSQL and ensure the ORM models work with both (SQLAlchemy supports this)

---

## Deploy Workflow

### How data gets to the live site

```
Local machine                              Render (production)
────────────                               ────────────────────
13f_holdings.db (811MB raw)
    │
    ▼
scripts/run_13f.py deploy-db
    │ (strips untracked → ~50-100MB)
    ▼
ownership_deploy.db                 ──────► POST /api/v1/db/upload-ownership
    │ (gzip compressed ~10-20MB)            (you may need to create this endpoint)
    ▼                                       ▼
                                     data/13f_holdings.db on Render
                                     Set ENABLE_13F=1 in Render env vars
```

### Current Render constraints

| Resource | Limit |
|----------|-------|
| RAM | 512 MB (upgrade pending approval) |
| Disk | 1 GB persistent |
| Current usage | ~600 MB (main DB + notes DB + screener cache) |

The ownership deploy DB must be small enough to fit alongside the existing data. The `deploy-db` command strips untracked holdings (keeping only positions in our fund universe), which should reduce it dramatically.

### Existing upload endpoints

| Endpoint | What it uploads |
|----------|----------------|
| `POST /api/v1/db/upload` | Main site DB (etp_tracker.db) |
| `POST /api/v1/db/upload-notes` | Structured notes DB |

You may need a new endpoint: `POST /api/v1/db/upload-ownership` — or reuse the existing one if the filename/path logic can be parameterized. Coordinate with Ryu on this.

---

## Ryu's Pipeline (reference only — do NOT run)

Ryu runs `scripts/run_daily.py` which does:
1. SEC filing scrape (discovers new ETF filings)
2. Structured notes scrape (structured products from SEC)
3. Sync filing CSVs to SQLite
4. Archive cache (C: drive → D: drive backup)
5. Bloomberg market data sync + screener cache build
6. Classify new/unmapped funds (updates fund_mapping.csv, attributes CSVs)
7. Compact DB
8. Upload screener cache to Render
9. Upload main DB to Render

This runs daily. It updates the classification CSVs you read from. After Ryu runs his pipeline, you should `git pull` to get the latest classification data.

---

## Git Workflow

### Branch strategy

```
main                    ← Ryu's daily work, auto-deploys to Render
  └── ownership-pillar  ← Your long-running branch
```

1. Work on `ownership-pillar` branch
2. Commit and push to your branch regularly
3. When a feature is ready, open a Pull Request to `main`
4. Ryu reviews and merges
5. To get Ryu's latest changes: `git fetch origin && git merge origin/main`

### Staying current with classification updates

Ryu's pipeline updates `config/rules/*.csv` files. To get these:

```bash
git fetch origin
git merge origin/main
```

Do this regularly (at least weekly) to keep your category data current.

### Viewing each other's work

**Ryu views your work** via git worktree:
```bash
git worktree add ../rexfinhub-ownership origin/ownership-pillar
cd ../rexfinhub-ownership
python -m uvicorn webapp.main:app --port 8001
```

**You view Ryu's work:**
```bash
git fetch origin
git diff origin/main -- webapp/templates/ config/rules/
```

---

## Local Development Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd rexfinhub

# 2. Switch to your branch
git checkout ownership-pillar

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your local config (copy from .env.example and fill in)
cp config/.env.example config/.env
# Then edit config/.env and set at minimum:
#   ENABLE_13F=1
#   SITE_PASSWORD=dev123       (any password — needed to log into the site)
#   ADMIN_PASSWORD=dev123      (any password — needed for admin panel)

# 5. Create data directory (DBs are auto-created empty on first run)
mkdir -p data
# Optional: if Ryu gives you 13f_holdings.db with existing data, place it here.
# etp_tracker.db is NOT required — it auto-creates empty on startup.
# Use the API or CSV files for classification data (see "Accessing Ryu's data" above).

# 6. Run the server
python -m uvicorn webapp.main:app --reload --port 8000

# 7. Open http://localhost:8000
#    Log in with whatever SITE_PASSWORD you set above.
#    Current ownership routes are at /holdings/ and /intel/ until you migrate them to /ownership/.
```

---

## Templates and Styling

All templates extend `base.html` which provides:
- Dark/light theme toggle
- Responsive navigation (mega-menu)
- Footer
- Shared CSS (`webapp/static/css/style.css`)

### Design system (follow existing patterns)

- **KPI cards**: Use the `.kpi-card` class with `.kpi-value` and `.kpi-label`
- **Tables**: Use `.data-table` class. Headers use dark background.
- **Charts**: Chart.js v4 loaded from CDN. Support both dark and light themes via `rex-theme-change` event.
- **Colors**: Primary `#2563EB`, success `#16A34A`, danger `#DC2626`, warning `#F59E0B`
- **Fonts**: System font stack (no web fonts)

### Breadcrumbs

All pages should have breadcrumbs:
```html
Home / Ownership / [Page Name]
```
Where "Ownership" links to `/ownership/`.

### Sub-navigation

Create a shared partial (e.g., `_ownership_subnav.html`) with links to your main pages. Include it on every ownership page for consistent navigation.

---

## Existing Code Summary

The current codebase has working (but potentially incomplete) implementations:

### holdings.py (1,078 lines)
- **5 page routes**: Institution list, crossover analysis, fund holders, institution history, institution detail
- **6 API routes**: Fund holders JSON, institution changes, institution trend, fund search, CSV exports
- **4 helpers**: `_fmt_value()`, `_pct_change()`, `_build_holders()`, `_build_position_changes()`

### intel.py, intel_competitors.py, intel_insights.py
- REX quarter reports, sales intelligence, competitor analysis, head-to-head, country/region views
- All gated behind ENABLE_13F

### Key helper: `_fmt_value(val)`
Formats USD values: `$1.2T`, `$450.3B`, `$23.5M`, `$1.2K`. Used everywhere. Keep or replace as needed.

### Route ordering warning
FastAPI matches routes in registration order. `/ownership/crossover` and `/ownership/funds/{ticker}` must be registered BEFORE `/ownership/{cik}` (catch-all). Reordering breaks routing silently.

---

## Quick Reference

| What | Where |
|------|-------|
| Your database | `data/13f_holdings.db` |
| Main site database (read-only) | `data/etp_tracker.db` |
| Classification rules | `config/rules/*.csv` |
| Your routers | `webapp/routers/holdings.py`, `intel*.py` |
| Your templates | `webapp/templates/holdings*.html`, `crossover.html`, `institution*.html` |
| Pipeline script | `scripts/run_13f.py` |
| Pipeline code | `etp_tracker/thirteen_f.py` |
| Models | `webapp/models.py` (Institution, Holding, CusipMapping) |
| DB setup | `webapp/database.py` (holdings_engine, HoldingsSessionLocal) |
| Feature gate | `ENABLE_13F=1` in env |
| Render upload | `POST /api/v1/db/upload-ownership` (to be created) |
| Site URL (prod) | https://rexfinhub.com |
| Local URL | http://localhost:8000 |
