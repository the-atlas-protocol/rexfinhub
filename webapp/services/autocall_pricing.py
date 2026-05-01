"""Black-Scholes Monte Carlo par-coupon pricer for worst-of autocallable notes."""
from __future__ import annotations

import bisect
import math
from datetime import date

import numpy as np

from webapp.services.autocall_engine import LevelStore, NoteParams


# ---------------------------------------------------------------------------
# Default dividend yields per ticker. TR indices accrue dividends internally
# so q = 0. Price indices use 2% as a generic estimate. Anything unknown -> 0.
# ---------------------------------------------------------------------------

_TR_INDICES_Q0 = {
    "BMAXUS Index", "B500T Index", "SPXT Index", "SPDAUDT Index",
    "LBUSTRUU Index", "LUACTRUU Index", "LF98TRUU Index",
    "MQUSLVA Index", "MQVTUSLE Index", "MQVTUSTE Index",
    "MQUSTVA Index", "MQUSQVA Index", "MQUSHIQL Index",
}
_PRICE_INDICES_Q002 = {"SPX Index", "NDX Index"}
_VOL_INDICES_Q0 = {"VIX Index"}


def _default_q(ticker: str) -> float:
    if ticker in _TR_INDICES_Q0 or ticker in _VOL_INDICES_Q0:
        return 0.0
    if ticker in _PRICE_INDICES_Q002:
        return 0.02
    # Also accept the bare-symbol form (without the "Index" suffix) for
    # convenience; most callers pass the full Bloomberg ticker.
    bare = ticker.split()[0] if " " in ticker else ticker
    if bare in {t.split()[0] for t in _TR_INDICES_Q0 | _VOL_INDICES_Q0}:
        return 0.0
    if bare in {t.split()[0] for t in _PRICE_INDICES_Q002}:
        return 0.02
    return 0.0


# ---------------------------------------------------------------------------
# Correlation calibration
# ---------------------------------------------------------------------------

_MIN_RETURN_DAYS = 50


def _trailing_log_returns(
    ticker: str,
    on_or_before: date,
    store: LevelStore,
    lookback_days: int = 252,
    min_days: int = _MIN_RETURN_DAYS,
) -> np.ndarray | None:
    """Return the trailing daily log returns ending on-or-before `on_or_before`.

    Uses up to `lookback_days` returns. If fewer are available, falls back to
    whatever is present (>= `min_days`). Returns None if not enough history.
    """
    dates = store._dates.get(ticker)
    levels = store._levels.get(ticker)
    if not dates:
        return None
    idx = bisect.bisect_right(dates, on_or_before) - 1
    # Need at least `min_days + 1` price points to form `min_days` returns.
    if idx < min_days:
        return None
    use_days = min(lookback_days, idx)
    rets = np.empty(use_days, dtype=np.float64)
    j = 0
    for i in range(idx - use_days + 1, idx + 1):
        prev = levels[i - 1]
        cur = levels[i]
        if prev > 0 and cur > 0:
            rets[j] = math.log(cur / prev)
        else:
            rets[j] = 0.0
        j += 1
    return rets


def _nearest_psd_cholesky(corr: np.ndarray, floor: float = 1e-8) -> np.ndarray | None:
    """Eigenvalue-clip a correlation matrix to PSD, then Cholesky.

    Returns the Cholesky factor L such that L @ L.T ≈ corr_psd, or None on failure.
    """
    try:
        eigvals, eigvecs = np.linalg.eigh(corr)
    except np.linalg.LinAlgError:
        return None
    eigvals = np.clip(eigvals, floor, None)
    psd = (eigvecs * eigvals) @ eigvecs.T
    # Re-normalize diagonal to 1.0 so it's a valid correlation matrix.
    d = np.sqrt(np.clip(np.diag(psd), 1e-12, None))
    psd = psd / np.outer(d, d)
    # Symmetrize.
    psd = 0.5 * (psd + psd.T)
    try:
        return np.linalg.cholesky(psd)
    except np.linalg.LinAlgError:
        return None


