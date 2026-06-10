# Stock Recs Upgrade A1 — Methodology Bug Fixes + Render-Time Competitor Guard

**Branch**: `audit-stockrecs-A1-bugs`
**Date**: 2026-05-11
**Owner**: implementer agent (Wave A1)
**Files touched** (3, sole-owner):
- `screener/li_engine/analysis/whitespace_v3.py`
- `screener/li_engine/analysis/whitespace_v4.py`
- `screener/li_engine/analysis/weekly_v2_report.py`

## Problem

Stage 1 audit (`docs/audit_2026-05-11/stock_recs_audit.md`) flagged three concrete
defects in the weekly L&I recommender pipeline:

1. **insider_pct sign error** — `whitespace_v3.WEIGHTS` coded insider ownership at
   `+0.08` (positive contribution to composite score). Audit directive says v2
   docs treat insider ownership as a NEGATIVE predictor; sign should flip.
2. **Subtitle lie in `weekly_v2_report.py`** — the report subtitle for the
   filing-recommendations section claims "no competitor 485APOS in last 180d",
   but the upstream filter (`whitespace_v4.apply_whitespace_filter`) merely
   *computed* and joined `n_competitor_485apos_180d` onto the universe and
   never used it as an actual filter.
3. **AMPX-class race condition** — `weekly_v2_report.load_launch_candidates()`
   returns underliers from a parquet that's rebuilt on a cadence; between
   rebuilds, a competitor 2x can launch on a recommended underlier (e.g.
   Defiance launched AMPU once AMPX was on the launch list). No render-time
   re-check existed.

## Fix Summary

### 1. `whitespace_v3.py` — sign flip on insider_pct

Changed `WEIGHTS["insider_pct"]` from `+0.08` to `-0.08` and updated the
driver-component label so report attributions read "low insider ownership
(negative weight)" instead of "insider ownership". Added a verbose comment
citing the Wave A1 directive AND flagging that pre-existing code documents
insider_pct as a validated POSITIVE signal — see Devil's-Advocate Note below.

### 2. `whitespace_v4.py` — actually use the 180d gate

Added a second filter pass inside `apply_whitespace_filter` that drops any
ticker with `n_competitor_485apos_180d > 0`. Runs after the cc-based
exclusion, so the existing live-product filter is preserved. Falls through
gracefully (with a WARNING) if the column is absent.

### 3. `weekly_v2_report.py::load_launch_candidates()` — render-time guard

Added a sqlite re-check at every report build. Queries `mkt_master_data`
for active competitor 2x+ products with a non-NULL `map_li_underlier`
(verified column names with `PRAGMA table_info`), normalises to the
launch_candidates index format (strip " US" suffix, uppercase), and drops
any candidates that intersect. Wrapped in try/except so a SQL hiccup
never breaks the report — failure logs a warning and lets the parquet
data through.

## Verification

### Sample composite_score before/after sign flip

Re-scored 5 of the report's pinned filing-candidate tickers using the
existing `insider_pct_z` values from `whitespace_v4.parquet`. Delta =
`-2 * 0.08 * insider_pct_z` (the sign flip).

| ticker | insider_pct (%) | insider_z | old_score | new_score | delta   |
| ------ | ---------------:| ---------:| ---------:| ---------:| -------:|
| LWLG   | 2.1             | -0.211    | +1.800    | +1.834    | +0.034  |
| SLS    | 1.2             | -0.324    | +0.926    | +0.978    | +0.052  |
| BW     | 3.9             | +0.028    | +0.522    | +0.518    | -0.005  |
| KOD    | 4.2             | +0.075    | +0.701    | +0.689    | -0.012  |
| NEXT   | 4.7             | +0.127    | +0.155    | +0.135    | -0.020  |

Deltas are small (weight is 8% and most insider_z values cluster near
zero), so ranking shifts will be modest. Tickers with very high or very
low insider ownership move the most.

### 180d competitor 485APOS gate impact

