# Email Pipeline - End to End

> How data flows from Bloomberg daily file update to email delivery.

Last updated: 2026-03-04

---

## Quick Reference

| Email | Subject | Frequency | Data Source |
|-------|---------|-----------|-------------|
| Daily Brief | `REX ETF Daily Brief - {date}` | Daily | SEC filings + Bloomberg snapshot |
| Weekly Report | `REX ETF Weekly Report - Week of {date}` | Weekly | Bloomberg (ETF only) |
| L&I Report | `REX ETF Leveraged & Inverse Report - {date}` | Weekly | Bloomberg (ETF only) |
| Income Report | `REX ETF Income Report - {date}` | Weekly | Bloomberg (ETF only) |

All reports exclude ETNs. ETN disclaimer in L&I and Income footers.

---

## Step 1: Update Bloomberg Daily File

**You do this manually.** Update `bloomberg_daily_file.xlsm` in OneDrive MASTER Data folder.

Sheets consumed:
- `w1` -- fund metadata (ticker, name, issuer, inception, structure)
- `w2` -- cost metrics (expense ratio, spread, tracking error)
- `w3` -- returns (1D through 3Y + yield)
- `w4` -- flows (1D through 3Y) + AUM + AUM history (aum_1..aum_36)
- `s1` -- stock data (used by screener, not emails)

Rules CSVs consumed from `data/rules/`:
- `fund_mapping.csv` -- ticker -> etp_category (LI, CC, Crypto, Defined, Thematic)
- `issuer_mapping.csv` -- issuer -> issuer_nickname (friendly display name)
- `attributes_LI.csv` -- LI attributes (category, subcategory, direction, leverage, underlier)
- `attributes_CC.csv` -- CC attributes (cc_type, cc_category, underlier, index)
- `rex_funds.csv` -- REX-owned tickers
- `rex_suite_mapping.csv` -- ticker -> rex_suite

**File resolution priority:**
1. OneDrive: `C:\Users\RyuEl-Asmar\REX Financial LLC\...\MASTER Data\bloomberg_daily_file.xlsm`
2. Local fallback: `data/DASHBOARD/bloomberg_daily_file.xlsm`

If the file is locked by Excel, resolution falls through to the next candidate.

---

## Step 2: Run Daily Pipeline

```bash
python scripts/run_daily.py
```

**Step 3.25** in `run_daily.py` calls `sync_market_data(db)`.

### 2a. Data Engine Builds DataFrames

`webapp/services/data_engine.py` -> `build_all(data_file)`

1. Read w1, w2, w3, w4 sheets from Excel
2. Rename Bloomberg abbreviated columns to canonical names (via `W1_COL_MAP` etc. in `market/config.py`)
3. Merge w1-w4 on `ticker` (left joins), prefixed as `t_w2.*`, `t_w3.*`, `t_w4.*`
4. Join `fund_mapping` -> adds `etp_category`
5. Join `issuer_mapping` -> adds `issuer_nickname`
6. Join category attributes (LI, CC, Crypto, Defined, Thematic) -> adds `map_*` columns
7. Derive `category_display`, `is_rex`, `primary_category`, `rex_suite`
8. **Deduplicate** by ticker (keeps first) -- this is the critical step that prevents double-counting
9. Build time series: unpivot `aum_1..aum_36` into long format `(ticker, months_ago, aum_value)`

Returns: `{"master": DataFrame, "ts": DataFrame}`

### 2b. Market Sync Writes to SQLite

`webapp/services/market_sync.py` -> `sync_market_data(db)`

| Step | Action | Table |
|------|--------|-------|
| 1 | `build_all()` produces master + time series DataFrames | -- |
| 2 | Create `MktPipelineRun` record (status="running") | `mkt_pipeline_runs` |
| 3 | DELETE all existing rows (full snapshot replace) | `mkt_report_cache`, `mkt_time_series`, `mkt_master_data` |
| 4 | Insert master data (batch 5000) | `mkt_master_data` |
| 5 | Insert time series (batch 10000) | `mkt_time_series` |
| 6 | Compute + cache reports (see Step 3 below) | `mkt_report_cache` |
| 6b | Sync global ETP supplement (non-fatal) | `mkt_global_etp` |
| 7 | Update pipeline run (status="completed"), commit | `mkt_pipeline_runs` |

