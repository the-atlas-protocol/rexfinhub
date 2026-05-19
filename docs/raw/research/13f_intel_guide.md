# 13F Intel — Operations Guide

**Status**: live, admin-gated, internal product + sales intelligence
**Audience**: REX product team + sales team. Never public.
**Last updated**: 2026-05-13

---

## What it is

Two URL prefixes on rexfinhub.com, both gated behind admin login:

- `/intel/*` — 12 aggregate views (REX report, competitors, products, head-to-head, country, asia, trends, etc.)
- `/holdings/*` — 5 entity-detail views (institution list, institution detail, fund holders, history, crossover)

All routes hit `data/13f_holdings.db` (SQLite). Cross-DB joins to the main `etp_tracker.db` work through a SQLite ATTACH wired in `webapp/database.py`.

Login: visit `/admin/`, enter `ADMIN_PASSWORD` from `config/.env`.

---

## Three-tier execution

| Layer | Role |
|---|---|
| **VPS** (`46.224.126.196`) | Scheduled quarterly ingest via systemd timer |
| **Local laptop** | Ad-hoc ops, dev, manual re-runs |
| **Render** | Web service — reads from `data/13f_holdings.db`, serves admin pages |

No script ever runs on Render. The `RENDER` env var blocks ingestion if attempted.

---

## Universe

The "ETP universe" is defined by:

```sql
SELECT * FROM mkt_master_data WHERE etp_category IS NOT NULL
```

Bloomberg's `mkt_master_data` table already provides `cusip`, `ticker`, `issuer`, `issuer_display`, `category_display`, `is_rex`, and `map_li_underlier`. No separate universe file or manual list is required.

**Stealth-REX gap**: a REX product filed with SEC but not yet in `mkt_master_data` would have no CUSIP available. `seed_cusip_mappings()` plugs this gap by calling OpenFIGI for any `FundStatus.ticker` (where `Trust.is_rex = True`) not found in `mkt_master_data`. Typically zero to a handful per quarter.

---

## Ingest modes

All three modes auto-run `_post_ingest_finalize()` at the end: pre-backup → ingest → dedupe → populate `last_filed` + `trust_id` → row-count delta check → `PRAGMA integrity_check`.

### Bulk (canonical quarterly path)

```bash
python scripts/run_13f.py bulk 01dec2025-28feb2026
```

Downloads SEC bulk ZIP (~90MB), extracts TSVs, ingests. ~22 minutes for one full quarter.

### Local (pre-extracted TSVs)

```bash
python scripts/run_13f.py local /path/to/extracted_tsv_dir
```

Skips download. Use when you already have SUBMISSION.tsv + COVERPAGE.tsv + INFOTABLE.tsv.

### Incremental (last 7 days via EFTS)

```bash
python scripts/run_13f.py incremental
```

Catches late filings between quarterly bulk drops. Parses XML infotables one at a time.

### Backfill (multi-quarter)

```bash
python scripts/run_13f.py backfill
```

This is what the systemd timer fires. Pulls the latest available quarter and ingests.

### Other modes

- `python scripts/run_13f.py seed` — CUSIP mapping refresh only (Bloomberg + OpenFIGI stealth-REX)
- `python scripts/run_13f.py health` — print diagnostic state
- `python scripts/run_13f.py deploy-db` — push DB to Render

---

## Quarterly schedule (VPS systemd)

```
/etc/systemd/system/rexfinhub-13f-quarterly.service
/etc/systemd/system/rexfinhub-13f-quarterly.timer
```

Fires 4× per year:

| Date (ET) | Quarter being ingested |
|---|---|
| Feb 19, 06:00 | Q4 (prior calendar year) |
| May 20, 06:00 | Q1 |
| Aug 19, 06:00 | Q2 |
| Nov 19, 06:00 | Q3 |

SEC publishes 13F bulk ZIPs ~50 days after quarter-end (45-day filing deadline + ~5 days).

`TZ=America/New_York` pinned at the systemd-unit level.

---

## Safeguards (auto-run on every ingest)

| Safeguard | Where | What it does |
|---|---|---|
| Pre-ingest snapshot | `_backup_holdings_db()` | Copies `data/13f_holdings.db` to `data/backups/13f_holdings_pre_{mode}_{ts}.db` |
| Dedupe | `_post_ingest_dedupe()` | DELETEs exact-duplicate holdings rows (group key: institution + cusip + report_date + value + shares + share_type) |
| `last_filed` populate | `_post_ingest_metadata()` | Sets `institutions.last_filed = MAX(holdings.report_date)` per institution |
| `trust_id` populate | `_post_ingest_metadata()` | Sets `cusip_mappings.trust_id` via cross-DB join to `main_site.fund_status` |
| Row-count delta alert | `_post_ingest_verify()` | Warns loudly if latest-quarter row count is <80% of prior quarter — catches partial SEC ZIPs |
| Integrity check | `_post_ingest_verify()` | Runs `PRAGMA integrity_check` |

All idempotent — safe to re-run on the same DB.

---

## Render deploy (after ingest)

```bash
python scripts/run_13f.py deploy-db
```

Or use the existing admin upload endpoint:

```bash
curl -X POST https://rexfinhub.com/api/v1/db/upload \
  -F "file=@data/13f_holdings.db" \
  -H "X-Admin-Token: <admin-token>"
```

Render's persistent disk is 10GB. Q4 2025 alone takes ~811MB. Multi-quarter retention fits comfortably.

---

## Verification

```bash
python -c "
from webapp.database import HoldingsSessionLocal
from etp_tracker.thirteen_f import data_health_report
data_health_report()
"
```

Or in a browser, log in as admin and visit:
- `/intel/` — should show hub KPIs + latest quarter selector
- `/intel/rex` — REX report
- `/intel/competitors` — competitor view
- `/holdings/` — institution list

If any of these 404, the admin gate is rejecting your session — re-login at `/admin/`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/intel/*` returns 404 | Routes not registered | Restart web service (`systemctl restart rex-etp-tracker` on VPS, or trigger Render redeploy) |
| `/intel/*` returns 302 to `/admin/` | Not admin-authed | Login at `/admin/` with `ADMIN_PASSWORD` |
| Ingest errors with "cannot run on Render" | Pipeline accidentally ran on Render | Move to VPS or local laptop |
| Delta alert fires (<80%) on latest quarter | Partial SEC ZIP or bad parse | Inspect the SEC bulk download, re-run `local` mode against verified TSVs |
| OpenFIGI lookups failing | Free tier rate limit (25/min, 5 per request) | Set `OPENFIGI_API_KEY` env var → bumps to 250/min, 100 per request |
| Integrity check returns non-`ok` | DB corruption | Restore from `data/backups/` snapshot, re-run ingest |

---

## Code anchors

- Router files: `webapp/routers/{intel,intel_competitors,intel_insights,holdings}.py` — all gated via `dependencies=[Depends(require_admin)]`
- Pipeline: `etp_tracker/thirteen_f.py` (single source of truth — `fetch_13f.py` was deleted)
- Auth helper: `webapp/dependencies.py:require_admin`
- Cross-DB ATTACH: `webapp/database.py:holdings_engine`
- Service definition: `webapp/services/holdings_intel.py` (1,052 lines of analytics functions)
- Systemd: `deploy/systemd/rexfinhub-13f-quarterly.{service,timer}`
