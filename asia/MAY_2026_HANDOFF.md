# May 2026 Asia Report — Session Log + Next-Month Handoff

**Closing this session:** Apr-2026 reports finalized and shipped (3 PDFs + ledger).
**Picking up next session:** May-2026 report cycle. Use this doc + `MONTHLY_RUNBOOK.md` +
`ASIA_REPORT_PROCEDURE.md` to resume.

---

## Part 1 — What we did this session (Apr-2026 cycle)

### 1.1 Database (Asia fact table)
- **April load completed.** Started with only 9 of 14 exchanges loaded ($1,214M). Ran the
  canonical `load_month.py --month 2026-04 --apply`; the 9 reported exchanges reproduced
  exactly, plus held vendors (Futu/MooMoo, MooMoo regions, SYFE) repriced per methodology.
- **Reverse-split repricing bug — fixed.** Six funds split between Mar→Apr (SMUP 29×, EOSU
  40×, SNDU, GLXU, BERZ, CORD). The raw-price reprice (pre-split shares × post-split NAV)
  was inflating held positions by the split ratio — **SMUP Asia overstated $35.9M → corrected
  to $11.7M**. Switched held positions to **fund-proportional reprice + carried shares**
  (split-immune; also correctly produces zero flows for held positions). Net April delta:
  $1,421M → $1,397.92M.
- **Oriental Harbour zeroed in March (not Feb).** Per Scott's "mark sell as 3/31" + Grace's
  13F-lag confirmation; Oriental Harbour's Q1 2026 13F-HR shows zero REX exposure. Surgical
  delete of exch_id=10 from month_id=14. March DB total: $1,233.67M → $1,169.82M.
  Feb intentionally left at $74.99M (13F is a Q1 event, not Q4).
- **SYFE April hold-repriced** (no broker file, no summary file — held FEPI position
  $1.25M → $1.35M at April NAV).
- **etp.name populated** for 96 of 96 funds (was NULL) via Bloomberg `w1` Fund Name column.

### 1.2 Pipeline (canonical ordering)
- **Found and fixed:** `audit_report.py` was overwriting the build's input
  (`enriched_report_data.json`) with a DB-global-denominator, narrative-less version. That
  produced the "Report Month" cover bug and the wrong "% in Asia" (20.0% instead of 19.6%).
- **Fix:** patched `/asia-report` skill and ran it ourselves as **generate → enrich → audit
  (with `--output-enriched _audit_check.json` temp file) → build**. Always run `enrich` last.
- **Postgres port** 5433 access restored (was blocked early in the session).