Column prefixes (`t_w2.`, `t_w3.`, `t_w4.`, `q_category_attributes.`) are stripped during insert. The DB stores flat column names (`aum`, `fund_flow_1week`, `map_li_subcategory`, etc.).

---

## Step 3: Report Pre-Computation

`market_sync._compute_and_cache_reports(db, master_df, run_id)`

1. Invalidates the in-memory report cache (`report_data.invalidate_cache()`)
2. Calls `get_li_report(db=db)` and `get_cc_report(db=db)`
3. Each report function:
   - Checks `mkt_report_cache` table first (cache miss since we just cleared it)
   - Falls through to `_get_cache(db)` -> `_load_from_db(db)`
   - `_load_from_db(db)` reads from `mkt_master_data` and `mkt_time_series` tables
   - Computes the full report dict (KPIs, segments, timelines, issuer breakdowns, etc.)
4. Serializes each report dict as JSON into `mkt_report_cache`

### What `_load_from_db(db)` produces

| Key | Source | Format |
|-----|--------|--------|
| `master` | `mkt_master_data` table | DataFrame, all funds, flat column names |
| `data_aum` | `mkt_time_series` table | Wide DataFrame: dates as index, tickers as columns |
| `data_flow` | -- | Empty DataFrame (flow charts only on hidden report pages) |
| `data_notional` | -- | Empty DataFrame (volume charts only on hidden report pages) |
| `rex_tickers` | `master[is_rex == True]` | Set of ticker strings |
| `data_as_of` | `mkt_pipeline_runs.finished_at` | String: "March 03, 2026" |

### What each report computes

**L&I Report** (`get_li_report`):
- Filters: `etp_category == "LI"` AND `fund_type == "ETF"`
- Splits into two segments: Index/ETF/Basket vs Single Stock (via `map_li_subcategory`)
- Per segment: KPIs, issuer breakdown, top/bottom 10 flows, AUM timeline, REX spotlight
- Category breakdown (Index) and underlier breakdown (SS)

**CC (Income) Report** (`get_cc_report`):
- Filters: `etp_category == "CC"`
- Splits into: Index/ETF/Basket vs Single Stock (via `cc_category`)
- Same segment structure as L&I, plus yield metrics
- Segment tabs: All, Traditional, Synthetic, Single Stock

---

## Step 4: DB Upload to Render

**Step 3.75** in `run_daily.py`: WAL checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`) flushes all pending writes.

**Step 4**: `upload_db_to_render()` sends the SQLite file:

```
POST https://rex-etp-tracker.onrender.com/api/v1/db/upload
  files: {"file": etp_tracker.db}
  headers: {"X-API-Key": <from config/.env>}
```

Render stores it on persistent disk at `/opt/render/project/src/data/etp_tracker.db`.

---

## Step 5: Email Rendering

### Bloomberg Reports (L&I + Income)

`webapp/services/report_emails.py`

`build_li_email(dashboard_url, db)` and `build_cc_email(dashboard_url, db)`:
1. Call `get_li_report(db)` / `get_cc_report(db)` -- reads from `mkt_report_cache` on Render
2. Pass report dict to `_build_report_email()` -- unified builder

**Email layout** (identical for both, Income adds Yield column):

```
Section 1: Single Stock (shown first)
  - KPI Banner (2 rows: market totals + REX KPIs)
  - AUM Timeline Chart (3-year area chart via QuickChart.io)
  - REX Spotlight (top 8 REX products, transposed table)
  - Market Share Bar (top 6 issuers, stacked HTML bar)
  - Issuer Breakdown Table (up to 15 issuers, REX rows green)
  - Category/Underlier Breakdown (horizontal bar + table)
  - Weekly Fund Flows (bi-directional inflow/outflow bars)

Section 2: Index / ETF / Basket
  - Same structure as above
```

Charts: rendered via QuickChart.io (`https://quickchart.io/chart?c=...`) -- no local dependencies.

### Daily Brief

`etp_tracker/email_alerts.py` -> `build_digest_html_from_db(db_session, dashboard_url, since_date, edition)`

Sources: SEC filing tables (Trust, Filing, FundStatus) + Bloomberg market snapshot (via `market_data` service reading from SQLite).

