# ETP Filing Tracker - Operations Guide

## Quick Reference

| What | Command |
|------|---------|
| Start local server | `cd D:\REX_ETP_TRACKER; uvicorn webapp.main:app --reload --port 8000` |
| Run full pipeline (incremental) | `cd D:\REX_ETP_TRACKER; python run_daily.py` |
| Run pipeline only (no email) | `cd D:\REX_ETP_TRACKER; python -c "from etp_tracker.run_pipeline import run_pipeline; from etp_tracker.trusts import get_all_ciks, get_overrides; run_pipeline(ciks=list(get_all_ciks()), overrides=dict(get_overrides()), refresh_submissions=True, user_agent='REX-ETP-Tracker/2.0')"` |
| Force full reprocess | `cd D:\REX_ETP_TRACKER; python -c "from etp_tracker.run_pipeline import run_pipeline; from etp_tracker.trusts import get_all_ciks, get_overrides; run_pipeline(ciks=list(get_all_ciks()), overrides=dict(get_overrides()), refresh_submissions=True, user_agent='REX-ETP-Tracker/2.0', force_reprocess=True)"` |
| Sync pipeline data to DB | `cd D:\REX_ETP_TRACKER; python -c "from webapp.database import SessionLocal, init_db; from webapp.services.sync_service import seed_trusts, sync_all; from pathlib import Path; init_db(); db=SessionLocal(); seed_trusts(db); sync_all(db, Path('outputs')); db.close(); print('Done')"` |
| Generate screener PDF | `cd D:\REX_ETP_TRACKER; python screener/generate_report.py` |
| Run candidate evaluation | `cd D:\REX_ETP_TRACKER; python screener/generate_report.py evaluate SCCO BHP RIO` |

---

## 1. Running the Local Server

Open PowerShell and run:

```powershell
cd D:\REX_ETP_TRACKER
uvicorn webapp.main:app --reload --port 8000
```

Then open: **http://localhost:8000**

Pages:
- `/` - Dashboard (122 trust cards, fund counts, recent filings with filters)
- `/funds/` - Fund Search (~7,000 ETF funds, mutual fund classes excluded)
- `/filings/` - Filing Search (all filings, search by trust/form/accession)
- `/trusts/<slug>` - Trust detail page
- `/screener/` - Launch Screener (Top 50 + Filed-only tables)
- `/screener/evaluate` - Candidate Evaluator (interactive ticker evaluation)
- `/screener/rex-funds` - REX Fund portfolio health
- `/screener/stock/<ticker>` - Competitive deep dive per stock
- `/search/` - EDGAR search (submit trust monitoring requests)
- `/admin/` - Admin panel (password: `123`)
- `/downloads/` - CSV/Excel exports

---

## 2. Running the SEC Pipeline (Local Only)

The pipeline fetches SEC filings for all 122 trusts. **Run locally, not on Render.**

The pipeline is **incremental by default** - it only processes NEW filings that haven't been extracted before. This is tracked via `_manifest.json` files in each trust's output folder.

- **Daily incremental run**: seconds if no new filings, minutes if new filings found
- **Full reprocess**: ~2-3 hours (all 122 trusts from scratch)

### Full daily run (pipeline + Excel + DB sync + Render upload + email digest):

```powershell
cd D:\REX_ETP_TRACKER
python run_daily.py
```

### Pipeline only (no email, incremental):

```powershell
cd D:\REX_ETP_TRACKER
python -c "
from etp_tracker.run_pipeline import run_pipeline
from etp_tracker.trusts import get_all_ciks, get_overrides
n = run_pipeline(
    ciks=list(get_all_ciks()),
    overrides=dict(get_overrides()),
    refresh_submissions=True,
    user_agent='REX-ETP-Tracker/2.0 (relasmar@rexfin.com)',
)
print(f'{n} trusts processed')
"
```

### Force full reprocess (clears all manifests):

```powershell
cd D:\REX_ETP_TRACKER
python -c "
from etp_tracker.run_pipeline import run_pipeline
from etp_tracker.trusts import get_all_ciks, get_overrides
n = run_pipeline(
    ciks=list(get_all_ciks()),
    overrides=dict(get_overrides()),
    refresh_submissions=True,
    user_agent='REX-ETP-Tracker/2.0 (relasmar@rexfin.com)',
    force_reprocess=True,
)
print(f'{n} trusts processed')
"
```

### Extraction strategies:

The pipeline routes filings to optimized extraction strategies:

| Form Type | Strategy | Speed | What It Does |
|-----------|----------|-------|-------------|
| 485BXT, 497J | `header_only` | Fast (~2KB read) | SGML header + effectiveness date from header |
| 485BPOS | `full+ixbrl` | Medium | SGML + iXBRL/OEF tags for structured dates + expense ratios |
| 485APOS, 497, 497K | `full` | Slow | SGML + full body text analysis + regex date extraction |

### Run summary:

After each run, check `outputs/_run_summary.json` for metrics:
- How many filings were new vs skipped
- Which extraction strategies were used
- Errors and duration

### After pipeline: sync to webapp DB:

```powershell
cd D:\REX_ETP_TRACKER
python -c "
from webapp.database import SessionLocal, init_db
from webapp.services.sync_service import seed_trusts, sync_all
from pathlib import Path
init_db()
db = SessionLocal()
seed_trusts(db)
sync_all(db, Path('outputs'))
db.close()
print('DB sync complete')
"
```