def _calibrate(
    refs: list[str], issue_date: date, store: LevelStore, lookback_days: int = 252,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int] | None:
    """Compute (sigmas, corr, chol, used_days) on the trailing window.

    Falls back to the shortest available window across all refs (>= 50 days)
    so that early issue dates still produce a usable calibration. Returns
    None on failure.
    """
    n = len(refs)
    rets_per_ref: list[np.ndarray] = []
    for r in refs:
        rets = _trailing_log_returns(r, issue_date, store, lookback_days)
        if rets is None or len(rets) < _MIN_RETURN_DAYS:
            return None
        rets_per_ref.append(rets)
    used_days = min(len(rs) for rs in rets_per_ref)
    if used_days < _MIN_RETURN_DAYS:
        return None
    # Align by taking the trailing `used_days` of each.
    ret_panel = np.empty((used_days, n), dtype=np.float64)
    for i, rs in enumerate(rets_per_ref):
        ret_panel[:, i] = rs[-used_days:]
    # Annualized vols.
    sigmas = ret_panel.std(axis=0, ddof=1) * math.sqrt(252.0)
    if n == 1:
        corr = np.array([[1.0]])
        chol = np.array([[1.0]])
        return sigmas, corr, chol, used_days
    # Pearson correlation.
    corr = np.corrcoef(ret_panel, rowvar=False)
    # Sanitize NaNs from constant series; identity on the diagonal.
    if not np.all(np.isfinite(corr)):
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)
    try:
        chol = np.linalg.cholesky(corr)
    except np.linalg.LinAlgError:
        chol_psd = _nearest_psd_cholesky(corr)
        if chol_psd is None:
            return None
        chol = chol_psd
    return sigmas, corr, chol, used_days


# ---------------------------------------------------------------------------
# Path simulation (vectorized over paths, looping over time steps)
# ---------------------------------------------------------------------------

