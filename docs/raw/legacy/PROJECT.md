# REX ETP Filing Tracker

A filing intelligence platform built for REX Financial. Monitors SEC EDGAR filings across 122 ETF trusts, tracks ~7,000 ETF fund statuses (effective, pending, delayed), and provides an ETF launch screener that scores stock underliers for leveraged product opportunities.

**Live**: https://rex-etp-tracker.onrender.com

---

## Goal

REX files leveraged ETF products (2x Long, 2x Short) under multiple trust umbrellas. The SEC filing process involves 485APOS (initial filing), 485BPOS (effective/trading), 485BXT (extension), and 497/497K (supplements). This system:

1. **Tracks every filing** across 122 trusts so REX knows the status of every fund in real time
2. **Detects new filings** from competitors (Direxion, ProShares, GraniteShares, etc.)
3. **Scores stock underliers** to identify the best candidates for new leveraged ETF launches
4. **Evaluates specific candidates** with a 4-pillar analysis when the team proposes new names

---

## Architecture

### SEC Filing Pipeline (runs locally)

A 5-step CSV pipeline that fetches and processes SEC EDGAR data:

```
Step 2: Fetch submissions from SEC EDGAR (cached on disk, 6hr expiry)
Step 3: Extract fund names from filings (strategy-routed: header_only / full / full+ixbrl)
Step 4: Roll up fund status (EFFECTIVE / PENDING / DELAYED / NOT FILED)
Step 5: Build name history (track fund renames over time)
```

Each trust gets its own output folder under `outputs/` with CSV files per step. After the pipeline runs, data syncs to the SQLite database and uploads to Render.

- **Incremental by default**: Only processes new filings via `_manifest.json` tracking (seconds if nothing new)
- **Full reprocess**: ~2-3 hours for all 122 trusts
- **iXBRL enrichment**: 485BPOS (100% iXBRL) gets structured effective dates from OEF taxonomy

**Scheduled**: Windows Task Scheduler runs `run_daily.py` at 8:00 AM daily.

### Web Application (FastAPI)

A full-featured web app with 17 HTML templates across these sections:

| Section | Pages | Purpose |
|---------|-------|---------|
| Dashboard | `/` | 122 trust cards, fund counts, recent filings with filters (days/form/trust) |
| Funds | `/funds/` | Search ~7,000 ETF funds (mutual fund classes excluded) |
| Filings | `/filings/` | Search filings by trust, form type, accession |
| Trust Detail | `/trusts/<slug>` | Deep dive into a single trust's filings and funds |
| Screener | `/screener/` | Top 50 ETF launch opportunities ranked by composite score |
| REX Funds | `/screener/rex-funds` | Portfolio health check for existing REX products |
| Evaluate | `/screener/evaluate` | Interactive candidate evaluator (type tickers, get verdicts) |
| Stock Detail | `/screener/stock/<ticker>` | Competitive deep dive for a specific underlier |
| EDGAR Search | `/search/` | Submit trust monitoring requests |
| Admin | `/admin/` | Trust approvals, subscriber mgmt, digest, screener scoring |
| Downloads | `/downloads/` | CSV/Excel exports |
| AI Analysis | `/analysis/` | Claude-powered filing analysis |

### Launch Screener

Scores stock underliers for leveraged ETF potential using Bloomberg data:

**Scoring Factors** (5 factors, data-driven weights from correlation analysis):
| Factor | Weight | Why |
|--------|--------|-----|
| Turnover / Traded Value | 30% | Strongest predictor of AUM success (r=0.74) |
| Total Open Interest | 30% | Direct options demand signal (r=0.65) |
| Market Cap | 20% | Swap/derivative viability |
| Volatility 30D | 10% | Retail traders want vol for leveraged products |
| Short Interest Ratio | 10% | Contrarian interest (inverted) |

**Competitive Penalty**: Stocks where existing products have low AUM get penalized:
- Total AUM < $10M after 6+ months: -25 pts ("Market Rejected")
- Total AUM < $50M after 12+ months: -15 pts ("Low Traction")

**Workflow**: Daily Bloomberg scrape -> save as `data/SCREENER/data.xlsx` -> Admin > Score Data -> view at `/screener/`

### Candidate Evaluator

When the team proposes specific tickers (e.g., "should we file on SCCO, BHP, RIO?"), the evaluator runs a 4-pillar analysis:

| Pillar | Source | Verdict |
|--------|--------|---------|
| Demand Signal | Bloomberg stock_data | HIGH / MEDIUM / LOW / DATA UNAVAILABLE |
| Competitive Landscape | Bloomberg etp_data | FIRST MOVER / EARLY STAGE / COMPETITIVE / CROWDED |
| Market Feedback | etp_data AUM trends | VALIDATED / MIXED / REJECTED / NO PRODUCTS |
| Filing Status | SEC pipeline DB | ALREADY TRADING / FILED / NOT FILED |

**Overall Verdict**: RECOMMEND / NEUTRAL / CAUTION (rules-based across all 4 pillars)