---

## 3. Screener Workflow

### Daily Bloomberg data update:

1. Scrape Bloomberg for etp_data and stock_data
2. Save the Excel file as `D:\REX_ETP_TRACKER\data\SCREENER\data.xlsx`
   - Must have two sheets: `stock_data` and `etp_data`
3. Go to **Admin Panel** > **Score Data** to re-score
4. View results at `/screener/`

No upload through the web UI. Place the file directly on disk.

### Re-score existing data:

1. Go to **Admin Panel** (`/admin/`, password `123`)
2. Click **Score Data**

### Generate PDF report (local):

```powershell
cd D:\REX_ETP_TRACKER
python screener/generate_report.py
```

Output: `reports/ETF_Launch_Screener_YYYYMMDD.pdf`

### Run candidate evaluation (local CLI):

```powershell
cd D:\REX_ETP_TRACKER
python screener/generate_report.py evaluate SCCO BHP RIO TECK SIL ZETA HBM ERO AMPX
```

Output: `reports/Candidate_Evaluation_YYYYMMDD.pdf`

### Run candidate evaluation (web):

1. Go to `/screener/evaluate`
2. Type ticker symbols and click **Add** (or paste comma-separated: `SCCO, BHP, RIO`)
3. Click **Evaluate** - results appear inline with 4 pillars per ticker
4. If a ticker is not in the Bloomberg dataset, it shows "DATA UNAVAILABLE" for Demand

---

## 4. Trust Request Flow

### As a user:
1. Go to `/search/`
2. Search for a trust name
3. Click **Verify** on the result
4. Click **Request Monitoring**

### As admin:
1. Go to `/admin/` (password: `123`)
2. See pending requests under **Pending Trust Requests**
3. Click **Approve** - trust is added to DB AND `trusts.py` registry
4. Run the pipeline locally to fetch its filings

---

## 5. Email Digest

### Send from admin panel:
1. Go to **Admin Panel** > **Email Digest**
2. Click **Send Digest Now**
3. Reads directly from the database (no CSV dependency)
4. Recipients configured in `email_recipients.txt`

### Subscriber management:
1. Users subscribe via the webapp (writes to `digest_subscribers.txt`)
2. Admin approves/rejects in **Admin Panel** > **Digest Subscribers**
3. Approved emails are added to `email_recipients.txt`

### Send from command line (DB-based):

```powershell
cd D:\REX_ETP_TRACKER
python -c "
from webapp.database import SessionLocal, init_db
from etp_tracker.email_alerts import send_digest_from_db
init_db()
db = SessionLocal()
send_digest_from_db(db, dashboard_url='https://rex-etp-tracker.onrender.com')
db.close()
"
```

### Send from command line (CSV-based, legacy):

```powershell
cd D:\REX_ETP_TRACKER
python -c "
from etp_tracker.email_alerts import send_digest_email
from pathlib import Path
send_digest_email(Path('outputs'), dashboard_url='https://rex-etp-tracker.onrender.com')
"
```

---

## 6. Admin Panel

The admin panel (`/admin/`, password `123`) has these sections:

| Section | What It Does |
|---------|-------------|
| Trust Request Approvals | Approve/reject trust monitoring requests (writes to DB + trusts.py) |
| Digest Subscriber Approvals | Approve/reject digest subscribers (writes to email_recipients.txt) |
| Email Digest | Send digest email to all approved recipients (reads from DB) |
| Score Data | Re-score Bloomberg screener data from data/SCREENER/data.xlsx |
| Email Report | Email screener report to subscribers |
| Ticker QC | Check pipeline ticker quality |
| AI Analysis Status | View Claude API usage |

**Not in admin**: Pipeline control (run locally only), screener data upload (place file on disk).

---

## 7. Deploying to Render

The app auto-deploys on push to `main`.

- **Live URL**: https://rex-etp-tracker.onrender.com
- **Persistent disk**: `data/` directory (1GB) - survives deploys
- **Ephemeral**: `outputs/` - lost on every deploy

### What works on Render:
- All web pages (dashboard, funds, filings, screener, search, evaluate)
- Screener scoring (data must be on persistent disk)
- Candidate evaluator (interactive ticker evaluation)
- Email digest from DB (if Azure Graph API is configured)
- AI filing analysis (if Anthropic API key is set)

### What must run locally:
- SEC filing pipeline (`run_daily.py`) - too resource-intensive for Render
- PDF report generation (needs local file output)

### Push and deploy:

```powershell
cd D:\REX_ETP_TRACKER
git add -A
git commit -m "your message"
git push origin main
```

---

## 8. Scheduled Daily Run (Windows Task Scheduler)

Run PowerShell **as Administrator**, then:

```powershell
schtasks /create /tn "ETP_Filing_Tracker" /tr "python D:\REX_ETP_TRACKER\run_daily.py" /sc daily /st 08:00 /f
```

This runs at 8am daily: pipeline + Excel exports + DB sync + Render upload + email digest.

**Important**: Your laptop must be on and not sleeping at 8am. Set power options to "Never sleep" when plugged in.

To check the task exists:
```powershell
schtasks /query /tn "ETP_Filing_Tracker"
```

To delete it:
```powershell
schtasks /delete /tn "ETP_Filing_Tracker" /f
```
