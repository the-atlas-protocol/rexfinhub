# REX Asia Monthly Runbook

**Workflow for running a monthly Asia report via terminal session (like this Claude session).**

Designed for paste-and-run. Each step is a single command. The agent running the session should pause after each step if anything looks wrong.

---

## Prerequisites (one-time per machine)

- Postgres running on `localhost:5433` with `rex_asia` DB (use `./pgsql/bin/pg_ctl.exe -D pgdata -l pg.log -o "-p 5433" start`)
- Graph API credentials in `C:/Foundry/Rexfinhub/config/.env` (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET)
- Python packages: `yaml`, `openpyxl`, `psycopg2`, `pandas`, `yfinance`, `pypdf`, `playwright` (and `chromium` installed)
- Node packages: `playwright`

---

## Monthly workflow — 10 steps

Replace `YYYY-MM` with the target month (e.g., `2026-04`).

### 1. Drop Grace's email into Downloads, then extract attachments
```bash
python extract_email_attachments.py "C:/Users/RyuEl-Asmar/Downloads/Asia Asset reporting - <MONTH> <YEAR> - data collection.eml" grace_data/YYYY-MM
```
**Expect**: N broker .xlsx files saved to `grace_data/YYYY-MM/`. Verify filenames match expected broker set.

### 2. Pull the Bloomberg daily file (Graph API)
```bash
python pull_bloomberg.py
```
**Expect**: file at `C:/Users/RyuEl-Asmar/REX Financial LLC/.../bloomberg_daily_file.xlsm` with today's mtime, >25 MB.

### 3. Audit Grace's summary against vendor config
```bash
python audit_grace_vs_expected.py --month YYYY-MM
```
**Expect**: exit 0 "CLEAN". If issues flagged:
  - `UNBLOCK?` on waiting vendor with new data → **update `config/vendor_status.yaml`** (status → `active_aggregate_only` or similar)
  - `UNEXPECTED_VALUE` on zeroed vendor → investigate, likely needs status change
  - `UNKNOWN_VENDOR` → new vendor, add to config
  - `MISSING` on active → Grace delayed; decide whether to ship partial or wait
  Rerun audit after each config edit until clean.

### 4. Add calendar_month row if first time for this month
```bash
./pgsql/bin/psql.exe -h localhost -p 5433 -U postgres -d rex_asia -c \
  "INSERT INTO calendar_month (month_id, month_end) VALUES (<NEXT>, '<YYYY-MM-DD>') ON CONFLICT DO NOTHING;"
```
Find `<NEXT>` via `SELECT MAX(month_id)+1 FROM calendar_month;`. Use last trading day of month as `month_end` if you want, but calendar-end is fine too (loader handles weekends).

### 5. Refresh global AUM (Bloomberg → etp_monthly_fund) for all months
```bash
python refresh_all_months.py              # dry-run first
python refresh_all_months.py --apply      # apply after reviewing _refresh_all_months_diff.txt
```
**Reads W1 for lifecycle** (inception/delist dates). Applies microsector overwrite with walk-back for weekend dates.
**Safety**: takes a backup automatically, single transaction, aborts if Asia sums change.

### 6. Load Asia vendor data (broker files → etp_exchange_monthly_aum)
```bash
python load_month.py --month YYYY-MM             # dry-run
python load_month.py --month YYYY-MM --apply     # apply
```
**Reads `config/vendor_status.yaml`** to decide per-vendor behavior. Backs up DB first.

### 7. Generate report data + enrich
```bash
python generate_report_data.py --month YYYY-MM --output report_data_<MMM>.json
python enrich_report_data.py --month YYYY-MM --input report_data_<MMM>.json --output enriched_report_data_<MMM>.json
```

### 8. Comprehensive audit
```bash
python comprehensive_audit.py
```
**Expect**: 97+ PASS, 0 FAIL. Address any FAILs before proceeding.

### 9. Deep audit (optional but recommended)
```bash
python audit_deep.py
```
**Expect**: 49+ PASS, 0 FAIL. Verifies raw broker files → DB, formula re-computation, cross-page consistency.

### 10. Build PDFs + Excel
```bash
NODE_PATH="C:/Users/RyuEl-Asmar/AppData/Roaming/npm/node_modules" node build_reports.js YYYY-MM <MonthLabel> enriched_report_data_<MMM>.json
cp reports/YYYY-MM/*.pdf reports/final/YYYY-MM/
python build_excel_log_v2.py
```
Replace `<MonthLabel>` with e.g. `Apr26`. Output:
- `reports/final/YYYY-MM/REX_Asia_Report_<Label>.pdf`
- `reports/final/YYYY-MM/REX_TREX_Asia_Report_<Label>.pdf`
- `reports/final/YYYY-MM/REX_MicroSectors_Asia_Report_<Label>.pdf`
- `REX_Asia_Monthly_Log_v2.xlsx`

---

## Event-specific handling (config changes)

Edit `config/vendor_status.yaml` when any of these happen:

| Event | YAML change | Example |
|---|---|---|
| New fund launches | Nothing — W1 handles it automatically on next refresh | APHU launched 2026-02-18 |
| Fund is delisted | Nothing — W1 "Delist Date" is authoritative, refresh reads it | ETQ delisted 2026-03-16 |
| Vendor's portfolio emptied (sold all shares) | `status: zeroed`, `since: <date>`, `reason: <note>` | Asset Plus DRNZ sold |
| Vendor structurally can't report | `status: frozen_permanent`, `methodology: shares_invariant_reprice` | Futu HK no contract |
| Vendor transitions from quarterly → monthly | `cadence: monthly`, update `parser:` and `file_pattern:` | MooMoo Japan (Mar 2026) |
| Waiting vendor sends data for the first time | `status: active` (or `active_aggregate_only` if Grace gives only aggregate) | MooMoo SG/MY (Apr 2026 audit discovered) |
| Grace's file structure changes | Update parser in `load_month.py` + possibly audit mapping | — |

**Methodologies**:
- `shares_invariant_reprice`: carry prior-month shares, multiply by current price. Flow = 0. For frozen/waiting.
- `scaled_to_grace_aggregate`: allocate prior-month shares scaled to match Grace's current aggregate. For vendors where Grace provides only a total, no per-fund breakdown.

---

## Rollback

Every `load_month.py --apply` and `refresh_all_months.py --apply` creates a backup first.

To roll back:
```bash
pg_restore -h localhost -p 5433 -U postgres -c -d rex_asia <backup_file>
```

Backup naming:
- `rex_asia_pre_load_<YYYYMM>.backup` — before `load_month --apply`
- `rex_asia_pre_month_refresh_<YYYYMMDD>_<HHMM>.backup` — before `refresh_all_months --apply`

Full file snapshot (nuclear option) was also taken: `pgdata_snapshot_20260414.tar.gz` (14 MB).

---

## Known quirks

- **Bloomberg daily file has no weekend rows in `microsector` sheet**. `refresh_all_months.py` walks back to Friday. Do not revert.
- **ETN AUM in `data_aum` is NOTIONAL, not actual** — always overwritten by `microsector` sheet values for the 20 MicroSectors tickers. Do not skip the overwrite.
- **Grace's `Mastui` typo** — she writes "Mastui" (not "Matsui"). Our SRC_NAME_MAP handles both.
- **Futu HK Feb values were repriced** in the DB — `scaled_to_grace_aggregate` on MooMoo SG/MY computes prior shares from stored AUM/price, which is correct. Don't trust the `shares_outstanding` column directly for quarterly vendors.
- **Total REX AUM authoritative source**: the number `$6,062.34M` we compute now matches Seamus's internal email within $1M. If a big gap opens (>$100M), check `data_aum` freshness or pending fund launches.
- **Historical context preserved**: `etp_exchange_monthly_aum` is **never retroactively modified**. Monthly loads only write the target month. Delisted funds still appear in historical months' Asia positions.

---

## File reference

| File | Purpose |
|---|---|
| `config/vendor_status.yaml` | Per-vendor lifecycle config (status, cadence, methodology) |
| `extract_email_attachments.py` | Email .eml → attachment files |
| `pull_bloomberg.py` | Graph API pull, mirrors to OneDrive + local |
| `audit_grace_vs_expected.py` | Cross-checks Grace's summary against YAML, flags issues |
| `refresh_all_months.py` | Refreshes `etp_monthly_fund` from Bloomberg, reads W1 for lifecycle |
| `load_month.py` | Loads `etp_exchange_monthly_aum` from broker files, reads YAML |
| `generate_report_data.py` | DB queries → `report_data.json` |
| `enrich_report_data.py` | Adds headlines, suites, per-fund derivations, narrative |
| `build_reports.js` | Playwright PDF render of `report_v15.html` with REPORT_DATA injected |
| `build_excel_log_v2.py` | Generates the per-month Excel ledger |
| `comprehensive_audit.py` | Layer-by-layer reconciliation audit |
| `audit_deep.py` | Deep audit: raw files → DB → formulas → charts |

---

## When something goes wrong

1. **First step**: run `python audit_deep.py`. It will tell you which layer is broken.
2. **If Bloomberg data looks off**: check `bloomberg_daily_file.xlsm` mtime. If stale, `python pull_bloomberg.py`.
3. **If a vendor is missing**: run `python audit_grace_vs_expected.py --month YYYY-MM`. It will flag MISSING/UNKNOWN.
4. **If totals don't match Seamus's Today's Numbers email**: check `data_aum` sheet, might have a raw AUM for an ETN that the microsector overwrite didn't catch (walk-back date wrong?). Rerun `refresh_all_months.py`.
5. **If PDF shows stale text**: the HTML template has a hardcoded placeholder that's not being overwritten by JS. Grep `report_v15.html` for specific value.
6. **If database is in a weird state**: restore from the most recent `rex_asia_pre_*.backup`.