### Weekly Report

`etp_tracker/weekly_digest.py` -> `build_weekly_digest_html(db_session, dashboard_url)`

Sources: Bloomberg data via `get_rex_summary()`, `get_category_summary()` (reads from SQLite) + SEC filing counts from last 7 days.

---

## Step 6: Email Sending

`etp_tracker/email_alerts.py` -> `_send_html_digest(html_body, recipients, ...)`

**Two-path delivery with automatic fallback:**

1. **Azure Graph API** (primary):
   - `webapp/services/graph_email.py`
   - MSAL client credentials flow -> `https://graph.microsoft.com/v1.0/users/{sender}/sendMail`
   - Config: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SENDER` in `config/.env`
   - HTTP 202 = success

2. **SMTP** (fallback):
   - `smtplib.SMTP` with STARTTLS
   - Config: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` in `config/.env`

### Recipients

- Main list: `config/email_recipients.txt` (one email per line, 23 recipients)
- Private list: `config/email_recipients_private.txt` (sent separately)
- Test sends: hardcoded to `relasmar@rexfin.com`

---

## Step 7: Admin Panel Controls

All at `/admin/` (requires admin login).

| Action | Route | Method |
|--------|-------|--------|
| Preview L&I | `/admin/reports/preview-li` | GET |
| Preview Income | `/admin/reports/preview-cc` | GET |
| Test L&I (to Ryu) | `/admin/reports/send-test-li` | POST |
| Test Income (to Ryu) | `/admin/reports/send-test-cc` | POST |
| Send L&I to all | `/admin/reports/send-li` | POST |
| Send Income to all | `/admin/reports/send-cc` | POST |
| Preview Daily | `/admin/digest/preview-daily` | GET |
| Send Daily to all | `/admin/digest/send` | POST |
| Preview Weekly | `/admin/digest/preview-weekly` | GET |
| Send Weekly to all | `/admin/digest/send-weekly` | POST |

---

## Local vs Render Behavior

| Concern | Local | Render |
|---------|-------|--------|
| Report computation | Happens in `sync_market_data()` | Never -- served from `mkt_report_cache` |
| `_ON_RENDER` flag | `False` | `True` |
| `get_li_report(db)` on cache miss | Falls to `_get_cache(db)` -> `_load_from_db(db)` | Returns `{"available": False}` |
| Chart rendering | QuickChart.io (remote) | QuickChart.io (remote) |
| `data_flow` / `data_notional` | Empty in DB path (populated only in legacy `_load_all`) | Always empty |

---

## Data Integrity

**Single source of truth**: `bloomberg_daily_file.xlsm` -> `data_engine.build_all()` -> SQLite.

All views (home page, market intelligence, email reports) read from the same SQLite tables. The previous `_load_all()` file path in `report_data.py` (which read Excel directly and caused ~1% AUM discrepancy from duplicate rows) is now bypassed when a DB session is available.

**Deduplication**: `data_engine.py` deduplicates by ticker before writing to `mkt_master_data`. The 81 tickers with multiple category mappings (mostly crypto products like BITX appearing in both LI and Crypto categories) are resolved to one row each.

---

## Key Files

| File | Role |
|------|------|
| `scripts/run_daily.py` | Orchestrator: pipeline + sync + upload + emails |
| `webapp/services/data_engine.py` | Builds master + time series DataFrames from Excel/CSV + rules |
| `webapp/services/market_sync.py` | Writes DataFrames to SQLite, pre-computes report cache |
| `webapp/services/report_data.py` | Report computation (L&I, CC). `_load_from_db()` reads SQLite |
| `webapp/services/report_emails.py` | HTML email rendering (V3 unified segment format) |
| `etp_tracker/email_alerts.py` | Email sending (Azure Graph + SMTP fallback) |
| `etp_tracker/weekly_digest.py` | Weekly report rendering + sending |
| `webapp/services/graph_email.py` | Azure Graph API email client |
| `webapp/routers/admin.py` | Admin panel routes (preview, test, send) |
| `market/config.py` | DATA_FILE resolution, column rename maps |
| `config/.env` | SMTP + Azure + API credentials |
| `config/email_recipients.txt` | Recipient list |
