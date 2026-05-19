# REX FinHub Automation

## Quick Reference

```bash
# Check everything
python scripts/automation_review.py

# Preview today's reports at 5PM
python scripts/send_email.py preview all

# Send manually
python scripts/send_email.py send daily
python scripts/send_email.py send weekly          # Weekly + LI + Income + Flow + Autocall

# Full pipeline
python scripts/run_daily.py
```

## What to Tell Claude

Run `/status` or say: "Run automation review and check everything"

Claude should run `python scripts/automation_review.py` and check:
1. All 3 scheduled tasks are Ready
2. Fund filings, notes, market data are fresh (today's date)
3. D: drive connected and in sync
4. Render site healthy
5. Daily archive exists for today
6. Bloomberg file updated after 5PM
7. No errors in watcher/rapid sync logs

## Schedule

| When | What | Emails |
|------|------|--------|
| Every 30 min | Watcher (new filings/trusts) | none |
| Every 2 hours | Rapid sync (scrape + upload to site) | none |
| Mon-Fri 6:00 PM | Full pipeline + archive + emails | Daily (every day) + Weekly bundle (Monday) |

## Daily Pipeline (6PM Mon-Fri)

1. Trust universe sync (SEC submissions.zip)
2. SEC filings + structured notes (parallel)
3. DB sync
4. Archive C: -> D:
5. Market data + ETN overrides baked into DB
6. Screener cache
7. Daily archive (9 files, ~37MB -> C: + D:)
8. Classification
9. Upload etp_tracker.db + structured_notes.db + screener cache to Render
10. Send emails

## Storage

Each day saves 9 files (~37MB) to both drives:
- `data/DASHBOARD/exports/screener_snapshots/YYYY-MM-DD/` (C: primary)
- `D:/sec-data/archives/screener/YYYY-MM-DD/` (D: cold backup)

Contents: Bloomberg file, ETP data, stock data, market master, screener cache, evaluator, results CSV, report metadata, autocall ranks.

## Flow Methodology

All US ETP flows lag by 1 day. Pulled Tue evening:
- 1W = Tue-Mon (5 trading days ending 1 day before pull)
- 1M = rolling 1 month back from 1 day before pull
- 1D = previous day's activity