Available as:
- **Web**: `/screener/evaluate` - interactive ticker input, instant results
- **CLI**: `python screener/generate_report.py evaluate SCCO BHP RIO` - generates PDF
- **PDF**: `reports/Candidate_Evaluation_YYYYMMDD.pdf`

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.13 |
| Web Framework | FastAPI + Jinja2 templates |
| Database | SQLite (via SQLAlchemy) |
| SEC Data | requests + BeautifulSoup + lxml |
| Data Processing | pandas |
| Screener Data | Bloomberg Excel exports (openpyxl) |
| PDF Reports | ReportLab |
| AI Analysis | Anthropic Claude API |
| Email | Azure Graph API (MSAL) |
| Deployment | Render (web) + Windows local (pipeline) |
| Scheduling | Windows Task Scheduler |

---

## Project Structure

```
rexfinhub/
|-- etp_tracker/           # SEC filing pipeline
|   |-- trusts.py          # CIK registry (122 trusts)
|   |-- run_pipeline.py    # Pipeline orchestrator (step2-5, incremental)
|   |-- step2.py           # Fetch submissions from EDGAR
|   |-- step3.py           # Extract fund names (strategy-routed)
|   |-- step4.py           # Roll up fund status
|   |-- step5.py           # Build name history
|   |-- manifest.py        # Incremental processing manifest + PIPELINE_VERSION
|   |-- ixbrl.py           # iXBRL/OEF taxonomy extractor
|   |-- sec_client.py      # HTTP client with disk cache
|   |-- email_alerts.py    # Email digest builder/sender (DB + CSV modes)
|
|-- screener/              # ETF launch screener
|   |-- config.py          # Weights, thresholds, paths (data.xlsx)
|   |-- data_loader.py     # Bloomberg Excel loader
|   |-- scoring.py         # Percentile scoring engine
|   |-- competitive.py     # Competitive density analysis
|   |-- filing_match.py    # Match screener tickers to pipeline filings
|   |-- candidate_evaluator.py  # 4-pillar candidate evaluation
|   |-- report_3x_generator.py  # PDF report builder
|   |-- generate_report.py      # CLI entry point
|
|-- webapp/                # FastAPI web application
|   |-- main.py            # App setup, middleware, mounts
|   |-- database.py        # SQLAlchemy engine + session
|   |-- models.py          # 9 DB models
|   |-- fund_filters.py    # Mutual fund exclusion patterns (shared)
|   |-- routers/           # 12 route modules
|   |-- services/          # Sync, screener, search services
|   |-- templates/         # 17 Jinja2 HTML templates
|   |-- static/            # CSS, JS, images
|
|-- outputs/               # Pipeline CSV outputs (per-trust folders)
|-- data/                  # SQLite DB + screener data (persistent on Render)
|   |-- SCREENER/data.xlsx # Bloomberg data file
|-- reports/               # Generated PDF reports
|-- tests/                 # pytest test suite
|
|-- run_daily.py           # Daily scheduler (pipeline + Excel + DB sync + email)
|-- email_recipients.txt   # Approved digest recipients
|-- digest_subscribers.txt # Pending subscriber requests
|-- trust_requests.txt     # Trust monitoring requests
|-- CLAUDE.md              # Claude Code project instructions
|-- OPERATIONS.md          # How to run everything
|-- requirements.txt       # Python dependencies
```

---

## Deployment

### Render (Web App)
- Auto-deploys on push to `main`
- Persistent disk: `data/` (1GB) - survives deploys
- Ephemeral: `outputs/` - lost on every deploy
- All web features work on Render (dashboard, screener, evaluator, etc.)
- Pipeline is **blocked** on Render (crashes the web server due to memory/CPU)

### Local (Pipeline + Reports)
- Pipeline runs locally via `run_daily.py`
- After pipeline: DB syncs locally, then uploads to Render via API
- PDF reports generated locally to `reports/`
- Bloomberg data saved locally to `data/SCREENER/data.xlsx`
- Scheduled via Windows Task Scheduler at 8:00 AM daily

---

## Key Design Decisions

1. **Pipeline runs locally, not on Render** - SEC filing extraction is CPU-intensive (regex parsing of HTML). Running as a BackgroundTask crashes Render's starter plan. Solution: run locally, upload the DB.

2. **CIKs verified, never guessed** - 8 of 14 original CIKs were wrong. Every CIK is verified against `data.sec.gov/submissions/CIK{padded}.json` before adding.

3. **Competitive penalty in scoring** - Raw percentile scoring puts stocks with failed existing products (low AUM, old age) too high. The penalty system catches "market rejected" underliers.

4. **Candidate evaluator handles missing data** - Many target tickers (ADRs, Canadian stocks, ETFs) aren't in Bloomberg US equity data. The evaluator gracefully degrades: Pillar 1 shows "DATA UNAVAILABLE" while Pillars 2-4 still work from ETP data and the filing pipeline.

5. **Trust approve writes to source code** - When a trust is approved via Admin, it's added to both the DB and `etp_tracker/trusts.py` so the pipeline always picks it up. This ensures the Python registry and DB stay in sync.

6. **Mutual fund exclusion** - Many trusts contain both ETF and mutual fund share classes. Fund search and dashboard KPIs filter out Class A/B/C/I/etc. patterns via `webapp/fund_filters.py` to show only ETF products.

7. **DB-based digest** - Email digest reads from the SQLite database instead of CSV files, eliminating dependency on `outputs/` directory being populated.

8. **Incremental pipeline with manifests** - Each trust has a `_manifest.json` tracking processed filings. Daily runs only process new filings (seconds vs hours). Bump `PIPELINE_VERSION` in `manifest.py` to force full reprocess.
