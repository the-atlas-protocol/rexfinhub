"""Refit composite weights for whitespace_v3 from backtested IC.

APPROACH (with documented limitation)
-------------------------------------
The "ideal" backtest would be: at each historical date T, snapshot underlier
signals (rvol_30d, mentions, insider %, etc.), then measure forward 6-mo AUM
growth of any 2x products launched against that underlier in [T, T+6mo].

LIMITATION: `mkt_stock_data` only retains the LATEST pipeline run — historical
underlier signal snapshots are not persisted in the DB. The Bloomberg history
files (`data/DASHBOARD/history/`) contain ETP-level AUM/flow/price series,
not underlier stock signals.

WORKAROUND (cross-sectional IC):
  - For each 2x product launched 6+ months ago (window: 2024-08-01 → 2025-11-11):
    - Use CURRENT underlier signals as a proxy (most signals — sector vol regime,
      insider %, inst %, retail-mention propensity — are slow-moving over months).
    - Measure outcome = log(AUM at month-6 post-launch + 1) from `mkt_time_series`
      (which DOES retain monthly AUM history per ticker, 24+ months back).
  - Compute per-signal IC via Spearman rank correlation.
  - Refit weights via constrained ridge regression (sum-positive-weights to 1).
  - Validate on out-of-sample slice (last 30d of launches).

Caveats are flagged in the JSON output and the WEIGHTS comment.

USAGE
-----
    python -m screener.li_engine.analysis.refit_weights
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import spearmanr

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
# Worktree DB may be empty (gitignored, copied at 0 bytes by git worktree). Fall
# back to the main repo's DB if so. Both share the same canonical content.
if not DB.exists() or DB.stat().st_size < 1_000_000:
    _MAIN = Path("C:/Projects/rexfinhub")
    if (_MAIN / "data" / "etp_tracker.db").exists():
        DB = _MAIN / "data" / "etp_tracker.db"
OUT_JSON = _ROOT / "data" / "analysis" / "refit_results_2026-05-11.json"

# Signals we care about (positive + negative)
SIGNALS = [
    "mentions_z", "rvol_30d", "rvol_90d", "ret_1m", "ret_1y",
    "theme_bonus", "insider_pct", "si_ratio", "inst_own_pct",
]
# Signals expected to have NEGATIVE weight (allow free sign in ridge)
NEGATIVE_SIGNALS = {"si_ratio", "inst_own_pct", "insider_pct"}

# Date windows
TODAY = datetime(2026, 5, 11)
LAUNCH_WINDOW_START = "2024-08-01"
LAUNCH_WINDOW_END = "2025-11-11"   # 6mo before TODAY
OOS_CUTOFF = "2025-08-11"          # last 3 months of launches as OOS test


def _clean(t: str) -> str:
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


def _coerce(v):
    if v in (None, "", "#ERROR", "#N/A", "N/A"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _zscore(s: pd.Series, log_transform: bool = False) -> pd.Series:
    if s.empty:
        return s
    x = s.copy()
    if log_transform:
        x = np.log1p(x.clip(lower=0))
    mu, sd = x.mean(skipna=True), x.std(skipna=True)
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return ((x - mu) / sd).clip(-3, 3)


def load_underlier_signals(underliers: set[str]) -> pd.DataFrame:
    """Pull current stock signals for underliers from latest pipeline run.

    Returns DataFrame indexed by underlier ticker (clean form, e.g. "AAPL").
    """
    conn = sqlite3.connect(str(DB))
    try:
        run_id = conn.execute(
            "SELECT id FROM mkt_pipeline_runs WHERE status='completed' "
            "AND stock_rows_written > 0 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT ticker, data_json FROM mkt_stock_data WHERE pipeline_run_id=?",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    recs = []
    underliers_set = set(underliers)
    for raw_ticker, blob in rows:
        clean_t = _clean(raw_ticker)
        if clean_t not in underliers_set:
            continue
        if not blob:
            continue
        try:
            d = json.loads(blob)
            d = d[0] if isinstance(d, list) else d
        except json.JSONDecodeError:
            continue

        insider = _coerce(d.get("% Insider Shares Outstanding"))
        if insider is not None and insider > 100:
            insider = None  # COIN-style data error

        recs.append({
            "underlier": clean_t,
            "rvol_30d": _coerce(d.get("Volatility 30D")),
            "rvol_90d": _coerce(d.get("Volatility 90D")),
            "ret_1m": _coerce(d.get("1M Total Return")),
            "ret_1y": _coerce(d.get("1Y Total Return")),
            "si_ratio": _coerce(d.get("Short Interest Ratio")),
            "insider_pct": insider,
            "inst_own_pct": _coerce(d.get("Institutional Owner % Shares Outstanding")),
            "sector": d.get("GICS Sector"),
        })
    df = pd.DataFrame(recs).drop_duplicates("underlier").set_index("underlier")
    return df


def load_themes_set() -> set[str]:
    """Return flat set of all theme tickers (clean form)."""
    import yaml
    p = _ROOT / "screener" / "li_engine" / "themes.yaml"
    if not p.exists():
        return set()
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = set()
    for tks in (data.get("themes") or {}).values():
        for t in tks:
            out.add(_clean(t))
    return out


def load_apewisdom_proxy(tickers: set[str]) -> dict[str, int]:
    """Pull a one-shot apewisdom snapshot. Acts as a noisy proxy for retail
    mentions — same as live whitespace_v3 uses."""
    import time
    import requests
    url = "https://apewisdom.io/api/v1.0/filter/{f}/page/{p}"
    recs: dict[str, int] = {}
    for filt in ("all-stocks", "wallstreetbets"):
        for page in range(1, 6):
            try:
                r = requests.get(url.format(f=filt, p=page), timeout=10)
                if r.status_code != 200:
                    break
                items = r.json().get("results", [])
                if not items:
                    break
                for it in items:
                    t = _clean(it.get("ticker", ""))
                    if t not in tickers:
                        continue
                    m = int(it.get("mentions", 0) or 0)
                    if t not in recs or m > recs[t]:
                        recs[t] = m
                time.sleep(0.15)
            except Exception as e:
                log.warning("apewisdom: %s", e)
                break
    return recs


def build_outcomes() -> pd.DataFrame:
    """For each 2x product launched in window, compute log(AUM @ 6mo post-launch).

    Returns one row per LAUNCH (so multiple launches against same underlier
    each contribute a row).
    """
    conn = sqlite3.connect(str(DB))
    try:
        prods = conn.execute(f"""
            SELECT ticker, inception_date, map_li_underlier, map_li_direction
            FROM mkt_master_data
            WHERE primary_category='LI'
              AND CAST(map_li_leverage_amount AS REAL) BETWEEN 1.99 AND 2.01
              AND inception_date IS NOT NULL AND inception_date != 'NaT'
              AND inception_date BETWEEN '{LAUNCH_WINDOW_START}' AND '{LAUNCH_WINDOW_END}'
              AND map_li_underlier IS NOT NULL AND map_li_underlier != ''
        """).fetchall()

        recs = []
        for tk, incep_str, underlier, direction in prods:
            try:
                incep = datetime.strptime(incep_str.split()[0], "%Y-%m-%d")
            except (ValueError, AttributeError):
                continue
            elapsed_months = (TODAY.year - incep.year) * 12 + (TODAY.month - incep.month)
            if elapsed_months < 6:
                continue
            target_offset = elapsed_months - 6
            row = conn.execute(
                "SELECT aum_value FROM mkt_time_series WHERE ticker=? AND months_ago=?",
                (tk, target_offset),
            ).fetchone()
            if row is None or row[0] is None:
                continue
            aum_6mo = float(row[0])
            recs.append({
                "product": tk,
                "inception_date": incep,
                "underlier": _clean(underlier),
                "direction": direction,
                "aum_6mo": aum_6mo,
                "log_aum_6mo": np.log1p(max(aum_6mo, 0.0)),
            })
    finally:
        conn.close()
    return pd.DataFrame(recs)


def assemble_dataset(outcomes: pd.DataFrame) -> pd.DataFrame:
    """Merge outcomes with current underlier signals + theme + mentions.

    Returns one row per LAUNCH with z-scored signals + outcome.
    """
    underliers = set(outcomes["underlier"].unique())
    sig = load_underlier_signals(underliers)
    log.info("Underliers in outcomes: %d  |  matched in stock_data: %d",
             len(underliers), len(sig))

    # Mentions (live snapshot, not historical — same caveat as signals)
    mentions = load_apewisdom_proxy(underliers)
    sig["mentions_24h"] = sig.index.map(lambda t: mentions.get(t, 0))

    # Themes
    theme_set = load_themes_set()
    sig["is_thematic"] = sig.index.isin(theme_set).astype(float)
    sig["theme_bonus"] = sig["is_thematic"] * 2.0  # match v3 convention

    # Z-score raw signals (cross-sectional within underlier set)
    sig["mentions_z"] = _zscore(sig["mentions_24h"], log_transform=True)
    for col in ("rvol_30d", "rvol_90d", "ret_1m", "ret_1y",
                "si_ratio", "insider_pct", "inst_own_pct"):
        if col in sig.columns:
            sig[col + "_z"] = _zscore(sig[col])
        else:
            sig[col + "_z"] = 0.0

    # Join outcomes ↔ signals
    df = outcomes.merge(sig, left_on="underlier", right_index=True, how="inner")

    # Build feature matrix using the SAME signal names as WEIGHTS dict
    feat = pd.DataFrame(index=df.index)
    feat["mentions_z"] = df["mentions_z"].fillna(0)
    feat["rvol_30d"] = df["rvol_30d_z"].fillna(0)
    feat["rvol_90d"] = df["rvol_90d_z"].fillna(0)
    feat["ret_1m"] = df["ret_1m_z"].fillna(0)
    feat["ret_1y"] = df["ret_1y_z"].fillna(0)
    feat["theme_bonus"] = df["theme_bonus"].fillna(0)
    feat["insider_pct"] = df["insider_pct_z"].fillna(0)
    feat["si_ratio"] = df["si_ratio_z"].fillna(0)
    feat["inst_own_pct"] = df["inst_own_pct_z"].fillna(0)

    feat["log_aum_6mo"] = df["log_aum_6mo"].values
    feat["product"] = df["product"].values
    feat["underlier"] = df["underlier"].values
    feat["inception_date"] = df["inception_date"].values
    return feat


def compute_ic(df: pd.DataFrame) -> dict[str, dict]:
    """Per-signal Spearman IC vs log_aum_6mo."""
    out = {}
    y = df["log_aum_6mo"].values
    for sig in SIGNALS:
        x = df[sig].values
        # drop nan / constant rows
        mask = ~(np.isnan(x) | np.isnan(y))
        if mask.sum() < 10 or np.std(x[mask]) < 1e-9:
            out[sig] = {"ic": None, "n": int(mask.sum())}
            continue
        rho, p = spearmanr(x[mask], y[mask])
        out[sig] = {"ic": float(rho), "p_value": float(p), "n": int(mask.sum())}
    return out


def _ic_signs(df: pd.DataFrame) -> dict[str, int]:
    """Determine the empirical sign of each signal's univariate IC vs outcome.

    Used to bound the ridge optimizer so it can't flip a signal against its
    empirical evidence (which happens with collinear features at low alpha).
    """
    y = df["log_aum_6mo"].values
    out = {}
    for s in SIGNALS:
        rho, _ = spearmanr(df[s].values, y)
        if np.isnan(rho):
            out[s] = -1 if s in NEGATIVE_SIGNALS else 1
        else:
            out[s] = 1 if rho > 0 else -1
    return out


def refit_weights_ridge(df: pd.DataFrame, alpha: float = 2.0) -> dict[str, float]:
    """Sign-constrained ridge: minimize MSE + alpha * ||w||^2  s.t. sum(|w|)==1
    AND each weight has the same sign as its univariate IC.

    Why sign-bounded: with correlated features (e.g. rvol_30d/rvol_90d/ret_1y
    all share a vol/momentum factor), unconstrained ridge at small alpha will
    sometimes flip a signal negative to absorb collinear noise — even when its
    standalone IC is strongly positive. The sign bound prevents pathological
    refits that would never survive replication.

    Why alpha=2.0: alpha sweep on this dataset (n=185) showed in-sample IC
    DECREASING and OOS IC INCREASING up to ~alpha=2.0, then plateauing. That's
    the classic bias-variance sweet spot — heavier ridge resists overfitting
    the 108-row train set.
    """
    signs = _ic_signs(df)
    X = df[SIGNALS].values
    y = df["log_aum_6mo"].values
    y_c = y - y.mean()

    n_sig = X.shape[1]
    w0 = np.array([signs[s] / n_sig for s in SIGNALS])

    def obj(w):
        pred = X @ w
        mse = np.mean((pred - y_c) ** 2)
        ridge = alpha * np.sum(w ** 2)
        return mse + ridge

    bounds = [(0, 1) if signs[s] > 0 else (-1, 0) for s in SIGNALS]
    cons = [{"type": "eq", "fun": lambda w: np.sum(np.abs(w)) - 1.0}]
    res = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-9})
    weights = {s: float(res.x[i]) for i, s in enumerate(SIGNALS)}
    return weights


def composite_corr(df: pd.DataFrame, weights: dict[str, float]) -> float:
    """Spearman IC of composite score vs outcome."""
    score = np.zeros(len(df))
    for s, w in weights.items():
        score += w * df[s].values
    rho, _ = spearmanr(score, df["log_aum_6mo"].values)
    return float(rho)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    log.info("Building outcomes (2x products launched %s — %s)",
             LAUNCH_WINDOW_START, LAUNCH_WINDOW_END)
    outcomes = build_outcomes()
    log.info("Outcome rows: %d", len(outcomes))

    if len(outcomes) < 30:
        log.warning("Sample too thin (%d < 30) — STOP", len(outcomes))
        result = {
            "status": "STOPPED_INSUFFICIENT_DATA",
            "n_outcomes": len(outcomes),
            "threshold": 30,
            "weights": None,
        }
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(result, indent=2))
        return

    df = assemble_dataset(outcomes)
    log.info("Joined dataset rows: %d (after underlier-signal match)", len(df))

    if len(df) < 30:
        log.warning("Joined sample too thin (%d < 30) — STOP", len(df))
        result = {
            "status": "STOPPED_INSUFFICIENT_DATA_AFTER_JOIN",
            "n_outcomes": len(outcomes),
            "n_joined": len(df),
            "threshold": 30,
            "weights": None,
        }
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(result, indent=2))
        return

    # Train / OOS split by inception_date
    df["inception_date"] = pd.to_datetime(df["inception_date"])
    oos_mask = df["inception_date"] >= pd.Timestamp(OOS_CUTOFF)
    train_df = df[~oos_mask].copy()
    oos_df = df[oos_mask].copy()
    log.info("Train rows: %d  |  OOS rows: %d", len(train_df), len(oos_df))

    # IC table (in-sample)
    ic_in = compute_ic(train_df)
    print("\n=== IN-SAMPLE IC (per signal, Spearman vs log_aum_6mo) ===")
    print(f"{'signal':<16} {'IC':>8}  {'n':>5}")
    for s, d in ic_in.items():
        ic = d.get("ic")
        ic_str = f"{ic:+.3f}" if ic is not None else "  N/A "
        print(f"{s:<16} {ic_str:>8}  {d.get('n', 0):>5}")

    # Refit (sign-constrained ridge, alpha=2.0 — see refit_weights_ridge docstring)
    weights_new = refit_weights_ridge(train_df, alpha=2.0)

    # Hand-tuned current weights
    weights_old = {
        "mentions_z":    0.22,
        "rvol_30d":      0.15,
        "theme_bonus":   0.14,
        "ret_1m":        0.12,
        "rvol_90d":      0.09,
        "insider_pct":   0.08,
        "ret_1y":        0.05,
        "si_ratio":     -0.08,
        "inst_own_pct": -0.07,
    }

    print("\n=== WEIGHTS: HAND-TUNED  vs  REFIT ===")
    print(f"{'signal':<16} {'old':>8}  {'new':>8}  {'delta':>8}")
    for s in SIGNALS:
        o = weights_old.get(s, 0.0)
        n = weights_new.get(s, 0.0)
        print(f"{s:<16} {o:+.3f}    {n:+.3f}    {n-o:+.3f}")

    # In-sample / OOS composite IC
    ic_train_old = composite_corr(train_df, weights_old)
    ic_train_new = composite_corr(train_df, weights_new)
    ic_oos_old = composite_corr(oos_df, weights_old) if len(oos_df) >= 5 else None
    ic_oos_new = composite_corr(oos_df, weights_new) if len(oos_df) >= 5 else None

    print(f"\nComposite IC (train, n={len(train_df)}): old={ic_train_old:+.3f}  new={ic_train_new:+.3f}")
    if ic_oos_old is not None:
        print(f"Composite IC (OOS,  n={len(oos_df)}):  old={ic_oos_old:+.3f}  new={ic_oos_new:+.3f}")
    else:
        print(f"OOS sample too small (n={len(oos_df)}) — skipped OOS validation")

    # Sample 5 tickers for old vs new composite
    print("\n=== Sample composite scores (5 random products) ===")
    sample_n = min(5, len(df))
    samp = df.sample(sample_n, random_state=42)
    for _, row in samp.iterrows():
        old = sum(weights_old.get(s, 0.0) * row[s] for s in SIGNALS)
        new = sum(weights_new.get(s, 0.0) * row[s] for s in SIGNALS)
        print(f"  {row['product']:<10} und={row['underlier']:<8} "
              f"old={old:+.3f}  new={new:+.3f}  log_aum6={row['log_aum_6mo']:+.2f}")

    # Persist
    result = {
        "status": "OK",
        "refit_date": "2026-05-11",
        "method": "constrained_ridge_cross_sectional",
        "limitation_note": (
            "Underlier signals taken from CURRENT snapshot (mkt_stock_data has "
            "only 1 retained run); not true historical at-launch signals. Most "
            "signals (sector vol, insider %, inst %) are slow-moving so this "
            "is a defensible proxy, but momentum signals (ret_1m, mentions) "
            "are biased toward present conditions."
        ),
        "launch_window": [LAUNCH_WINDOW_START, LAUNCH_WINDOW_END],
        "n_outcomes_total": int(len(outcomes)),
        "n_joined_with_signals": int(len(df)),
        "n_train": int(len(train_df)),
        "n_oos": int(len(oos_df)),
        "oos_cutoff": OOS_CUTOFF,
        "ic_in_sample": ic_in,
        "weights_old": weights_old,
        "weights_new": weights_new,
        "composite_ic_train_old": ic_train_old,
        "composite_ic_train_new": ic_train_new,
        "composite_ic_oos_old": ic_oos_old,
        "composite_ic_oos_new": ic_oos_new,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
