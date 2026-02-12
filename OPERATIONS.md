# ETP Filing Tracker - Operations Guide

## Quick Reference

| What | Command |
|------|---------|
| Start local server | `cd D:\REX_ETP_TRACKER; uvicorn webapp.main:app --reload --port 8000` |
| Run full pipeline | `cd D:\REX_ETP_TRACKER; python run_daily.py` |
| Run pipeline only (no email) | `cd D:\REX_ETP_TRACKER; python -c "from etp_tracker.run_pipeline import run_pipeline; from etp_tracker.trusts import get_all_ciks, get_overrides; run_pipeline(ciks=list(get_all_ciks()), overrides=dict(get_overrides()), since='2024-11-14', refresh_submissions=True, user_agent='REX-ETP-Tracker/2.0')"` |
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
- `/` - Dashboard (trust overview, fund counts, recent filings)
- `/funds/` - Fund Search (all 2,836 funds, search/filter)
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

The pipeline fetches SEC filings for all 16 trusts. Takes 30-60 min. **Run locally, not on Render.**

### Full daily run (pipeline + Excel + email digest):

```powershell
cd D:\REX_ETP_TRACKER
python run_daily.py
```

### Pipeline only (no email):

```powershell
cd D:\REX_ETP_TRACKER
python -c "
from etp_tracker.run_pipeline import run_pipeline
from etp_tracker.trusts import get_all_ciks, get_overrides
n = run_pipeline(
    ciks=list(get_all_ciks()),
    overrides=dict(get_overrides()),
    since='2024-11-14',
    refresh_submissions=True,
    user_agent='REX-ETP-Tracker/2.0 (relasmar@rexfin.com)',
)
print(f'{n} trusts processed')
"
```

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

## 3. Screener Operations

### Upload Bloomberg data (Render or Local):

1. Go to **Admin Panel** > **Launch Screener**
2. Click **Upload New Data** and select your `.xlsx` file
3. File must have sheets: `stock_data` and `etp_data`
4. Scoring runs automatically. Page auto-refreshes in 10-15s.
5. Go to **Screener** tab to see results.

### Re-score existing data:

1. Go to **Admin Panel** > **Launch Screener**
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
3. Requires pipeline CSV data in `outputs/` folder
4. Recipients configured in `email_recipients.txt`

### Send from command line:

```powershell
cd D:\REX_ETP_TRACKER
python -c "
from etp_tracker.email_alerts import send_digest_email
from pathlib import Path
send_digest_email(Path('outputs'), dashboard_url='https://rex-etp-tracker.onrender.com')
"
```

---

## 6. Deploying to Render

The app auto-deploys on push to `main`.

- **Live URL**: https://rex-etp-tracker.onrender.com
- **Persistent disk**: `data/` directory (1GB) - survives deploys
- **Ephemeral**: `outputs/` - lost on every deploy

### What works on Render:
- All web pages (dashboard, funds, filings, screener, search, evaluate)
- Bloomberg data upload and scoring (via admin panel)
- Candidate evaluator (interactive ticker evaluation)
- Email digest (if Azure Graph API is configured)
- AI filing analysis (if Anthropic API key is set)

### What must run locally:
- SEC filing pipeline (`run_daily.py`) - too long, crashes web server
- PDF report generation (needs local file output)

### Push and deploy:

```powershell
cd D:\REX_ETP_TRACKER
git add -A
git commit -m "your message"
git push origin main
```

---

## 7. Scheduled Daily Run (Windows Task Scheduler)

```powershell
schtasks /create /tn "ETP_Filing_Tracker" /tr "python D:\REX_ETP_TRACKER\run_daily.py" /sc daily /st 17:00
```

This runs at 5pm daily: pipeline + Excel exports + email digest.
