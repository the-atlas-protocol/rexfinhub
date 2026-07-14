# REX Asia Report — February 2026 Postmortem

## What Happened

The February 2026 REX Asia Report went through multiple rounds of corrections over March 23-24 before reaching a clean state. Data integrity issues were discovered incrementally rather than caught upfront, resulting in several versions being sent to stakeholders before the final report.

## Errors Made

### 1. Trusted inherited data without verification
Sean's database was restored and used as-is. No independent verification was run against Bloomberg or Grace's source data. The database contained:
- A SQL view (`asia_family_rollup`) that computed global AUM using only Asia-active funds, deflating the denominator
- Global AUM values (`etp_monthly_fund`) that didn't match Bloomberg for 259 fund-month combinations
- Price data with 221 mismatches vs Bloomberg
- Only 84 of 93 REX funds tracked

### 2. Hardcoded derived values copied between report versions
Flows, market move, MoM, suite KPIs, country shares, timeline data, and fund tables were all hardcoded in the HTML. When the database was corrected, these values were not recalculated. The `fix_flows.py` script initially only updated 3 of 7 fields per fund (mom, flows, globalMom) — leaving aum, total, and pct stale.

### 3. Piecemeal fixing instead of systematic audit
Each time a problem was found, only that specific value was fixed. This led to:
- Flows fixed but global AUM still wrong
- Global AUM fixed but timeline data still stale
- Timeline fixed but country shares still placeholder
- Country shares fixed but appendix total/pct still from old data
- Appendix total row showing wrong percentage (20.2% vs 19.6%)
- Others row missing, then showing wrong count in suite reports

### 4. Repeatedly claimed "all verified" when it wasn't
Multiple audit scripts were written and passed, but they only checked what was explicitly tested. The appendix `total` field (Global AUM per fund) was never included in any audit until Ryu flagged it. The COUNTRY_SHARES array had placeholder values (all months showing identical ~72% Korea) that no audit caught because the scan wasn't looking at that array.

### 5. Bloomberg daily file not used as single source of truth
The report drew global AUM from Sean's database instead of the Bloomberg daily file. When Sean's values diverged from Bloomberg (especially for ETNs where Bloomberg reports notional values), the errors propagated silently.

## Root Causes

1. **Sean's `asia_family_rollup` view** only summed global AUM for Asia-active funds. This was a design choice for his Power BI dashboard but wrong for flow calculations.

2. **ETN AUM in Bloomberg's `data_aum` sheet is unreliable.** Bloomberg reports notional/indicative values for ETNs, not actual AUM. The `microsector` sheet has the correct values and must always overwrite.

3. **No single source of truth.** Data came from Sean's DB, Grace's emails, Bloomberg terminal, and yfinance — with no clear hierarchy or automated reconciliation.

4. **Hardcoded values are fragile.** Any correction to the database requires manually updating every data array in the HTML. Missing even one array means the report shows inconsistent numbers.

## What Must Be Done for March

### Before anything else
1. Pull Bloomberg daily file from SharePoint (use Graph API download script)
2. Run `rebuild_global_aum.py` to update `etp_monthly_fund` from Bloomberg
3. Load Grace's March broker data
4. Run repricing for quarterly reporters
5. Run `fix_flows.py` — this now updates ALL fields (aum, total, pct, mom, flows, globalMom)
6. Run `generate_report_data.py`
7. Run `audit_report.py` — must show 0 mismatches
8. Run `full_audit.py` — must show 0 mismatches across all 13+ months
9. Run `exhaustive_audit.py` — must show 0 issues
10. Run `final_cross_calc.py` — independent calculation verification
11. Run `find_all_stale.py` — catch any remaining hardcoded values
12. Generate reports with `build_reports.js`
13. Visual check of every page in all 3 reports

### Data sources (in priority order)
- **Asia AUM**: Grace's monthly email + broker attachments (authoritative)
- **Global AUM (ETFs)**: Bloomberg `data_aum` sheet, month-end row, values in $M
- **Global AUM (ETNs)**: Bloomberg `microsector` sheet, overwrites `data_aum` for all 20 MicroSectors ETNs, values in raw $
- **Prices**: Bloomberg `data_price` sheet
- **Fund universe**: `rex_suite_mapping.csv` (93 REX ETPs excl Osprey trusts)

### Key rules
- **ETN data in `data_aum` is wrong.** Always overwrite with `microsector` sheet.
- **Never use Sean's legacy views.** They are in the `legacy` schema. Query raw tables only.
- **`fix_flows.py` must update ALL fields** — aum, total, pct, mom, flows, globalMom. Not just flows.
- **The Others row** in the full report appendix captures non-Asia REX funds' global AUM. Suite reports do not show this row.
- **US tickers before LN tickers** when reading `data_aum` — FEPI US must not be overwritten by FEPI LN.
- **The `pct` trend line** on page 1 uses Bloomberg total (all 93 funds) as denominator, not the DB total (84 funds).

### Grace data checklist
- [ ] All monthly reporters present (KSD, SBI, Rakuten, Monex, Matsui, Asset Plus)
- [ ] Quarterly reporters identified (Futu/MooMoo, Oriental Harbour, ViewTrade)
- [ ] Request fund-level Futu/MooMoo breakdown (not just total) to close the $16.5M gap
- [ ] Check for new exchanges or markets
- [ ] Verify country totals match Grace's summary

### Audit checklist (run ALL of these, in order)
1. `full_audit.py` — DB vs Bloomberg, all months, all fields (must be 0 mismatches)
2. `audit_report.py` — flow balances, AUM match, absurd flows, smell test, Grace cross-check
3. `exhaustive_audit.py` — every hardcoded array vs DB
4. `find_all_stale.py` — scan for any remaining stale values
5. `final_cross_calc.py` — independent recalculation of flows, percentages, totals
6. Internal consistency check — APPENDIX totals = DATA timeline, APPENDIX flows = GAINERS/LOSERS, suite sums = KPIs, COUNTRIES sum = total
7. Visual review of all pages in all 3 reports — check for overflow, alignment, methodology placement

### Known limitations to carry forward
- Futu/MooMoo HK: $16.5M gap between Grace ($168.3M) and database ($151.8M). Need fund-level breakdown.
- SYFE: $1M verbal report, no documentation
- Repriced positions (~18% of total): estimates until quarterly data arrives
- Asia AUM months 1-12: from Sean's original load of Grace's prior emails, cannot independently verify

## Future: Eliminate hardcoded values
The March report should wire `report_data.json` into the HTML so all derived values are computed at render time. No more hardcoded flows, market move, MoM, country shares, or timeline data. One source, one computation, zero copy-paste errors. This is the single most important improvement to make.
