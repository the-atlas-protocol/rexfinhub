# 13F Holdings Pillar — Activation Guide

**Status**: MVP (top 10 institutions, most recent quarter only)

---

## How to Run

```bash
# Dry-run — prints what it would fetch, no DB writes
python scripts/fetch_13f.py --dry-run

# Live run — fetches most recent 13F-HR for all 10 institutions
python scripts/fetch_13f.py

# Single institution
python scripts/fetch_13f.py --institution blackrock
```

No arguments = most recent quarter (latest 13F-HR per institution from SEC submissions JSON).

---

## How to Verify

After a live run, open the holdings DB and count rows:

```bash
python -c "
from webapp.database import init_holdings_db, HoldingsSessionLocal
from webapp.models import Institution, Holding
init_holdings_db()
db = HoldingsSessionLocal()
print('Institutions:', db.query(Institution).count())
print('Holdings:    ', db.query(Holding).count())
db.close()
"
```

Expected after a clean run: 10 institutions, 10,000–100,000 holdings (varies by quarter).

---

## How to Enable on Render

Set the environment variable in Render dashboard:

```
ENABLE_13F=1
```

This registers the `/holdings/*` routes (institution list, crossover, fund holders,
institution detail, institution history). Without this variable, all holdings routes
return 404.

The script runs **locally only** — the `RENDER` env var blocks pipeline execution on
the web process. Run `python scripts/fetch_13f.py` locally, then upload the populated
`data/13f_holdings.db` to Render's persistent disk:

```bash
# Upload via the existing DB upload endpoint
curl -X POST https://rexfinhub.com/api/v1/db/upload \
  -F "file=@data/13f_holdings.db" \
  -H "X-Admin-Token: <admin-token>"
```

---

## Known Limitations (MVP)

| Limitation | Status |
|---|---|
| Top 10 institutions only | Extend `TOP_10` list in `scripts/fetch_13f.py` |
| One quarter (most recent) | Backfill deferred — add `--quarter YYYY-MM-DD` later |
| No CUSIP enrichment | `is_tracked=False` for all rows — enrich separately |
| No scheduled refresh | Add Windows Task Scheduler or Render cron later |
| No admin endpoint | Manual run + DB upload for now |

---

## Architecture Note

The script uses `https://data.sec.gov/submissions/CIK{cik}.json` to find the
most recent 13F-HR accession number, then fetches the infotable XML directly
from `https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/infotable.xml`.

HTTP is handled by `etp_tracker.sec_client.SECClient` (rate-limited, retry-enabled,
User-Agent: `REX-ETP-Tracker/2.0 relasmar@rexfin.com`).

*Last updated: 2026-05-06*