### 1.3 Report (`report_v15.html`) — bugs found and fixed
| # | Bug | Fix |
|---|---|---|
| 1 | Cover subtitle = literal "Report Month" | `narrative.month_long` from enrich → "April 2026" |
| 2 | Cover "Total REX AUM $7.0B" / "% in Asia 20.0%" | Use BBG denominator → $7.1B / 19.6% |
| 3 | p2 title: hardcoded "What Drove the $228M Decline?" | Sign-aware → "Increase" for positive months |
| 4 | p2 market insight: "Broad equity selloff… T-REX declined… absorbed" | Sign-aware rebound/gained/added |
| 5 | p2 flow insight: "$38.8M flowing INTO MicroSectors" | Sign-aware → "out of MicroSectors" |
| 6 | `momSign()` returned '' for negatives (color-only) | Return '-' so flows/market show explicit minus |
| 7 | ASIA MOM % double-minus regression from #6 | Use inline `(v >= 0 ? '+' : '')` for the % column |
| 8 | EOSU in `GI_TICKERS` (it's T-REX 2X EOSE) | Removed — G&I bullet $21.0M/12 → $19.3M/11; double-count gone |
| 9 | EPI/G&I historical country charts showed combined Income mix | Split `fetch_suite_country_6m` by ticker so each page shows its own 6-month history |

### 1.4 Data verified end-to-end
Source (Grace KSD file $1,040.71M) = DB exchange row, exact. Headline = sum funds = sum
countries = sum exchanges = sum suites = **$1,397,920,344.19** (to the penny). Per-suite
and headline `flows + market = ΔAUM`. Income = EPI + G&I. April prices = Bloomberg
`data_nav` @ 4/29 (80 of 80 funds, zero mismatch).

### 1.5 Universe sanity (Apr-2026)
- **Launches:** zero REX launches in April. Most-recent before April were PAAU and SNDU
  (both 3/12), both captured.
- **Delistings:** BMAX (REX Bitcoin Corporate Treasury Convertible) delisted 4/13 — correctly
  excluded from April. 7 T-REX funds delisted 3/16 (ARMU, AXUP, BKNU, BULU, DKUP, ETQ, PXIU)
  — correctly absent. No missed delistings.

### 1.6 Deliverables shipped
1. `reports/2026-04/REX_Asia_Report_Apr26.pdf` (full, 9pp)
2. `reports/2026-04/REX_TREX_Asia_Report_Apr26.pdf` (T-REX cut, 3pp)
3. `reports/2026-04/REX_MicroSectors_Asia_Report_Apr26.pdf` (MicroSectors cut, 3pp)
4. `REX_Asia_Monthly_Log.xlsx` — clean name, new **Summary** landing sheet (KPIs, by-suite,
   by-country, top-10), April tab added.
- New explanatory `ASIA_REPORT_PROCEDURE.md` (data model + methodology + gotchas + glossary).

---

## Part 2 — What to do next month (May-2026 cycle)

### 2.1 Universe updates BEFORE running
- **Add AXTU** to the `etp` table — *T-REX 2X Long AXTI Daily Target ETF*, launched 2026-05-05.
  Bloomberg `w1` has it; it just isn't in our DB yet. Without this, May's global universe is
  missing one fund.
- **SOLX and XRPK** (T-REX 2X SOL / XRP) delisted **2026-05-04**. Bloomberg `w1` Delist Date
  drives auto-exclusion via `refresh_all_months.py`, so they will drop from May automatically.
  Just verify after the refresh that they're absent from May's data.
- Watch for additional May launches/delistings — re-run the launches/delistings check used
  this session (filter w1 by inception/delist date in 2026-05, cross-check vs etp + m16).

### 2.2 Grace's May data — expect mid-June
- **Asset report summary May 2026.xlsx** — *chase Grace if missing.* It was missing for April,
  which caused MooMoo SG/MY/JP to fall back to repriced (correct but flagged as
  `scaled_aggregate` due to a label bug in `load_month.py`, see §2.5).
- **MooMoo MY & SG broker file** — was missing in April. If still missing in May, fallback
  will reprice from prior shares (acceptable).
- **MooMoo Japan broker file** — was missing in April; same fallback.
- **Futu/MooMoo HK** — `frozen_permanent`, no file expected. Will reprice per methodology.
- **13F Q2 (~mid-August):** confirm Oriental Harbour still $0. No action needed unless they
  re-establish a position.

### 2.3 Run the pipeline (in this exact order)
Use the patched `/asia-report` skill, or run by hand:
```
python3 generate_report_data.py --month 2026-05 --output report_data.json
python3 enrich_report_data.py   --month 2026-05 --input report_data.json --output enriched_report_data.json
python3 audit_report.py report_data.json --output-enriched _audit_check.json   # TEMP — must not clobber enriched
NODE_PATH=... node build_reports.js 2026-05 May26 enriched_report_data.json
python3 build_excel_log_v2.py        # produces REX_Asia_Monthly_Log.xlsx (Summary sheet auto-refreshes)
```
Backup the DB before any `--apply` (the loaders do it automatically).

### 2.4 Reverse splits — keep watching
The leveraged book splits often. Detect: month-end price ratio > 2× or < 0.5×. Held positions
are now protected (fund-proportional reprice), but verify after each load that the split
funds' held positions aren't inflated. Look at `_refresh_all_months_diff.txt` for the
"top 5 writes per month by AUM delta" — anything outsized on a held vendor for a split fund
is a smell.

### 2.5 Tech-debt items (do when convenient)
- **`scaled_aggregate` label is cosmetically wrong** when the fallback fires (it's actually
  a `repriced` MtM). Doesn't affect values; fix in `load_month.py`'s active_aggregate_only
  branch.
- **Hardcoded suite KPI strip fallbacks** in `report_v15.html` (lines ~508–727) carry Feb
  values like $699M, $489M, +5.1pp. JS overwrites them so they never render — but they're
  fragile. Worth blanking some day.
- **Pipeline ordering enforcement:** consider making `audit_report.py` default its
  `--output-enriched` to a non-clobbering name so the bug we hit can't recur.

### 2.6 What May's narrative is likely to discuss
- **Asia rebound continuation vs pullback** — April was +21.5% MoM driven by a leveraged-book
  rally; watch whether that holds.
- **Korea concentration** — Korea is 75.9% of Asia AUM. Trend has been rising; flag if it
  flattens or reverses.
- **Oriental Harbour effectively gone** — Hong Kong share will continue to compress as Futu
  is the only remaining HK 13F position (and it's frozen/repriced).
- **G&I suite growth** — small but rising; TSII + NVII drive it, both Korea-heavy.

### 2.7 Reference docs (read in this order if picking up cold)
1. `ASIA_REPORT_PROCEDURE.md` — the *why* (data model, methodology, gotchas).
2. `MONTHLY_RUNBOOK.md` — the *how* (10 paste-and-run steps).
3. `POSTMORTEM.md` — the *what-went-wrong-before* (Feb-2026 incident).
4. `_dbfix_log_2026-05-28.md` — DB corrections + Apr generation details.
5. **This doc** — Apr session log + May plan.

---

## Apr final tie-out (locked)
- Asia AUM: **$1,397.92M** (19.55% of REX $7.15B BBG)
- MoM: **+$228.1M (+19.5%)** — market move +$242.4M / est. flows −$14.3M
- Suites: T-REX $804.0M (+$22.7M flows), MicroSectors $453.9M (−$38.8M flows),
  EPI $119.9M, G&I $19.3M, Other / Osprey ~$0.8M.
- Countries: Korea $1,061M (75.9%) · Japan $138M (9.9%) · HK $132M (9.4%) ·
  Singapore $43M (3.1%) · Taiwan $15M (1.1%) · Malaysia $8M (0.6%).
