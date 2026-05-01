"""Autocallable note simulator.

Pure-Python engine. No Plotly, no Flask, no SQLAlchemy session leaks.
Caller passes:
  • a `LevelStore` (date-indexed level lookup per ticker)
  • a list of ref tickers (1..5)
  • an issue date
  • a `NoteParams` dataclass

Returns a `NoteResult` with the full observation log + final outcome.

Design notes
------------
* Observation dates use Excel-style EDATE: same day-of-month + k*freq months,
  clamped to month-end when the source day exceeds the target month's length
  (Jan 31 + 1mo -> Feb 28/29). `dateutil.relativedelta` does exactly this.
* Reference levels at any observation date use LOCF: the last available
  level on-or-before that date. If a ref has no data on-or-before
  issue_date, the simulation is invalid.
* In-progress: if an observation date falls past the data end, the note
  is marked IN_PROGRESS and the result reflects state at the last
  completed observation. No autocall / matured-above / matured-below
  outcome is assigned.
* Memory coupon: missed coupons accrue (no time value). On the next
  non-missed observation, accrued + current are both paid. If memory
  is OFF, missed coupons are simply lost.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable

from dateutil.relativedelta import relativedelta


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------

class Outcome(str, Enum):
    AUTOCALLED = "autocalled"
    MATURED_ABOVE = "matured_above"
    MATURED_BELOW = "matured_below"
    IN_PROGRESS = "in_progress"
    INVALID = "invalid"


class ObsStatus(str, Enum):
    COUPON_PAID = "coupon_paid"
    COUPON_MISSED = "coupon_missed"
    MEMORY_CATCHUP = "memory_catchup"   # paid this period + cleared bank
    AUTOCALL = "autocall"
    MATURITY_ABOVE = "maturity_above"
    MATURITY_BELOW = "maturity_below"


@dataclass(frozen=True)
class NoteParams:
    tenor_months: int = 60
    obs_freq_months: int = 1
    coupon_rate_pa_pct: float = 10.0      # annualized %
    coupon_barrier_pct: float = 60.0      # of initial
    ac_barrier_pct: float = 100.0         # of initial
    protection_barrier_pct: float = 50.0  # of initial
    memory: bool = False
    no_call_months: int = 12

    @property
    def total_obs(self) -> int:
        return self.tenor_months // self.obs_freq_months

    @property
    def per_period_coupon_pct(self) -> float:
        """Coupon paid per observation period (% of notional)."""
        return self.coupon_rate_pa_pct * self.obs_freq_months / 12.0


@dataclass
class Observation:
    k: int                          # 1..total_obs
    obs_date: date
    levels: dict[str, float]        # raw ref levels at obs (LOCF)
    perfs: dict[str, float]         # ref level / initial ref level
    worst_perf: float
    status: ObsStatus
    coupon_paid_pct: float          # this-period payout (incl. memory catchup)
    memory_bank_pct: float          # banked AFTER this obs (memory mode only)


@dataclass
class NoteResult:
    refs: list[str]
    issue_date: date
    initial_levels: dict[str, float]      # ref -> level at issue_date (LOCF)
    params: NoteParams
    outcome: Outcome
    outcome_date: date | None
    observations: list[Observation] = field(default_factory=list)
    coupons_paid_pct: float = 0.0          # cumulative across all paid obs
    n_coupons_paid: int = 0
    n_coupons_missed: int = 0
    final_principal_pct: float = 100.0     # 100 = full return; <100 = loss
    error: str | None = None

    @property
    def total_return_pct(self) -> float:
        """Total return = principal_return - 100 + coupons (all in % of notional)."""
        return self.final_principal_pct - 100.0 + self.coupons_paid_pct

    @property
    def annualized_return_pct(self) -> float:
        if self.outcome_date is None or self.outcome == Outcome.INVALID:
            return 0.0
        days = (self.outcome_date - self.issue_date).days
        if days <= 0:
            return 0.0
        years = days / 365.25
        gross = 1.0 + self.total_return_pct / 100.0
        if gross <= 0:
            return -100.0
        return (gross ** (1.0 / years) - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Level lookup (LOCF on a sorted (date, level) list per ticker)
# ---------------------------------------------------------------------------

class LevelStore:
    """Date-indexed level lookup.

    Build once per request from `AutocallIndexLevel` rows; serves O(log n)
    LOCF lookups via bisect on parallel sorted arrays.
    """
    def __init__(self):
        # ticker -> (sorted dates, parallel levels)
        self._dates: dict[str, list[date]] = {}
        self._levels: dict[str, list[float]] = {}
        self._max_date: date | None = None
        self._min_date: dict[str, date] = {}

    def load_rows(self, rows: Iterable[tuple[date, str, float]]) -> None:
        """Bulk-load (date, ticker, level) tuples. Assumes any order."""
        per: dict[str, list[tuple[date, float]]] = {}
        for d, t, lvl in rows:
            per.setdefault(t, []).append((d, lvl))
        for t, pairs in per.items():
            pairs.sort()
            self._dates[t] = [p[0] for p in pairs]
            self._levels[t] = [p[1] for p in pairs]
            self._min_date[t] = pairs[0][0]
            mx = pairs[-1][0]
            if self._max_date is None or mx > self._max_date:
                self._max_date = mx

    @property
    def max_date(self) -> date | None:
        return self._max_date

    def inception(self, ticker: str) -> date | None:
        return self._min_date.get(ticker)

    def locf(self, ticker: str, on_or_before: date) -> float | None:
        """Last available level for `ticker` on or before `on_or_before`."""
        ds = self._dates.get(ticker)
        if not ds:
            return None
        # bisect_right gives index of first date > target; we want index-1.
        idx = bisect.bisect_right(ds, on_or_before) - 1
        if idx < 0:
            return None
        return self._levels[ticker][idx]


# ---------------------------------------------------------------------------
# Observation date generation (Excel EDATE behavior)
# ---------------------------------------------------------------------------

def _edate(start: date, months: int) -> date:
    """Excel EDATE: preserve day-of-month, clamp to month-end if overflow."""
    return start + relativedelta(months=months)


def observation_dates(issue: date, params: NoteParams) -> list[date]:
    return [_edate(issue, k * params.obs_freq_months) for k in range(1, params.total_obs + 1)]


# ---------------------------------------------------------------------------
# Core simulator
# ---------------------------------------------------------------------------

def simulate_note(
    refs: list[str],
    issue_date: date,
    params: NoteParams,
    store: LevelStore,
) -> NoteResult:
    if not refs:
        return NoteResult(
            refs=[], issue_date=issue_date, initial_levels={}, params=params,
            outcome=Outcome.INVALID, outcome_date=None,
            error="At least one reference required.",
        )
    if store.max_date is None:
        return NoteResult(
            refs=refs, issue_date=issue_date, initial_levels={}, params=params,
            outcome=Outcome.INVALID, outcome_date=None,
            error="No price data loaded.",
        )

    # Initial levels: LOCF on issue date.
    initial_levels: dict[str, float] = {}
    for r in refs:
        lvl = store.locf(r, issue_date)
        if lvl is None or lvl <= 0:
            return NoteResult(
                refs=refs, issue_date=issue_date, initial_levels={}, params=params,
                outcome=Outcome.INVALID, outcome_date=None,
                error=f"No price data for '{r}' on or before issue date {issue_date}.",
            )
        initial_levels[r] = lvl

    coupon_pct = params.per_period_coupon_pct
    bank_pct = 0.0  # accrued unpaid coupons (memory mode)
    coupons_paid_total = 0.0
    n_paid = 0
    n_missed = 0
    obs_log: list[Observation] = []
    max_data_date = store.max_date
    obs_dates = observation_dates(issue_date, params)

    for k, obs_date in enumerate(obs_dates, start=1):
        # In-progress: any obs past data end stops the simulation (no outcome).
        if obs_date > max_data_date:
            return NoteResult(
                refs=refs, issue_date=issue_date, initial_levels=initial_levels,
                params=params, outcome=Outcome.IN_PROGRESS,
                outcome_date=max_data_date,
                observations=obs_log,
                coupons_paid_pct=coupons_paid_total,
                n_coupons_paid=n_paid,
                n_coupons_missed=n_missed,
                final_principal_pct=100.0,
            )

        # Per-ref level + perf at this obs.
        levels_t: dict[str, float] = {}
        perfs_t: dict[str, float] = {}
        for r in refs:
            lvl = store.locf(r, obs_date)
            if lvl is None:
                # Should not happen if initial succeeded, but guard.
                return NoteResult(
                    refs=refs, issue_date=issue_date, initial_levels=initial_levels,
                    params=params, outcome=Outcome.INVALID, outcome_date=None,
                    error=f"Missing level for {r} at {obs_date}.",
                )
            levels_t[r] = lvl
            perfs_t[r] = lvl / initial_levels[r]
        worst_perf = min(perfs_t.values())

        # Months elapsed since issue (for no-call gate).
        months_elapsed = k * params.obs_freq_months
        ac_barrier = params.ac_barrier_pct / 100.0
        coupon_barrier = params.coupon_barrier_pct / 100.0
        protect_barrier = params.protection_barrier_pct / 100.0

        # ---- Final maturity check (k == total_obs, no autocall yet) ----
        is_final = (k == params.total_obs)

        # ---- Autocall trigger ----
        can_autocall = months_elapsed > params.no_call_months
        if can_autocall and worst_perf >= ac_barrier:
            # Autocall: pay this period coupon + any banked memory + 100% principal.
            paid_now = coupon_pct + (bank_pct if params.memory else 0.0)
            coupons_paid_total += paid_now
            n_paid += 1
            obs_log.append(Observation(
                k=k, obs_date=obs_date, levels=levels_t, perfs=perfs_t,
                worst_perf=worst_perf, status=ObsStatus.AUTOCALL,
                coupon_paid_pct=paid_now, memory_bank_pct=0.0,
            ))
            return NoteResult(
                refs=refs, issue_date=issue_date, initial_levels=initial_levels,
                params=params, outcome=Outcome.AUTOCALLED, outcome_date=obs_date,
                observations=obs_log,
                coupons_paid_pct=coupons_paid_total,
                n_coupons_paid=n_paid,
                n_coupons_missed=n_missed,
                final_principal_pct=100.0,
            )

        # ---- Coupon pay/miss logic ----
        if worst_perf >= coupon_barrier:
            # Pay current + (banked memory if memory ON).
            extra = bank_pct if params.memory else 0.0
            paid_now = coupon_pct + extra
            status = ObsStatus.MEMORY_CATCHUP if (params.memory and extra > 0) else ObsStatus.COUPON_PAID
            bank_pct = 0.0
            coupons_paid_total += paid_now
            n_paid += 1
        else:
            # Missed.
            paid_now = 0.0
            status = ObsStatus.COUPON_MISSED
            if params.memory:
                bank_pct += coupon_pct
            n_missed += 1

        # ---- Final maturity (override status if at final obs and no autocall) ----
        if is_final:
            if worst_perf >= protect_barrier:
                principal = 100.0
                final_status = ObsStatus.MATURITY_ABOVE
                final_outcome = Outcome.MATURED_ABOVE
            else:
                # 1-for-1 below barrier: principal = worst_perf * 100%
                principal = worst_perf * 100.0
                final_status = ObsStatus.MATURITY_BELOW
                final_outcome = Outcome.MATURED_BELOW
            # The coupon decision above already happened; final status overrides
            # the obs status only for log labeling (the coupon already counted).
            obs_log.append(Observation(
                k=k, obs_date=obs_date, levels=levels_t, perfs=perfs_t,
                worst_perf=worst_perf, status=final_status,
                coupon_paid_pct=paid_now,
                memory_bank_pct=bank_pct if params.memory else 0.0,
            ))
            return NoteResult(
                refs=refs, issue_date=issue_date, initial_levels=initial_levels,
                params=params, outcome=final_outcome, outcome_date=obs_date,
                observations=obs_log,
                coupons_paid_pct=coupons_paid_total,
                n_coupons_paid=n_paid,
                n_coupons_missed=n_missed,
                final_principal_pct=principal,
            )

        # ---- Standard observation: log and continue ----
        obs_log.append(Observation(
            k=k, obs_date=obs_date, levels=levels_t, perfs=perfs_t,
            worst_perf=worst_perf, status=status,
            coupon_paid_pct=paid_now,
            memory_bank_pct=bank_pct if params.memory else 0.0,
        ))

    # Should never reach here (final-maturity branch returns above).
    return NoteResult(
        refs=refs, issue_date=issue_date, initial_levels=initial_levels,
        params=params, outcome=Outcome.INVALID, outcome_date=None,
        observations=obs_log, error="Unreachable: simulation completed without final-obs branch.",
    )


# ---------------------------------------------------------------------------
# Suggested coupon (heuristic, NOT a real product price)
# ---------------------------------------------------------------------------
# Real autocallables are priced from a vol surface + funding curve + correlation
# matrix. We don't have those. This heuristic blends realized vol with barrier
# distance, memory, basket size and ac-barrier difficulty to produce a plausible
# annualized coupon. Calibrated to give ~6-9% for SPX 5y 60/100/50 single,
# ~7-10% with memory, ~8-11% for two-ref baskets. Use with prominent disclaimer.

import math


def realized_vol(
    ticker: str, on_or_before: date, store: LevelStore, lookback_days: int = 252,
) -> float | None:
    """Annualized stdev of daily log returns over the trailing window."""
    dates = store._dates.get(ticker)
    levels = store._levels.get(ticker)
    if not dates:
        return None
    idx = bisect.bisect_right(dates, on_or_before) - 1
    if idx < lookback_days:
        return None
    rets: list[float] = []
    for i in range(idx - lookback_days + 1, idx + 1):
        prev = levels[i - 1]
        cur = levels[i]
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 50:
        return None
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / max(1, n - 1)
    return math.sqrt(var) * math.sqrt(252)


def suggest_coupon(
    refs: list[str], issue_date: date, params: NoteParams, store: LevelStore,
) -> float | None:
    """Heuristic vol-based suggested annualized coupon (% per annum).

    Returns None if vol can't be computed (insufficient history). NOT a real
    product price — this is a teaching/scenario tool, surfaced with disclaimer.
    """
    vols: list[float] = []
    for r in refs:
        v = realized_vol(r, issue_date, store, lookback_days=252)
        if v is not None:
            vols.append(v)
    if not vols:
        return None
    avg_vol = sum(vols) / len(vols)

    cb_dist = max(0.0, 1.0 - params.coupon_barrier_pct / 100.0)
    prot_dist = max(0.0, 1.0 - params.protection_barrier_pct / 100.0)
    memory_premium = 1.0 if params.memory else 0.0
    basket_premium = max(0, len(refs) - 1) * 0.5
    ac_discount = max(0.0, (params.ac_barrier_pct - 100.0) / 100.0) * 5.0

    coupon = (
        3.0
        + 40.0 * avg_vol * cb_dist
        + 5.0 * prot_dist
        + memory_premium
        + basket_premium
        - ac_discount
    )
    return max(2.0, min(20.0, coupon))


# ---------------------------------------------------------------------------
# DB → LevelStore helper
# ---------------------------------------------------------------------------

def load_level_store(db, tickers: list[str] | None = None) -> LevelStore:
    """Build a LevelStore from the autocall_index_levels table.

    If `tickers` is None, loads all rows (~125K).
    """
    from webapp.models import AutocallIndexLevel
    q = db.query(AutocallIndexLevel.date, AutocallIndexLevel.ticker, AutocallIndexLevel.level)
    if tickers is not None:
        q = q.filter(AutocallIndexLevel.ticker.in_(tickers))
    store = LevelStore()
    store.load_rows(q.all())
    return store
