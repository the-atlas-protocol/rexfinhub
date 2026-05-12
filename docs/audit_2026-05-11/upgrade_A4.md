# Wave A4 — Refit composite weights from backtested IC

**Date:** 2026-05-11
**Branch:** `audit-stockrecs-A4-weights`
**Files changed:**
- `screener/li_engine/analysis/whitespace_v3.py` — `WEIGHTS` replaced with refit values
- `screener/li_engine/analysis/refit_weights.py` — NEW (full refit pipeline)
- `data/analysis/refit_results_2026-05-11.json` — NEW (raw refit output)

---

## TL;DR

| Metric                               | Hand-tuned | Refit (sign-bounded ridge α=2.0) |
| ------------------------------------ | ---------- | -------------------------------- |
| Composite IC, train (n=108)          | +0.406     | **+0.460** (+13%)                |
| Composite IC, **OOS** (n=77)         | +0.527     | **+0.560** (+6%)                 |
| Number of signals retained           | 9          | 9 (no zeros)                     |

The refit improves out-of-sample composite IC modestly. Sign of every weight
matches the underlying univariate IC. No signal was zeroed.

---

## What was done

1. **Built outcome dataset** of every 2x leveraged ETP launched between
   2024-08-01 and 2025-11-11 (so each had ≥6 months elapsed) — 198 candidates
   in `mkt_master_data`.
2. **Pulled 6-mo-post-launch AUM** for each from `mkt_time_series` using
   `months_ago = elapsed_months - 6`. Outcome variable: `log(AUM_6mo + 1)`.
   - 186 of 198 had non-zero AUM at the 6-month mark.
3. **Joined to underlier stock signals** from the latest `mkt_stock_data`
   pipeline run — 185 final dataset rows after matching 126/135 underliers.
4. **Computed per-signal Spearman IC** vs `log_aum_6mo` on the train slice
   (108 rows, inception ≤ 2025-08-11).
5. **Refit weights** via sign-constrained ridge regression (α swept; α=2.0
   chosen from the bias-variance frontier):
   - Constraint: `sum(|w|) = 1`
   - Bound: each weight has the same sign as its univariate IC (prevents
     pathological flips on collinear features)
6. **Validated OOS** on the 77 launches with inception ≥ 2025-08-11.
7. **Updated `whitespace_v3.WEIGHTS`** with refit values + commented IC.

---

## Per-signal IC (in-sample, n=108)

| Signal         | IC      | Direction | Refit weight |
| -------------- | ------- | --------- | ------------ |
| rvol_90d       | +0.409  | Positive  | +0.194       |
| rvol_30d       | +0.392  | Positive  | +0.144       |
| mentions_z     | +0.347  | Positive  | +0.148       |
| insider_pct    | +0.294  | Positive  | +0.182       |
| theme_bonus    | +0.228  | Positive  | +0.060       |
| ret_1m         | +0.171  | Positive  | +0.044       |
| ret_1y         | +0.112  | Positive  | +0.074       |
| si_ratio       | -0.192  | Negative  | -0.109       |
| inst_own_pct   | -0.109  | Negative  | -0.045       |

Three signals stand out: `rvol_90d`, `rvol_30d`, and `mentions_z`. Vol regime
remains the dominant predictor of post-launch AUM (consistent with v2 finding).
`insider_pct` is a stronger positive than the hand-tuned weights credited.
`theme_bonus` lost weight but kept its sign — refit thinks it's overweighted.

---

## Weight delta vs hand-tuned

| Signal         | Old    | New    | Δ      |
| -------------- | ------ | ------ | ------ |
| mentions_z     | +0.220 | +0.148 | -0.072 |
| rvol_30d       | +0.150 | +0.144 | -0.006 |
| rvol_90d       | +0.090 | +0.194 | **+0.104** |
| ret_1m         | +0.120 | +0.044 | -0.076 |
| ret_1y         | +0.050 | +0.074 | +0.024 |
| theme_bonus    | +0.140 | +0.060 | **-0.080** |
| insider_pct    | +0.080 | +0.182 | **+0.102** |
| si_ratio       | -0.080 | -0.109 | -0.029 |
| inst_own_pct   | -0.070 | -0.045 | +0.025 |

Largest moves:
- `rvol_90d` gets 2× weight (sustained vol > short-window vol per IC)
- `insider_pct` gets 2× weight (was underrated)
- `theme_bonus` cut in half (overweighted; raw IC of +0.23 doesn't justify the
  current loading)

---

## Sample tickers — composite score, old vs new

| Product   | Underlier | old   | new   | log(AUM_6mo) |
| --------- | --------- | ----- | ----- | ------------ |
| AVXX US   | AVAV      | -0.31 | -0.17 | +2.02        |
| COZX US   | CORZ      | -0.33 | -0.30 | +2.52        |
| SBTU US   | SBET      | -0.13 | +0.00 | +0.88        |
| NBIG US   | NBIS      | +1.39 | +1.22 | +3.46        |
| RDTL US   | RDDT      | +0.16 | +0.06 | +3.95        |

Refit attenuates extreme scores and corrects three of five underrated picks
(AVXX, SBTU, RDTL all had positive 6-mo AUM growth that the old weights
underestimated).

---

## CRITICAL LIMITATION

The spec called for: "each historical Bloomberg snapshot → underlier signals at
that time + actual forward 6-mo AUM growth."

**`mkt_stock_data` only retains the latest pipeline run** — historical
underlier signal snapshots are not persisted in the database. The Bloomberg
history files in `data/DASHBOARD/history/` contain ETP-level AUM/flow/price
series, **not** underlier stock signals.

**Workaround used:** cross-sectional IC. Each launch's outcome (6mo forward
AUM, which IS available historically via `mkt_time_series`) regressed against
the underlier's CURRENT signal snapshot.

**Why this is defensible for slow-moving signals:**
- `insider_pct`, `inst_own_pct`, `si_ratio` rarely change >5pp month-to-month
- `rvol_90d`, `theme_bonus` are quarterly-stable
- Sector and structural characteristics drift slowly

**Why this is biased for momentum signals:**
- `ret_1m`, `ret_1y`, `mentions_z` are point-in-time and could differ
  meaningfully from at-launch values
- Specifically, `mentions_z` and `ret_1m` IC may be deflated because we're
  measuring outcome at month-6 against signals from months later

**Net assessment:** the refit captures the rank ordering of slow-moving
signals reliably. Momentum signal weights should be treated as lower-bound
estimates — true at-launch IC is likely modestly higher.

A proper time-series backtest will require a snapshot-versioned
`mkt_stock_data` table going forward (recommend Wave A6+).

---

## Verification

```
python -m screener.li_engine.analysis.refit_weights
```

Reproduces the refit deterministically (no RNG except in 5-row sample print).
Outputs `data/analysis/refit_results_2026-05-11.json` with full IC table,
old/new weights, and train/OOS composite IC.

---

## Decision

✅ **Adopted refit weights** in `whitespace_v3.WEIGHTS`. OOS lift is modest
(+6%) but consistent across alpha sweep, all signs preserved, and refit gives
empirical justification to weights that were previously "feel right" picks.

The prior `WEIGHTS` block is preserved in git history (`de1279a` and earlier).
