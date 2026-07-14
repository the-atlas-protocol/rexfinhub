# Development Plan — Mar 2026 NAV Fix + April Forward

**Created**: 2026-05-05 (current Claude session)
**Status**: Mar report adjustments pending; April fix scaffolded
**Pickup**: same Claude chat session (atlas-rexasia-2026-04-24)

---

## Background

On 2026-05-05 Ryu fixed Mar 31 NAV values in `data_nav` sheet of `bloomberg_daily_file.xlsm`.
Verification revealed:

- **`data_nav` Mar 31 values are now CORRECT** (match yfinance close to within 0.5%)
- **`data_price` Mar 31 values are CORRUPT** for 78 of 96 REX tickers (gaps 0.5–96%)
- **`microsector` Mar 31 AUM values are CORRECT** (verified via market-move sanity)

Pattern looks like Bloomberg pulled a wrong-tick / partial-day snapshot for `data_price`
on Mar 31 only; neighbor days (Mar 27, 30 / Apr 1, 2) match yfinance perfectly.

## Constraints

- Ryu cannot fix Bloomberg `data_price` Mar 31 right now
- Need to ship March report with the data we have
- Cleanup for April onwards

## Affected vs Unaffected (current March report)

**Unaffected** (correct):
- Total Asia AUM ($1,224M)
- Total REX AUM ($6,062M, matches Seamus's email within $1M)
- % in Asia headline (20.19%)
- Per-fund Asia AUM, Global AUM, % of Global

**Affected** (wrong, needs fix):
- `etp_monthly_fund.price_usd` for month_id=14 — 78 tickers, 3–96% off
- Shares-invariant reprice for frozen vendors (~$200M of stale-bucket positions)
- Implied shares displayed in Excel ledger

## EOSU / SMUP — Special Investigation

Both show 96% gap in BOTH NAV and Price. Likely reverse stock split, but
**must be verified not assumed**.

Action when picked up:
```python
import yfinance as yf
yf.Ticker("EOSU").actions  # check splits column
yf.Ticker("SMUP").actions
```

If split: apply factor to historical Asia positions (scaled).
If not split: freeze at last-known-good, flag for Bloomberg cleanup.

## Plan A — Today's March Adjustment

Ordered execution. Each step backs up DB, single transaction.

1. Verify EOSU/SMUP via yfinance `actions`. Document finding.
2. Modify `refresh_all_months.py`:
   - Primary price source: `data_nav` (walk-back ≤ 7 days)
   - Fallback: `data_price` (walk-back ≤ 7 days)
   - Special case: EOSU/SMUP per investigation outcome
3. Run `refresh_all_months.py --apply` (rewrites all 14 months' prices using NAV; Mar 31 flips from $16.42 TSLT to $17.02 etc.)
4. Run `load_month.py --month 2026-03 --apply` (re-applies shares-invariant repricing using corrected prices)
5. `generate_report_data.py --month 2026-03`
6. `enrich_report_data.py`
7. `comprehensive_audit.py` + `audit_deep.py`
8. `build_reports.js 2026-03 Mar26 enriched_report_data_mar.json`
9. `build_excel_log_v2.py`
10. Copy PDFs to Downloads

## Plan B — April Forward (Permanent)

### New artifacts

- **`verify_prices.py`** — pulls yfinance Mar 31 / Apr 30 / etc closes for full REX universe;
  compares to `data_price` and `data_nav`; emits flag list
- **`config/fund_overrides.yaml`** — per-ticker overrides for splits, frozen pricing, manual values
- **`refresh_all_months.py`** updated to:
  - Read `data_nav` first, `data_price` as fallback
  - Apply `fund_overrides.yaml` adjustments
- **`MONTHLY_RUNBOOK.md`** — add "pre-flight price audit" step before pipeline runs

### Sample `fund_overrides.yaml` entry

```yaml
overrides:
  EOSU:
    split:
      effective_date: 2026-XX-XX
      factor: 25.0  # if confirmed reverse split
      source: "yfinance actions confirmed"
  SMUP:
    split:
      effective_date: 2026-XX-XX
      factor: 25.0
      source: "yfinance actions confirmed"
```

### Methodology page

Already updated to say:

> "AUM is marked-to-market based on the latest month-end NAV"

Aligns with NAV-as-primary code path.

## Open Questions for Next Pickup

1. EOSU / SMUP — reverse split? (yfinance answer)
2. Any other tickers with split-style 90%+ gaps that we missed?
3. After NAV-based refresh, does Total Asia AUM still match Seamus to within $1M?
4. Should `MSTZ`, `NVDQ`, `TSLZ` (inverse 2x) get special handling in NAV vs price?
   Inverse leveraged products have path-dependent NAV that can drift from market price even
   on clean days.

## Status File Locations

- This plan: `C:/Projects/rex-asia/DEVELOPMENT_PLAN_MAR2026_NAV_FIX.md`
- Audit logs: `_full_review.txt`, `_nav_nonlev.txt`, `_nav_date_check.txt`
- Verification scripts: `_full_scale_review.py`, `_nav_nonleveraged_check.py`, `_nav_date_check.py`
- Most recent backup: `rex_asia_pre_load_202603.backup`
- Vendor config: `config/vendor_status.yaml`
- Monthly runbook: `MONTHLY_RUNBOOK.md`
