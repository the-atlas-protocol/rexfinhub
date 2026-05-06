"""Step 2C — Does flow momentum beat the sticky baseline?

We can't backtest mentions_24h historically (no snapshots). But we have 4.5
years of daily flow data, from which we derive:
    1. 2w trailing flow sum (vs 4w trailing) — acceleration
    2. Rank change over 3 weeks — climbers / fallers
    3. Pure recency weighting — exponential decay

For each candidate strategy, measure hit rate vs the 76.3% sticky baseline.

Strategies:
    A. Sticky             — predict top-20(W) = top-20(W-1)          [baseline]
    B. 2w trailing flow   — rank by trailing 2-week flow as of W-1
    C. 4w trailing flow   — rank by trailing 4-week flow as of W-1
    D. Acceleration       — 2w_flow / 4w_flow ratio
    E. Sticky + momentum  — take top-15 from sticky + top-5 climbers
    F. Pure rank-climber  — rank by (rank at W-1 minus rank at W-4)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PANEL = _ROOT / "data" / "analysis" / "weekly_top20_panel.parquet"


def _pred_hit_rate(pred: set, actual: set, n: int) -> float:
    if not pred or not actual:
        return 0.0
    return len(pred & actual) / n


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    w = pd.read_parquet(PANEL)
    w = w.sort_values(["underlier", "week"]).reset_index(drop=True)

    # For each underlier, compute trailing sums at each week
    w["abs_flow"] = w["weekly_flow"].abs()
    w["flow_1w"] = w["abs_flow"]
    w["flow_2w"] = w.groupby("underlier")["abs_flow"].transform(
        lambda x: x.rolling(2, min_periods=1).sum()
    )
    w["flow_4w"] = w.groupby("underlier")["abs_flow"].transform(
        lambda x: x.rolling(4, min_periods=1).sum()
    )
    w["rank_in_week"] = w.groupby("week")["abs_flow"].rank(ascending=False, method="first")

    # Prior-week values (shift by 1 within underlier)
    for c in ("flow_1w", "flow_2w", "flow_4w", "rank_in_week", "in_top"):
        w[f"prior_{c}"] = w.groupby("underlier")[c].shift(1)

    # Rank 3 weeks ago
    w["rank_3w_ago"] = w.groupby("underlier")["rank_in_week"].shift(3)
    w["rank_improvement"] = w["rank_3w_ago"] - w["prior_rank_in_week"]

    # Acceleration signal
    w["accel"] = w["prior_flow_2w"] / w["prior_flow_4w"].replace(0, np.nan)

    # Per-week ranking under each strategy
    weeks = sorted(w["week"].unique())
    results = []

    for week in weeks:
        sub = w[w["week"] == week].copy()
        actual_top = set(sub[sub["in_top"] == 1]["underlier"])
        if len(actual_top) == 0:
            continue

        prior_sub = sub.dropna(subset=["prior_rank_in_week"])
        if len(prior_sub) < 20:
            continue

        # Strategy A: sticky
        sticky_set = set(prior_sub[prior_sub["prior_in_top"] == 1]["underlier"])

        # Strategy B: rank by 2w trailing flow (as of prior week)
        b_set = set(prior_sub.nlargest(20, "prior_flow_2w")["underlier"])

        # Strategy C: rank by 4w trailing flow
        c_set = set(prior_sub.nlargest(20, "prior_flow_4w")["underlier"])

        # Strategy D: rank by acceleration (2w / 4w, require meaningful 4w)
        d_src = prior_sub[prior_sub["prior_flow_4w"] >= 1.0].copy()  # $1M floor to avoid tiny denom
        d_set = set(d_src.nlargest(20, "accel")["underlier"]) if len(d_src) >= 20 else set()

        # Strategy E: hybrid — sticky top-15 + top-5 rank climbers
        #   Climbers = not in sticky, best rank_improvement, min 2w flow
        sticky_list = list(prior_sub[prior_sub["prior_in_top"] == 1].nsmallest(15, "prior_rank_in_week")["underlier"])
        non_sticky = prior_sub[~prior_sub["underlier"].isin(sticky_list)]
        climbers = non_sticky.dropna(subset=["rank_improvement"]).nlargest(5, "rank_improvement")
        e_set = set(sticky_list) | set(climbers["underlier"])

        # Strategy F: pure rank climber
        f_src = prior_sub.dropna(subset=["rank_improvement"])
        f_set = set(f_src.nlargest(20, "rank_improvement")["underlier"]) if len(f_src) >= 20 else set()

        results.append({
            "week": week,
            "n_actual": len(actual_top),
            "A_sticky": _pred_hit_rate(sticky_set, actual_top, 20),
            "B_2w_flow": _pred_hit_rate(b_set, actual_top, 20),
            "C_4w_flow": _pred_hit_rate(c_set, actual_top, 20),
            "D_accel": _pred_hit_rate(d_set, actual_top, 20),
            "E_hybrid": _pred_hit_rate(e_set, actual_top, 20),
            "F_rank_climber": _pred_hit_rate(f_set, actual_top, 20),
        })

    res = pd.DataFrame(results)

    print("=" * 70)
    print("STEP 2C — Signal strategies vs sticky baseline")
    print("=" * 70)
    print(f"Weeks tested: {len(res)}")
    print()
    print(f"{'Strategy':<30} {'Mean':>8} {'Median':>8} {'Std':>8} {'Beat A?':>10}")
    print("-" * 70)
    for strat in ["A_sticky", "B_2w_flow", "C_4w_flow", "D_accel", "E_hybrid", "F_rank_climber"]:
        vals = res[strat]
        beat = (vals > res["A_sticky"]).mean()
        print(f"{strat:<30} {vals.mean():.1%}  {vals.median():.1%}  {vals.std():.1%}    {beat:.1%}")

    print()
    print("=" * 70)
    print("Incremental delta vs baseline (strategy_hit - sticky_hit per week)")
    print("=" * 70)
    for strat in ["B_2w_flow", "C_4w_flow", "D_accel", "E_hybrid", "F_rank_climber"]:
        delta = res[strat] - res["A_sticky"]
        print(f"{strat:<30} mean_delta={delta.mean():+.2%}  weeks_better={((delta > 0).mean()):.1%}  weeks_worse={((delta < 0).mean()):.1%}")

    # Save
    res.to_parquet(_ROOT / "data" / "analysis" / "step2c_strategy_hit_rates.parquet", index=False)
    print(f"\nSaved per-week hit rates.")


if __name__ == "__main__":
    main()
