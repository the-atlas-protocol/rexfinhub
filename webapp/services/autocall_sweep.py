"""Distribution sweep over every valid issue date for an autocall product spec."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from statistics import mean, median

from dataclasses import replace

from webapp.services.autocall_engine import (
    LevelStore,
    NoteParams,
    Outcome,
    simulate_note,
    suggest_coupon as _heuristic_coupon,
)


# Histogram edges for total return %: 21 edges -> 20 bins, [-100, 100] step 10.
_HIST_EDGES: list[int] = list(range(-100, 110, 10))
_SAMPLES_CAP: int = 500


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolation percentile on a pre-sorted list. pct in [0, 100]."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _bin_index(value: float, edges: list[int]) -> int:
    """Place `value` into a bin; clamp to [0, len(edges)-2]."""
    n_bins = len(edges) - 1
    if value <= edges[0]:
        return 0
    if value >= edges[-1]:
        return n_bins - 1
    # Linear scan is fine for 20 bins.
    for i in range(n_bins):
        if edges[i] <= value < edges[i + 1]:
            return i
    return n_bins - 1


def _params_to_dict(params: NoteParams) -> dict:
    return asdict(params)


def sweep(
    refs: list[str],
    params: NoteParams,
    store: LevelStore,
    coupon_mode: str = "manual",
) -> dict:
    """Run simulate_note() at every valid issue date in the primary ref's date set.

    coupon_mode:
      'manual'    — use params.coupon_rate_pa_pct as-is at every issue date.
      'suggested' — recompute the coupon at each issue date via suggest_coupon().
                    Slower (vol calc per date), but reflects vol regime.

    Returns a JSON-serializable distribution payload. See router for shape.
    """
    if not refs:
        raise ValueError("At least one ref required.")
    if coupon_mode not in ("manual", "suggested"):
        raise ValueError("coupon_mode must be 'manual' or 'suggested'.")

    primary = refs[0]
    primary_dates: list[date] = list(store._dates.get(primary, []))
    if not primary_dates:
        raise ValueError(f"No price data for primary ref '{primary}'.")

    inceptions = [store.inception(r) for r in refs]
    if any(i is None for i in inceptions):
        raise ValueError("Missing inception for one or more refs.")
    earliest_valid = max(i for i in inceptions if i is not None)
    issue_dates = [d for d in primary_dates if d >= earliest_valid]

    counts = {
        "autocalled": 0,
        "matured_above": 0,
        "matured_below": 0,
        "in_progress": 0,
    }
    completed_returns: list[float] = []
    completed_annualized: list[float] = []
    histogram_counts = [0] * (len(_HIST_EDGES) - 1)
    samples: list[tuple[str, str, float | None, float | None]] = []
    coupons_used: list[float] = []  # only when coupon_mode == 'suggested'

    n_valid = 0
    sample_stride = max(1, len(issue_dates) // _SAMPLES_CAP) if issue_dates else 1

    for idx, issue_date in enumerate(issue_dates):
        if coupon_mode == "suggested":
            # Sweep uses the fast vol-based HEURISTIC per date (BS MC at every
            # date would be ~80min). The single-scenario panel uses BS via the
            # /suggest-coupon endpoint when the user is parked on one date.
            sc = _heuristic_coupon(refs, issue_date, params, store)
            if sc is None:
                continue
            scenario_params = replace(params, coupon_rate_pa_pct=sc)
            coupons_used.append(sc)
        else:
            scenario_params = params
        result = simulate_note(refs, issue_date, scenario_params, store)
        if result.outcome == Outcome.INVALID:
            continue
        n_valid += 1
        outcome_key = result.outcome.value
        if outcome_key not in counts:
            continue
        counts[outcome_key] += 1
        tr: float | None
        sc_used: float | None = (
            scenario_params.coupon_rate_pa_pct if coupon_mode == "suggested" else None
        )
        if result.outcome == Outcome.IN_PROGRESS:
            tr = None
        else:
            tr = result.total_return_pct
            completed_returns.append(tr)
            completed_annualized.append(result.annualized_return_pct)
            histogram_counts[_bin_index(tr, _HIST_EDGES)] += 1
        if idx % sample_stride == 0 and len(samples) < _SAMPLES_CAP:
            samples.append((issue_date.isoformat(), outcome_key, tr, sc_used))

    if completed_returns:
        sorted_rets = sorted(completed_returns)
        stats = {
            "mean_total_return_pct": mean(completed_returns),
            "median_total_return_pct": median(completed_returns),
            "p10_total_return_pct": _percentile(sorted_rets, 10),
            "p90_total_return_pct": _percentile(sorted_rets, 90),
            "mean_annualized_return_pct": mean(completed_annualized),
        }
    else:
        stats = {
            "mean_total_return_pct": 0.0,
            "median_total_return_pct": 0.0,
            "p10_total_return_pct": 0.0,
            "p90_total_return_pct": 0.0,
            "mean_annualized_return_pct": 0.0,
        }

    buckets = (
        {k: (v / n_valid) * 100.0 for k, v in counts.items()}
        if n_valid
        else {k: 0.0 for k in counts}
    )

    first_issue = issue_dates[0].isoformat() if issue_dates else ""
    last_issue = issue_dates[-1].isoformat() if issue_dates else ""

    coupon_summary: dict | None = None
    if coupon_mode == "suggested" and coupons_used:
        sorted_c = sorted(coupons_used)
        coupon_summary = {
            "mean_pa_pct": mean(coupons_used),
            "median_pa_pct": median(coupons_used),
            "min_pa_pct": min(coupons_used),
            "max_pa_pct": max(coupons_used),
            "p10_pa_pct": _percentile(sorted_c, 10),
            "p90_pa_pct": _percentile(sorted_c, 90),
        }

    return {
        "refs": list(refs),
        "params": _params_to_dict(params),
        "coupon_mode": coupon_mode,
        "coupon_summary": coupon_summary,
        "sample_size": n_valid,
        "first_issue_date": first_issue,
        "last_issue_date": last_issue,
        "buckets": buckets,
        "counts": counts,
        "stats": stats,
        "histogram": {
            "edges": list(_HIST_EDGES),
            "counts": histogram_counts,
        },
        "samples": [
            {"issue_date": d, "outcome": o, "total_return_pct": r, "coupon_pa_pct": c}
            for (d, o, r, c) in samples
        ],
    }