On today's `whitespace_v4.parquet`: **0 of 1,477 rows** would be excluded
by the new gate. The cc-based primary filter (which considers any active
or filed competitor product) already catches everything currently in the
180d 485APOS set. The new gate is therefore a **belt-and-suspenders**
defence — it makes the report subtitle truthful and protects against
future regressions in the cc-builder.

### Render-time competitor guard

- `mkt_master_data` query returns **208 mapped active competitor 2x+
  underliers** today.
- Top-50 `launch_candidates.parquet` candidates with `has_signals=True`:
  zero overlap with that set today (already filtered upstream).
- Smoke test: `load_launch_candidates(15)` returns
  `[DOCN, FSLY, AMC, FMCC, FNMA, AUR, TE, HBM, TRON, DDOG, INFQ, AXON, VIAV, UI, LAES]`
  with no INFO log line for "dropped" candidates — the guard activated
  but found nothing to drop, as expected.

## Devil's-Advocate Note (insider_pct sign)

The audit directive states "v2 docs state insider ownership is a NEGATIVE
predictor." A code-base sweep gives the opposite signal:

- `screener/li_engine/analysis/whitespace_v2.py:199` — comment reads
  `"insider_pct": 0.08, # IC vs success = +0.161"` (positive, post-launch
  binary success target).
- `screener/li_engine/analysis/generate_docx_v3.py:147-149` — section
  titled "Insider ownership is a genuine signal" with weight 14.5%, median
  |IC| 0.227, "3 of 4 targets positive."
- `generate_docx_v4.py:115` — cross-section forward-30d flow report:
  "insider_pct (+0.23)".
- `generate_docx_v4.py:169` — "insider_pct: +0.126 post-launch (weaker
  than cross-section +0.23 but same direction — real signal, smaller
  magnitude)."
- `generate_docx_v4.py:215` — robustness section: "Conclusion: insider_pct
  passes every robustness check. Not outlier-driven... The 14.5% weight is
  defensible."
- `whitespace_candidates.py:193` — comment `"insider_pct": 0.08, # IC 0.13`.

Every documented IC measurement in the repo points to insider_pct being a
**positive** predictor that has passed winsorization, outlier removal,
size-partialling, and bootstrap robustness. The Wave A1 sign flip
contradicts this body of evidence.

The flip has been applied per the explicit task directive. **Reviewer
should reconcile before merging to main.** Either:
- (a) the audit found a methodology document not visible in the codebase
  that supersedes the embedded comments and the docx generators, in which
  case those stale references should also be cleaned up in a follow-up,
  OR
- (b) the audit is mistaken and the sign flip should be reverted.

## Limitations of the render-time guard

The `mkt_master_data` SQL only catches competitor 2x+ products with a
populated `map_li_underlier` AND a `map_li_leverage_amount` castable to
`>= 2.0`. Today **88 active competitor L/I-pattern funds** in
`mkt_master_data` have `map_li_underlier IS NULL` — the AMPU example is
exactly one of these (NULL underlier, NULL leverage_amount). The guard
will NOT catch them.

These unmapped products are caught upstream by
`whitespace_v4.apply_whitespace_filter` via `competitor_counts.parquet`'s
fund-name regex fallback (which correctly maps `AMPU` → `AMPX` underlier,
verified: `competitor_counts.parquet` shows `AMPX` row with
`competitor_active_long=1`). So defence in depth holds:

1. **Upstream**: cc-parquet regex fallback excludes AMPX from
   `whitespace_v4.parquet`.
2. **Render-time** (this fix): mkt_master_data direct re-check catches
   any newly-mapped competitor product after the launch_candidates
   parquet was built but before the report runs.

Closing the AMPU-style hole at the render-time guard would require either
(a) extending the SQL with a fund-name regex against `fund_name` columns
or (b) joining to `competitor_counts.parquet` at render time. Out of
scope for Wave A1; flagged for a Wave A2 candidate.

## Files Changed

```
screener/li_engine/analysis/whitespace_v3.py        |  +12 -3
screener/li_engine/analysis/whitespace_v4.py        |  +20 -10
screener/li_engine/analysis/weekly_v2_report.py     |  +37 -2
docs/audit_2026-05-11/upgrade_A1.md                 |  +new
```