def _simulate_paths(
    n_paths: int,
    n_obs: int,
    dt_years: float,
    sigmas: np.ndarray,
    chol: np.ndarray,
    r: float,
    qs: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return perfs array shape (n_paths, n_obs, n_refs) of S/S0 at each obs."""
    n_refs = len(sigmas)
    drift = (r - qs - 0.5 * sigmas ** 2) * dt_years  # shape (n_refs,)
    vol_step = sigmas * math.sqrt(dt_years)          # shape (n_refs,)
    # Pre-draw all standard normals: (n_paths, n_obs, n_refs).
    z = rng.standard_normal(size=(n_paths, n_obs, n_refs))
    # Apply Cholesky so eps[..., i] = sum_j chol[i,j] * z[..., j].
    # That's z @ chol.T  (since chol is lower-triangular).
    eps = z @ chol.T
    # Cumulative sum of log increments, exponentiated, gives S/S0.
    log_incs = drift + vol_step * eps  # broadcast
    log_levels = np.cumsum(log_incs, axis=1)
    return np.exp(log_levels)


# ---------------------------------------------------------------------------
# Cashflow logic per path (mirrors simulate_note's payoff branch)
# ---------------------------------------------------------------------------

def _path_cashflows(
    perfs: np.ndarray,
    df: np.ndarray,
    params: NoteParams,
    coupon_pa_pct: float,
) -> float:
    """PV of cashflows for one path. perfs shape (n_obs, n_refs).

    df shape (n_obs,) — discount factor at each obs date from issue_date.
    Returns PV in % of notional (so 100.0 = par).
    """
    n_obs = perfs.shape[0]
    coupon_pct = coupon_pa_pct * params.obs_freq_months / 12.0
    bank_pct = 0.0
    pv = 0.0
    ac_b = params.ac_barrier_pct / 100.0
    cb_b = params.coupon_barrier_pct / 100.0
    pb_b = params.protection_barrier_pct / 100.0
    memory = params.memory
    no_call = params.no_call_months
    obs_freq = params.obs_freq_months

    worst = perfs.min(axis=1)  # shape (n_obs,)

    for k in range(n_obs):
        wp = worst[k]
        months_elapsed = (k + 1) * obs_freq
        is_final = (k == n_obs - 1)

        # Autocall branch.
        if months_elapsed > no_call and wp >= ac_b:
            paid_now = coupon_pct + (bank_pct if memory else 0.0)
            pv += df[k] * (paid_now + 100.0)
            return pv

        # Coupon decision.
        if wp >= cb_b:
            extra = bank_pct if memory else 0.0
            paid_now = coupon_pct + extra
            bank_pct = 0.0
            pv += df[k] * paid_now
        else:
            if memory:
                bank_pct += coupon_pct

        # Final maturity (no autocall).
        if is_final:
            if wp >= pb_b:
                principal = 100.0
            else:
                principal = wp * 100.0
            pv += df[k] * principal
            return pv

    # Should be unreachable.
    return pv


def _mean_pv(
    paths: np.ndarray, df: np.ndarray, params: NoteParams, coupon_pa_pct: float,
) -> float:
    """Mean PV over all paths."""
    n_paths = paths.shape[0]
    total = 0.0
    for i in range(n_paths):
        total += _path_cashflows(paths[i], df, params, coupon_pa_pct)
    return total / n_paths


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def price_par_coupon(
    refs: list[str],
    issue_date: date,
    params: NoteParams,
    store: LevelStore,
    *,
    n_paths: int = 10000,
    risk_free_rate: float = 0.04,
    dividend_yields: dict[str, float] | None = None,
    seed: int | None = 42,
) -> dict:
    """Par-pricing annualized coupon under risk-neutral GBM Monte Carlo.

    Returns dict with 'method' = 'mc' on success or 'fallback' if calibration
    failed (insufficient history). Caller falls back to suggest_coupon().
    """
    lookback_days = 252
    fallback_payload = {
        "coupon_pa_pct": None,
        "realized_vol": {},
        "correlation": [],
        "pv_at_par": None,
        "method": "fallback",
        "n_paths": 0,
        "lookback_days": lookback_days,
        "risk_free_rate": risk_free_rate,
        "dividend_yields": {},
    }

    if not refs:
        return fallback_payload

    # 1) Calibrate vols + correlation from the trailing return panel.
    calib = _calibrate(refs, issue_date, store, lookback_days)
    if calib is None:
        return fallback_payload
    sigmas_arr, corr, chol, used_days = calib
    rv: dict[str, float] = {r: float(sigmas_arr[i]) for i, r in enumerate(refs)}

    # 2) Dividend yields.
    qs_dict: dict[str, float] = {}
    for r in refs:
        if dividend_yields is not None and r in dividend_yields:
            qs_dict[r] = float(dividend_yields[r])
        else:
            qs_dict[r] = _default_q(r)
    qs_arr = np.array([qs_dict[r] for r in refs])

    # 3) Time grid + discount factors.
    n_obs = params.tenor_months // params.obs_freq_months
    dt_years = params.obs_freq_months / 12.0
    obs_year_offsets = np.arange(1, n_obs + 1, dtype=np.float64) * dt_years
    df = np.exp(-risk_free_rate * obs_year_offsets)

    # 4) Simulate paths once.
    rng = np.random.default_rng(seed)
    paths = _simulate_paths(
        n_paths=n_paths, n_obs=n_obs, dt_years=dt_years,
        sigmas=sigmas_arr, chol=chol, r=risk_free_rate, qs=qs_arr, rng=rng,
    )

    # 5) Bisect par coupon. PV is monotone increasing in coupon.
    lo, hi = 0.0, 50.0
    pv_lo = _mean_pv(paths, df, params, lo)
    pv_hi = _mean_pv(paths, df, params, hi)

    # If even hi doesn't reach par, return hi (capped). If lo already exceeds
    # par, return lo.
    if pv_hi < 100.0:
        coupon_final = hi
        pv_final = pv_hi
    elif pv_lo > 100.0:
        coupon_final = lo
        pv_final = pv_lo
    else:
        for _ in range(20):
            mid = 0.5 * (lo + hi)
            pv_mid = _mean_pv(paths, df, params, mid)
            if pv_mid < 100.0:
                lo = mid
            else:
                hi = mid
            if abs(pv_mid - 100.0) < 0.05:
                break
        coupon_final = 0.5 * (lo + hi)
        pv_final = _mean_pv(paths, df, params, coupon_final)

    return {
        "coupon_pa_pct": float(coupon_final),
        "realized_vol": {r: float(rv[r]) for r in refs},
        "correlation": [[float(x) for x in row] for row in corr],
        "pv_at_par": float(pv_final / 100.0),
        "method": "mc",
        "n_paths": int(n_paths),
        "lookback_days": int(used_days),
        "risk_free_rate": float(risk_free_rate),
        "dividend_yields": qs_dict,
    }
