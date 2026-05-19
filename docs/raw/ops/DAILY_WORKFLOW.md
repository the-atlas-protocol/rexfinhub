# Ryu's Daily Workflow

## Morning Routine

### 8:00 AM - Automated Morning Run
- [ ] PC wakes from sleep (Task Scheduler)
- [ ] `run_all_pipelines.py` runs automatically:
  - SEC pipeline catches overnight EDGAR filings
  - Market pipeline skips (no new Bloomberg data yet)
  - DB uploaded to Render
  - Email digest sent
- [ ] PC goes back to sleep
- [ ] Check email digest for overnight filing activity

## During the Day

### Bloomberg Data Updates
- [ ] Update Bloomberg data in OneDrive
  - `bbg_data.xlsx` auto-syncs to `data/DASHBOARD/bbg_data.xlsx`
- [ ] Update screener data at `data/SCREENER/data.xlsx`
- [ ] Any ad hoc analysis or reports

### If Needed: Manual Pipeline Runs
```bash
# Re-run SEC pipeline only
python scripts/run_all_pipelines.py --skip-market --skip-email

# Force market pipeline re-process
python scripts/run_all_pipelines.py --skip-sec --force-market

# Full manual run
python scripts/run_all_pipelines.py
```

## End of Day

### By 5:00 PM - Finalize Data
- [ ] Ensure `bbg_data.xlsx` is saved and synced
- [ ] Ensure `data/SCREENER/data.xlsx` is updated

### 5:30 PM - Automated Evening Run
- [ ] PC wakes from sleep (Task Scheduler)
- [ ] `run_all_pipelines.py` runs automatically:
  - SEC pipeline catches any afternoon filings
  - Market pipeline processes new `bbg_data.xlsx`
  - DB uploaded to Render
  - Email digest sent
- [ ] PC goes back to sleep
- [ ] Website on Render has fresh data for tomorrow

---

## Key Locations

| What | Where |
|------|-------|
| Project root | `C:\Projects\rexfinhub` |
| Bloomberg data | `data/DASHBOARD/bbg_data.xlsx` |
| Screener data | `data/SCREENER/data.xlsx` |
| Pipeline logs | `logs/pipeline_YYYYMMDD_HHMM.log` |
| Database | `data/etp_tracker.db` |
| Live site | https://rex-etp-tracker.onrender.com |
| Admin panel | https://rex-etp-tracker.onrender.com/admin/ |

## Key Commands

```bash
# Start local dev server
uvicorn webapp.main:app --reload --port 8000

# Run all pipelines
python scripts/run_all_pipelines.py

# SEC pipeline only (daily run)
python scripts/run_daily.py

# Market pipeline only
python scripts/run_market_pipeline.py

# Force market pipeline (ignore change detection)
python scripts/run_market_pipeline.py --force

# Generate screener PDF
python screener/generate_report.py
```

## Scheduled Tasks

| Task | Schedule | What It Does |
|------|----------|--------------|
| `REX_Morning_Pipeline` | 8:00 AM Mon-Fri | SEC + market + upload + email |
| `REX_Evening_Pipeline` | 5:30 PM Mon-Fri | SEC + market + upload + email |

### Check task status
```powershell
Get-ScheduledTask -TaskName "REX_*" | Format-Table TaskName, State
```

### Trigger manually
```powershell
Start-ScheduledTask -TaskName "REX_Morning_Pipeline"
```

---

## Troubleshooting

### Pipeline didn't run overnight
1. Check if PC was in Sleep (not Hibernate or Off)
2. Check wake timers: `powercfg /waketimers`
3. Check logs: `ls -t logs/pipeline_*.log | head -1`

### Market data shows "unchanged, skipping"
Expected if `bbg_data.xlsx` hasn't been modified. Force with `--force-market`.

### Render shows stale data
1. Check the pipeline log for upload errors
2. Manual upload: `python -c "from scripts.run_daily import upload_db_to_render; upload_db_to_render()"`

### Need to reprocess all SEC filings from scratch
```bash
python -c "from etp_tracker.run_pipeline import run_pipeline; from etp_tracker.trusts import get_all_ciks, get_overrides; run_pipeline(ciks=list(get_all_ciks()), overrides=dict(get_overrides()), user_agent='REX-ETP-Tracker/2.0', force_reprocess=True)"
```
This clears all manifests and reprocesses everything (~2-3 hours).
