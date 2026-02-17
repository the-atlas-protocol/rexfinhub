# REX ETP Filing Tracker

## Project Overview
SEC filing tracker for leveraged ETF products. Monitors 20 trusts via EDGAR, runs a 5-step CSV pipeline, and serves results through a FastAPI webapp deployed on Render.

**Owner**: Ryu El-Asmar (relasmar@rexfin.com)
**GitHub**: https://github.com/ryuoelasmar/REX_ETP_TRACKER.git (branch: main)
**Live site**: https://rex-etp-tracker.onrender.com
**Admin password**: `123`

## Architecture

```
etp_tracker/         SEC filing pipeline (steps 2-5)
  trusts.py          CIK registry - source of truth for monitored trusts
  run_pipeline.py    Pipeline orchestrator (incremental, with metrics)
  step2.py           Filing discovery from submissions JSON
  step3.py           Fund extraction (strategy-routed: header_only / full / full+ixbrl)
  step4.py           Fund status rollup (EFFECTIVE/PENDING/DELAYED)
  step5.py           Name history tracking
  manifest.py        Incremental processing manifest (tracks processed filings)
  ixbrl.py           iXBRL/OEF taxonomy extractor (structured dates, expense ratios)
  run_summary.py     Pipeline run metrics and observability
  sec_client.py      HTTP client with file cache + header-only reader
  config.py          Form types, extraction strategies, SEC endpoints
  sgml.py            SGML header parser (Series/Class extraction)
  body_extractors.py HTML/PDF text extraction
screener/            Bloomberg-based 3x/4x launch screener
  config.py          Scoring weights, thresholds, data file path
  candidate_evaluator.py   4-pillar evaluation engine
  report_3x_generator.py   PDF report generator
  report_ipo_generator.py  Pre-IPO filing landscape PDF
webapp/              FastAPI + Jinja2 web application
  routers/screener.py      Screener routes (6 tabs)
  routers/admin.py         Admin panel (pipeline, screener, digest)
  services/screener_3x_cache.py  In-memory analysis cache
  database.py              SQLite DB setup
run_daily.py         Full daily run: pipeline + Excel + DB sync + Render upload + email
outputs/             Pipeline CSV output (gitignored, ephemeral on Render)
  {trust}/_manifest.json   Per-trust processing manifest
  _run_summary.json        Last pipeline run metrics
data/                Bloomberg data + DB (gitignored, persistent disk on Render)
reports/             Generated PDFs and CSVs
```

## Pipeline
- 5 steps: step2 (fetch filings) -> step3 (extract fund names) -> step4 (determine status) -> step5 (resolve names)
- 20 trusts, ~3,300 funds
- **Incremental by default**: Step 3 tracks processed filings via `_manifest.json` per trust. Daily runs process only new filings (~0.3-5s vs 30-90 min for full reprocess).
- **Strategy-routed extraction**: 485BXT/497J use header-only parsing (~2KB read). 485BPOS uses iXBRL/OEF enrichment. Others use full body parsing.
- **iXBRL enrichment**: 485BPOS (100% iXBRL) gets structured effective dates from `oef:ProspectusDate` - no regex guessing. Also extracts expense ratios, management fees.
- `force_reprocess=True` in `run_pipeline()` clears all manifests to force full re-extraction.
- `PIPELINE_VERSION` in `manifest.py` can be bumped to invalidate all manifests.
- Pipeline MUST run locally. `RENDER` env var blocks execution on Render (crashes web process).
- HTTP responses cached on disk in `http_cache/` (~13GB). SEC rate limit: 0.35s pause.

## SEC Filing Logic
- **485BPOS** = fund is trading (EFFECTIVE). 100% have iXBRL with OEF taxonomy tags.
- **485BXT** = extension with new effective date. Header-only extraction (fast).
- **485APOS** = initial filing (+75 days default effectiveness)
- **497/497K** = supplement (fund already EFFECTIVE). ~37% of 497s have iXBRL.
- **Delaying amendment** = DELAYED status
- Effective date confidence levels: IXBRL > HEADER > HIGH > MEDIUM
- Submissions JSON contains `isInlineXBRL` flag per filing (0 or 1)

## CIK Management - CRITICAL
CIKs MUST be verified before adding. Never guess.

1. Search: `https://efts.sec.gov/LATEST/search-index?q="Trust+Name"&forms=485BPOS`
2. Verify: `https://data.sec.gov/submissions/CIK{padded_10_digits}.json` - check the `name` field
3. Add via `add_trust()` in `etp_tracker/trusts.py` or manually to `TRUST_CIKS` dict

Historical note: 8 of 14 original CIKs were wrong. All now verified.

## Render Deployment
- Auto-deploys on push to `main`
- Persistent disk: `/opt/render/project/src/data` (1GB) - survives deploys
- `data/` is gitignored - Bloomberg data must be uploaded via admin panel
- `outputs/` is ephemeral - lost on every deploy
- DB upload endpoint: `POST /api/v1/db/upload`

## Environment
- Python 3.13, no virtualenv (global install)
- Windows, USB drive D:\REX_ETP_TRACKER
- SEC rate limit: 0.25s pause between requests (10 req/s allowed)
- No emoji in console output (cp1252 encoding on Windows)

## Key Commands
```bash
# Local server
uvicorn webapp.main:app --reload --port 8000

# Full pipeline + email (incremental - only new filings)
python run_daily.py

# Force full reprocess (clears all manifests)
python -c "from etp_tracker.run_pipeline import run_pipeline; from etp_tracker.trusts import get_all_ciks, get_overrides; run_pipeline(ciks=list(get_all_ciks()), overrides=dict(get_overrides()), user_agent='REX-ETP-Tracker/2.0', force_reprocess=True)"

# Screener PDF
python screener/generate_report.py

# Candidate evaluation
python screener/generate_report.py evaluate SCCO BHP RIO
```

## User Preferences
- Has ADHD - prefer clear, step-by-step communication
- Executive deliverables must be PDF with professional formatting (not CSV/TXT)
- Follow existing ReportLab styling patterns in `report_3x_generator.py` for new reports

## Known Issues
- Render: `Data file not found: /opt/render/project/src/data/SCREENER/datatest.xlsx` - Bloomberg data needs re-upload via admin
- Step 3 CSV uses `Series Name` column (not `fund_name`) for extracted fund names
